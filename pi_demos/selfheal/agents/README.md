# pi-agent 自愈工作区（`agents/`）

一个**独立的 TypeScript 工程**：用 [`@earendil-works/pi-coding-agent`](https://www.npmjs.com/package/@earendil-works/pi-coding-agent) 驱动一个真正的 agent 循环，把「跑不过的 Web 测试脚本」修到通过。

它不是为了证明「我用过 pi」，而是为了演示 **pi-agent 的四个架构模式**：扩展系统、自定义工具、hooks、持久化记忆。

> 平台侧的入口是导航栏的「🤖 pi-Agent」页；那个页面只负责编排与存历史，真正干活的是这里（详见文末「与平台的关系」）。

---

## 1. 一句话讲清楚它做什么

输入一个跑不通的测试工作区：

```
workdir/
├── case.json              # 用例的声明式步骤（navigate / input / click / assert）= 预期行为
├── site/index.html        # 被测页面源码 = 事实基准（只读，不许改）
└── tests/test_web_case.py # 已经写好、但定位器/断言写错的 Playwright 测试
```

输出：`tests/test_web_case.py` 被修到 pytest 全绿，并把这次的经验写进长期记忆。

整个过程**没有任何一步是让模型凭空猜的**——每改一次都必须真跑一次 pytest 验证。

---

## 2. 四个架构模式分别落在哪

这是讲解时的主线，建议按这个表逐层展开：

| # | pi-agent 的架构模式 | 本 demo 的落点 | 关键 API |
|---|---|---|---|
| 1 | **扩展系统** | `extensions/selfheal.ts` | `additionalExtensionPaths` + `loader.reload()` |
| 2 | **自定义工具** | 同上，注册 4 个领域工具 | `defineTool()` + `pi.registerTool()` |
| 3 | **Hooks** | 同上，`tool_result` 拦截 `run_test` 失败 | `pi.on("tool_result", …)` 返回部分补丁 |
| 4 | **持久化记忆** | `remember` 工具写入 → `run.ts` 读回 | `agentsFilesOverride`（AGENTS.md 机制） |

配套的「分层」体现在目录划分上：`src/` 只做**装配**（Provider / 提示词 / 会话），`extensions/` 只做**能力**（工具与 hook），两者互不知道对方的实现细节。

---

## 3. 目录结构

```
agents/
├── package.json                 # ESM 工程，依赖 pi-coding-agent 0.83.0 + typebox
├── tsconfig.json                # include 覆盖 src/ 与 extensions/
│
├── src/                         # ── 装配层：只负责「把 agent 拼起来」──
│   ├── cli.ts                   # 入口：preflight / smoke / selfheal 三个子命令
│   ├── config.ts                # 路径常量、.env 加载、智谱 provider 定义
│   ├── provider.ts              # ModelRuntime.create + registerProvider（智谱 GLM）
│   ├── preflight.ts             # 环境自检（Node / API Key / Python / pytest / chromium）
│   ├── prompt.ts                # 系统提示词 + 两个任务提示（自愈 / 冒烟）
│   └── run.ts                   # ★ 装配核心：扩展加载、记忆注入、会话创建、事件订阅
│
├── extensions/                  # ── 能力层：agent 能做什么，全在这里 ──
│   └── selfheal.ts              # ★ demo 主角：4 个工具 + 1 个 hook
│
├── fixtures/web-case-001/       # 内置示范用例（模板，运行时复制一份到 workdir）
│   ├── case.json                # 5 步声明式用例
│   ├── conftest.py              # 本地静态站点夹具（base_url fixture）
│   ├── site/index.html          # 被测页面（含 login-form / #login-submit / #welcome）
│   └── tests/test_web_case.py   # 故意埋了两处 bug 的测试脚本
│
├── scripts/export_case.py       # 把 web_cases 表的声明式步骤导出成骨架（Python，平台侧用）
│
├── .pi-agent/                   # 本地化的 pi 全局目录（隔离，不碰 ~/.pi）
│   ├── auth.json                # ModelRuntime 自管，不要手改
│   └── memory.md                # ★ 持久化记忆（remember 工具写入）
│
└── workdir/                     # 每次运行的隔离工作目录（跑完即可删）
```

---

## 4. 快速开始

### 前置条件

| 依赖 | 版本 | 用途 |
|---|---|---|
| Node.js | **≥ 22.19.0**（见下方注意） | 跑 pi-agent |
| `ZHIPU_API_KEY` | — | 模型调用，从 `.env` 或环境变量读取 |
| Python + `pytest` | 3.10+ | 被 agent 调用的测试执行器 |
| `playwright` + chromium | — | pytest 里真实打开浏览器 |

```powershell
# 浏览器依赖（用仓库自带的 venv）
.\venv\Scripts\python.exe -m pip install pytest playwright
.\venv\Scripts\python.exe -m playwright install chromium
```

> ⚠️ **Node 版本注意**：`package.json` 的 `engines` 与自检脚本都只写 `>=20`，但 `pi-coding-agent@0.83.0` 实际要求 **≥22.19.0**。在 22.16.0 上能跑通，但启动会打警告；低于 20 会直接失败。`engines` 字段不参与实际校验，别只信它。

### 三个命令

```powershell
cd pi_demos\selfheal\agents
npm install            # node_modules 已随仓库携带，可跳过

npm run preflight      # ① 只有环境自检，不发任何模型请求
npm run smoke          # ② 冒烟：让 agent 只做静态分析，不跑测试（不需要 playwright）
npm run selfheal       # ③ 完整自愈：跑 pytest → 修脚本 → 重跑到通过
```

三个命令都会先跑一遍环境自检。区别在于**放行的检查分组**：

- `smoke` 只要求 `core` 组（Node + API Key）通过
- `selfheal` 要求全部通过（额外含 Python / pytest / playwright / chromium）

### 指定工作目录

```powershell
# 默认：把 fixtures/web-case-001 复制到 workdir/selfheal-<时间戳> 跑
npm run selfheal

# 指定目录：直接在给定目录上跑，不复制模板
npm run selfheal -- --workdir "workdir\web-case-001"

# 从平台的 web_cases 表导出一份骨架，再对它自愈
..\..\..\venv\Scripts\python.exe scripts\export_case.py --case-id <web_case_id> --user-id <uid>
npm run selfheal -- --workdir "workdir\case-<web_case_id>"
```

> 每次自愈都在**独立目录**里进行，为的是任务之间不互相污染：上一个任务的 pytest 缓存、临时文件、修复结果都不会带进下一个。

---

## 5. 一次自愈的完整流程

### 5.1 时序

```
npm run selfheal
  └─ src/cli.ts           环境自检 → 解析/创建 workdir → runAgent()
       └─ src/run.ts
            ├─ createZhipuRuntime()          # 注册 GLM provider
            ├─ 读 .pi-agent/memory.md        # 上次的经验（可能为空）
            ├─ new DefaultResourceLoader({…})# 挂扩展 + 注入记忆
            ├─ await loader.reload()         # ★ 自定义 loader 必须手动 reload
            ├─ createAgentSession({…})       # 白名单只放 4 个扩展工具
            └─ session.prompt(SELF_HEAL_TASK)
                 │
                 └── agent 循环（模型决定调用哪个工具）
                      run_test  → 失败
                        └─ tool_result hook 追加 [自愈提示]
                      inspect_dom  → 拿到真实定位器
                      heal_selector → 改脚本
                      run_test  → 再验证
                      …（重复到全绿）
                      remember  → 写入 memory.md
```

### 5.2 真实运行日志（摘录）

```
[扩展已加载] D:\...\agents\extensions\selfheal.ts
[上下文文件] D:\...\agents\.pi-agent\memory.md（180 字符，来自上一次 remember）

我来帮你修复这个 Web 测试用例。首先执行测试看看真实的失败原因。

[工具] run_test
测试失败了，定位器 `#submit-btn` 超时。现在查看页面源码，找出正确的定位器：

[工具] inspect_dom
现在把测试脚本里错误的定位器 `#submit-btn` 替换为正确的 `#login-submit`：

[工具] heal_selector
现在重新运行测试验证修复是否成功：

[工具] run_test
现在需要检查欢迎文本的定位器问题。查看页面中的欢迎元素：

[工具] inspect_dom
定位器 `#welcome` 是正确的，但断言文本不匹配…

[工具] heal_selector
现在重新运行测试验证：

[工具] run_test
太好了！测试通过了。现在记录这次修复经验：

[工具] remember
修改了测试脚本中的登录按钮定位器从 `#submit-btn` 改为 `#login-submit`，并调整了欢迎文本断言…

[本轮结束]
```

上面这段日志值得逐行讲：**两处 bug 是被分成两轮修的**（先修定位器、再修断言文案），每轮都跑了一次真实 pytest。这正是「最小改动 + 立即验证」的约束在起作用，而不是模型一口气把两处都猜着改了。

### 5.3 内置示例埋了什么 bug

`fixtures/web-case-001/tests/test_web_case.py` 故意写错两处：

| 位置 | 脚本里写的 | 页面里真实的 |
|---|---|---|
| 登录按钮定位器 | `#submit-btn` | `#login-submit` |
| 欢迎文案断言 | `欢迎回来` | `欢迎回来，qa_user` |

被测页面 `site/index.html` 是一个纯静态表单：填账号密码 → 点提交 → JS 把 `#welcome` 的 `textContent` 设成 `欢迎回来，<用户名>` 并显示出来。

---

## 6. 四个工具（`extensions/selfheal.ts`）

工具用 `defineTool()` 定义——它保留了 `parameters` 的类型推断，所以 `execute` 里能直接访问 `params.keyword`，不用手写泛型。

| 工具 | 参数 | 作用 | 失败时 |
|---|---|---|---|
| `run_test` | `note?` | 在 `ctx.cwd` 执行 `python -m pytest -x -q`，返回退出码 + 输出尾部 4000 字符 | 退出码非 0 → `isError: true` |
| `inspect_dom` | `keyword?`, `limit?` | 正则解析 `site/index.html`，列出候选元素与建议定位器（含 `role=` 提示） | 文件不存在 → 报错 |
| `heal_selector` | `old_selector`, `new_selector`, `reason?` | 把 `tests/test_web_case.py` 里的定位器字面量整段替换 | 找不到字面量 → 报错并提示先 `inspect_dom` |
| `remember` | `site`, `old_selector`, `new_selector`, `note?` | 去重追加一条经验到 `.pi-agent/memory.md` | — |

几个刻意为之的设计：

- **`run_test` 是 `bash` 的替代品**。这个 demo 从根上不给模型 `bash`：能执行什么、在哪执行、超时多久，全部写死在工具里。`heal_selector` 同理——它只能改 `tests/` 下那一个文件，碰不到 `site/`。
- **`inspect_dom` 不引 Playwright**。纯正则解析已经够用，还省掉一层依赖；页面是 Runner 预取的静态 HTML 快照（SPA / 需登录的页面在预取阶段就已经渲染成 HTML 落盘）。
- **`run_test` 用 `spawn` 而不是 `spawnSync`**。同步执行会把事件循环整段卡住，`session.subscribe` 的流式输出就全没了，日志会变成「跑完一次性吐出来」。
- **每个工具都带 `promptSnippet` / `promptGuidelines`**。这些会进入模型看到的工具说明，是「怎么用」的提示位，比塞进系统提示词更贴近工具本身。

---

## 7. Hook：`tool_result`

```ts
pi.on("tool_result", (event) => {
  if (event.toolName !== "run_test") return;
  const body = event.content.map((c) => (c.type === "text" ? c.text : "")).join("\n");

  if (body.includes("[自愈提示]")) return;                       // 已经推过一次，不再重复刷屏
  if (!event.isError && !LOCATOR_FAILURE.test(body)) return;      // 没失败就不介入

  return { content: [...event.content, { type: "text" as const, text: HEAL_HINT }] };
});
```

要点：

- `tool_result` 的处理器返回的是**部分补丁**（`{ content, details, isError, usage }`），只有返回的字段会被覆盖，其余保持原样。
- `run_test` 的 `isError` 是工具自己设的，但模型的失败有时体现在输出文本里（`FAILED` / `TimeoutError`），所以这里再补一层 `LOCATOR_FAILURE` 正则兜底。
- 这段提示**不是**系统提示词的重复，而是「临场推一把」：在刚刚失败的那一刻，把下一步动作顺序直接贴到工具结果后面，比放在几千 token 之前的系统提示里有效得多。

---

## 8. 持久化记忆闭环

这是整个 demo 最值得讲的一圈，因为它同时用到了「写」和「读」两个不同机制：

```
第 N 次任务
  └─ 模型调用 remember(site, old, new)
       └─ 追加一行到 .pi-agent/memory.md
                                     │
                                     ▼
第 N+1 次任务
  └─ src/run.ts 启动时 readFileSync(MEMORY_PATH)
       └─ agentsFilesOverride(base => ({
              agentsFiles: [...base.agentsFiles, { path: MEMORY_PATH, content: memory }]
          }))
            └─ 以 context file（AGENTS.md 机制）注入 → 模型在上下文里直接看到
```

记忆文件长这样：

```markdown
# pi-agent 自愈记忆

> 由 remember 工具维护：记录「站点 + 失效定位器 → 有效定位器」的修复经验。
> 该文件会被加载为 context file，在后续任务开始时自动进入上下文。
- [登录页] `#submit-btn` → `#login-submit` — 登录按钮的id是login-submit，不是submit-btn
```

`run.ts` 会把注入结果打印出来，讲解时可以指着这行说「这就是上次的记忆被读回来了」：

```
[上下文文件] D:\...\agents\.pi-agent\memory.md（180 字符，来自上一次 remember）
```

三个容易踩的点：

1. **记忆走 context file，不做 turn 前预注入**。`noContextFiles: true` 关掉的是「自动发现 AGENTS.md」，`agentsFilesOverride` 在其**之后**执行，两者可以共存——所以既没有意外文件混进来，又能精确注入这一份。
2. **`remember` 自己做了去重**：同一组 `old → new` 已存在就跳过写入，否则第二轮起记忆会线性膨胀。
3. **`DefaultResourceLoader` 传了自定义 loader 后，必须手动 `await loader.reload()`**。SDK 只在「你没传 loader」时才自动 reload，漏了这步的表现是：扩展没加载、记忆没注入，但**不会报任何错**。

---

## 9. 装配层要点（`src/run.ts`）

这一层是讲解「分层」的落点。四个必须按原样写、否则会出问题的点：

### 9.1 Provider 直接注册到 ModelRuntime

`src/provider.ts` 里绕过 `.pi/extensions/`，直接 `runtime.registerProvider(...)`。原因：扩展注册的 provider 要等 `createAgentSession` 的 `bindCore` 阶段才 flush，那时**已经过了选模型的时机**，结果就是「模型未注册成功」。

### 9.2 `agentDir` 必须传两处，且是同一个值

```ts
new DefaultResourceLoader({ agentDir: LOCAL_AGENT_DIR, … })
createAgentSession({ agentDir: LOCAL_AGENT_DIR, … })
```

不传时 `SettingsManager` 会回落到默认的 `~/.pi/agent`——demo 就不再自包含，会污染用户目录，换台机器行为还不一样。

### 9.3 工具白名单要点名扩展工具

```ts
export const SELF_HEAL_TOOLS = ["run_test", "inspect_dom", "heal_selector", "remember"];
```

`tools` 这个白名单（SDK 内部叫 `allowedToolNames`）会**同时过滤内置工具和扩展工具**。所以必须把扩展工具一个一个写出来，否则 `registerTool` 注册完也会被过滤掉，模型看到的是「工具不存在」。

顺带：内置的 `read` / `bash` / `edit` / `write` 一个都没进白名单，这就是「不给 bash」最干净的实现方式。

### 9.4 系统提示词必须覆盖

pi-agent 默认的人设是硬编码的 *"expert coding assistant operating inside pi"*，跟测试平台完全不搭。`systemPromptOverride: () => SYSTEM_PROMPT` 是必须的。

`src/prompt.ts` 里的 `SYSTEM_PROMPT` 只描述**怎么做事**（先跑真实测试、别猜 id、每轮只改最小一处、不许改被测页面、不许删断言为了让测试变绿），工具本身由扩展注册、经模型 API 的 `tools` 字段下发。

### 9.5 事件订阅与「静默失败」

`run.ts` 用 `session.subscribe` 渲染流式输出。三个坑：

- **模型调用失败不抛异常**，只体现在 `message.stopReason === "error"`（配合 `msg.errorMessage`）。不主动渲染的话，表现就是「跑完了但一句输出都没有」。
- **用 `agent_settled` 而不是 `agent_end`** 判断收尾：后者在 retry / compaction 时会提前触发，会打出假的「本轮结束」。
- **退出码按「有没有真的干活」判定，不按「进程有没有崩」判定**。`runAgent` 返回 `{ toolCalls, modelErrors }`，`selfheal` 在 `toolCalls === 0` 时打印 `[自愈失败] …` 并置 `process.exitCode = 1`。
  否则模型全程 `Connection error` 时进程照样正常退出，Runner 只看退出码，就会把「脚本一行没改」报成「✅ 自愈完成」。
  Runner 据此把任务置为 `failed`，并从日志里提取 `[自愈失败]` 那一行作为页面展示的失败原因。
- `session.subscribe` **收不到** `context` / `tool_call` / `tool_result` / `before_agent_start` / `input` / `model_select` 这几类事件，要用 `pi.on(...)` 才行。用错订阅方式不会报错，只是**静默收不到**。

---

## 10. 环境变量与模型配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ZHIPU_API_KEY` | — | **必需**。只校验存在性，不落盘 |
| `GLM_MODEL_ID` | `glm-4` | 可换成 `glm-4-plus` 等 |

`.env` 的加载规则（`src/config.ts`）：从 `agents/` 目录**逐层向上**找 `.env`，找到就用；**只补进程里缺失的键，绝不覆盖已有环境变量**。所以 demo 被搬到任意层级、或直接把 key 设成环境变量，都能跑。

API Key 走 `$ENV` 插值（`apiKey: "$ZHIPU_API_KEY"`），请求时才从进程环境读取，避免明文写进 `auth.json`。

`compat` 那几个字段（`supportsDeveloperRole: false` / `supportsStore: false` / `maxTokensField: "max_tokens"`）是必须的：智谱虽然兼容 OpenAI 协议，但不是 OpenAI 官方服务端，不显式关掉会被服务端拒绝。

---

## 11. 与平台的关系

**语言边界**：平台是 Python，这里是 TypeScript，Python 不 import 这里的任何东西。两侧只通过四样东西交互：

```
命令行参数  +  工作目录里的文件  +  stdout 日志  +  退出码
```

平台侧调用链：

```
🤖 pi-Agent 页面（app_frontend.py）
  → POST /api/v1/pi-agent/tasks               （后端编排 + 落 pi_agent_tasks 表）
    → POST http://127.0.0.1:8765/pi-agent     （本机 Runner）
      → 预取被测页面 → 写 case.json / conftest.py / 测试骨架
      → node node_modules/tsx/dist/cli.mjs src/cli.ts selfheal --workdir <job_dir>
      → stdout 落盘 pi-agent.log，前端轮询平台接口拿日志
```

Runner 负责的「铺工件」这一步是必须的，因为 `inspect_dom` 只读本地文件：

- 用无头 Chromium 打开 `start_url`（`ignore_https_errors=True`），把渲染后的 `page.content()` 落盘成 `site/index.html`——SPA 和需登录的页面只有这样才有正确的 DOM 快照；
- `conftest.py` 的 `base_url` fixture 指向预取时确认的**最终地址**（不是配置里填的原始地址，这样能正确处理跳转）；
- 测试骨架从 `web_cases.steps` 机械翻译而来，定位器保留成字面量（`page.locator('#x')`），`heal_selector` 才做得成精确替换。

平台侧的产物**不回写** `web_cases.steps`：steps 是声明式用例的唯一事实来源，自愈产出的是命令式脚本，两者语义不同，混在一起会互相污染。自愈结果存在 `pi_agent_tasks` 表里（`fixed_test` / `memory` / `log`）。

---

## 12. 讲解提纲（建议按这个顺序演示）

| 步骤 | 动作 | 要讲的话 |
|---|---|---|
| 1 | `npm run preflight` | 先看「环境自检」的输出。三个分组 `core` / `runner` 对应两种放行门槛，`smoke` 只要 core，`selfheal` 要全绿 |
| 2 | 打开 `fixtures/web-case-001/tests/test_web_case.py` | 指出两处 bug + `site/index.html` 里真实的 id 和文案。**事实基准与预期行为是分开的**：页面是基准，脚本是要修的对象 |
| 3 | 打开 `extensions/selfheal.ts` | 主角。四个 `defineTool` + 一个 `pi.on("tool_result")`。强调「这里没有 bash」 |
| 4 | 打开 `src/run.ts` | 装配。逐条讲 §9 的五个要点，尤其是 `additionalExtensionPaths` + `tools` 白名单 + `agentsFilesOverride` |
| 5 | `npm run smoke` | 先跑冒烟：只有静态分析，不跑测试，用来确认管路通了（不需要 playwright） |
| 6 | `npm run selfheal` | 完整自愈。跟着日志讲：`[扩展已加载]` → `[上下文文件]` → 工具调用序列 → 两轮修复 → `[本轮结束]` |
| 7 | 看修复后的 `workdir/selfheal-*/tests/test_web_case.py` | 两处 bug 都已修好，且断言**没有被删掉** |
| 8 | 看 `.pi-agent/memory.md` | 持久化记忆落盘 |
| 9 | **再跑一次 `npm run selfheal`** | 关键收尾：这次日志里 `[上下文文件]` 会显示上次的记忆被读回来了，模型也更容易一次修对。**「写进去 → 下次读回来」这一圈才是 demo 的重点** |
| 10 | 切到平台「🤖 pi-Agent」页跑一遍 | 展示同一套能力如何被平台调用（可选） |

第 9 步是整个演示的高潮，不要跳过。

---

## 13. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 模型说「工具不存在」 | 扩展没加载成功，或工具不在白名单里 | 看开头的 `[扩展已加载]`；没有就检查 `additionalExtensionPaths`、`loader.reload()`、`SELF_HEAL_TOOLS` |
| 跑完了但一句输出都没有 | 模型侧报错不抛异常，只体现在 `stopReason` | 看 `[模型调用失败]` 那行；检查 API Key、额度、模型 id |
| 页面显示「自愈失败：工具调用成功 0 次…」 | 模型全程没连上（如 `Connection error`），脚本一行没改 | 检查本机到 `open.bigmodel.cn` 的网络与代理；这条**不是**假成功，是修复前的假成功被纠正过来了 |
| 输出是「跑完一次性吐出来」 | 用了 `spawnSync` 阻塞事件循环 | 工具里改用 `spawn`（见 `run_test`） |
| 日志里卡在连续 `write` 无文本输出 | GLM 并发抢配额时的静默空转，已知失败模式 | 不要并发跑多个会话；平台侧用「终止自愈」按钮人工叫停 |
| `PLAYWRIGHT` 报找不到 chromium | 没装浏览器 | `python -m playwright install chromium` |
| pytest 找不到 | 用的 Python 解释器 ≠ 装了 pytest 的那个 | 自检会打印实际用的解释器；对齐后重试 |
| 修好了但断言被删了 | 模型为了变绿走捷径 | 系统提示词已明令禁止；review 时重点看断言有没有被删 |

---

## 14. 相关文件

- 上一级说明（含另一条技术路线 A）：[`../README.md`](../README.md)
- 步骤导出脚本：[`scripts/export_case.py`](scripts/export_case.py)
- 平台侧编排：`app/routers/pi_agent.py`、`local_runner/appium_runner.py`（`pi_agent_job`）