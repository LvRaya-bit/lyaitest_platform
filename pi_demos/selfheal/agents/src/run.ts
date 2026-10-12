import { existsSync, readFileSync } from "node:fs";
import { createAgentSession, DefaultResourceLoader, SessionManager } from "@earendil-works/pi-coding-agent";
import { EXTENSION_PATH, LOCAL_AGENT_DIR, MEMORY_PATH, ZHIPU } from "./config.js";
import { SYSTEM_PROMPT } from "./prompt.js";
import { createZhipuRuntime } from "./provider.js";

const C = {
  dim: "\x1b[2m",
  cyan: "\x1b[36m",
  green: "\x1b[32m",
  red: "\x1b[31m",
  yellow: "\x1b[33m",
  reset: "\x1b[0m",
};

/** 只放行自愈扩展注册的四个工具，内置的 read / bash / edit / write 一个都不开 */
export const SELF_HEAL_TOOLS = ["run_test", "inspect_dom", "heal_selector", "remember"];

export interface RunOptions {
  workdir: string;
  task: string;
}

/** 一次会话的可判定结果：退出码要据此决定，避免「模型全挂但进程正常退出」被当成成功 */
export interface RunOutcome {
  /** 成功执行（未报错）的工具调用次数 */
  toolCalls: number;
  /** 模型侧报错次数，如 Connection error / 限流 / 鉴权失败 */
  modelErrors: number;
}

/**
 * 装配并驱动一次 pi-agent 会话。
 *
 * 这里只做「组装」：Provider → 扩展 → 系统提示词 → createAgentSession。
 * 与 v1 的关键差别：
 *   - noExtensions 仍然开着（不自动发现用户的扩展），但用 additionalExtensionPaths
 *     显式挂上自己的 extensions/selfheal.ts —— 四个领域工具 + tool_result hook 都在里面；
 *   - tools 传白名单，模型只能看到这四个工具，bash 这个安全口从根上关掉；
 *   - .pi-agent/memory.md 以 context file 形式注入，让 remember 写下的经验形成闭环。
 */
export async function runAgent({ workdir, task }: RunOptions): Promise<RunOutcome> {
  const modelRuntime = await createZhipuRuntime();

  const model = modelRuntime.getModel(ZHIPU.providerId, ZHIPU.modelId);
  if (!model) {
    throw new Error(`模型未注册成功：${ZHIPU.providerId}/${ZHIPU.modelId}`);
  }

  // remember 工具写下的经验：下一轮以 context file 的形式回到上下文里
  const memory = existsSync(MEMORY_PATH) ? readFileSync(MEMORY_PATH, "utf8").trim() : "";

  const loader = new DefaultResourceLoader({
    cwd: workdir,
    agentDir: LOCAL_AGENT_DIR,
    systemPromptOverride: () => SYSTEM_PROMPT,
    // noExtensions 只关掉「自动发现」：additionalExtensionPaths 依然照常加载
    noExtensions: true,
    additionalExtensionPaths: [EXTENSION_PATH],
    // AGENTS.md 与 skill 索引仍可能被自动追加，这里一并关掉，只留自己的记忆文件
    noContextFiles: true,
    noSkills: true,
    agentsFilesOverride: (base) =>
      memory
        ? { agentsFiles: [...base.agentsFiles, { path: MEMORY_PATH, content: memory }] }
        : base,
  });
  // 自定义 resourceLoader 必须自己 reload，否则配置不生效（SDK 仅在未传 loader 时自动 reload）
  await loader.reload();
  reportExtensionIssues(loader, memory);

  const { session } = await createAgentSession({
    cwd: workdir,
    // 必须和 ResourceLoader 传同一个本地 agentDir：
    // 不传时 SettingsManager 会回落到默认的 ~/.pi/agent，demo 就不再是自包含的了
    agentDir: LOCAL_AGENT_DIR,
    model,
    modelRuntime,
    thinkingLevel: "off", // GLM-4 无推理档，避免 clamp 出意外行为
    resourceLoader: loader,
    // 白名单：扩展工具必须在这里点名，否则会被 allowedToolNames 过滤掉
    tools: SELF_HEAL_TOOLS,
    // 默认会把会话落到 ~/.pi/agent/sessions/，本 demo 完全隔离在内存里
    sessionManager: SessionManager.inMemory(workdir),
  });

  const out = (s: string) => process.stdout.write(s);
  let settled = false;
  let toolCalls = 0;
  let modelErrors = 0;

  // 外部订阅层：只有 message_* / tool_execution_* / agent_settled 这类事件能在这里收到
  const unsubscribe = session.subscribe((event) => {
    if (event.type === "message_update" && event.assistantMessageEvent?.type === "text_delta") {
      out(event.assistantMessageEvent.delta);
    }
    // 模型侧报错（鉴权失败 / 限流 / 服务端拒绝）不会抛异常，只体现在 stopReason 上，
    // 不主动渲染就会表现为「跑完了但一句输出都没有」
    if (event.type === "message_end" && event.message.role === "assistant") {
      const msg = event.message;
      if (msg.stopReason === "error") {
        modelErrors += 1;
        out(`\n${C.red}[模型调用失败] ${msg.errorMessage ?? "未知错误"}${C.reset}\n`);
      }
    }
    if (event.type === "tool_execution_start") {
      out(`\n${C.cyan}[工具] ${event.toolName}${C.reset}\n${C.dim}`);
    }
    if (event.type === "tool_execution_end") {
      out(C.reset);
      if (event.isError) out(`${C.red}[工具失败] ${event.toolName}${C.reset}\n`);
      else toolCalls += 1;
    }
    // 用 agent_settled 而不是 agent_end：后者在 retry / compaction 时会提前触发
    if (event.type === "agent_settled" && !settled) {
      settled = true;
      out(`\n${C.green}[本轮结束]${C.reset}\n`);
    }
  });

  try {
    await session.prompt(task);
  } finally {
    // 双保险：即便 agent_settled 没派发，也保证有明确的结束信号
    if (!settled) out(`\n${C.green}[本轮结束]${C.reset} (finally 兜底)\n`);
    unsubscribe();
    session.dispose(); // 不 dispose 会残留监听器与后台任务
  }

  return { toolCalls, modelErrors };
}

/**
 * 扩展加载失败是静默的：模型只会看到「工具不存在」。
 * 这里把 diagnostics 与注入的上下文文件打出来，避免出现「莫名其妙不能用」。
 */
function reportExtensionIssues(loader: DefaultResourceLoader, memory: string): void {
  const result = loader.getExtensions();
  for (const err of result.errors) {
    process.stdout.write(`${C.yellow}[扩展加载失败] ${err.path} — ${err.error}${C.reset}\n`);
  }
  const names = result.extensions.map((e) => e.path).join(", ");
  if (names) {
    process.stdout.write(`${C.dim}[扩展已加载] ${names}${C.reset}\n`);
  }
  process.stdout.write(
    memory
      ? `${C.dim}[上下文文件] ${MEMORY_PATH}（${memory.length} 字符，来自上一次 remember）${C.reset}\n`
      : `${C.dim}[上下文文件] 无历史记忆，本次为首次自愈${C.reset}\n`,
  );
}