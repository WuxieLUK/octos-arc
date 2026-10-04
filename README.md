# Octos ARC — Full Rewrite Agent

> 面向 **ARC-Bench Web 任务** 的自主实现 Agent：从空白 starter 出发，理解需求树，通过模型工具调用完成 React/Vite + Express/SQLite 应用的实现、构建、启动与本地验收。项目不依赖 Octos，也不复用 v5 编排循环。

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![ARC-Bench](https://img.shields.io/badge/ARC--Bench-agent-8A2BE2)](#)
[![Stack](https://img.shields.io/badge/Stack-React%20%2B%20Vite%20%2B%20Express%20%2B%20SQLite-61DAFB?logo=react&logoColor=white)](#)
[![Tests](https://img.shields.io/badge/tests-unittest-blue)](#tests)

## 它做什么

1. 从 ARC-Bench 空白 starter 初始化工作区，只补缺失文件、不覆盖已有文件。
2. 解析需求树：兼容 `REQ-1.2` 与 `REQ-1-1-1` 两种 ID，自动依赖排序并拒绝环。
3. 在受控工具集中执行“读取需求 → 写代码 → 构建 → 启动 → 验证 → 修复”循环。
4. 交付前验收：前端构建成功、`GET /api/health` 返回成功、根路径可用、未知 URL 返回 404；可选运行前后端测试。
5. 写入 ARC-Bench 要求的 runner events、traceability 与 git 记录。

## 为什么有竞争力

- **Task-agnostic 设计**：不针对具体题硬编码，只依赖 requirements tree 与通用工具契约。
- **可验证交付**：不是“生成完就结束”，而是真实 build + start + 接口断言全部通过才算完成。
- **受控工具边界**：路径沙箱、接口记录只接受真实存在的源文件，不伪造 traceability。
- **上下文压缩与 checkpoint**：长任务中保留初始契约、近期工作笔记与关键文件列表，避免丢失目标。
- **视觉参考双通道**：可用 `VISUAL_*` 独立配置，未配置时回退主模型；支持 DeepSeek Flash 原生视觉模型。

## 快速开始

```bash
git clone https://github.com/WuxieLUK/octos-arc.git
cd octos-arc
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

运行入口：

```bash
python3 main.py /path/to/requirements --output-dir /path/to/output --type web
```

Runner 至少注入以下环境变量：

| 变量 | 是否必需 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | 必需 | 主模型 API Key |
| `OPENAI_BASE_URL` | 必需 | OpenAI 兼容 Chat 端点 |
| `MODEL` | 必需 | 主模型名称 |
| `VISUAL_API_KEY` | 可选 | 独立视觉模型 Key，未配置回退主模型 |
| `VISUAL_BASE_URL` | 可选 | 独立视觉模型端点 |
| `VISUAL_MODEL` | 可选 | 视觉模型名称 |

运行限制均可通过环境变量调优：`ARC_AGENT_TIME_BUDGET`、`ARC_AGENT_MAX_TOOL_ROUNDS`、`ARC_AGENT_REQUEST_TIMEOUT`、`ARC_AGENT_MAX_OUTPUT_TOKENS`、`ARC_AGENT_MAX_FILE_BYTES`。

## 工作流程

```text
starter 初始化
   → 需求树解析（依赖排序 / 环检测 / 完整 scenario）
   → 模型工具循环（inspect → implement → build → start → validate）
   → 修复与 checkpoint
   → 最终验收（frontend build / health / root / 404）
   → traceability 与 runner events 落盘
```

## 模块

| 路径 | 说明 |
|---|---|
| `arc_agent/requirements_tree.py` | YAML 解析、依赖排序、prompt 序列化 |
| `arc_agent/model_client.py` | OpenAI 兼容 chat / tool-call 传输，带重试与用量统计 |
| `arc_agent/workspace_tools.py` | 受控源码工具与 SDK 接口记录 |
| `arc_agent/validator.py` | 构建、启动与 HTTP 契约校验 |
| `arc_agent/orchestrator.py` | 模型 / 工具循环、starter 初始化、deadline、最终验收与 checkpoint |
| `arc_agent/config.py` | 环境变量与运行限制解析 |
| `arcbench-agent-runtime/` | runner events、traceability、gitops 辅助包 |
| `skills/` | checkpoint、runtime-signals、traceability 三个 ARC-Bench skill |
| `template/` | React + Vite + Express + SQLite 生成模板 |
| `tests/` | agent core 单元测试 |

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 边界

- 当前目标类型为 `web`，使用内置 React/Vite + Express/SQLite 模板。
- 仅实现 agent 侧生成逻辑，不依赖 Octos 或 v5 编排循环。
- 视觉分析是可选增强；未配置 `VISUAL_*` 时依赖文字需求继续执行。
