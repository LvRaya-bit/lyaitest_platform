"""用例自愈 demo · 路线 A：让 AI 直接修 steps。

流程：起本地静态站点 -> Playwright 真实执行 steps -> 把「真实报错 + 页面可交互元素清单」
交给 GLM -> 产出修正后的 steps -> 重跑，直到全部通过或达到轮次上限。

不依赖平台后端与数据库，只需要：playwright（含 chromium）+ ZHIPU_API_KEY。

用法：
    python demos/selfheal/heal_steps.py
    python demos/selfheal/heal_steps.py --case demos/selfheal/cases/example_login.json --show-browser
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import web_executor  # noqa: E402  （同目录模块，需先加入 sys.path）

DEMO_DIR = Path(__file__).resolve().parent
DEFAULT_CASE = DEMO_DIR / "cases" / "example_login.json"
DEFAULT_SITE = DEMO_DIR / "agents" / "fixtures" / "web-case-001" / "site"

# 最多自愈轮次，避免无限循环
MAX_ROUNDS = 5
GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

SYSTEM_PROMPT = """你是 Web 自动化测试用例修复专家。
用户会给你一条真实执行失败的 Playwright 用例步骤（JSON 数组）以及页面真实可交互元素清单。
你的任务是输出修正后的完整 steps JSON 数组。

硬性要求：
1. 保持步骤的语义、数量和顺序不变，只修正错误的选择器、URL、期望值等参数。
2. 选择器优先级：id 选择器（#id）> name 属性 > data-testid > 可见文本。
3. 只能使用「页面可交互元素清单」中真实存在的元素，严禁臆造选择器。
4. 断言（assert）的 expected 必须与被测页面真实文案一致。
5. 只输出 JSON 数组本身，不要解释、不要 markdown 代码块、不要多余文字。
6. navigate 步骤里的 {{base_url}} 是站点根地址占位符，必须原样保留，不要替换成具体 URL。

步骤字段说明：action(navigate/click/input/assert/wait)、element(选择器)、
value(input 的值)、expected(assert 期望文本)。"""


def find_dotenv() -> Optional[Path]:
    """从 demo 目录逐层向上找 .env，兼容 demo 被搬到任意层级或独立目录。"""
    directory = DEMO_DIR
    for _ in range(8):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
        if directory.parent == directory:
            return None
        directory = directory.parent
    return None


def load_env() -> None:
    """极简 .env 加载器：只补进程里缺失的键，绝不覆盖已有环境变量。"""
    path = find_dotenv()
    if not path:
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and not os.environ.get(key):
            os.environ[key] = value


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # 静音，避免静态资源请求刷屏
        pass


def serve_site(site_dir: Path) -> Tuple[str, ThreadingHTTPServer]:
    """起一个本地静态服务器（端口随机），返回 (base_url, server)。"""
    if not site_dir.is_dir():
        raise SystemExit(f"被测站点目录不存在：{site_dir}")
    handler = partial(_QuietHandler, directory=str(site_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return f"http://{host}:{port}", server


def resolve_placeholders(steps: List[Dict[str, Any]], base_url: str) -> List[Dict[str, Any]]:
    """把 {{base_url}} 换成真实站点地址后交给执行器（模型看到的始终是占位符形式）。"""
    text = json.dumps(steps, ensure_ascii=False).replace("{{base_url}}", base_url)
    return json.loads(text)


def collect_failures(summary: Dict[str, Any]) -> Tuple[List[str], List[Dict[str, Any]]]:
    """从执行结果里提取失败步骤说明与页面元素清单。"""
    failed_msgs: List[str] = []
    dom_outline: List[Dict[str, Any]] = summary.get("dom_outline") or []
    for entry in summary.get("logs", []):
        if entry.get("status") == "failed":
            failed_msgs.append(
                f"- 步骤 {entry.get('step_index')} [{entry.get('action')}] {entry.get('message', '')}"
            )
        if entry.get("dom_outline"):
            dom_outline = entry["dom_outline"]
    return failed_msgs, dom_outline


def parse_steps(text: str) -> List[Dict[str, Any]]:
    """从模型回复里解析 steps JSON 数组，容忍 markdown 代码块包裹。"""
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fenced:
        cleaned = fenced.group(1).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("模型返回的不是非空数组")
    for item in parsed:
        if not isinstance(item, dict) or "action" not in item:
            raise ValueError("步骤格式不合法，缺少 action 字段")
    return parsed


def ask_llm(model: str, case_name: str, steps: List[Dict[str, Any]],
            failed_msgs: List[str], dom_outline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把失败详情与页面元素清单交给 GLM，取回修正后的 steps。"""
    api_key = os.getenv("ZHIPU_API_KEY")
    if not api_key:
        raise SystemExit("未找到 ZHIPU_API_KEY：请在仓库根 .env 中配置后重试")

    dom_text = json.dumps(dom_outline, ensure_ascii=False, indent=1) if dom_outline else "（未采集到）"
    user_content = (
        f"用例名称：{case_name}\n\n"
        f"当前步骤 JSON：\n{json.dumps(steps, ensure_ascii=False, indent=1)}\n\n"
        f"执行失败详情：\n" + "\n".join(failed_msgs) + "\n\n"
        f"页面可交互元素清单（导航后采集）：\n{dom_text}\n\n"
        "请输出修正后的完整 steps JSON 数组。"
    )
    response = requests.post(
        f"{GLM_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        },
        timeout=120,
    )
    if response.status_code >= 400:
        raise SystemExit(f"模型调用失败（HTTP {response.status_code}）：{response.text[:300]}")
    content = response.json()["choices"][0]["message"]["content"]
    return parse_steps(content)


