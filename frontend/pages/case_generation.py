"""用例生成页（含导出按钮）"""
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
from frontend.pages.web_automation import _run_generated_cases_batch

def render_case_export_button(case_set: dict, export_type: str, label: str, suffix: str, mime: str):
    """使用当前登录态准备导出内容，再提供下载按钮，避免直接访问相对 API 地址。"""
    set_id = case_set["id"]
    state_key = f"_case_export_{set_id}_{export_type}"
    if st.button(f"准备{label}", key=f"prepare_export_{set_id}_{export_type}", use_container_width=True):
        endpoint = f"/case-generation/sets/{set_id}/export.{suffix}"
        response = call_api("GET", endpoint)
        if response and response.status_code == 200:
            st.session_state[state_key] = {
                "data": response.content,
                "filename": f"{case_set.get('name', '测试用例集')}_{set_id}.{suffix}",
            }
        elif response:
            try:
                st.error(response.json().get("detail", f"{label}失败"))
            except Exception:
                st.error(f"{label}失败（HTTP {response.status_code}）")
    cached = st.session_state.get(state_key)
    if cached:
        st.download_button(
            f"下载{label}", data=cached["data"], file_name=cached["filename"],
            mime=mime, key=f"download_export_{set_id}_{export_type}", use_container_width=True,
        )


# ============================================================
# AI 助手中心：左右等宽卡片
# - 支持自定义会话名称 + 删除会话
# - 支持选择知识库/接口/Web用例执行测试
# ============================================================

