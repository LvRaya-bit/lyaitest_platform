/**
 * 自愈扩展 —— demo 的主角。
 *
 * 这里演示的是 pi-agent 的「扩展系统」这一层，而不是把内置的
 * read / bash / edit / write 直接开给模型：
 *
 *   1. registerTool 注册四个领域工具（跑测试 / 看页面 / 改定位器 / 记经验），
 *      把「能做什么」收敛成语义明确的白名单，顺带堵掉 bash 这个安全口；
 *   2. on("tool_result") 注册 hook，在测试失败时把修复流程再推一把；
 *   3. remember 工具把成功的定位器写进 .pi-agent/memory.md，
 *      由 run.ts 以 context file 的形式注入下一轮上下文 —— 形成持久化记忆。
 *
 * 加载方式见 run.ts：DefaultResourceLoader({ noExtensions: true, additionalExtensionPaths: [EXTENSION_PATH] })
 */
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { MEMORY_PATH } from "../src/config.js";

/** 工具的返回值统一走 pi-agent 的 AgentToolResult 形状 */
function text(value: string, details: unknown = undefined) {
  return { content: [{ type: "text" as const, text: value }], details };
}

function errorText(value: string) {
  return { content: [{ type: "text" as const, text: value }], details: undefined, isError: true };
}

/** 工作目录内拼路径 */
function inside(cwd: string, ...parts: string[]): string {
  return join(cwd, ...parts);
}

/* ------------------------------------------------------------------ *
 * 1. run_test —— 替代内置 bash 的受控执行口
 * ------------------------------------------------------------------ */

let pythonCmd: string | undefined;

/** python / python3 / py 探测一次并缓存（Windows 上常见的是 py 或 python） */
function detectPython(): string | undefined {
  if (pythonCmd) return pythonCmd;
  for (const candidate of ["python", "python3", "py"]) {
    const r = spawnSync(candidate, ["-V"], { encoding: "utf8", shell: false });
    const out = `${r.stdout ?? ""}${r.stderr ?? ""}`;
    if (r.status === 0 && /python\s+\d/i.test(out)) {
      pythonCmd = candidate;
      return candidate;
    }
  }
  return undefined;
}

interface ExecResult {
  code: number | null;
  output: string;
}

/** 异步执行子进程，避免 spawnSync 把事件循环（含流式输出）整段卡住 */
function run(cmd: string, args: string[], cwd: string, timeoutMs: number): Promise<ExecResult> {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { cwd, shell: false });
    let output = "";
    let done = false;
    const finish = (result: ExecResult) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(result);
    };

    const timer = setTimeout(() => {
      output += `\n[工具] 超时 ${Math.round(timeoutMs / 1000)}s，已终止子进程`;
      child.kill();
    }, timeoutMs);

    child.stdout.on("data", (d: Buffer) => (output += d.toString("utf8")));
    child.stderr.on("data", (d: Buffer) => (output += d.toString("utf8")));
    child.on("error", (err) => finish({ code: -1, output: `${output}\n[工具] 无法启动 ${cmd}：${err.message}` }));
    child.on("close", (code) => finish({ code, output }));
  });
}

/** pytest 输出很长，只把尾部给模型（尾部才带失败原因） */
function tail(raw: string, max = 4000): string {
  const s = raw.trim();
  return s.length <= max ? s : `…（已截断前 ${s.length - max} 字符）\n${s.slice(-max)}`;
}

