#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo5 后台任务层 — job 注册表（后台命令 + 后台 Subagent 的公共底座）

    思路对齐 Claude Code 的后台任务机制：把「跑得久、结束后要告诉模型」的活
    抽象成 job——起进程/线程不等它结束、输出落盘日志文件、watcher 盯完成状态、
    完成后把 <task-notification> 通知注入下一次模型请求的 messages。

    shell job：subprocess.Popen 起进程（不 wait），stdout/stderr 重定向日志，
               watcher 线程盯结束；kill = 终止进程树
    agent job：subagent 的子循环跑在线程里，trace 写日志、最终报告挂 job.result，
               kill = 协作式停止标志（循环每轮检查）

教学减法（相对 Claude Code）：单份全局注册表（子 agent 起的后台命令也进这里，
不会随子 agent 结束自动清理）；空闲唤醒简化为"主循环结束后等待 + 注入通知"，
不做输入框旁路的自动唤醒。
"""

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field

from render import print_step


JOBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs")

# system prompt 追加的后台用法引导（工具描述管「怎么填」，这里管「什么时候用」）
JOBS_GUIDANCE = (
    "\n\n## 后台任务\n\n"
    "长驻或耗时命令（dev server、长测试）用 execute_bash 的 run_in_background=True "
    "放到后台执行——只在不需要立刻拿到结果时使用。后台 job 结束后你会收到 "
    "<task-notification> 通知，所以**不要主动轮询等待**（不要反复用 read_file 读日志）；"
    "期间确有需要时可以用 read_file 看一次已有输出，也可以用 job_kill 提前终止。"
    "收到与后台任务无关的新问题时就正常回答，**不要在每个回复里重复后台任务的运行状态**。"
)


def _new_job_id(prefix: str) -> str:
    """b 前缀 = shell job，a 前缀 = agent job；随机后缀防重启覆盖旧日志"""
    import random
    import string
    return prefix + "".join(random.choices(string.digits + string.ascii_lowercase, k=8))


@dataclass
class Job:
    id: str
    kind: str                     # "shell" / "agent"
    description: str
    log_path: str
    status: str = "running"       # running / completed / failed / killed
    returncode: int | None = None
    result: str | None = None     # agent job 的最终报告（shell job 的输出在日志里）
    notified: bool = False
    _kill_func: object = None
    _stop_event: threading.Event = field(default_factory=threading.Event)

    def summary(self) -> str:
        text = {  # status → 一句话总结
            "completed": "执行成功",
            "failed":    "执行失败",
            "killed":    "已被终止",
        }.get(self.status, "运行中")
        if self.returncode is not None and self.kind == "shell":
            text += f"，exit code {self.returncode}"
        return f"后台{('命令' if self.kind == 'shell' else ' Subagent')}「{self.description[:50]}」{text}"


class JobRegistry:
    """单份全局注册表（模块级 REGISTRY 单例，agent.py 启动时使用）"""

    def __init__(self) -> None:
        os.makedirs(JOBS_DIR, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self.on_complete = None   # agent.py 注入的完成提示回调（可取消 prompt 用）

    # ------------------------------------------------------------------
    # shell job：Popen 起进程，不等它结束
    # ------------------------------------------------------------------

    def spawn_shell(self, command: str) -> Job:
        job_id = _new_job_id("b")
        log_path = os.path.join(JOBS_DIR, f"{job_id}.log")
        log_file = open(log_path, "wb")
        # stdout/stderr 合并重定向到日志文件；start_new_session 起独立进程组（为整树终止）
        proc = subprocess.Popen(
            command, shell=True, stdout=log_file, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        job = Job(id=job_id, kind="shell", description=command, log_path=log_path)
        job._kill_func = lambda: _kill_process_tree(proc.pid)
        self._jobs[job_id] = job
        # watcher 线程盯进程结束，更新状态 + 终端提示
        threading.Thread(target=self._watch_shell, args=(job, proc, log_file), daemon=True).start()
        return job

    def _watch_shell(self, job: Job, proc, log_file) -> None:
        returncode = proc.wait()
        log_file.close()
        if job.status == "killed":
            return
        job.returncode = returncode
        job.status = "completed" if returncode == 0 else "failed"
        self._notify(f"[后台] {job.summary()}（{job.log_path}）")

    def _notify(self, text: str) -> None:
        """job 完成的终端提示。agent.py 可注入回调：先取消活跃的 prompt 再打印
        （提示符与主线程输出不能共存），否则直接打印。"""
        if self.on_complete is not None:
            self.on_complete(text)
        else:
            print_step("tool_return", text, limit=200)

    # ------------------------------------------------------------------
    # agent job：subagent 子循环跑在线程里
    # ------------------------------------------------------------------

    def spawn_agent(self, description: str, run_fn) -> Job:
        job_id = _new_job_id("a")
        log_path = os.path.join(JOBS_DIR, f"{job_id}.log")
        log_file = open(log_path, "a", encoding="utf-8")
        log_file.write(f"=== Subagent {job_id} ===\ndescription: {description}\n\n")
        log_file.flush()
        job = Job(id=job_id, kind="agent", description=description, log_path=log_path)
        self._jobs[job_id] = job

        def _run() -> None:
            try:
                job.result = run_fn(job, log_file)
                if job.status != "killed":
                    job.status = "completed"
            except Exception as e:
                job.result = f"[错误] {e}"
                job.status = "failed"
            finally:
                log_file.write(f"\n=== 最终报告 ===\n{job.result}\n")
                log_file.close()
                self._notify(f"[后台] {job.summary()}（{log_path}）")

        threading.Thread(target=_run, daemon=True).start()
        return job

    # ------------------------------------------------------------------
    # 终止 / 查询 / 通知 / 清理
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def kill(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status != "running":
            return False
        job.status = "killed"
        job._stop_event.set()          # agent job：协作式停止（循环每轮检查）
        if job._kill_func:             # shell job：终止进程树
            job._kill_func()
        return True

    def has_running(self) -> bool:
        return any(j.status == "running" for j in self._jobs.values())

    def has_unnotified(self) -> bool:
        return any(j.status != "running" and not j.notified for j in self._jobs.values())

    def running_jobs(self) -> list:
        return [j for j in self._jobs.values() if j.status == "running"]

    def pop_unnotified(self) -> list:
        """取出「已结束但还没通知」的 job，取出即标记已通知（防重复通知）"""
        done = [j for j in self._jobs.values()
                if j.status != "running" and not j.notified]
        for j in done:
            j.notified = True
        return done

    def build_notifications(self) -> list:
        """把待通知的 job 拼成 <task-notification> 文本（注入下一次请求的 messages）"""
        notes = []
        for job in self.pop_unnotified():
            result = f"<result>{job.result}</result>\n" if job.result else ""
            notes.append(
                "<task-notification>\n"
                f"<task-id>{job.id}</task-id>\n"
                f"<task-type>{job.kind}</task-type>\n"
                f"<output-file>{job.log_path}</output-file>\n"
                f"<status>{job.status}</status>\n"
                f"<summary>{job.summary()}，可用 read_file 读输出文件</summary>\n"
                f"{result}"
                "</task-notification>"
            )
        return notes

    def shutdown(self) -> int:
        """退出时清理：终止所有还在跑的 job，避免进程/线程泄露"""
        running = self.running_jobs()
        for job in running:
            self.kill(job.id)
        return len(running)


def _kill_process_tree(pid: int) -> None:
    """Windows 下终止整个进程树（taskkill /T）"""
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=10)
    except Exception:
        pass


# ============================================================
# job_kill 工具：模型可以主动终止一个不再需要的 job
# ============================================================

REGISTRY = JobRegistry()

JOB_KILL_TOOL = {
    "name": "job_kill",
    "description": (
        "终止一个后台 job（shell 命令或 Subagent）。"
        "用不到某个后台任务时调用，job id 见放入后台时的返回信息。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "要终止的 job id"},
        },
        "required": ["job_id"],
    },
}


def job_kill(job_id: str) -> str:
    """终止后台 job。"""
    job = REGISTRY.get(job_id)
    if job is None:
        return f"[错误] job {job_id} 不存在"
    if not REGISTRY.kill(job_id):
        return f"job {job_id} 已经结束（{job.status}），无需终止"
    return f"job {job_id} 已终止"
