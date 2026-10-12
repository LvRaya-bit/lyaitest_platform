import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import { LOCAL_AGENT_DIR, ZHIPU } from "./config.js";

/**
 * 构造 ModelRuntime 并注册智谱 GLM provider。
 *
 * 为什么不用 `.pi/extensions/` 里的扩展来 registerProvider：
 * 扩展注册的 provider 要等 createAgentSession 的 bindCore 阶段才 flush，
 * 那时已经过了「选模型」的时机。直接注册到 runtime 上，时序完全确定。
 *
 * 为什么 modelsPath 传 null：
 * demo 的 provider 完全由代码定义，不依赖任何 models.json 文件，换机器即跑。
 */
export async function createZhipuRuntime(): Promise<ModelRuntime> {
  mkdirSync(LOCAL_AGENT_DIR, { recursive: true });

  const runtime = await ModelRuntime.create({
    authPath: join(LOCAL_AGENT_DIR, "auth.json"),
    modelsPath: null,
  });

  runtime.registerProvider(ZHIPU.providerId, {
    name: "智谱 GLM",
    baseUrl: ZHIPU.baseUrl,
    // 支持 $ENV 插值；下面的 setRuntimeApiKey 是双保险
    apiKey: `$${ZHIPU.envKeyName}`,
    api: "openai-completions",
    models: [
      {
        id: ZHIPU.modelId,
        name: `${ZHIPU.modelId} (智谱)`,
        reasoning: false,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 128_000,
        maxTokens: 4096,
        /**
         * 智谱虽然兼容 OpenAI 协议，但不是 OpenAI 官方服务端：
         * 不显式关掉这些字段，请求会被服务端拒绝。
         */
        compat: {
          supportsDeveloperRole: false,
          supportsStore: false,
          maxTokensField: "max_tokens",
          supportsReasoningEffort: false,
        },
      },
    ],
  });

  const apiKey = process.env[ZHIPU.envKeyName];
  if (!apiKey) {
    throw new Error(
      `未找到 ${ZHIPU.envKeyName}。请在仓库根目录的 .env 中配置，形如 ${ZHIPU.envKeyName}=<your-key>`,
    );
  }
  // 只在此校验存在性，不落盘：apiKey 走上面的 $ENV 插值，请求时从进程环境读取

  return runtime;
}