const runTest = defineTool({
  name: "run_test",
  label: "run_test",
  description:
    "在当前工作目录执行 `python -m pytest -x -q`，返回退出码与输出尾部。" +
    "读取真实失败原因是修复的前提；不要凭猜测修改脚本。",
  promptSnippet: "run_test: 跑一次 pytest，拿到真实报错",
  promptGuidelines: ["先 run_test 拿到真实报错，再动手改，改完必须再 run_test 验证。"],
  parameters: Type.Object({
    note: Type.Optional(Type.String({ description: "本次运行的目的，仅用于日志" })),
  }),
  async execute(_toolCallId, _params, _signal, _onUpdate, ctx) {
    const py = detectPython();
    if (!py) return errorText("未找到 python / python3 / py，无法执行 pytest。");

    const { code, output } = await run(py, ["-m", "pytest", "-x", "-q"], ctx.cwd, 180_000);
    const body = [
      `exit_code: ${code}`,
      `工作目录: ${ctx.cwd}`,
      "--- pytest 输出（尾部）---",
      tail(output) || "(无输出)",
    ].join("\n");

    return code === 0 ? text(body, { exitCode: code }) : errorText(body);
  },
});

/* ------------------------------------------------------------------ *
 * 2. inspect_dom —— 看被测页面，给出候选定位器
 * ------------------------------------------------------------------ */

interface Candidate {
  tag: string;
  id?: string;
  name?: string;
  cls?: string;
  text?: string;
  selector: string;
}

/** 交互性元素优先：这些才是用例真正会去点的东西 */
const INTERACTIVE = new Set(["button", "input", "a", "select", "textarea", "form", "label"]);

function attr(attrs: string, key: string): string | undefined {
  const m = attrs.match(new RegExp(`${key}\\s*=\\s*("([^"]*)"|'([^']*)'|([^\\s>]+))`, "i"));
  return m ? (m[2] ?? m[3] ?? m[4]) : undefined;
}

/** 纯文本解析 HTML（不引 Playwright，够用且零依赖） */
function parseCandidates(html: string): Candidate[] {
  const out: Candidate[] = [];
  const tagRe = /<([a-zA-Z][\w-]*)((?:\s+[^<>]*?)?)\s*(\/?)>/g;
  let m: RegExpExecArray | null;
  while ((m = tagRe.exec(html)) !== null) {
    const tag = m[1].toLowerCase();
    const attrs = m[2] ?? "";
    if (tag === "script" || tag === "style" || tag === "meta" || tag === "link") continue;

    const id = attr(attrs, "id");
    const name = attr(attrs, "name");
    const cls = (attr(attrs, "class") ?? "").split(/\s+/).filter(Boolean)[0];
    // 开标签之后紧跟的文本，当作按钮 / 链接 / 标题的可读文案
    const rest = html.slice(tagRe.lastIndex, tagRe.lastIndex + 200);
    const textMatch = rest.match(/^\s*([^<>{}]{1,60})/);
    const elemText = textMatch ? textMatch[1].trim() : undefined;

    let selector = "";
    if (id) selector = `#${id}`;
    else if (name) selector = `[name="${name}"]`;
    else if (cls) selector = `${tag}.${cls}`;
    if (!selector) continue; // 没有任何稳定锚点的元素，提了也没用

    out.push({ tag, id, name, cls, text: elemText, selector });
  }
  return out;
}

/** role 建议：Playwright 的 role 定位器比 id / class 更稳，顺带提示一句 */
function roleHint(c: Candidate): string {
  const role =
    c.tag === "button"
      ? "button"
      : c.tag === "a"
        ? "link"
        : c.tag === "input"
          ? "textbox"
          : c.tag === "select"
            ? "combobox"
            : undefined;
  if (!role) return "";
  const label = c.text ? `[name="${c.text}"]` : c.name ? `[name="${c.name}"]` : "";
  return label ? `  role=${role}${label}` : `  role=${role}`;
}

