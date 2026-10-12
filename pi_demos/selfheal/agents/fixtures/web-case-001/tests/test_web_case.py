"""登录页表单校验 —— 由 agents/ 下的 pi-agent 自愈修复。

用例预期见同目录的 case.json。
"""
from playwright.sync_api import Page, expect


def test_login_form(page: Page, base_url: str):
    page.goto(base_url)

    page.fill("#username", "qa_user")
    page.fill("#password", "qa_pass_123")

    # BUG-1：这个 id 在页面上并不存在
    page.click("#submit-btn")

    # BUG-2：断言漏了页面实际输出中的用户名的部分
    expect(page.locator("#welcome")).to_have_text("欢迎回来")