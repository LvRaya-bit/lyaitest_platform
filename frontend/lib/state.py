"""会话状态集中管理：跨页共享的核心 key 统一初始化与访问。

范围：身份（token/username/user_id）、路由（page）、选择与编辑
（selected_*/editing_*）、消息（messages）、web 运行结果（web_run_results）。
页内临时 UI 状态（如 web_login_state、wc_steps_rows、if_* 等）仍直接用
st.session_state，不集中管理，避免过度抽象。
"""
import streamlit as st


# 核心 key 及默认值
_DEFAULTS = {
    "token": None,
    "username": None,
    "user_id": None,
    "page": "AI助手中心",
    "selected_session": None,
    "selected_kb": None,
    "selected_interface": None,
    "selected_web_case": None,
    "selected_app_device": None,
    "editing_interface": None,
    "editing_web_case": None,
    "messages": [],
    "web_run_results": None,
    # 浏览器持久化登录态的恢复/写入队列。组件异步返回结果，不能直接在
    # 登录按钮所在的脚本运行中假定 Cookie 已经写入。
    "_auth_restore_checked": False,
    "_auth_persistence_pending": None,
}

# 退出登录时清理的 key（身份 + 选择 + 列表缓存）
_LOGOUT_KEYS = list(_DEFAULTS.keys()) + [
    "sessions", "knowledge_bases", "interfaces", "web_cases",
]

# clear_user_selections 清理的 key（保留登录态，仅清选择/编辑/列表缓存）
_SELECTION_KEYS = [
    "sessions", "messages", "knowledge_bases", "interfaces", "web_cases",
    "selected_session", "selected_kb", "selected_interface",
    "selected_web_case", "selected_app_device",
    "editing_interface", "editing_web_case",
    "web_run_results",
]


def init_state():
    """在 main() 开头调用，为缺失的核心 key 写入默认值。已存在不覆盖。"""
    for k, v in _DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---- 身份 ----
def token():
    return st.session_state.get("token")


def username():
    return st.session_state.get("username")


def user_id():
    return st.session_state.get("user_id")


def is_logged_in():
    return st.session_state.get("token") is not None


def is_admin():
    return st.session_state.get("username") == "admin"


def set_identity(access_token, name, uid, *, persist=True):
    """登录成功后写入身份，并排队写入浏览器持久化登录态。"""
    st.session_state["token"] = access_token
    st.session_state["username"] = name
    st.session_state["user_id"] = uid
    if persist:
        queue_persist_identity()


def queue_persist_identity():
    """请求浏览器组件保存当前登录态。"""
    import uuid

    st.session_state["_auth_persistence_pending"] = {
        "action": "write",
        "request_id": uuid.uuid4().hex,
        "identity": {
            "token": st.session_state.get("token"),
            "username": st.session_state.get("username"),
            "user_id": st.session_state.get("user_id"),
        },
    }


def queue_clear_persisted_identity():
    """请求浏览器组件清除持久化登录态。"""
    import uuid

    st.session_state["_auth_persistence_pending"] = {
        "action": "clear",
        "request_id": uuid.uuid4().hex,
        "identity": {},
    }


# ---- 路由 ----
def current_page():
    return st.session_state.get("page", "AI助手中心")


def set_page(name):
    st.session_state["page"] = name


# ---- 退出 / 清理 ----
def logout():
    """退出登录：清身份 + 选择 + 列表缓存。"""
    for k in _LOGOUT_KEYS:
        if k in st.session_state:
            del st.session_state[k]
    # 清理后再排队，确保刷新或 401 触发的退出也会删除浏览器 Cookie。
    queue_clear_persisted_identity()


def clear_user_selections():
    """清理用户选择与编辑状态；保留登录态。供 api.clear_user_cache 转发。"""
    for k in _SELECTION_KEYS:
        if k in st.session_state:
            del st.session_state[k]
