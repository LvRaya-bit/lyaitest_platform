# 用例自愈 Demo

Web 自动化用例自愈能力的独立演示。两条路线都能单独跑、单独讲，**不依赖平台后端与数据库**。

| 路线 | 做法 | 入口 |
|---|---|---|
| **A · 修 steps** | 把「真实报错 + 页面可交互元素清单」交给 GLM，让它直接输出修正后的声明式步骤 JSON | [`heal_steps.py`](heal_steps.py) |
| **B · 修 pytest 脚本** | 用 pi-agent 驱动真实 pytest：跑失败 → 读报错与页面源码 → 改测试脚本 → 重跑至通过 | [`agents/`](agents/)（Node 工程） |

两条路线的共同点是**闭环**：都必须真实执行一次拿到真实报错，再改，再真跑验证，而不是让模型凭空猜。

## 设计原则

### 动机

Web UI 改版后，最常失效的是**定位器**——按钮 id 变了、文案变了、结构变了。传统做法是测试人员手动改 pytest 脚本，维护成本随用例数线性增长。自愈让 AI 接管这个闭环：真跑一次拿报错 → 读页面真实状态 → 改定位器/断言 → 重跑验证。

### 两条路线的设计权衡

| 维度 | 路线 A · 修 steps | 路线 B · 修 pytest 脚本 |
|------|-------------------|--------------------------|
| 改的对象 | 声明式 steps JSON（`navigate`/`click`/`input`/`assert`） | 命令式 pytest 脚本（Playwright Python） |
| 调用方式 | HTTP 直调 GLM | pi-agent 驱动 agent 循环 |
| 工具循环 | 无 | 4 个自定义工具（`run_test`/`inspect_dom`/`heal_selector`/`remember`） |
| Hook | 无 | `tool_result` 拦截 `run_test` 失败时自动追加自愈提示 |
| 持久化记忆 | 无 | `remember` 工具写入 `.pi-agent/memory.md`，下次任务作为 context file 加载 |
| 演示价值 | 轻量、可移植、快速验证"LLM 能改数据" | 完整 Agent 架构，演示 pi-agent 四大模式：扩展系统 / 自定义工具 / Hooks / 持久化记忆 |
| 适用场景 | 平台 steps 数据的轻量修复 | 复杂 pytest 工程的深度自愈 |

路线 A 和 B 默认共用同一被测页面（`agents/fixtures/web-case-001/site`），跑通结果可直接横向对比。

### 与平台集成的边界

**自愈产物不回写 `web_cases.steps`**。这是刻意设计：

- `web_cases.steps` 是**声明式**用例的唯一事实来源——`navigate`/`click`/`input`/`assert` 描述"做什么"，与代码无关
- 路线 B 产出的是**命令式** pytest 脚本——描述"怎么做"，含 Playwright API 调用、断言写法、fixture 约定

两者**语义不同**，混写会让"事实来源"被命令式实现污染。平台只编排与存历史（任务日志、修复后的脚本、记忆快照），真正的自愈逻辑全部在本目录。

## 目录结构

```
pi_demos/selfheal/
├── heal_steps.py                     # 路线 A 入口（CLI，纯 Python）
├── web_executor.py                   # 路线 A 自带的迷你 Playwright 执行器
├── cases/example_login.json          # 示例用例（故意埋了两处错）
├── agents/                           # 路线 B：pi-agent 工程（TypeScript）
│   ├── src/                          # 装配层：CLI、模型注册、提示词、会话装配
│   ├── extensions/selfheal.ts        # 能力层：4 个自定义工具 + tool_result hook
│   ├── fixtures/web-case-001/        # 内置示范用例（pytest 脚本 + 被测页面）
│   ├── scripts/export_case.py        # 把 web_cases 的步骤导出成测试骨架
│   ├── .pi-agent/memory.md           # 持久化记忆（remember 工具写入）
│   └── workdir/                      # 每次自愈的隔离工作目录（运行后生成，可删）
└── README.md
```

路线 B 的详细拆解（架构模式、工具签名、记忆闭环、排查表、讲解提纲）见 [`agents/README.md`](agents/README.md)。

## 前置条件

- **Python 3.10+**，且已安装：`playwright`（含 chromium 浏览器）
- **Node.js ≥ 22.19.0**（只有路线 B 需要）
- **`ZHIPU_API_KEY`**：两条路线都会从自身目录逐层向上查找 `.env`，也可以直接设成环境变量

```powershell
# 以仓库内的 venv 为例
.\venv\Scripts\python.exe -m pip install playwright
.\venv\Scripts\python.exe -m playwright install chromium
```

## 路线 A：让 AI 直接修 steps

