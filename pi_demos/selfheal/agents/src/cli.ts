import { copyFileSync, existsSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { FIXTURE_DIR, WORKDIR_ROOT, ZHIPU } from "./config.js";
import { allOk, checkEnvironment, printChecks } from "./preflight.js";
import { SELF_HEAL_TASK, SMOKE_TASK } from "./prompt.js";
import { runAgent } from "./run.js";

function argValue(flag: string): string | undefined {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

/**
 * 逐文件复制：不用 fs.cpSync 的 recursive 模式。
 * Windows 上 cpSync 内部会对路径做 \\?\ 长路径前缀转换，
 * 项目路径含中文时部分 Node 版本会抛 EIO/Access is denied；
 * copyFileSync 直接按原始路径打开文件，不做该转换。
 */
function copyTree(src: string, dest: string): void {
  mkdirSync(dest, { recursive: true });
  for (const name of readdirSync(src)) {
    const from = join(src, name);
    const to = join(dest, name);
    if (statSync(from).isDirectory()) copyTree(from, to);
    else copyFileSync(from, to);
  }
}

/** 复制一份模板到独立工作目录，保证每次自愈都在干净环境里跑 */
function materialize(label: string): string {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const workdir = join(WORKDIR_ROOT, `${label}-${stamp}`);
  mkdirSync(WORKDIR_ROOT, { recursive: true });
  copyTree(FIXTURE_DIR, workdir);
  return workdir;
}

function resolveWorkdir(label: string): string {
  const fromArg = argValue("--workdir");
  if (!fromArg) return materialize(label);
  const workdir = resolve(fromArg);
  if (!existsSync(workdir)) {
    throw new Error(`--workdir 指向的目录不存在：${workdir}`);
  }
  return workdir;
}

const HELP = `
用法：npm run <command> [-- <options>]

  preflight                  只做环境自检，不发起任何模型调用
  smoke                      冒烟：只让 agent 做静态分析，不跑测试（不需要 playwright）
  selfheal                   完整自愈：跑 pytest → 修脚本 → 重跑直至通过

options:
  --workdir <path>           在指定目录上运行，而不是复制内置模板
`;

async function main(): Promise<void> {
  const cmd = process.argv[2];

  if (!cmd || cmd === "help" || cmd === "--help") {
    console.log(HELP);
    return;
  }

  if (cmd !== "preflight" && cmd !== "smoke" && cmd !== "selfheal") {
    console.error(`未知命令：${cmd}`);
    console.log(HELP);
    process.exitCode = 1;
    return;
  }

  const checks = checkEnvironment();
  printChecks(checks);

  // smoke 只需要核心项（够发起模型调用）；完整自愈还要求 runner 项齐备
  const needGroup = cmd === "selfheal" ? undefined : "core";
  if (!allOk(checks, needGroup)) {
    console.error(
      cmd === "selfheal"
        ? "环境未就绪，自愈会跑不起来。请先按上面的提示补齐依赖。\n"
        : "核心依赖未就绪（Node / API Key）。\n",
    );
    process.exitCode = 1;
    return;
  }

  if (cmd === "preflight") return;

  const workdir = resolveWorkdir(cmd);
  console.log(`工作目录：${workdir}\n`);
  console.log("─".repeat(64));

  const outcome = await runAgent({ workdir, task: cmd === "smoke" ? SMOKE_TASK : SELF_HEAL_TASK });

  if (cmd === "selfheal") {
    // 自愈的最低门槛是「至少成功跑过一次工具」。一次都没有，说明模型压根没连上或全程被拒，
    // 脚本根本没被碰过。此时进程正常退出会被上游当成成功（Runner 只看退出码），必须显式失败。
    if (outcome.toolCalls === 0) {
      console.error(
        `\n[自愈失败] 工具调用成功 0 次、模型报错 ${outcome.modelErrors} 次，测试脚本未被修改。` +
          `\n若报错为 Connection error，请检查本机到 ${ZHIPU.baseUrl} 的网络与代理。\n`,
      );
      process.exitCode = 1;
      return;
    }
    console.log(`\n提示：直接查看修复结果 → ${join(workdir, "tests", "test_web_case.py")}`);
  }
}

main().catch((err: unknown) => {
  console.error("\n运行失败：", err instanceof Error ? err.message : err);
  process.exitCode = 1;
});