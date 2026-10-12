"""接口测试页"""
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

def render_api_test():
    # pending-load：在表单 widget 渲染前设置值，避免 "widget already rendered" 错误
    if st.session_state.pop("_pending_load_interface", None):
        detail = st.session_state.get("editing_interface", {})
        st.session_state.if_name = detail.get("name", "")
        st.session_state.if_url = detail.get("url", "")
        st.session_state.if_method = detail.get("method", "GET")
        st.session_state.if_headers = json.dumps(detail.get("headers", {}), ensure_ascii=False, indent=2)
        st.session_state.if_params = json.dumps(detail.get("params", {}), ensure_ascii=False, indent=2)
        st.session_state.if_body = json.dumps(detail.get("body", {}), ensure_ascii=False, indent=2)

    st.markdown("""
    <h1 class="page-title"><span style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;background:#eff6ff;border-radius:8px;font-size:18px;">🔌</span> 接口测试</h1>
    <p class="page-subtitle">维护接口定义，支持单接口快速执行与结果查看</p>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([1, 1], gap="medium")

    editing = st.session_state.get("editing_interface")  # dict or None

    with col1:
        render_divider()
        with st.container(border=True):
            if editing:
                st.markdown(f'<div class="card-title">✏️ 编辑接口<span class="editing-tag">编辑中：{editing.get("name","")}</span></div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="card-title">➕ 新建接口</div>', unsafe_allow_html=True)

            with st.form("interface_form", clear_on_submit=False):
                if_name = st.text_input("接口名称", key="if_name", placeholder="请输入接口名称")
                if_url = st.text_input("请求 URL", key="if_url", placeholder="https://api.example.com/endpoint")
                if_method = st.selectbox("请求方法", ["GET", "POST", "PUT", "DELETE"], key="if_method")
                if_headers = st.text_area("Headers (JSON)", key="if_headers", height=80)
                if_params = st.text_area("Params (JSON)", key="if_params", height=80)
                if_body = st.text_area("Body (JSON)", key="if_body", height=80)

                f_col1, f_col2, f_col3, f_col4 = st.columns(4)
                with f_col1:
                    save_new = st.form_submit_button("💾 新建保存", use_container_width=True, type="primary")
                with f_col2:
                    update_btn = st.form_submit_button("🔄 更新", use_container_width=True, type="secondary", disabled=(not editing))
                with f_col3:
                    run_btn = st.form_submit_button("▶ 执行", use_container_width=True, type="secondary", disabled=(not editing))
                with f_col4:
                    cancel_btn = st.form_submit_button("✖ 取消编辑", use_container_width=True, type="secondary")

            if save_new:
                if if_name and if_url:
                    try:
                        headers_json = json.loads(if_headers) if if_headers else {}
                        params_json = json.loads(if_params) if if_params else {}
                        body_json = json.loads(if_body) if if_body else {}
                        r = call_api("POST", "/api-test/interfaces", json={
                            "name": if_name, "url": if_url, "method": if_method,
                            "headers": headers_json, "params": params_json, "body": body_json,
                        })
                        if r and r.status_code == 200:
                            st.success("保存成功")
                            _reset_interface_form()
                            st.rerun()
                    except json.JSONDecodeError:
                        st.error("JSON 格式错误，请检查 Headers / Params / Body")
                else:
                    st.error("请填写接口名称和 URL")

            if update_btn and editing:
                if_id_val = editing.get("id")
                try:
                    headers_json = json.loads(if_headers) if if_headers else {}
                    params_json = json.loads(if_params) if if_params else {}
                    body_json = json.loads(if_body) if if_body else {}
                    r = call_api("PUT", f"/api-test/interfaces/{if_id_val}", json={
                        "name": if_name, "url": if_url, "method": if_method,
                        "headers": headers_json, "params": params_json, "body": body_json,
                    })
                    if r and r.status_code == 200:
                        st.success("更新成功")
                        _reset_interface_form()
                        st.rerun()
                except json.JSONDecodeError:
                    st.error("JSON 格式错误，请检查 Headers / Params / Body")

            if run_btn and editing:
                if_id_val = editing.get("id")
                r = call_api("POST", f"/api-test/interfaces/{if_id_val}/run")
                if r and r.status_code == 200:
                    st.session_state[f"run_result_{if_id_val}"] = r.json()
                    st.rerun()
                else:
                    st.error(f"执行失败：{r.text if r else '请求失败'}")

            if cancel_btn:
                _reset_interface_form()
                st.rerun()


    with col2:
        render_divider()
        with st.container(border=True):
            st.markdown('<div class="card-title">📋 接口列表（点击查看编辑）</div>', unsafe_allow_html=True)
            if st.button("🔄 刷新列表", key="refresh_interfaces", use_container_width=True, type="secondary"):
                _cached_get.clear()  # 刷新时清缓存，确保拿到最新数据
                st.rerun()
            # 首次/慢加载时显示骨架占位，fetch 返回后替换为真实列表
            _list_ph = st.empty()
            with _list_ph.container():
                render_skeleton(4)
            interfaces = fetch_data("/api-test/interfaces")
            _list_ph.empty()
            if interfaces:
                for interface in interfaces:
                    is_selected = st.session_state.get("selected_interface") == interface["id"]
                    btn_type = "primary" if is_selected else "secondary"
                    label = f"{interface.get('method','GET')}  {interface['name']}"
                    row_c1, row_c2, row_c3 = st.columns([4, 1, 1])
                    with row_c1:
                        if st.button(label, key=f"interface_{interface['id']}", use_container_width=True, type=btn_type):
                            # 列表已含全字段，但 headers/params/body 为 JSON 字符串，本地解析避免二次请求
                            detail = dict(interface)
                            for fk in ("headers", "params", "body"):
                                raw = detail.get(fk) or "{}"
                                try:
                                    detail[fk] = json.loads(raw) if isinstance(raw, str) else raw
                                except (json.JSONDecodeError, TypeError):
                                    detail[fk] = {}
                            st.session_state.editing_interface = detail
                            st.session_state.selected_interface = interface["id"]
                            st.session_state._pending_load_interface = True
                            st.rerun()
                    with row_c2:
                        if st.button("▶", key=f"run_if_{interface['id']}", use_container_width=True, type="secondary", help="执行"):
                            r = call_api("POST", f"/api-test/interfaces/{interface['id']}/run")
                            if r and r.status_code == 200:
                                st.session_state[f"run_result_{interface['id']}"] = r.json()
                            else:
                                st.session_state[f"run_result_{interface['id']}"] = {"error": r.text if r else "请求失败"}
                            st.rerun()
                    with row_c3:
                        if st.button("🗑", key=f"del_if_{interface['id']}", use_container_width=True, type="secondary", help="删除"):
                            r = call_api("DELETE", f"/api-test/interfaces/{interface['id']}")
                            if r and r.status_code == 200:
                                if st.session_state.get("selected_interface") == interface["id"]:
                                    _reset_interface_form()
                                st.success("已删除")
                                st.rerun()

                    # 执行结果展示
                    run_result = st.session_state.get(f"run_result_{interface['id']}")
                    if run_result:
                        if "error" in run_result:
                            st.error(f"执行失败：{run_result['error']}")
                        else:
                            status_code = run_result.get("status_code", 0)
                            resp_time = run_result.get("response_time", 0)
                            # 状态码颜色：2xx 绿、4xx 黄、5xx 红
                            if status_code < 300:
                                color = "#16a34a"
                            elif status_code < 500:
                                color = "#ca8a04"
                            else:
                                color = "#dc2626"
                            st.markdown(f"""
                            <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin:4px 0 12px 0;">
                                <div style="display:flex;gap:16px;align-items:center;margin-bottom:8px;">
                                    <span style="font-weight:bold;color:{color};font-size:18px;">{status_code}</span>
                                    <span style="color:#64748b;font-size:13px;">⏱ {resp_time:.3f}s</span>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
                            # 响应头（可折叠）
                            with st.expander("响应头", expanded=False):
                                headers = run_result.get("headers", {})
                                st.json(headers)
                            # 响应体
                            st.markdown("**响应体：**")
                            body = run_result.get("body", "")
                            try:
                                body_json = json.loads(body) if isinstance(body, str) else body
                                st.json(body_json)
                            except (json.JSONDecodeError, TypeError):
                                st.code(body, language="text")
                            # 清除按钮
                            if st.button("清除结果", key=f"clear_run_{interface['id']}", use_container_width=True, type="secondary"):
                                del st.session_state[f"run_result_{interface['id']}"]
                                st.rerun()
            else:
                st.markdown('<div class="session-empty">暂无接口<br/>请在左侧新建</div>', unsafe_allow_html=True)



def _reset_interface_form():
    """清空接口表单的 session_state"""
    st.session_state.editing_interface = None
    st.session_state.selected_interface = None
    for k in ["if_name", "if_url", "if_method", "if_headers", "if_params", "if_body"]:
        if k in st.session_state:
            del st.session_state[k]



def _load_interface_to_form(if_id):
    """拉取接口详情并回填表单"""
    detail = fetch_data(f"/api-test/interfaces/{if_id}")
    if detail:
        st.session_state.editing_interface = detail
        st.session_state.selected_interface = if_id
        st.session_state.if_name = detail.get("name", "")
        st.session_state.if_url = detail.get("url", "")
        st.session_state.if_method = detail.get("method", "GET")
        st.session_state.if_headers = json.dumps(detail.get("headers", {}), ensure_ascii=False, indent=2)
        st.session_state.if_params = json.dumps(detail.get("params", {}), ensure_ascii=False, indent=2)
        st.session_state.if_body = json.dumps(detail.get("body", {}), ensure_ascii=False, indent=2)
        st.rerun()


# ============================================================
# Web 自动化：仅素材管理 + 列表点击回填表单查看编辑
# ============================================================

