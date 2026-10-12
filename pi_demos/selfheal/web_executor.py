"""自愈 demo 自带的迷你 Web 用例执行器。

平台里的同类实现在 local_runner/appium_runner.py，但那个模块还牵着 Appium / ADB
等一堆依赖。demo 要求独立可跑，所以这里只保留 Web 用例真正用到的部分：用 Playwright
打开 Chromium 逐步执行 steps，并采集页面可交互元素清单，供模型判断真实选择器。

不做任何伪造：无法识别的动作记为 skipped，执行失败记为 failed。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

STEP_TIMEOUT_MS = 10000


def collect_dom_outline(page, limit: int = 80) -> List[Dict[str, Any]]:
    """采集当前页面可交互元素清单（tag/id/name/type/testid/文本）。

    自愈时让模型知道页面上真实存在的选择器，否则它只能靠猜。
    """
    try:
        return page.evaluate(
            """(limit) => {
                const nodes = Array.from(document.querySelectorAll(
                    'a, button, input, select, textarea, form, [role=button], [onclick], [data-testid], [id]'
                ));
                const out = [];
                for (const el of nodes) {
                    if (out.length >= limit) break;
                    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '')
                        .trim().replace(/\\s+/g, ' ').slice(0, 60);
                    out.push({
                        tag: el.tagName.toLowerCase(),
                        id: el.id || '',
                        name: el.getAttribute('name') || '',
                        type: el.getAttribute('type') || '',
                        testid: el.getAttribute('data-testid') || '',
                        text: text,
                    });
                }
                return out;
            }""",
            limit,
        )
    except Exception:
        return []


def assertion(page, element: str, expected: str, last_status_code):
    """执行一条 Web 断言，返回 (是否通过, 说明)。

    注意：element 为选择器时直接取文本，取不到会抛异常，由调用方记为执行失败 ——
    「选择器写错」和「断言语义不符」由此得到两种不同的报错，模型据此判断该改哪个字段。
    """
    key = element.lower()
    if key in {"status", "status_code", "状态码"}:
        if last_status_code is None:
            return False, "断言状态码失败：之前没有 navigate 步骤记录响应"
        ok = str(last_status_code) == expected
        return ok, f"断言状态码 {expected} → {'通过' if ok else f'失败（实际 {last_status_code}）'}"
    if key in {"url", "地址"}:
        actual = page.url
        ok = expected in actual
        return ok, f"断言 URL 包含 {expected} → {'通过' if ok else f'失败（实际 {actual}）'}"
    if key in {"title", "标题"}:
        actual = page.title()
        ok = expected in actual
        return ok, f"断言标题包含 {expected} → {'通过' if ok else f'失败（实际 {actual}）'}"
    actual = page.inner_text(element)
    ok = expected in actual
    return ok, f"断言 {element} 文本包含 {expected} → {'通过' if ok else f'失败（实际 {actual[:120]}）'}"


def run_steps(page, case: Dict[str, Any], screenshot_dir: Optional[Path] = None) -> Dict[str, Any]:
    """在真实浏览器页面上逐步执行单个用例，返回结果摘要。"""
    name = case.get("name") or "未命名用例"
    steps = case.get("steps") or []
    logs: List[Dict[str, Any]] = []
    passed = failed = skipped = 0
    last_status_code = None
    dom_outline: List[Dict[str, Any]] = []

    for index, step in enumerate(steps):
        action = str(step.get("action") or "").strip().lower()
        element = str(step.get("element") or "").strip()
        value = str(step.get("value") or "").strip()
        expected = str(step.get("expected") or "").strip()
        entry: Dict[str, Any] = {
            "step_index": index + 1,
            "action": action,
            "element": element,
            "value": value,
            "expected": expected,
        }
        try:
            if action in {"navigate", "open", "goto"}:
                url = element or value
                if not url:
                    raise ValueError("navigate 步骤缺少 URL")
                response = page.goto(url, wait_until="load")
                last_status_code = response.status if response else None
                dom_outline = collect_dom_outline(page)
                entry["dom_outline"] = dom_outline
                entry["status"] = "passed"
                entry["message"] = f"访问 {url} → HTTP {last_status_code}"
            elif action in {"click", "tap"}:
                if not element:
                    raise ValueError("click 步骤缺少元素选择器")
                page.click(element)
                entry["status"] = "passed"
                entry["message"] = f"点击 {element}"
            elif action in {"input", "fill", "type", "send_keys"}:
                if not element:
                    raise ValueError("input 步骤缺少元素选择器")
                page.fill(element, value)
                entry["status"] = "passed"
                entry["message"] = f"在 {element} 输入 {value}"
            elif action in {"assert", "verify", "check"}:
                if not expected:
                    raise ValueError("assert 步骤缺少 expected")
                ok, message = assertion(page, element, expected, last_status_code)
                entry["status"] = "passed" if ok else "failed"
                entry["message"] = message
            elif action in {"wait", "wait_for"}:
                if element.isdigit():
                    page.wait_for_timeout(int(element))
                    entry["message"] = f"等待 {element} 毫秒"
                elif element:
                    page.wait_for_selector(element)
                    entry["message"] = f"等待元素 {element} 出现"
                else:
                    page.wait_for_timeout(1000)
                    entry["message"] = "等待 1000 毫秒"
                entry["status"] = "passed"
            elif action in {"screenshot", "capture"}:
                entry["status"] = "passed"
                if screenshot_dir:
                    shot_path = Path(screenshot_dir) / f"step{index + 1}.png"
                    page.screenshot(path=str(shot_path), full_page=True)
                    entry["message"] = f"截图已保存 {shot_path}"
                    entry["screenshot"] = str(shot_path)
                else:
                    entry["message"] = "截图步骤已跳过（未指定输出目录）"
            else:
                entry["status"] = "skipped"
                entry["message"] = f"暂不支持的动作「{action or '(空)'}」，未执行"
        except Exception as exc:
            # 失败时也采一次页面元素，方便自愈判断可用选择器
            dom_outline = collect_dom_outline(page)
            entry["dom_outline"] = dom_outline
            entry["status"] = "failed"
            entry["message"] = f"{action} {element} 执行失败：{exc}"

        if entry["status"] == "passed":
            passed += 1
        elif entry["status"] == "failed":
            failed += 1
        else:
            skipped += 1
        logs.append(entry)

    return {
        "name": name,
        "total": len(steps),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "dom_outline": dom_outline,
        "logs": logs,
    }


def run_case(case: Dict[str, Any], *, headless: bool = True,
             screenshot_dir: Optional[Path] = None,
             step_timeout_ms: int = STEP_TIMEOUT_MS) -> Dict[str, Any]:
    """起一个 Chromium 执行单个用例，返回结果摘要。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            page.set_default_timeout(step_timeout_ms)
            try:
                return run_steps(page, case, screenshot_dir)
            finally:
                page.close()
        finally:
            browser.close()