# Mini Agent

一个极简的 AI Agent，通过 LLM 交互执行本地工具。

## 功能

- 🤖 基于 Claude 模型的智能对话
- 🛠️ 三个核心工具：执行命令、读写文件
- 🔄 自动循环直到任务完成
- 💬 支持交互模式和单次执行模式

## 安装

```bash
# 克隆仓库
git clone https://github.com/alicexl/mini-agent.git
cd mini-agent

# 安装依赖
pip install -r requirements.txt
```

## 配置

### 环境变量（推荐）

```bash
# 必填：API Key
export ANTHROPIC_API_KEY=your_api_key_here

# 可选：自定义 API 地址（默认使用官方地址）
export ANTHROPIC_BASE_URL=https://api.anthropic.com

# 可选：模型名称（默认 claude-sonnet-4-6）
export ANTHROPIC_MODEL=claude-sonnet-4-6
```

Windows:
```cmd
set ANTHROPIC_API_KEY=your_api_key_here
set ANTHROPIC_BASE_URL=https://api.anthropic.com
set ANTHROPIC_MODEL=claude-sonnet-4-6
```

### 代码中传入

```python
from src.llm_client import LLMClient
from src.agent import Agent

llm_client = LLMClient(
    api_key="your_api_key",           # 必填
    base_url="https://api.anthropic.com",  # 可选
    model="claude-sonnet-4-6"         # 可选
)
agent = Agent(llm_client)
```

### 参数说明

| 参数 | 环境变量 | 默认值 | 说明 |
|------|----------|--------|------|
| `api_key` | `ANTHROPIC_API_KEY` | - | API 密钥（必填） |
| `base_url` | `ANTHROPIC_BASE_URL` | 官方地址 | API 服务地址 |
| `model` | `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | 模型名称 |

## 使用

### 交互模式

```bash
python run.py
```

```
Mini Agent 已启动，输入 'quit' 或 'exit' 退出

用户: 创建一个 hello.txt 文件，内容是 Hello World

[调用工具...]
  - write_file({'path': 'hello.txt', 'content': 'Hello World'})

助手: 文件 hello.txt 已成功创建，内容为 "Hello World"，共 11 个字符。

用户: 读取 hello.txt 的内容

[调用工具...]
  - read_file({'path': 'hello.txt'})

助手: 文件内容是：
Hello World

用户: quit
再见！
```

### 单次执行模式

```bash
python run.py "列出当前目录的文件"

python run.py "创建一个 test 目录"

python run.py "读取 config.json 文件"
```

### 交互命令

| 命令 | 说明 |
|------|------|
| `quit` / `exit` / `q` | 退出程序 |
| `clear` | 清空对话历史 |

## 工具说明

| 工具 | 功能 | 参数 |
|------|------|------|
| `execute_bash` | 执行 shell 命令 | `command`: 命令字符串 |
| `read_file` | 读取文件内容 | `path`: 文件路径 |
| `write_file` | 写入文件内容 | `path`: 文件路径, `content`: 文件内容 |

## 项目结构

```
mini-agent/
├── run.py                 # 主入口
├── requirements.txt       # 依赖
├── DESIGN.md              # 设计文档
└── src/
    ├── llm_client.py      # LLM 客户端
    ├── agent.py           # Agent 循环
    └── tools/
        ├── definitions.py # 工具定义
        └── executor.py    # 工具实现
```

## 测试

```bash
python -m pytest test/ -v
```

## 了解更多

详细的设计思路和源码解读，请参阅 [DESIGN.md](DESIGN.md)

## License

MIT