const inspectDom = defineTool({
  name: "inspect_dom",
  label: "inspect_dom",
  description:
    "读取工作目录下 site/index.html，按关键词筛出候选元素，并给出可用的定位器建议。" +
    "改定位器之前必须先用它确认真实的 id / name / 文案，不要凭空猜。",
  promptSnippet: "inspect_dom: 看被测页面，拿候选定位器",
  promptGuidelines: ["不要靠猜 id / class；用 inspect_dom 从页面源码里确认。"],
  parameters: Type.Object({
    keyword: Type.Optional(
      Type.String({
        description: "只列出 id / name / class / 文案 命中该关键词的元素；省略则列出所有可交互元素",
      }),
    ),
    limit: Type.Optional(Type.Number({ description: "最多返回多少条，默认 15" })),
  }),
  async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
    const file = inside(ctx.cwd, "site", "index.html");
    if (!existsSync(file)) {
      return errorText(`页面源码不存在：${file}（自愈工作区里必须有 site/index.html）`);
    }

    const html = readFileSync(file, "utf8");
    const keyword = (params.keyword ?? "").toLowerCase().trim();
    const limit = Math.max(1, Math.min(50, params.limit ?? 15));

    const all = parseCandidates(html);
    const matched = keyword
      ? all.filter((c) => [c.id, c.name, c.cls, c.text].some((v) => v && v.toLowerCase().includes(keyword)))
      : all.filter((c) => INTERACTIVE.has(c.tag));

    const picked = (matched.length > 0 ? matched : all).slice(0, limit);
    if (picked.length === 0) return text("页面里没解析出带 id / name / class 的元素。");

    const lines = picked.map((c, i) => {
      const parts = [
        `${i + 1}. <${c.tag}>`,
        c.id ? `id=${c.id}` : "",
        c.name ? `name=${c.name}` : "",
        c.cls ? `class=${c.cls}` : "",
        c.text ? `text="${c.text}"` : "",
      ].filter(Boolean);
      return `${parts.join(" ")}\n   建议定位器：${c.selector}${roleHint(c)}`;
    });

    return text(
      `site/index.html 候选元素（共解析 ${all.length} 个，命中 ${matched.length} 个，返回 ${picked.length} 个）\n\n` +
        lines.join("\n"),
      { total: all.length, matched: matched.length },
    );
  },
});

/* ------------------------------------------------------------------ *
 * 3. heal_selector —— 真正动手改测试脚本
 * ------------------------------------------------------------------ */

const healSelector = defineTool({
  name: "heal_selector",
  label: "heal_selector",
  description:
    "把 tests/test_web_case.py 里失效的定位器字面量替换成新的。只动这一个文件，" +
    "不会碰 site/ 里的被测页面；也不允许删断言。",
  promptSnippet: "heal_selector: 把脚本里写错的定位器换成正确的",
  promptGuidelines: ["每轮只改最小的一处，改完立刻 run_test 验证。"],
  parameters: Type.Object({
    old_selector: Type.String({ description: "脚本里现在写错的定位器字面量，例如 #submit-btn" }),
    new_selector: Type.String({ description: "替换成的新定位器，例如 #login-submit" }),
    reason: Type.Optional(Type.String({ description: "为什么这样替换，例如 页面上的按钮 id 是 login-submit" })),
  }),
  async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
    const oldSelector = params.old_selector.trim();
    const newSelector = params.new_selector.trim();
    if (!oldSelector || !newSelector) return errorText("old_selector 与 new_selector 都不能为空。");
    if (oldSelector === newSelector) return text("新旧定位器相同，无需修改。");

    const file = inside(ctx.cwd, "tests", "test_web_case.py");
    if (!existsSync(file)) return errorText(`测试脚本不存在：${file}`);

    const before = readFileSync(file, "utf8");
    const hits = before.split(oldSelector).length - 1;
    if (hits === 0) {
      return errorText(`测试脚本里找不到字面量 ${oldSelector}。请先用 inspect_dom 确认页面，再核对脚本原文。`);
    }

    writeFileSync(file, before.split(oldSelector).join(newSelector), "utf8");
    return text(
      `已替换 ${hits} 处：${oldSelector} → ${newSelector}\n文件：tests/test_web_case.py` +
        (params.reason ? `\n原因：${params.reason}` : "") +
        "\n请立刻 run_test 验证。",
      { replaced: hits },
    );
  },
});

