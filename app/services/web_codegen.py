"""把审核后的 Web 用例导出为 Pytest + Playwright + POM 工程文件。

生成器只接受平台已经保存的 executable steps，不让模型直接输出 Python，
从而保留探索证据校验和现有 JSON Runner 的兼容性。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List


def _json_literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=4)


def generate_pytest_bundle(cases: List[Dict[str, Any]]) -> Dict[str, str]:
    runnable = []
    for case in cases:
        steps = case.get("executable_steps") or case.get("executable") or []
        if not steps:
            continue
        runnable.append({
            "title": case.get("title") or "未命名用例",
            "priority": case.get("priority") or "P2",
            "test_type": case.get("test_type") or "功能测试",
            "steps": steps,
        })

    case_data = _json_literal(runnable)
    generated_test = (
        "import pytest\n"
        "from pages.generated_case_page import GeneratedCasePage\n\n"
        f"CASES = {case_data}\n\n\n"
        "@pytest.mark.parametrize(\"case\", CASES, ids=[case[\"title\"] for case in CASES])\n"
        "def test_generated_web_case(page, case):\n"
        "    # 由平台审核后的 P0/P1 Web 用例。\n"
        "    GeneratedCasePage(page).run(case[\"steps\"], case[\"title\"])\n"
    )
    return {
        "config.py": '''import os

BASE_URL = os.getenv("WEB_BASE_URL", "")
BROWSER = os.getenv("WEB_BROWSER", "chromium")
HEADLESS = os.getenv("WEB_HEADLESS", "1") == "1"
DEFAULT_TIMEOUT = int(os.getenv("WEB_TIMEOUT_MS", "10000"))
''',
        "conftest.py": '''import pytest
from playwright.sync_api import sync_playwright
import config


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, config.BROWSER)
        instance = browser_type.launch(headless=config.HEADLESS)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(ignore_https_errors=True)
    context.set_default_timeout(config.DEFAULT_TIMEOUT)
    page = context.new_page()
    yield page
    context.close()
''',
        "pages/__init__.py": "",
        "pages/generated_case_page.py": '''import re
from pathlib import Path
from playwright.sync_api import expect


class GeneratedCasePage:
    """统一承载探索结果中的稳定定位器，测试用例只负责描述场景。"""

    def __init__(self, page):
        self.page = page
        self.artifact_dir = Path("artifacts")
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def locator(self, raw):
        raw = (raw or "").strip()
        if raw.startswith("role="):
            body = raw[len("role="):].strip()
            match = re.fullmatch(r"([A-Za-z_][\\w-]*)(?:\\[name=(?:\\\"([^\\\"]*)\\\"|'([^']*)')\\])?", body)
            if not match:
                raise ValueError(f"role 定位器格式错误: {raw}")
            name = match.group(2) if match.group(2) is not None else match.group(3)
            return self.page.get_by_role(match.group(1), name=name) if name is not None else self.page.get_by_role(match.group(1))
        if raw.startswith("text="):
            return self.page.get_by_text(raw[len("text="):])
        if raw.startswith("testid="):
            return self.page.get_by_test_id(raw[len("testid="):])
        return self.page.locator(raw)

    def run(self, steps, case_name):
        for index, step in enumerate(steps, start=1):
            action = str(step.get("action") or "").lower()
            element = str(step.get("element") or "")
            value = str(step.get("value") or "")
            expected = str(step.get("expected") or "")
            if action == "navigate":
                response = self.page.goto(element or value, wait_until="load")
                assert response is None or response.status < 500, f"导航失败: {element or value}"
            elif action == "click":
                self.locator(element).click()
            elif action == "input":
                self.locator(element).fill(value)
            elif action == "wait":
                if element.isdigit():
                    self.page.wait_for_timeout(int(element))
                else:
                    self.locator(element).wait_for()
            elif action == "assert":
                if element.lower() in {"title", "标题"}:
                    expect(self.page).to_have_title(re.compile(re.escape(expected)))
                elif element.lower() in {"url", "地址"}:
                    assert expected in self.page.url, f"URL 不包含 {expected}: {self.page.url}"
                else:
                    expect(self.locator(element)).to_contain_text(expected)
            elif action == "screenshot":
                safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", case_name)[:80]
                self.page.screenshot(path=str(self.artifact_dir / f"{safe_name}_{index}.png"), full_page=True)
            else:
                raise AssertionError(f"不支持的动作: {action}")
''',
        "testcases/__init__.py": "",
        "testcases/test_generated_cases.py": generated_test,
        "README.md": """# Generated Web Tests

运行：

```bash
pip install pytest playwright
playwright install chromium
$env:WEB_HEADLESS='1'
pytest testcases/ -q --junitxml=reports/junit.xml
```

本目录由平台根据审核后的 P0/P1 用例生成，原平台 JSON Runner 仍可继续执行。
""",
    }
