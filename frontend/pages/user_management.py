"""用户管理页"""
import streamlit as st
import requests
import json
import os
import re
import time

import pandas as pd

from frontend.lib.api import (
    API_BASE,
    get_headers,
    fetch_data,
    call_api,
    clear_user_cache,
    _api_detail,
)
from frontend.lib.components import (
    render_skeleton,
    render_card,
    render_divider,
)
from frontend.lib.constants import _RUNNER_START_COMMAND, _RUNNER_HINT

def render_user_management():
    st.markdown("""
    <h1 class="page-title"><span style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;background:#eff6ff;border-radius:8px;font-size:18px;">👥</span> 用户管理</h1>
    <p class="page-subtitle">查看所有注册用户及其会话信息</p>
    """, unsafe_allow_html=True)
    st.markdown('<div class="banner">📌 本页面展示所有注册用户及其会话信息</div>', unsafe_allow_html=True)

    users_resp = call_api("GET", "/admin/users")
    if users_resp is None:
        st.error("请求失败，请确认后端服务已启动")
        return
    if users_resp.status_code == 403:
        st.error("仅管理员可访问用户管理")
        return
    if users_resp.status_code != 200:
        st.error(f"获取用户列表失败（HTTP {users_resp.status_code}）")
        return
    users = users_resp.json()
    if users:
        st.markdown(f'<div class="stat-card"><div class="stat-label">总用户数</div><div class="stat-value">{len(users)}</div></div>', unsafe_allow_html=True)
        render_divider()

        for user in users:
            with st.expander(f"👤 {user.get('username', '')}  (ID: {user.get('id', '')})"):
                st.markdown(f"<p><strong>创建时间：</strong> {user.get('created_at', '-')}</p>", unsafe_allow_html=True)
                sessions = fetch_data(f"/admin/users/{user['id']}/sessions")
                if sessions:
                    st.markdown("<h4 style='margin-top:12px;color:#1e293b;'>会话列表：</h4>", unsafe_allow_html=True)
                    for session in sessions:
                        st.markdown(f"- 💬 {session.get('name', '')}  ({session.get('created_at', '')[:16]})")
                else:
                    st.markdown('<p style="color:#94a3b8;">暂无会话</p>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="card"><div class="session-empty" style="padding:60px 0;">暂无用户</div></div>', unsafe_allow_html=True)


# ============================================================
# Android APP 自动化：设备配置、Runner 检测、探索任务
# ============================================================

