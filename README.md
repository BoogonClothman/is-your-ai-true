# Is Your AI True -- 你的AI保真吗？

基于 **PyQt6** 的桌面 GUI 应用，用于调试与测试 AI / LLM API 端点。
支持流式与非流式请求、Markdown 渲染、性能与用量统计。

支持三种主流协议：

| 协议 | 端点 | 说明 |
| --- | --- | --- |
| OpenAI Chat Completions | `/v1/chat/completions` | OpenAI 官方及市面绝大多数兼容中转 |
| OpenAI Responses | `/v1/responses` | 注意其 SSE 为命名事件、用量字段为 `input_tokens` / `output_tokens` |
| Anthropic Messages | `/v1/messages` | `system` 消息自动置于顶层，`max_tokens` 必填（留空时自动补 4096） |

## 核验与归档

本工具的根本用途之一是**核验端点声称的模型与实际返回的模型是否一致**，
并把每次回复完整留档备查：

- **模型核验**：请求时填写的模型 ID 与响应中实际报告的模型 ID
  （Chat Completions 的 `model`、Responses 的 `response.model`、Anthropic 的 `model`）
  进行比对，一致绿色标注、不一致红色告警；响应未报告时如实标注
- **指纹提取**：从响应中提取 `response id`、`system_fingerprint`（fp_*）、
  `service_tier`（Anthropic 不提供指纹）
- **思维链**：OpenAI 的 `reasoning_content` / `reasoning_summary_text`、
  Anthropic 的 `thinking` 块会以**灰色区块**渲染，与正文区分
- **完整结构落盘**：每次请求（含成功 / 失败 / 被停止）都以
  `responses.jsonl` 按行追加完整结构，包括：
  - 请求快照（协议、Base URL、模型、消息、参数、实际请求体；API Key 脱敏为 `sk-…xxxx`）
  - 响应元数据（response id、报告模型、指纹、服务层级、用量、核验结果）
  - 正文与思维链全文
  - 原始结构（非流式为完整响应 payload；流式为全部 SSE 事件）

归档位置：`%AppData%\is-your-ai-true\records\responses.jsonl`
（界面「核验与归档」区可直接打开所在目录）。

## 功能

- **请求构造**：端点 URL、API Key（可显示/隐藏）、模型、超时、附加请求头
- **消息编辑**：system / user / assistant 角色列表，逐条增删
- **采样参数**：temperature / top_p / max_tokens，各自可勾选「覆盖」来控制是否发送
- **流式输出**：SSE 逐 token 显示，Markdown 渲染（含代码块、表格），自动跟随滚动
- **统计**：TTFT（首 token 耗时）、总耗时、生成耗时、吞吐（tokens/s）、字符数、用量
- **核验与归档**：见上节
- **调试视图**：原始事件流（`渲染` / `原始事件` 两个标签页）
- **预设管理**：端点配置保存为预设（持久化到本地），可一键切换 / 删除
- **错误处理**：从 HTTP 错误响应体提取可读消息与原始详情

## 快速开始

早期阶段暂时与开发时期保持一致，要求 Python >= 3.15，使用 [uv](https://docs.astral.sh/uv/) 管理环境：

```powershell
uv sync            # 安装依赖
uv run python -m is_your_ai_true     # 启动应用
# 或者：
uv run is-your-ai-true
```

## 使用流程

1. 左侧面板选择**协议**（会自动填入官方默认 URL 与模型候选）
2. 填入 Base URL（可指向官方或任意兼容网关）、API Key、模型名
3. 添加消息（至少一条）
4. 按需调整采样参数、取消勾选「流式输出」可切换非流式模式
5. 点击**发送**；请求过程中可点击**停止**中断
6. 右侧查看渲染结果 / 原始事件 / 统计指标
7. 保存常用端点为**预设**（工具栏，`Ctrl+S`），下次启动自动恢复

> 统计中的 tokens 数优先使用端点返回的 usage；
> 端点未返回 usage 时按 4 字符/token 粗略估算（吞吐指标同理）。

## 项目结构

```
src/is_your_ai_true/
├── __init__.py            # 应用入口 main()
├── core/
│   ├── models.py          # 端点配置 / 消息 / 采样参数
│   ├── events.py          # 统一流事件（Delta / Reasoning / Usage / Metadata / Error）
│   ├── sse.py             # SSE 流解析
│   ├── stats.py           # TTFT / 吞吐 / 用量统计
│   ├── providers.py       # 三种协议适配器（含思维链与元数据提取）
│   ├── record.py          # ResponseRecord：模型核验 / 指纹 / 完整结构
│   ├── archive.py         # JSONL 归档
│   └── worker.py          # 后台 QThread 请求线程（信号驱动 UI + 归档）
└── ui/
    ├── request_panel.py   # 左侧：请求构造
    ├── response_panel.py  # 右侧：渲染 / 原始事件 / 核验与归档 / 统计
    └── main_window.py     # 主窗口与预设持久化
```

## 测试

```powershell
uv run pytest tests/ -q                       # 单元测试（SSE 解析 / 协议适配 / 统计）
uv run python tests/smoke_stream.py           # 端到端冒烟（本地 SSE 模拟服务器 + worker）
```