def render_case_generation():
    st.markdown("""
    <h1 class="page-title"><span style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;background:#eff6ff;border-radius:8px;font-size:18px;">✨</span> 测试用例生成</h1>
    <p class="page-subtitle">先生成可审核草稿，再筛选和维护可执行用例</p>
    """, unsafe_allow_html=True)

    tab_doc, tab_explore, tab_web, tab_api = st.tabs(["📄 APP功能文档", "🧭 App探索结果", "🌐 Web探索结果", "🔌 接口文档"])
    with tab_doc:
        st.markdown('<div class="card">上传 Markdown/TXT 功能文档，按模块生成正常、异常和稳定性用例。</div>', unsafe_allow_html=True)
        function_file = st.file_uploader("功能文档", type=["md", "markdown", "txt"], key="function_case_file")
        function_name = st.text_input("用例集名称", value="APP功能文档用例集", key="function_case_name")
        if st.button("生成 APP 用例", key="generate_function_cases", type="primary", disabled=function_file is None):
            r = call_api("POST", "/case-generation/function-document", files={
                "file": (function_file.name, function_file.getvalue(), "text/markdown")
            }, data={"name": function_name})
            if r and r.status_code == 200:
                st.success(f"生成完成，共 {r.json().get('case_count', 0)} 条")
                st.rerun()
            elif r:
                st.error(r.json().get("detail", "生成失败"))

    with tab_explore:
        explorations = fetch_data("/app-automation/explorations")
        completed = [x for x in explorations if x.get("status") == "completed"] if explorations else []
        if completed:
            options = {f"{x['id']} · {x.get('device_name', '-')}": x['id'] for x in completed}
            label = st.selectbox("选择已完成的探索任务", list(options.keys()), key="case_exploration_selector")
            exp_name = st.text_input("用例集名称", value="APP探索用例集", key="explore_case_name")
            if st.button("根据探索结果生成用例", key="generate_explore_cases", type="primary"):
                r = call_api("POST", f"/case-generation/exploration/{options[label]}", params={"name": exp_name})
                if r and r.status_code == 200:
                    result = r.json()
                    matrix = result.get("case_matrix", {})
                    replaced = (result.get("replaced") or {}).get("case_sets", 0)
                    hint = f"，已覆盖上一次生成的 {replaced} 个用例集" if replaced else ""
                    st.success(f"生成完成，共 {result.get('case_count', 0)} 条{hint}")
                    if matrix:
                        st.caption(
                            "优先级：" + " / ".join(
                                f"{key} {value} 条" for key, value in matrix.get("by_priority", {}).items()
                            )
                        )
                    st.rerun()
                elif r:
                    st.error(r.json().get("detail", "生成失败"))
        else:
            st.info("暂无已完成探索任务，请先到 APP 自动化页面执行探索")

    with tab_web:
        st.markdown('<div class="card">把「Web自动化」页的探索结果转成可直接执行的用例，同时写入用例生成模块统一管理。</div>', unsafe_allow_html=True)
        web_explorations = fetch_data("/web-automation/explorations")
        web_completed = [x for x in web_explorations if x.get("status") == "completed"] if web_explorations else []
        if web_completed:
            web_options = {
                f"{x['id']} · {x.get('start_url', '-')} · {str(x.get('created_at', ''))[:16]}": x['id']
                for x in web_completed
            }
            web_label = st.selectbox("选择已完成的 Web 探索任务", list(web_options.keys()),
                                     key="case_web_exploration_selector")
            web_name = st.text_input("用例集名称", value="Web探索用例集", key="web_case_name")
            if st.button("根据 Web 探索结果生成用例", key="generate_web_cases", type="primary"):
                r = call_api("POST", f"/case-generation/web-exploration/{web_options[web_label]}",
                             params={"name": web_name})
                if r is not None and r.status_code == 200:
                    result = r.json()
                    replaced = result.get("replaced") or {}
                    replaced_hint = ""
                    if replaced.get("web_cases") or replaced.get("case_sets"):
                        replaced_hint = (f"；已覆盖上一次生成的 {replaced.get('web_cases', 0)} 条用例"
                                         f"、{replaced.get('case_sets', 0)} 个用例集")
                    st.success(f"生成完成，共 {result.get('case_count', 0)} 条"
                               f"（其中 AI 增强 {result.get('ai_case_count', 0)} 条），"
                               f"已写入用例生成模块{replaced_hint}")
                    matrix = result.get("case_matrix", {})
                    if matrix:
                        st.caption("优先级：" + " / ".join(
                            f"{key} {value} 条" for key, value in matrix.get("by_priority", {}).items()))
                    st.rerun()
                elif r is not None:
                    st.error(_api_detail(r))
        else:
            st.info("暂无已完成的 Web 探索任务，请先到「Web自动化」页面执行网页自动探索")

    with tab_api:
        st.markdown('<div class="card">第一版支持 Markdown/TXT；生成结果包含正常、必填缺失、未登录、资源不存在等基础维度，并保留文档行号。</div>', unsafe_allow_html=True)
        api_file = st.file_uploader("接口文档", type=["md", "markdown", "txt"], key="api_case_file")
        api_name = st.text_input("用例集名称", value="接口文档测试用例集", key="api_case_name")
        if st.button("生成接口测试用例", key="generate_api_cases", type="primary", disabled=api_file is None):
            r = call_api("POST", "/case-generation/api-document", files={
                "file": (api_file.name, api_file.getvalue(), "text/markdown")
            }, data={"name": api_name})
            if r and r.status_code == 200:
                result = r.json()
                st.success(f"生成完成，共 {result.get('case_count', 0)} 条，解析接口 {result.get('inventory', {}).get('counts', {}).get('total', 0)} 个")
                st.rerun()
            elif r:
                st.error(r.json().get("detail", "生成失败"))

    st.markdown('<div class="card"><div class="card-title">📚 已生成用例集</div>', unsafe_allow_html=True)
    sets = fetch_data("/case-generation/sets")
    if sets:
        for item in sets[:20]:
            with st.expander(f"{item.get('name', '-') } · {item.get('source_type', '-')} · {item.get('case_count', 0)} 条"):
                st.write(f"来源：{item.get('source_ref', '-')}　状态：{item.get('status', 'draft')}")
                detail = fetch_data(f"/case-generation/sets/{item['id']}")
                if detail:
                    cases = detail.get("cases", [])
                    for case in cases[:10]:
                        st.markdown(f"**{case.get('priority', 'P1')} · {case.get('title', '')}**　`{case.get('test_type', '')}`")
                        st.caption(case.get("expected", ""))
                    if len(cases) > 10:
                        st.caption("上方仅展示前 10 条，完整数据请使用下方按钮下载。")

                    # 批量操作：多选用例后可一次执行或一次删除。
                    executable_count = sum(1 for case in cases
                                           if (case.get("metadata") or {}).get("web_case_id"))
                    if executable_count:
                        st.caption(f"共 {len(cases)} 条，其中 {executable_count} 条来自 Web 探索、可直接执行")
                    else:
                        st.caption(f"共 {len(cases)} 条；当前来源没有可执行步骤，批量执行会提示跳过")

                    case_options = {case["id"]: f"{case.get('priority', 'P1')} · {case.get('title', '')}"
                                    for case in cases}
                    with st.form(f"cg_batch_{item['id']}"):
                        selected = st.multiselect(
                            "选择用例（可多选）", options=list(case_options.keys()),
                            format_func=lambda cid: case_options.get(cid, cid),
                            key=f"cg_sel_{item['id']}",
                        )
                        batch_headless = st.checkbox("无头执行（不弹出浏览器窗口）", value=True,
                                                     key=f"cg_hl_{item['id']}")
                        btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 2])
                        do_run = btn_col1.form_submit_button("⚡ 批量执行", use_container_width=True, type="primary")
                        do_delete = btn_col2.form_submit_button("🗑 批量删除", use_container_width=True)

                    if do_run:
                        if not selected:
                            st.warning("请先勾选要执行的用例")
                        else:
                            _run_generated_cases_batch(selected, batch_headless)
                    if do_delete:
                        if not selected:
                            st.warning("请先勾选要删除的用例")
                        else:
                            r = call_api("POST", "/case-generation/cases/batch-delete",
                                         json={"case_ids": selected})
                            if r is not None and r.status_code == 200:
                                st.success(f"已删除 {r.json().get('deleted', 0)} 条用例"
                                           f"（含 Web 自动化副本 {r.json().get('deleted_web_cases', 0)} 条）")
                                st.rerun()
                            elif r is not None:
                                st.error(_api_detail(r))

                    source_type = item.get("source_type")
                    if source_type == "api_document":
                        manage_cols = st.columns([1, 1, 1, 1])
                    elif source_type == "web_exploration":
                        manage_cols = st.columns([1, 1, 1, 1, 1])
                    else:
                        manage_cols = st.columns([1, 1, 1])
                    with manage_cols[0]:
                        if st.button("🗑 删除用例集", key=f"del_set_{item['id']}",
                                     use_container_width=True,
                                     help="删除整个用例集及其全部用例，Web 探索来源的会同步清理 Web 自动化里的可执行用例"):
                            r = call_api("DELETE", f"/case-generation/sets/{item['id']}")
                            if r is not None and r.status_code == 200:
                                result = r.json()
                                st.success(f"{result.get('message', '用例集已删除')}"
                                           f"（用例 {result.get('deleted_cases', 0)} 条，"
                                           f"Web自动化副本 {result.get('deleted_web_cases', 0)} 条）")
                                st.rerun()
                            elif r is not None:
                                st.error(_api_detail(r))
                    with manage_cols[1]:
                        render_case_export_button(item, "csv", "CSV", "csv", "text/csv")
                    if source_type == "web_exploration":
                        with manage_cols[2]:
                            render_case_export_button(item, "markdown", "Markdown 9列", "md", "text/markdown")
                        with manage_cols[3]:
                            render_case_export_button(item, "pytest", "P0/P1 Pytest", "pytest.zip", "application/zip")
                    elif source_type == "api_document":
                        with manage_cols[2]:
                            render_case_export_button(item, "openapi", "OpenAPI JSON", "openapi.json", "application/json")
                        with manage_cols[3]:
                            render_case_export_button(item, "postman", "Postman JSON", "postman.json", "application/json")
    else:
        st.markdown('<div class="session-empty">暂无生成用例集</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


# ============================================================
# pi-Agent 自愈（独立页）
# ============================================================