/* ------------------------------------------------------------------ *
 * 4. remember —— 持久化记忆
 * ------------------------------------------------------------------ */

const MEMORY_HEADER = [
  "# pi-agent 自愈记忆",
  "",
  "> 由 remember 工具维护：记录「站点 + 失效定位器 → 有效定位器」的修复经验。",
  "> 该文件会被加载为 context file，在后续任务开始时自动进入上下文。",
  "",
].join("\n");

const remember = defineTool({
  name: "remember",
  label: "remember",
  description:
    "把一条「失效定位器 → 有效定位器」的修复经验写入持久化记忆（.pi-agent/memory.md），" +
    "下次任务会被自动加载。修复验证通过后调用一次即可。",
  promptSnippet: "remember: 把这次修好的定位器写进长期记忆",
  promptGuidelines: ["只在 run_test 通过之后调用 remember，避免把没验证的猜测写进记忆。"],
  parameters: Type.Object({
    site: Type.String({ description: "站点标识，例如 登录页 / https://example.com/login" }),
    old_selector: Type.String({ description: "失效的定位器" }),
    new_selector: Type.String({ description: "验证有效的定位器" }),
    note: Type.Optional(Type.String({ description: "补充说明，例如 页面按钮 id 是 login-submit" })),
  }),
  async execute(_toolCallId, params) {
    const line = `- [${params.site}] \`${params.old_selector}\` → \`${params.new_selector}\`${
      params.note ? ` — ${params.note}` : ""
    }`;

    const existing = existsSync(MEMORY_PATH) ? readFileSync(MEMORY_PATH, "utf8") : "";
    const duplicated = existing
      .split(/\r?\n/)
      .some((l) => l.includes(params.old_selector) && l.includes(params.new_selector));
    if (duplicated) return text(`记忆中已存在同一条经验，跳过写入：\n${line}`);

    mkdirSync(dirname(MEMORY_PATH), { recursive: true });
    const body = existing.trim().length > 0 ? existing.replace(/\s*$/, "") : MEMORY_HEADER.trimEnd();
    writeFileSync(MEMORY_PATH, `${body}\n${line}\n`, "utf8");

    return text(`已写入记忆：\n${line}\n文件：${MEMORY_PATH}`, { memoryPath: MEMORY_PATH });
  },
});

/* ------------------------------------------------------------------ *
 * tool_result hook —— 测试失败时把修复流程再推一把
 * ------------------------------------------------------------------ */

/** 定位失败的典型特征（Playwright + pytest 的报错文案） */
const LOCATOR_FAILURE =
  /Timeout|TimeoutError|waiting for locator|no element|strict mode violation|not visible|FAILED|failed/i;

const HEAL_HINT = [
  "",
  "[自愈提示] 上一步是失败的测试运行。按这个顺序继续：",
  "1. inspect_dom —— 从 site/index.html 里找出真实存在的元素与它的定位器",
  "2. heal_selector —— 把脚本里写错的定位器换成上一步确认的那个（每轮只改一处）",
  "3. run_test —— 立刻重跑验证",
  "全部通过后调用 remember，把这次的经验写进长期记忆。",
].join("\n");

export default function selfhealExtension(pi: ExtensionAPI): void {
  pi.registerTool(runTest);
  pi.registerTool(inspectDom);
  pi.registerTool(healSelector);
  pi.registerTool(remember);

  pi.on("tool_result", (event) => {
    if (event.toolName !== "run_test") return;
    const body = event.content.map((c) => (c.type === "text" ? c.text : "")).join("\n");

    // 已经推过一次就不再追加，避免多轮重复刷屏
    if (body.includes("[自愈提示]")) return;
    if (!event.isError && !LOCATOR_FAILURE.test(body)) return;

    return { content: [...event.content, { type: "text" as const, text: HEAL_HINT }] };
  });
}