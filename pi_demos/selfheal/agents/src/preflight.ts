import { spawnSync } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { hasZhipuKey, ZHIPU } from "./config.js";

const C = { green: "\x1b[32m", red: "\x1b[31m", dim: "\x1b[2m", yellow: "\x1b[33m", reset: "\x1b[0m" };

export type CheckGroup = "core" | "runner";

export interface Check {
  name: string;
  ok: boolean;
  detail: string;
  /** 不满足时告诉用户该敲什么命令 */
  fix?: string;
  group: CheckGroup;
}

function sh(command: string): { ok: boolean; out: string } {
  const r = spawnSync(command, { encoding: "utf8", shell: true });
  const out = `${r.stdout ?? ""}${r.stderr ?? ""}`.trim();
  return { ok: r.status === 0 && !out.toLowerCase().includes("not found"), out };
}

/** Python 解释器可能叫 python 也可能是 python3，探测一次并复用 */
function detectPython(): string | undefined {
  for (const candidate of ["python", "python3", "py"]) {
    const r = sh(`${candidate} -V`);
    if (r.ok && /python\s+\d/i.test(r.out)) return candidate;
  }
  return undefined;
}

/** Playwright 把浏览器装在 ms-playwright 目录下，检查里面有没有 chromium */
function chromiumInstalled(): boolean {
  const base =
    process.env.PLAYWRIGHT_BROWSERS_PATH ??
    process.platform === "win32"
      ? join(process.env.LOCALAPPDATA ?? join(homedir(), "AppData", "Local"), "ms-playwright")
      : join(homedir(), ".cache", "ms-playwright");
  if (!existsSync(base)) return false;
  try {
    return readdirSync(base).some((n) => n.toLowerCase().startsWith("chromium"));
  } catch {
    return false;
  }
}

export function checkEnvironment(): Check[] {
  const checks: Check[] = [];

  const nodeMajor = Number(process.versions.node.split(".")[0]);
  checks.push({
    name: `Node.js ${process.versions.node}`,
    ok: nodeMajor >= 20,
    detail: nodeMajor >= 20 ? "版本满足 (>=20)" : "需要 Node 20 及以上",
    fix: "升级 Node.js 到 20+",
    group: "core",
  });

  checks.push({
    name: `环境变量 ${ZHIPU.envKeyName}`,
    ok: hasZhipuKey(),
    detail: hasZhipuKey() ? "已配置（值已隐去）" : "未配置",
    fix: `在仓库根目录 .env 中加一行 ${ZHIPU.envKeyName}=<your-key>`,
    group: "core",
  });

  const py = detectPython();
  checks.push({
    name: "Python",
    ok: Boolean(py),
    detail: py ? `${py} 可用` : "未找到 python / python3",
    fix: "安装 Python 3.10+ 并确保在 PATH 中",
    group: "runner",
  });

  if (py) {
    const pytest = sh(`${py} -m pytest --version`);
    checks.push({
      name: "pytest",
      ok: pytest.ok,
      detail: pytest.ok ? pytest.out.split("\n")[0] : "未安装",
      fix: `${py} -m pip install pytest`,
      group: "runner",
    });

    // playwright 包没有 __version__ 属性，必须走 importlib.metadata 取版本
    const pw = sh(`${py} -c "import importlib.metadata as m; print(m.version('playwright'))"`);
    checks.push({
      name: "playwright (Python 包)",
      ok: pw.ok,
      detail: pw.ok ? `v${pw.out}` : "未安装到当前 Python 解释器",
      fix: `${py} -m pip install playwright`,
      group: "runner",
    });

    const chromium = chromiumInstalled();
    checks.push({
      name: "Playwright chromium 浏览器",
      ok: chromium,
      detail: chromium ? "已下载" : "未下载",
      fix: `${py} -m playwright install chromium`,
      group: "runner",
    });
  }

  return checks;
}

export function printChecks(checks: Check[]): void {
  console.log("\n环境自检");
  console.log("─".repeat(64));
  for (const c of checks) {
    const mark = c.ok ? `${C.green}OK  ${C.reset}` : `${C.red}FAIL${C.reset}`;
    const tag = c.group === "runner" ? `${C.dim}[自愈所需]${C.reset}` : `${C.dim}[必需]${C.reset}`;
    console.log(`  ${mark} ${c.name} — ${c.detail} ${tag}`);
  }
  const missing = checks.filter((c) => !c.ok);
  if (missing.length > 0) {
    console.log("─".repeat(64));
    console.log(`${C.yellow}待处理：${C.reset}`);
    for (const c of missing) console.log(`  • ${c.name} → ${c.fix ?? "请手动修复"}`);
  }
  console.log("");
}

export function allOk(checks: Check[], group?: CheckGroup): boolean {
  return checks.filter((c) => !group || c.group === group).every((c) => c.ok);
}