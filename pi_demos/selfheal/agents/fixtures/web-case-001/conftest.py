"""自愈工作区的公共夹具：本地静态站点 + Playwright 浏览器。

刻意不引入 pytest-playwright 插件 —— 只用 playwright 一个依赖，
这样 preflight 的检查项和实际依赖完全一一对应。
"""
import functools
import http.server
import os
import socket
import threading

import pytest

SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """默认的 handler 会把每个请求打到 stderr，这里静音，避免干扰 agent 读日志"""

    def log_message(self, *args):  # noqa: D102
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url():
    handler = functools.partial(_QuietHandler, directory=SITE_DIR)
    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}/index.html"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


@pytest.fixture()
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    pg.set_default_timeout(5000)
    try:
        yield pg
    finally:
        context.close()