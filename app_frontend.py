import streamlit as st
import requests
import json
import os
import re
import time
import uuid

import pandas as pd

from frontend.lib.style import inject_global_css
from frontend.lib.components import render_skeleton, render_card, render_divider
from frontend.lib.api import (
    API_BASE,
    get_headers,
    fetch_data,
    call_api,
    clear_user_cache,
)
from frontend.lib import state
from frontend.lib import auth_persistence
from frontend.pages.ai_assistant import render_ai_assistant
from frontend.pages.knowledge_base import render_knowledge_base
from frontend.pages.api_test import render_api_test
from frontend.pages.web_automation import render_web_automation
from frontend.pages.app_automation import render_app_automation
from frontend.pages.case_generation import render_case_generation
from frontend.pages.pi_agent import render_pi_agent
from frontend.pages.report import render_report
from frontend.pages.user_management import render_user_management

st.set_page_config(page_title="LYAITEST AI测试平台", page_icon="🤖", layout="wide", initial_sidebar_state="expanded")


# ============================================================
# 顶部全局导航栏：加大内边距、放大功能面板
# ============================================================
def render_top_navbar():
    nav_items = [
        ("AI助手中心", "🤖"),
        ("项目知识库", "📚"),
        ("接口测试", "🔌"),
        ("Web自动化", "🌐"),
        ("APP自动化", "📱"),
        ("用例生成", "✨"),
        ("pi-Agent", "🤖"),
        ("测试报告", "📊"),
        ("用户管理", "👥"),
    ]
    # 「用户管理」仅管理员可见，与后端 /admin/* 的 require_admin 保持一致。
    if not state.is_admin():
        nav_items = [item for item in nav_items if item[0] != "用户管理"]
    current = state.current_page()

    st.markdown('<div class="topnav-wrap">', unsafe_allow_html=True)

    cols = st.columns([1.4] + [1] * len(nav_items) + [1])

    with cols[0]:
        st.markdown("""
        <div class="topnav-brand">
            <span class="logo-icon">🤖</span>
            <span>LYAITEST</span>
        </div>
        """, unsafe_allow_html=True)

    for i, (item, icon) in enumerate(nav_items):
        with cols[i + 1]:
            is_active = (current == item)
            btn_type = "primary" if is_active else "secondary"
            if st.button(f"{icon} {item}", key=f"nav_{item}", use_container_width=True, type=btn_type):
                state.set_page(item)
                st.rerun()

    with cols[-1]:
        if st.button("⏏ 退出", key="nav_logout", use_container_width=True, type="secondary"):
            state.logout()
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)


