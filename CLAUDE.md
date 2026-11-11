# mini-agent

demo(N) = base × 轴N 六 demo；讲稿↔代码↔README 四向同步。

## demo 启动（各 demo 的 agent.py）

这些 demo 走智谱 BigModel Anthropic 兼容网关 + `glm-5.2`（`base_url` / `model` 均已写死在 agent.py）。

**启动命令**（Key 格式 `id.secret`，在 bigmodel.cn 生成；`-X utf8` 防中文乱码）：

```bash
cd /d/workspace/mini-agent/demo4-plan && \
  env -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL -u ANTHROPIC_DEFAULT_SONNET_MODEL \
  ANTHROPIC_API_KEY=你的智谱key python -X utf8 agent.py
```

- **为什么必须 `env -u ANTHROPIC_*`**：Claude Code harness 在环境里设了 `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` 等变量（指向当前 provider，如 DeepSeek）。Anthropic SDK 会同时读取构造函数参数和环境变量，把两套配置**合并**——结果一条请求里同时带 `x-api-key`（智谱正确 key）和 `authorization: Bearer`（harness 的 key），智谱不认识 Bearer → 401。**demo 与 harness 不同 provider 时必须清掉 harness 变量**，否则双 auth header 冲突。
- **API Key 走 env，别硬编码**：设了 `ANTHROPIC_API_KEY` 就免改代码。两版差异——demo1–5（已迁移多文件结构：agent.py + tools.py / memory.py / mcp.py / plan.py / skill.py / ask.py / subagent.py + render.py + commands.py）：`os.environ.get("ANTHROPIC_API_KEY") or ""`，仅 env → 交互式输入两级回退，代码内无常量；demo6（单文件版）：`os.environ.get("ANTHROPIC_API_KEY") or API_KEY`，仍保留代码内 `API_KEY` 常量；demo7（LangGraph 对照篇）：新增 **`.env` 文件**三级回退（env var > `demo7-langgraph/.env` > 交互式；模板 `.env.example`，已被 .gitignore 覆盖）。
- **demo7 依赖**：`langgraph` + `langchain-anthropic` + `langgraph-checkpoint-sqlite`（见 demo7 requirements.txt），与裸 anthropic SDK 的 demo1–6 不同；`ChatAnthropic` 同样读 `ANTHROPIC_*` 环境变量，双 auth 坑照旧。管道运行时审批门自动 deny（安全优先）。
- **交互式 REPL**：自动跑用管道 `printf '任务\n/quit\n' | env -u ANTHROPIC_AUTH_TOKEN ... ANTHROPIC_API_KEY=xxx python -X utf8 agent.py`（demo1/2 新结构命令纯斜杠，退出用 `/quit`；`agent_single.py` 原始版仍可用裸 `quit`）。
- **依赖**：`pip install -r requirements.txt`（`anthropic` SDK）。