```powershell
# 默认跑内置示例（自动为本机起一个静态服务器指向被测站点）
python pi_demos/selfheal/heal_steps.py

# 看浏览器窗口（演示用）
python pi_demos/selfheal/heal_steps.py --show-browser

# 指定自己的用例与页面目录、限制轮次、换模型
python pi_demos/selfheal/heal_steps.py `
    --case pi_demos\selfheal\cases\example_login.json `
    --site pi_demos\selfheal\agents\fixtures\web-case-001\site `
    --rounds 5 --model glm-4-plus
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--case` | `cases/example_login.json` | 用例 JSON 路径 |
| `--site` | `agents/fixtures/web-case-001/site` | 被测站点目录，demo 会为本机起一个静态服务器指向它 |
| `--rounds` | `5` | 最大自愈轮次 |
| `--model` | `glm-4`（可用 `GLM_MODEL_ID` 覆盖） | 模型 ID |
| `--show-browser` | 关 | 显示浏览器窗口，便于演示 |
| `--out` | `<用例名>.healed.json` | 修正后 steps 的落盘位置 |

两条路线默认共用同一个被测页面（`agents/fixtures/web-case-001/site`），
所以 A/B 的跑通结果可以直接横向对比。

跑完会打印每一轮的真实执行结果与模型修正内容，并把最终步骤写到
`cases/example_login.healed.json`。

**示例用例里故意埋了两处 bug**：提交按钮写成 `#submit-btn`（真实是 `#login-submit`）、
断言文案写成 `登录成功`（真实是 `欢迎回来，qa_user`）。预期能看到：

1. 第 1 轮真实执行：2 步失败（选择器超时 + 断言不符）
2. 模型依据采集到的元素清单改掉选择器与断言文案
3. 数轮内全部通过，修正后的 steps 落盘

用例里的 `{{base_url}}` 是站点根占位符，执行时才替换成真实地址，模型看到的始终是占位符。

> 路线 A 直接用 HTTP 调 GLM，**不经过 pi-agent**，所以它演示的是「LLM 改数据」，没有工具循环、
> 没有持久化记忆。要讲这些架构能力请走路线 B。

## 路线 B：让 pi-agent 修 pytest 脚本

```powershell
cd pi_demos\selfheal\agents
npm install          # 安装 pi-agent 与 tsx 依赖（node_modules 不随仓库分发）
npm run preflight    # 只做环境自检，不调用模型
npm run smoke        # 冒烟：只让 agent 做静态分析，不跑测试
npm run selfheal     # 完整自愈：跑 pytest → 修脚本 → 重跑
```

每次自愈会把 `fixtures/web-case-001` 复制到 `workdir/selfheal-<时间戳>` 独立运行，
修复结果看 `workdir/selfheal-*/tests/test_web_case.py`。

如果想在指定目录上自愈（例如把示例用例导出成一份可编辑的骨架）：

```powershell
# 1) 从仓库根导出骨架（用仓库 venv 的 python），输出到 agents/workdir/web-case-001
.\venv\Scripts\python.exe pi_demos\selfheal\agents\scripts\export_case.py `
    --from-json pi_demos\selfheal\agents\fixtures\web-case-001\case.json

# 2) 到 agents/ 里对这份骨架跑自愈
cd pi_demos\selfheal\agents
npm run selfheal -- --workdir "workdir\web-case-001"
```

`export_case.py` 也支持直接从平台的 `web_cases` 表读用例：

```powershell
.\venv\Scripts\python.exe pi_demos\selfheal\agents\scripts\export_case.py `
    --case-id <web_case_id> --user-id <uid>
```

## 与平台的关系

平台上的入口是导航栏的 **「🤖 pi-Agent」** 页：

```
🤖 pi-Agent 页面
  → POST /api/v1/pi-agent/tasks             （后端编排 + 落 pi_agent_tasks 表）
    → POST http://127.0.0.1:8765/pi-agent   （本机 Runner）
      → 无头预取被测页面 → 写 case.json / conftest.py / 测试骨架
      → node …/tsx src/cli.ts selfheal --workdir <job_dir>   ← 就是本目录的路线 B
```

平台只负责编排与存历史（日志、修复后的脚本、记忆快照），真正的自愈逻辑仍然全部在本目录。

两侧的语言边界是 **命令行参数 + 工作目录文件 + stdout 日志 + 退出码**：
Python 不 import TypeScript，Runner 只负责把工件铺好再起一个 Node 子进程。

自愈产物**不回写** `web_cases.steps`——steps 是声明式用例的唯一事实来源，
自愈产出的是命令式脚本，两者语义不同，混在一起会互相污染。