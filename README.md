# Octos ARC · ARC-Bench 初赛前测试阶段

> 这个仓库对应 ARC-Bench 黑客松里，**初赛（9.24–9.30）之前**的测试阶段：拿 12306、携程、Keep、BookStack、StackOverflow 这些公开基准题，把 Agent 从“能跑”磨到“会自己发现并解决问题”。

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![ARC-Bench](https://img.shields.io/badge/ARC--Bench-agent-8A2BE2)](#)
[![Stack](https://img.shields.io/badge/Stack-React%20%2B%20Vite%20%2B%20Express%20%2B%20SQLite-61DAFB?logo=react&logoColor=white)](#)

## 一句话背景

ARC-Bench 不是“写一个网站”，而是一条完整链路：

```text
需求文档 → Agent → 生成应用 → 构建 / 启动 → Playwright 自动评测 → 得分
```

Agent 拿到一份陌生需求，要自己理解、写代码、跑起来、测出来、发现问题、修掉，最后交出一个能被浏览器自动验收的应用。`octos-arc` 就是这条链路里的 Agent。

## 初赛前，我们在测什么

初赛是两个未知赛题，但赛前平台给了公开基准题。我们把它们当成“照妖镜”，一道一道本地跑：

| 题目 | 类型 | 测试结论 |
| --- | --- | --- |
| `smoke--dice` | 冒烟 | **100% 通过** |
| `smoke--counter` | 冒烟 | 本地复现通过；早期在前后端构建环节卡过 |
| `arc-bench-web--12306` | 铁路购票 | 本地复现打磨（订票题默认连 3301 端口，平台起在 3000，为此补了双端口监听） |
| `arc-bench-web--ctrip` | 旅行预订 | 本地复现打磨 |
| `arc-bench-web--keep` | 健身 | **32/32 通过**，代价巨大：~127 min / ~1930 万 tokens / ~16.77 CNY / 315 次请求 / 147 次 repair |
| `arc-bench-web--bookstack` | 文档站 | 本地复现打磨 |
| `arc-bench-web--prestashop` | 电商 | 本地复现打磨 |
| `arc-bench-web--stackoverflow` | 问答社区 | **65/66 通过**，但约 9.9 h / ~2.436 亿 tokens / ~111.95 CNY / 30 个 repair turns |

> 说明：12306 / ctrip / bookstack / prestashop 这些题当时主要用于本地复现和打磨，没有保留公开分数的就不编数字；Keep 与 StackOverflow 是当时记录最完整的两道。

## 时间线（初赛前）

- **09-17** 第一次 smoke 摸底：Dice **100% 通过**、Lite 前端 `npm run build` 失败 → 定下「本地复现 + 看真实日志」的排障方法。
- **09-17 ~ 09-23** 用官方本地模拟环境（[code-philia/hackathon-local-simulation](https://github.com/code-philia/hackathon-local-simulation)）逐题跑公开基准。
- **Keep 32/32** → 第一次意识到「能做对」不等于「设计好」。
- **StackOverflow 65/66** → 锁定 repair 是最大的成本黑洞。
- **09-23** 建立 GitHub 版本管理，v1/v2/v3 与同学同步开发，所有 bundle 可追溯。
- **09-24 起** 进入初赛（两个未知赛题）。

## 这个阶段最重要的三个认知

1. **能跑通 ≠ 设计好。** Keep 32/32，但花了 127 分钟、1930 万 tokens——对 Agent 来说，通过不是唯一目标，代价同样重要。
2. **Repair 是成本黑洞。** StackOverflow 65/66 的背后是 9.9 小时、2.436 亿 tokens。无脑「重观察 → 重思考 → 重改 → 全量重测」会把任务复杂度直接放大成账单。
3. **不要 task-specific optimization。** 我们坚决不给 12306、Keep、BookStack 各写一套策略；要的是「以不变应万变」的稳定流水线，而不是背题。

## 测试阶段的做法

```text
需求树 → 拓扑排序 → 逐节点[设计 → 实现 → 本地验收 → 修复 ≤ 5 轮 → commit] → 打包上传
```

- 本地验收直接复用平台原版 Playwright 测试，改前改后跑同一套，只比数字。
- 守护规则：未验证不得宣称完成、连续同一错误触发止损、保护路径不可改。
- 订票类题目的端口陷阱：测试默认连 3301，平台起在 3000，所以后端两个端口都监听。

## 当前代码（本仓库）

初赛前的测试阶段，实际跑 12306 等公开题的实现基于 Octos 内核 + `arc/` 适配层；当前仓库里的 `arc_agent/` 是那之后的轻量重写，**不依赖 Octos**。

```text
main.py                  # 入口
arc_agent/               # 重写后的 Agent：需求树 / 模型工具循环 / 校验 / 编排
arcbench-agent-runtime/  # runner events / traceability / gitops
skills/                  # ARC-Bench skills
template/                # Web 应用 starter
examples/                # 模型调用与 SDK 示例
```

| 模块 | 说明 |
| --- | --- |
| `arc_agent/requirements_tree.py` | YAML 解析、依赖排序、prompt 序列化 |
| `arc_agent/model_client.py` | OpenAI 兼容 chat / tool-call，带重试与用量统计 |
| `arc_agent/workspace_tools.py` | 受控源码工具与 SDK 接口记录 |
| `arc_agent/validator.py` | 构建、启动与 HTTP 契约校验 |
| `arc_agent/orchestrator.py` | 模型 / 工具循环、deadline、最终验收与 checkpoint |
| `arc_agent/config.py` | 环境变量与运行限制解析 |

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
| --- | --- | --- |
| `OPENAI_API_KEY` | 必需 | 主模型 API Key |
| `OPENAI_BASE_URL` | 必需 | OpenAI 兼容 Chat 端点 |
| `MODEL` | 必需 | 主模型名称 |
| `VISUAL_API_KEY` | 可选 | 独立视觉模型 Key，不配置回退主模型 |
| `VISUAL_BASE_URL` | 可选 | 独立视觉模型端点 |
| `VISUAL_MODEL` | 可选 | 视觉模型名称 |

运行限制可用环境变量调优：`ARC_AGENT_TIME_BUDGET`、`ARC_AGENT_MAX_TOOL_ROUNDS`、`ARC_AGENT_REQUEST_TIMEOUT`、`ARC_AGENT_MAX_OUTPUT_TOKENS`、`ARC_AGENT_MAX_FILE_BYTES`。


## runs/ · 保留的正式 run 记录

初赛前测试阶段的 3 个官方 run 导出 + 1 份具体测试日志：

| 文件 | 题目 | 结果 |
| --- | --- | --- |
| `runs/5023c6c54362-run-export.json` | `arc-bench-web--12306` | **PASSED 100 分，135/135 通过**；~12.1 h / 2.89 亿 tokens |
| `runs/3ba956e27e8b-run-export.json` | `arc-bench-web--keep` | **PASSED 100 分，32/32 通过**；~3.6 h / 7063 万 tokens |
| `runs/e7a1cccd5e6c-run-export.json` | `arc-bench-web--stackoverflow` | FAILED 98.5 分，65/66 通过；~9.9 h / 2.44 亿 tokens（Repair 成本黑洞的来源） |
| `runs/12306-stdout.log` | `arc-bench-web--12306` | 12306 那次 run 的完整 runner + agent 标准输出日志 |
## 后续

- 初赛（09-24 ~ 09-30）之后，10.01 起的新一轮演进与全部 bundle 见 [`WuxieLUK/arcbench-agent-journey`](https://github.com/WuxieLUK/arcbench-agent-journey)。
- 9 月以来的完整复盘见 `WuxieLUK/arc-bench-hackathon-review`（私有）。

> 所有成绩以 ARC-Bench 官方最终结果为准；本仓库只记录初赛前测试阶段的本地复现过程。
