# 用例自愈速查

> Web 自动化用例的 AI 闭环修复 demo。**不依赖平台后端与数据库**，可单独跑、单独讲。
> 详细文档见 [README.md](README.md) 与 [agents/README.md](agents/README.md)。

## 两条路线

| 维度 | A · 修 steps | B · 修 pytest 脚本 |
|------|--------------|---------------------|
| 改的对象 | 声明式 steps JSON | 命令式 pytest 脚本 |
| 调用 | HTTP 直调 GLM | pi-agent agent 循环 |
| 工具循环 | 无 | 4 工具（run_test/inspect_dom/heal_selector/remember） |
| Hook | 无 | `tool_result` 拦 run_test 失败追加自愈提示 |
| 持久化记忆 | 无 | `.pi-agent/memory.md`，下次作为 context file |
| 演示价值 | 轻量、可移植 | 完整 Agent 架构（pi 四大模式） |

## 核心原则

- **闭环**：每改一次必须真跑一次验证，不许模型凭空猜（避免幻觉）
- **不回写** `web_cases.steps`：声明式用例是唯一事实来源，自愈产出命令式脚本，语义不同，混写会互相污染

## 与平台关系

```
🤖 pi-Agent 页 → POST /api/v1/pi-agent/tasks → Runner :8765 → node cli.ts selfheal
                                                              （本目录路线 B）
```

平台只编排 + 存历史（日志/修复脚本/记忆快照），真正的自愈逻辑全部在本目录。

## 入口

| 路线 | 命令 |
|------|------|
| A | `python pi_demos/selfheal/heal_steps.py`（可选 `--show-browser` / `--rounds N` / `--model glm-4-plus`） |
| B | `cd pi_demos/selfheal/agents && npm run selfheal`（先 `npm run preflight` 自检） |

## 前置

- Python 3.10+ + `playwright` + chromium
- 路线 B 另需 Node ≥ 22.19.0（`node_modules` 已随仓库携带）
- `ZHIPU_API_KEY`（两条路线都需要）
