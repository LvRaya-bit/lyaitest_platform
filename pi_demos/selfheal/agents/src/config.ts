import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
/** agents/ 目录 */
export const AGENTS_ROOT = resolve(here, "..");
/** 每次任务的独立工作目录都落在这里，跑完即可删 */
export const WORKDIR_ROOT = join(AGENTS_ROOT, "workdir");
/** 内置的示范用例（模板，运行时会复制一份到 workdir） */
export const FIXTURE_DIR = join(AGENTS_ROOT, "fixtures", "web-case-001");
/**
 * 把 pi 的全局配置目录也收在本地，避免污染 ~/.pi/
 * （默认值 ~/.pi/agent 是 CLI 单用户设计的产物，demo 不碰它）
 */
export const LOCAL_AGENT_DIR = join(AGENTS_ROOT, ".pi-agent");
/**
 * pi-agent 的持久化记忆。
 *
 * 由扩展里的 remember 工具写入，再由 run.ts 以 context file（AGENTS.md 机制）
 * 注入回下一轮上下文 —— 「写进去 → 下次读回来」这一整圈才是 demo 的重点。
 */
export const MEMORY_PATH = join(LOCAL_AGENT_DIR, "memory.md");
/** 自愈扩展：四件套工具 + tool_result hook 都在这一个文件里 */
export const EXTENSION_PATH = join(AGENTS_ROOT, "extensions", "selfheal.ts");

/** 从 agents/ 逐层向上找 .env：demo 被搬到任意层级或独立目录时仍能读到密钥 */
function findDotEnv(): string | undefined {
  let dir = AGENTS_ROOT;
  for (;;) {
    const candidate = join(dir, ".env");
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir) return undefined;
    dir = parent;
  }
}

/** 极简 .env 加载器：只补进程里缺失的键，绝不覆盖已有环境变量 */
function loadDotEnv(): void {
  const envPath = findDotEnv();
  if (!envPath) return;
  for (const raw of readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    const quoted =
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"));
    if (quoted) value = value.slice(1, -1);
    if (!process.env[key]) process.env[key] = value;
  }
}

loadDotEnv();

export const ZHIPU = {
  providerId: "zhipu",
  /** 可用 GLM_MODEL_ID 覆盖，例如 glm-4-plus */
  modelId: process.env.GLM_MODEL_ID ?? "glm-4",
  baseUrl: "https://open.bigmodel.cn/api/paas/v4",
  envKeyName: "ZHIPU_API_KEY",
} as const;

/** 智谱 key 是否就绪（不打印任何密钥内容） */
export function hasZhipuKey(): boolean {
  return Boolean(process.env[ZHIPU.envKeyName]);
}