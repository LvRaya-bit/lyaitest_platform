"""浏览器登录态持久化。

使用 Streamlit 双向组件把登录态写入同源 Cookie。Cookie 只用于恢复前端会话，
每次恢复仍由后端 JWT 校验；不使用 URL 查询参数，避免令牌出现在历史记录和日志中。
"""
from pathlib import Path

import streamlit.components.v1 as components


_COMPONENT = components.declare_component(
    "lyaitest_auth_persistence",
    path=str(Path(__file__).with_name("auth_persistence_component")),
)


def run(*, action, request_id, identity=None):
    """执行一次 Cookie 操作并返回组件结果。首次调用可能返回 None。"""
    return _COMPONENT(
        action=action,
        request_id=request_id,
        identity=identity or {},
        key="lyaitest_auth_persistence",
        default=None,
    )
