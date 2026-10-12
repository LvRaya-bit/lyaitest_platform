"""AI助手中心页"""
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

def render_ai_assistant():
    st.markdown("""
    <h1 class="page-title"><span style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;background:#eff6ff;border-radius:8px;font-size:18px;">🤖</span> AI 助手中心</h1>
    <p class="page-subtitle">与小测协作，快速完成测试资产维护与执行</p>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([22, 78], gap="medium")

    # ---- 左侧：会话管理（新建自定义名称 + 删除 + 绑定知识库） ----
    with col1:
        st.markdown('<div class="card"><div class="card-title">💬 会话管理</div>', unsafe_allow_html=True)

        with st.form("new_session_form", clear_on_submit=True):
            new_name = st.text_input("会话名称", placeholder="输入会话名称（留空则自动生成）")
            submitted = st.form_submit_button("✨ 新建对话", use_container_width=True, type="primary")
            if submitted:
                payload_name = new_name.strip() if new_name.strip() else "新对话"
                r = call_api("POST", "/sessions/", json={"name": payload_name})
                if r and r.status_code == 200:
                    st.session_state.selected_session = r.json()["session_id"]
                    st.session_state.messages = []
                    st.success(f"会话「{payload_name}」已创建")
                    st.rerun()
                elif r:
                    st.error(f"创建失败（{r.status_code}）：{r.text[:200]}")

        render_divider()

        # 预加载知识库列表，供会话绑定使用。注意：必须在主线程顺序调用——
        # fetch_data 内部用 @st.cache_data 并读取 st.session_state.token，
        # 子线程缺少 ScriptRunContext 会导致拿不到 token（401）且缓存失效。
        kb_list = fetch_data("/knowledge-base/")
        sessions = fetch_data("/sessions/")
        if sessions:
            for s in sessions:
                sid = s["session_id"]
                is_selected = st.session_state.get("selected_session") == sid
                s_col1, s_col2 = st.columns([4, 1])
                with s_col1:
                    btn_type = "primary" if is_selected else "secondary"
                    if st.button(f"💬 {s['name']}", key=f"session_{sid}", use_container_width=True, type=btn_type):
                        st.session_state.selected_session = sid
                        msgs = fetch_data(f"/sessions/{sid}/messages")
                        st.session_state.messages = msgs if msgs else []
                        st.rerun()
                with s_col2:
                    if st.button("🗑", key=f"del_session_{sid}", use_container_width=True, type="secondary", help="删除会话"):
                        r = call_api("DELETE", f"/sessions/{sid}")
                        if r and r.status_code == 200:
                            if st.session_state.get("selected_session") == sid:
                                st.session_state.selected_session = None
                                st.session_state.messages = []
                            st.success("已删除")
                            st.rerun()

                # 选中会话显示知识库绑定切换器
                if is_selected:
                    current_kb_id = s.get("knowledge_base_id")
                    kb_options = ["未绑定"] + [f"{kb['name']} ({kb['id']})" for kb in kb_list]
                    # 计算当前选中项索引
                    default_idx = 0
                    if current_kb_id:
                        for i, kb in enumerate(kb_list):
                            if kb["id"] == current_kb_id:
                                default_idx = i + 1
                                break
                    bind_kb = st.selectbox(
                        "📚 绑定知识库",
                        kb_options,
                        index=default_idx,
                        key=f"bind_kb_{sid}",
                        help="切换后点击保存即更新当前会话绑定的知识库"
                    )
                    if st.button("💾 保存知识库绑定", key=f"save_kb_{sid}", use_container_width=True, type="secondary"):
                        new_kb_id = None if bind_kb == "未绑定" else bind_kb.split("(")[-1].replace(")", "").strip()
                        r = call_api("PUT", f"/sessions/{sid}", json={"knowledge_base_id": new_kb_id})
                        if r and r.status_code == 200:
                            st.success("知识库绑定已更新")
                            st.rerun()
                        else:
                            st.error("绑定失败")
        else:
            st.markdown('<div class="session-empty">暂无历史会话<br/>上方输入名称新建对话</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ---- 右侧：AI 对话卡片 ----
    with col2:
        st.markdown("""
        <div class="card">
            <div class="card-title-row">
                <span class="card-title">
                    <span style="display:inline-block;width:8px;height:8px;background:#3b82f6;border-radius:50%;margin-right:8px;"></span>
                    🤖 小测 · AI 测试助手
                </span>
                <span class="online-badge"><span class="online-dot"></span>在线</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        selected_session = st.session_state.get("selected_session")

        # 机器人欢迎区 + 快捷指令
        st.markdown("""
        <div class="ai-welcome">
            <div class="ai-robot">🤖</div>
            <div class="ai-hello">你好，我是小测</div>
            <div class="ai-intro">查数据、跑测试、生成用例、记录结果，测试工作都可以直接交给我。</div>
        </div>
        """, unsafe_allow_html=True)

        # 消息列表
        if "messages" in st.session_state and st.session_state.messages:
            for msg in st.session_state.messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                st.markdown(f"""
                <div class="msg-row {role}">
                    <div class="msg-bubble {role}">{content}</div>
                </div>
                """, unsafe_allow_html=True)

        st.markdown('<div class="chat-tip">AI 可能会出错，请核对关键测试结果</div>', unsafe_allow_html=True)

        # 执行目标选择器：接口 / Web用例（知识库已在左侧会话管理中绑定）
        render_divider()
        with st.expander("🎯 选择执行目标（可选，用于直接执行指定素材）", expanded=False):
            interfaces = fetch_data("/api-test/interfaces")
            web_cases = fetch_data("/web-automation/cases")

            if_options = ["不选择"] + [f"{itf['name']} [{itf.get('method','GET')}] ({itf['id']})" for itf in interfaces]
            web_options = ["不选择"] + [f"{c['name']} ({c['id']})" for c in web_cases]

            sel_if = st.selectbox("🔌 执行接口", if_options, key="agent_sel_if")
            sel_web = st.selectbox("🌐 执行Web用例", web_options, key="agent_sel_web")

            # 直接执行按钮：每个按钮只传对应 ID，避免类型错乱
            def _extract_id(sel_str):
                if not sel_str or sel_str.startswith("不"):
                    return None
                return sel_str.split("(")[-1].replace(")", "").strip()

            exec_col1, exec_col2 = st.columns(2)
            with exec_col1:
                run_if_btn = st.button("▶ 执行接口测试", key="run_if_direct", use_container_width=True, type="primary",
                                       disabled=(sel_if == "不选择"))
            with exec_col2:
                run_web_btn = st.button("▶ 执行Web测试", key="run_web_direct", use_container_width=True, type="primary",
                                        disabled=(sel_web == "不选择"))

            # 直接执行接口测试
            if run_if_btn and sel_if != "不选择":
                if not selected_session:
                    st.warning("请先在左侧新建或选择一个会话")
                else:
                    if "messages" not in st.session_state:
                        st.session_state.messages = []
                    if_id = _extract_id(sel_if)
                    st.session_state.messages.append({"role": "user", "content": f"▶ 直接执行接口测试: {sel_if}"})
                    with st.spinner("正在执行接口测试..."):
                        r = call_api("POST", "/agent/", json={
                            "message": "执行选中的接口测试",
                            "session_id": selected_session,
                            "interface_id": if_id,
                            "web_case_id": None,
                        })
                        if r and r.status_code == 200:
                            response = r.json().get("reply", r.json().get("response", ""))
                            st.session_state.messages.append({"role": "assistant", "content": response})
                        else:
                            err_msg = "接口测试执行失败"
                            if r is not None:
                                try:
                                    err_msg = r.json().get("detail", err_msg)
                                except Exception:
                                    err_msg = r.text[:200]
                            st.session_state.messages.append({"role": "assistant", "content": f"⚠️ {err_msg}"})
                    st.rerun()

            # 直接执行 Web 测试
            if run_web_btn and sel_web != "不选择":
                if not selected_session:
                    st.warning("请先在左侧新建或选择一个会话")
                else:
                    if "messages" not in st.session_state:
                        st.session_state.messages = []
                    web_id = _extract_id(sel_web)
                    st.session_state.messages.append({"role": "user", "content": f"▶ 直接执行Web测试: {sel_web}"})
                    with st.spinner("正在执行Web测试..."):
                        r = call_api("POST", "/agent/", json={
                            "message": "执行选中的Web自动化测试",
                            "session_id": selected_session,
                            "interface_id": None,
                            "web_case_id": web_id,
                        })
                        if r and r.status_code == 200:
                            response = r.json().get("reply", r.json().get("response", ""))
                            st.session_state.messages.append({"role": "assistant", "content": response})
                        else:
                            err_msg = "Web测试执行失败"
                            if r is not None:
                                try:
                                    err_msg = r.json().get("detail", err_msg)
                                except Exception:
                                    err_msg = r.text[:200]
                            st.session_state.messages.append({"role": "assistant", "content": f"⚠️ {err_msg}"})
                    st.rerun()

        # 底部输入框 + 发送按钮（普通对话，不绑定接口/Web ID，由 agent 按意图自动判断）
        render_divider()
        input_col, btn_col = st.columns([4, 1])
        with input_col:
            user_input = st.text_input("消息", label_visibility="collapsed", placeholder="输入测试需求…", key="ai_input")
        with btn_col:
            send_clicked = st.button("发送 ➤", key="ai_send", use_container_width=True, type="primary")

        if send_clicked and user_input:
            if not selected_session:
                st.warning("请先在左侧新建或选择一个会话")
            else:
                if "messages" not in st.session_state:
                    st.session_state.messages = []
                st.session_state.messages.append({"role": "user", "content": user_input})

                with st.spinner("小测思考中..."):
                    # 普通对话不传 interface_id / web_case_id，由 agent 按自然语言意图自动判断
                    r = call_api("POST", "/agent/", json={
                        "message": user_input,
                        "session_id": selected_session,
                    })
                    if r and r.status_code == 200:
                        try:
                            response = r.json().get("reply", r.json().get("response", ""))
                        except Exception:
                            response = ""
                        st.session_state.messages.append({"role": "assistant", "content": response})
                    else:
                        err_msg = "AI 处理失败"
                        if r is not None:
                            try:
                                err_msg = r.json().get("detail", err_msg)
                            except Exception:
                                err_msg = r.text[:200]
                        st.session_state.messages.append({"role": "assistant", "content": f"⚠️ {err_msg}"})
                st.rerun()


# ============================================================
# 项目知识库：左侧知识库管理 + 右侧文档管理
# ============================================================