# ============================================================
# 侧边栏：仅展示用户信息（支持折叠后恢复展开）
# ============================================================
def render_sidebar():
    with st.sidebar:
        username = state.username() or "用户"
        user_id = state.user_id() or "-"
        st.markdown(f"""
        <div class="sidebar-user-card">
            <div class="sidebar-user-avatar">👤</div>
            <div>
                <div class="sidebar-user-name">{username}</div>
                <div class="sidebar-user-meta">ID: {user_id}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div class="sidebar-section-title">账户信息</div>', unsafe_allow_html=True)
        st.markdown(f"""
        <div style="padding: 0 16px; font-size: 13px; color: #64748b; line-height: 2;">
            <div>👤 用户名：{username}</div>
            <div>🆔 用户ID：{user_id}</div>
            <div>🟢 状态：在线</div>
        </div>
        """, unsafe_allow_html=True)


# ============================================================
# 登录页：左右分栏垂直居中 + 消除底部留白 + 自适应窗口高度
# ============================================================
def render_login():
    inject_global_css()

    st.markdown("""
    <style>
    [data-testid="stSidebar"] {display: none !important;}
    /* 登录页：彻底消除顶部白色缝隙 */
    [data-testid="stHeader"] {
        display: none !important;
        height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        background: transparent !important;
    }
    [data-testid="stToolbar"] {display: none !important;}
    [data-testid="stDecoration"] {display: none !important;}
    /* 登录页主容器：占满视口高度 */
    .block-container {
        padding-top: 0 !important;
        padding-bottom: 0 !important;
        padding-left: 0 !important;
        padding-right: 0 !important;
        max-width: 100% !important;
        margin-top: 0 !important;
    }
    [data-testid="stAppViewContainer"] > section,
    section[data-testid="stMain"] {
        padding: 0 !important;
        margin: 0 !important;
    }
    [data-testid="stMainBlockContainer"] {
        padding: 0 !important;
        margin: 0 !important;
    }
    [data-testid="stMain"] > div:first-child {
        padding-top: 0 !important;
        margin-top: 0 !important;
    }
    /* 左右分栏容器：占满视口高度并垂直居中 */
    [data-testid="stHorizontalBlock"] {
        min-height: 100vh !important;
        align-items: stretch !important;
        gap: 0 !important;
        margin-top: 0 !important;
        margin-bottom: 0 !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
        border: none !important;
    }
    [data-testid="stHorizontalBlock"] > div {
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
        margin-top: 0 !important;
        margin-bottom: 0 !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
        border: none !important;
        background: transparent !important;
    }
    [data-testid="stHorizontalBlock"] > div > div {
        gap: 0 !important;
        padding: 0 !important;
        margin: 0 !important;
        border: none !important;
        background: transparent !important;
    }
    [data-testid="stHorizontalBlock"] > div:first-child {
        min-height: 100vh !important;
        padding: 0 !important;
        margin: 0 !important;
        background: transparent !important;
    }
    /* 左侧品牌面板 */
    .login-left {
        min-height: 100vh;
        background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
        color: #ffffff;
        padding: 60px 56px;
        display: flex;
        flex-direction: column;
        justify-content: center;
    }
    .login-left .logo-circle {
        width: 64px; height: 64px;
        background: rgba(255,255,255,0.2);
        border-radius: 16px;
        display: flex; align-items: center; justify-content: center;
        font-size: 32px; margin-bottom: 24px;
    }
    .login-left h1 { font-size: 34px; font-weight: 700; margin: 0 0 12px; }
    .login-left .sub { font-size: 15px; opacity: 0.9; margin-bottom: 32px; line-height: 1.6; }
    .login-left .feature { display: flex; align-items: center; gap: 10px; font-size: 14px; opacity: 0.95; margin-bottom: 14px; }
    .login-left .feature .dot { width: 6px; height: 6px; background: #ffffff; border-radius: 50%; opacity: 0.7; }
    /* 右侧表单面板：标准分栏，无独立卡片容器 */
    .login-right-wrap {
        background: transparent;
        padding: 0;
        margin: 0;
    }
    .login-right-wrap .welcome { font-size: 24px; font-weight: 700; color: #1e293b; margin-bottom: 4px; }
    .login-right-wrap .tip { font-size: 14px; color: #64748b; margin-bottom: 28px; }
    /* 右列容器：浅蓝底色，占满视口高度 */
    [data-testid="stHorizontalBlock"] > div:nth-child(2) {
        background: #f8fafc;
        min-height: 100vh;
        padding: 0 !important;
    }
    /* 右列内容：内部统一内边距，作为整体垂直居中 */
    [data-testid="stHorizontalBlock"] > div:nth-child(2) > div {
        padding-left: 56px !important;
        padding-right: 56px !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
        max-width: 100% !important;
        background: transparent !important;
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
    }
    .login-footer { text-align: center; font-size: 12px; color: #94a3b8; margin-top: 24px; }
    /* Tabs 样式 */
    .stTabs [data-baseweb="tab-list"] { gap: 0; border-bottom: 1px solid #e2e8f0; background: transparent; }
    .stTabs [data-baseweb="tab"] {
        color: #64748b !important;
        padding: 10px 24px !important;
        font-size: 15px !important;
        font-weight: 500 !important;
        border-bottom: 2px solid transparent !important;
        background: transparent !important;
    }
    .stTabs [aria-selected="true"][data-baseweb="tab"] {
        color: #3b82f6 !important;
        border-bottom: 2px solid #3b82f6 !important;
        font-weight: 600 !important;
    }
    .stTabs [data-baseweb="tab-highlight"] { background-color: #3b82f6 !important; }
    .stTabs [data-baseweb="tab-border"] { border: none !important; }
    </style>
    """, unsafe_allow_html=True)

    col_left, col_right = st.columns([38, 62], gap="small")

    with col_left:
        st.markdown("""
        <div class="login-left">
            <div class="logo-circle">🤖</div>
            <h1>LYAITEST</h1>
            <div class="sub">AI 驱动的测试智能体平台<br/>让测试资产的创建、维护与执行更高效</div>
            <div class="feature"><span class="dot"></span> AI 对话式测试用例生成</div>
            <div class="feature"><span class="dot"></span> 项目知识库智能检索</div>
            <div class="feature"><span class="dot"></span> 接口与 Web 自动化素材管理</div>
            <div class="feature"><span class="dot"></span> 测试报告自动归档</div>
        </div>
        """, unsafe_allow_html=True)

    with col_right:
        st.markdown("""
        <div class="login-right-wrap">
            <div class="welcome">欢迎使用 👋</div>
            <div class="tip">登录或注册以开始使用 LYAITEST 平台</div>
        </div>
        """, unsafe_allow_html=True)

        tab_login, tab_register = st.tabs(["登录", "注册"])

        with tab_login:
            with st.form("login_form", clear_on_submit=False):
                username = st.text_input("用户名", placeholder="请输入用户名")
                password = st.text_input("密码", type="password", placeholder="请输入密码")
                submitted = st.form_submit_button("登 录", use_container_width=True, type="primary")
            if submitted:
                if not username or not password:
                    st.error("请输入用户名和密码")
                else:
                    try:
                        r = requests.post(f"{API_BASE}/auth/login", data={"username": username, "password": password})
                        if r.status_code == 200:
                            result = r.json()
                            clear_user_cache()
                            state.set_identity(result["access_token"], result["username"], result["user_id"])
                            st.success("登录成功")
                            st.rerun()
                        else:
                            st.error("用户名或密码错误")
                    except Exception as e:
                        st.error(f"登录失败: {str(e)}")

        with tab_register:
            with st.form("register_form", clear_on_submit=False):
                r_username = st.text_input("用户名", placeholder="请输入用户名")
                r_password = st.text_input("密码", type="password", placeholder="请输入密码")
                r_confirm = st.text_input("确认密码", type="password", placeholder="请再次输入密码")
                r_submitted = st.form_submit_button("注 册", use_container_width=True, type="primary")
            if r_submitted:
                if not r_username or not r_password:
                    st.error("请输入用户名和密码")
                elif r_password != r_confirm:
                    st.error("两次密码输入不一致")
                else:
                    try:
                        r = requests.post(f"{API_BASE}/auth/register", json={"username": r_username, "password": r_password})
                        if r.status_code == 200:
                            st.success("注册成功，请切换到登录 Tab 登录")
                        else:
                            try:
                                st.error(r.json().get("detail", "注册失败"))
                            except Exception:
                                st.error("注册失败")
                    except Exception as e:
                        st.error(f"注册失败: {str(e)}")

        st.markdown('<div class="login-footer">登录即表示你同意平台使用条款与隐私政策</div>', unsafe_allow_html=True)


# API 辅助函数已抽到 frontend/lib/api.py
# （API_BASE / get_headers / _cached_get / fetch_data / call_api / clear_user_cache）


def main():
    # 先注入全局样式，保证登录页和登录后的所有页面都使用居中悬浮加载效果。
    inject_global_css()
    state.init_state()

    # Streamlit 刷新后会重建 session_state，先从同源 Cookie 恢复登录态。
    # 组件首次加载尚未返回结果时暂不渲染登录页，避免出现闪一下登录页的误导。
    pending = st.session_state.get("_auth_persistence_pending")
    if pending is None and not st.session_state.get("_auth_restore_checked"):
        pending = {
            "action": "read",
            "request_id": uuid.uuid4().hex,
            "identity": {},
        }
        st.session_state["_auth_persistence_pending"] = pending

    if pending:
        result = auth_persistence.run(
            action=pending["action"],
            request_id=pending["request_id"],
            identity=pending.get("identity"),
        )
        matched = isinstance(result, dict) and result.get("request_id") == pending["request_id"]
        if pending["action"] == "read" and not matched:
            with st.spinner("⏳ 正在恢复登录状态……"):
                return
        if matched:
            action = pending["action"]
            identity = result.get("identity") or {}
            st.session_state["_auth_persistence_pending"] = None
            if action == "read":
                st.session_state["_auth_restore_checked"] = True
                token = identity.get("token")
                if token:
                    try:
                        check = requests.get(
                            f"{API_BASE}/auth/me",
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=5,
                        )
                    except requests.RequestException:
                        check = None
                    if check is not None and check.status_code == 200:
                        user = check.json()
                        state.set_identity(token, user["username"], user["id"], persist=False)
                    elif check is not None and check.status_code == 401:
                        state.queue_clear_persisted_identity()
            elif action == "clear":
                # 用户主动退出后不要立刻再次触发一次 Cookie 读取。
                st.session_state["_auth_restore_checked"] = True
            # write/clear 只需消费结果，不阻塞当前页面。

    if not state.is_logged_in():
        with st.spinner("⏳ 页面加载中……"):
            render_login()
        return

    render_top_navbar()
    render_sidebar()

    page = state.current_page()
    # 页面切换或刷新时统一显示加载状态，覆盖各后台页面的数据请求和组件渲染。
    # Streamlit 会在页面函数执行完毕后自动关闭 spinner。
    with st.spinner("⏳ 页面加载中……"):
        if page == "AI助手中心":
            render_ai_assistant()
        elif page == "项目知识库":
            render_knowledge_base()
        elif page == "接口测试":
            render_api_test()
        elif page == "Web自动化":
            render_web_automation()
        elif page == "APP自动化":
            render_app_automation()
        elif page == "用例生成":
            render_case_generation()
        elif page == "pi-Agent":
            render_pi_agent()
        elif page == "测试报告":
            render_report()
        elif page == "用户管理":
            render_user_management()


if __name__ == "__main__":
    main()