def print_summary(summary: Dict[str, Any]) -> None:
    marks = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}
    for entry in summary.get("logs", []):
        print(f"  [{marks.get(entry.get('status'), '?')}] 步骤 {entry.get('step_index')}: "
              f"{entry.get('message')}")
    print(f"  小计：通过 {summary.get('passed')} / 失败 {summary.get('failed')} "
          f"/ 跳过 {summary.get('skipped')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="用例自愈 demo：AI 直接修正失败的 steps")
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE,
                        help="用例 JSON 文件（含 name 与 steps），默认用内置示例")
    parser.add_argument("--site", type=Path, default=DEFAULT_SITE,
                        help="被测站点目录，demo 会为本机起一个静态服务器指向它")
    parser.add_argument("--rounds", type=int, default=MAX_ROUNDS, help="最大自愈轮次")
    parser.add_argument("--model", default=os.getenv("GLM_MODEL_ID", "glm-4"), help="模型 ID")
    parser.add_argument("--show-browser", action="store_true", help="显示浏览器窗口（演示用）")
    parser.add_argument("--out", type=Path, default=None,
                        help="修正后 steps 的落盘位置，默认写到 <用例名>.healed.json")
    args = parser.parse_args()

    load_env()
    case = json.loads(args.case.read_text(encoding="utf-8"))
    case_name = case.get("name") or args.case.stem
    steps = case.get("steps") or []
    if not steps:
        raise SystemExit("用例里没有可执行的 steps")

    out_path = args.out or args.case.with_name(f"{args.case.stem}.healed.json")
    base_url, server = serve_site(args.site)

    print("=" * 68)
    print(f"用例自愈 demo · 路线 A（AI 直接修 steps）")
    print(f"用例：{case_name}    模型：{args.model}    最大轮次：{args.rounds}")
    print(f"被测站点：{base_url}（{args.site}）")
    print("=" * 68)

    succeeded = False
    try:
        for round_index in range(1, args.rounds + 1):
            print(f"\n--- 第 {round_index} 轮：真实执行 ---")
            runnable = {"name": case_name, "steps": resolve_placeholders(steps, base_url)}
            summary = web_executor.run_case(runnable, headless=not args.show_browser)
            print_summary(summary)

            if summary["failed"] == 0 and summary["skipped"] == 0:
                print(f"\n[完成] 第 {round_index} 轮全部通过，共 {len(steps)} 步。")
                succeeded = True
                break

            failed_msgs, dom_outline = collect_failures(summary)
            if not failed_msgs:
                failed_msgs = ["- 未从日志中解析出具体失败原因"]
            print(f"\n--- 第 {round_index} 轮：交给模型修正 ---")
            print("失败详情：" + "\n".join(failed_msgs))
            print(f"页面元素清单：采集到 {len(dom_outline)} 个可交互元素")
            steps = ask_llm(args.model, case_name, steps, failed_msgs, dom_outline)
            print("模型返回修正后 steps：")
            print(json.dumps(steps, ensure_ascii=False, indent=1))

        if not succeeded:
            print(f"\n[未完成] 已执行 {args.rounds} 轮仍未全部通过。")
    finally:
        server.shutdown()

    out_path.write_text(
        json.dumps({"name": case_name, "steps": steps}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\n修正后的 steps 已写入：{out_path}")
    if not succeeded:
        sys.exit(1)


if __name__ == "__main__":
    main()