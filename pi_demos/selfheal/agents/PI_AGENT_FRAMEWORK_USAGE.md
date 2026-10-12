# pi-agent 框架能力对照表

> 本文回答一个问题：**这个 demo 到底用了 `@earendil-works/pi-coding-agent` 的哪些能力，分别写在哪段代码里？**
>
> 面向讲解场景，逐项给出「框架能力 → API → 代码位置」。实现细节与运行方法见同目录 [README.md](./README.md)。

---

## 0. 一句话结论

只直接依赖 **一个** 包：

```json
"dependencies": {
  "@earendil-works/pi-coding-agent": "0.83.0",
  "typebox": "1.3.7"
}
```

（[package.json L15-18](./package.json#L15-L18)）

`pi-ai`、`pi-agent-core`、`pi-tui` 均**未被直接使用**——它们要么是间接依赖，要么根本没进代码。另外 `src/cli.ts` 与 `src/preflight.ts` **不 import 任何 pi-agent API**，是纯 Node 的环境自检与命令分发层（[cli.ts L1-6](./src/cli.ts#L1-L6)、[preflight.ts L1-5](./src/preflight.ts#L1-L5)）。

真正碰框架的只有三个文件：

```
src/provider.ts      ← 模型层
src/run.ts           ← 装配层（用框架用得最密）
extensions/selfheal.ts ← 扩展层
```

---

## 1. 能力总表

| 框架能力 | 用到的 API | 代码位置 |
|---|---|---|
| 模型运行时 | `ModelRuntime.create()` | [provider.ts L19-22](./src/provider.ts#L19-L22) |
| Provider 注册 | `runtime.registerProvider()` | [provider.ts L24-51](./src/provider.ts#L24-L51) |
| 取模型实例 | `runtime.getModel()` | [run.ts L45-48](./src/run.ts#L45-L48) |
| 资源装载（渐进式上下文） | `new DefaultResourceLoader({...})` | [run.ts L53-67](./src/run.ts#L53-L67) |
| 手动重载资源 | `await loader.reload()` | [run.ts L69](./src/run.ts#L69) |
| 扩展加载诊断 | `loader.getExtensions()` | [run.ts L137-151](./src/run.ts#L137-L151) |
| 扩展系统入口 | `export default (pi: ExtensionAPI)` | [selfheal.ts L344](./extensions/selfheal.ts#L344-L359) |
| 注册自定义工具 | `pi.registerTool(tool)` ×4 | [selfheal.ts L345-348](./extensions/selfheal.ts#L345-L348) |
| 工具生命周期 hook | `pi.on("tool_result", handler)` | [selfheal.ts L350-359](./extensions/selfheal.ts#L350-L359) |
| 自定义工具定义 | `defineTool({ name, parameters, execute })` | [selfheal.ts L93 / L187 / L244 / L294](./extensions/selfheal.ts#L93) |
| 工具结果契约 | `AgentToolResult` 形状 `{ content, details }` | [selfheal.ts L24-26](./extensions/selfheal.ts#L24-L26) |
| 工具参数 schema | typebox 的 `Type.Object / String / Optional / Number` | [selfheal.ts L195-202](./extensions/selfheal.ts#L195-L202) |
| 会话装配 | `createAgentSession({...})` | [run.ts L72-85](./src/run.ts#L72-L85) |
| 会话持久化策略 | `SessionManager.inMemory(workdir)` | [run.ts L84](./src/run.ts#L84) |
| 事件订阅 | `session.subscribe(handler)` | [run.ts L93-119](./src/run.ts#L93-L119) |
| 会话驱动 | `session.prompt(task)` | [run.ts L122](./src/run.ts#L122) |
| 会话清理 | `session.dispose()` | [run.ts L127](./src/run.ts#L127) |

---

## 2. 模型层 —— `src/provider.ts`

```ts
const runtime = await ModelRuntime.create({     // L19-22
  authPath: join(LOCAL_AGENT_DIR, "auth.json"),
  modelsPath: null,
});

runtime.registerProvider(ZHIPU.providerId, {    // L24-51
  name: "智谱 GLM",
  baseUrl: ZHIPU.baseUrl,
  apiKey: `$${ZHIPU.envKeyName}`,
  api: "openai-completions",
  models: [{ id: ZHIPU.modelId, /* ... */ compat: { /* ... */ } }],
});
```

三个值得讲解的取舍：

1. **`modelsPath: null`** —— provider 完全由代码定义，不读 `models.json`，换机器即跑。
2. **不走 `.pi/extensions/` 注册 provider** —— 扩展里注册的 provider 要等 `createAgentSession` 的 `bindCore` 阶段才 flush，那时已经过了「选模型」的时机。直接注册到 `runtime` 上，时序完全确定。
3. **`apiKey: "$ZHIPU_API_KEY"` 用 `$ENV` 插值** —— 密钥留在进程环境里，不落盘到 `auth.json`。

`compat` 字段（[L43-48](./src/provider.ts#L43-L48)）是必须的：智谱兼容 OpenAI 协议但不是 OpenAI 官方服务端，不显式关掉 `supportsDeveloperRole` / `supportsStore` / `supportsReasoningEffort`，请求会被服务端拒绝。

---

## 3. 装配层 —— `src/run.ts`（框架用得最密）

### 3.1 `DefaultResourceLoader` 的七个选项

```ts
const loader = new DefaultResourceLoader({
  cwd: workdir,
  agentDir: LOCAL_AGENT_DIR,
  systemPromptOverride: () => SYSTEM_PROMPT,     // 替换默认的编码助手人设
  noExtensions: true,                            // 关掉"自动发现"
  additionalExtensionPaths: [EXTENSION_PATH],    // ← 但显式挂上自己的扩展
  noContextFiles: true,
  noSkills: true,
  agentsFilesOverride: (base) => ...,            // ← 手动注入 memory.md
});
await loader.reload();
```

（[run.ts L53-69](./src/run.ts#L53-L69)）

几个容易踩的点：

- **`noExtensions` 只关「自动发现」**，`additionalExtensionPaths` 依然照常加载——这正是本 demo 挂载 `selfheal.ts` 的方式。
- **自定义 `resourceLoader` 必须自己 `await loader.reload()`**，否则配置不生效（SDK 仅在「未传 loader」时才自动 reload）。
- **`agentsFilesOverride` 在 `noContextFiles` 之后执行**，两者可以共存：前者清掉自动发现的 AGENTS.md，后者精确塞入我们自己的记忆文件。

### 3.2 记忆注入（持久化记忆的「读」路径）

```ts
const memory = existsSync(MEMORY_PATH) ? readFileSync(MEMORY_PATH, "utf8").trim() : "";
// ...
agentsFilesOverride: (base) =>
  memory ? { agentsFiles: [...base.agentsFiles, { path: MEMORY_PATH, content: memory }] } : base,
```

（[run.ts L51](./src/run.ts#L51)、[L63-66](./src/run.ts#L63-L66)）

`remember` 工具写下的经验，下一轮以 **context file** 的形式回到上下文——写入与读回由此形成闭环。

### 3.3 `createAgentSession` 的关键参数

```ts
const { session } = await createAgentSession({
  cwd: workdir,
  agentDir: LOCAL_AGENT_DIR,        // 必须与 ResourceLoader 传同一个
  model,
  modelRuntime,
  thinkingLevel: "off",             // GLM-4 无推理档
  resourceLoader: loader,
  tools: SELF_HEAL_TOOLS,           // ← 白名单
  sessionManager: SessionManager.inMemory(workdir),
});
```

（[run.ts L72-85](./src/run.ts#L72-L85)）

- **`agentDir` 要传两处**（`createAgentSession` 与 `DefaultResourceLoader`）。只传一处时 `SettingsManager` 会回落到默认的 `~/.pi/agent`，demo 就不再自包含。
- **`tools` 是白名单，扩展工具必须在这里点名**，否则会被 `allowedToolNames` 过滤掉。白名单定义见 [L17](./src/run.ts#L17)：
  ```ts
  export const SELF_HEAL_TOOLS = ["run_test", "inspect_dom", "heal_selector", "remember"];
  ```
  内置的 read / bash / edit / write **一个都不开**——`bash` 这个安全口从根上关掉。

### 3.4 事件订阅与可判定结果

```ts
const unsubscribe = session.subscribe((event) => { /* ... */ });   // L93-119
try {
  await session.prompt(task);                                      // L122
} finally {
  unsubscribe();
  session.dispose();                                               // L127
}
return { toolCalls, modelErrors };                                 // L130
```

（[run.ts L93-130](./src/run.ts#L93-L130)）

`runAgent()` 返回 `RunOutcome`（[L25-30](./src/run.ts#L25-L30)），退出码据此判定——避免「模型全挂但进程正常退出」被当成成功。

---

## 4. 扩展层 —— `extensions/selfheal.ts`

```ts
export default function selfhealExtension(pi: ExtensionAPI): void {
  pi.registerTool(runTest);        // L345
  pi.registerTool(inspectDom);     // L346
  pi.registerTool(healSelector);   // L347
  pi.registerTool(remember);       // L348

  pi.on("tool_result", (event) => {                  // L350
    if (event.toolName !== "run_test") return;
    const body = event.content.map((c) => (c.type === "text" ? c.text : "")).join("\n");
    if (body.includes("[自愈提示]")) return;          // 推过一次就不再追加
    if (!event.isError && !LOCATOR_FAILURE.test(body)) return;
    return { content: [...event.content, { type: "text" as const, text: HEAL_HINT }] };  // L358
  });
}
```

（[selfheal.ts L344-359](./extensions/selfheal.ts#L344-L359)）

hook 的三个要点：

1. **返回值是「部分补丁」**，只给 `content` 即可，其余字段保持原样。
2. **`LOCATOR_FAILURE` 正则兜底**（[L332-333](./extensions/selfheal.ts#L332-L333)）—— `run_test` 是以非 0 退出码返回的，`isError` 已经能捕获，正则用于覆盖「Launched 成功但断言失败」这类仍算失败的情况。
3. **为什么用 hook 而不是写进系统提示词** —— 失败时才注入提示，属于「渐进式上下文」：成功路径上不占 token，也不干扰模型判断。

### 四个自定义工具

| 工具 | 定义位置 | 作用 |
|---|---|---|
| `run_test` | [L93](./extensions/selfheal.ts#L93) | spawn `python -m pytest -x -q`，回传退出码与输出尾部 |
| `inspect_dom` | [L187](./extensions/selfheal.ts#L187) | 解析 `site/index.html`，给候选元素与建议定位器 |
| `heal_selector` | [L244](./extensions/selfheal.ts#L244) | 替换 `tests/test_web_case.py` 里的定位器字面量 |
| `remember` | [L294](./extensions/selfheal.ts#L294) | 去重后追加写入 `MEMORY_PATH` |

工具返回值统一走 `AgentToolResult` 形状（[L24-26](./extensions/selfheal.ts#L24-L26)）：

```ts
function text(value: string, details: unknown = undefined) {
  return { content: [{ type: "text" as const, text: value }], details };
}
```

参数 schema 用 typebox（**注意：这是 `typebox` 包，pi-coding-agent 不 re-export `Type`**），例如 [L195-202](./extensions/selfheal.ts#L195-L202)。

---

## 5. 订阅到的事件契约（5 类）

| 事件 | 用途 | 位置 |
|---|---|---|
| `message_update` + `assistantMessageEvent.type === "text_delta"` | 流式输出文本 | [L94-96](./src/run.ts#L94-L96) |
| `message_end`（`role === "assistant"`） | 读 `stopReason === "error"`，捕获**静默失败** | [L99-105](./src/run.ts#L99-L105) |
| `tool_execution_start` | 打印调用了哪个工具 | [L106-108](./src/run.ts#L106-L108) |
| `tool_execution_end` | 读 `isError`，统计成败 | [L109-113](./src/run.ts#L109-L113) |
| `agent_settled` | 判断收尾 | [L115-118](./src/run.ts#L115-L118) |

两条踩坑结论：

- **模型调用失败不抛异常**，只体现在 `stopReason === "error"`（配合 `msg.errorMessage`）。不主动渲染的话，表现就是「跑完了但一句输出都没有」。
- **用 `agent_settled` 而不是 `agent_end`** —— 后者在 retry / compaction 时会提前触发，会打出假的「本轮结束」。

---

## 6. 明确**没有**使用的部分

这些是「框架有、但 demo 故意关掉或不涉及」的，讲解时值得点一下：

| 未使用 | 说明 |
|---|---|
| 内置工具（read / bash / edit / write / grep） | `tools` 白名单只放四个扩展工具 |
| Skills | `noSkills: true` |
| 自动 context file 发现（AGENTS.md） | `noContextFiles: true`，改用 `agentsFilesOverride` 精确控制 |
| 磁盘会话持久化 | 用 `SessionManager.inMemory`，不污染 `~/.pi/` |
| TUI / 交互式终端（`CustomEditor`、pi-tui） | 完全未涉及，demo 走编程式一次性 `prompt()` |
| PromptTemplate / 斜杠命令 | 未使用 |
| MCP 集成 | 未使用 |
| Compaction 配置 | 依赖 SDK 默认行为，demo 未显式配置 |
| `pi-ai` / `pi-agent-core` | 非直接依赖 |

---

## 7. 官方示例对照（推荐对照讲解）

SDK 自带示例与本 demo 一一对应，可以拿来印证「不是自创写法」：

```
node_modules/@earendil-works/pi-coding-agent/examples/sdk/
  06-extensions.ts      ← 对应 extensions/selfheal.ts（扩展注册）
  07-context-files.ts   ← 对应 agentsFilesOverride 注入 memory.md
  11-sessions.ts        ← 对应 SessionManager.inMemory
  12-full-control.ts    ← 对应 provider.ts 的 ModelRuntime 写法
```

本 demo 的差异在于：把这几个示例**拼成了一个真实的业务闭环**（跑测试 → 看 DOM → 改脚本 → 重跑 → 记经验），而不是各自孤立的 API 演练。这正是它比「照抄示例」更能说明问题的原因。

---

## 8. 讲解时可强调的三条设计取舍

1. **`agentDir` 传两处** —— 保证 demo 自包含，不溢出到 `~/.pi/`。
2. **白名单只放扩展工具** —— 用 `tools` 取代「注册一堆工具再逐个禁用」，`bash` 从根上不可达。
3. **退出码按「有没有真的干活」判定** —— `toolCalls === 0` 即失败，不按「进程有没有崩」判定。否则模型全程 `Connection error` 时进程照样正常退出，会被上游误判为「自愈成功」。实现见 [run.ts L89-130](./src/run.ts#L89-L130) 与 [cli.ts](./src/cli.ts) 的 `selfheal` 分支。