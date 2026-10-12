"""项目知识库页"""
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

def render_knowledge_base():
    st.markdown("""
    <h1 class="page-title"><span style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;background:#eff6ff;border-radius:8px;font-size:18px;">📚</span> 项目知识库</h1>
    <p class="page-subtitle">上传项目文档，构建团队共享的 AI 知识库</p>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([1, 1], gap="medium")

    with col1:
        render_divider()
        with st.container(border=True):
            st.markdown('<div class="card-title">📁 知识库管理</div>', unsafe_allow_html=True)
            with st.form("create_kb_form", clear_on_submit=True):
                kb_name = st.text_input("知识库名称", placeholder="请输入知识库名称")
                kb_description = st.text_area("描述", placeholder="简要描述知识库用途")
                if st.form_submit_button("创建知识库", use_container_width=True, type="primary"):
                    if kb_name:
                        r = call_api("POST", "/knowledge-base/", data={"name": kb_name, "description": kb_description})
                        if r and r.status_code == 200:
                            st.success("创建成功")
                            st.rerun()
            render_divider()

            if st.button("🔄 刷新列表", key="refresh_kb", use_container_width=True, type="secondary"):
                st.rerun()

            knowledge_bases = fetch_data("/knowledge-base/")
            if knowledge_bases:
                for kb in knowledge_bases:
                    is_selected = st.session_state.get("selected_kb") == kb["id"]
                    btn_type = "primary" if is_selected else "secondary"
                    if st.button(f"📚 {kb['name']}", key=f"kb_{kb['id']}", use_container_width=True, type=btn_type):
                        st.session_state.selected_kb = kb["id"]
                        st.rerun()
            else:
                st.markdown('<div class="session-empty">暂无知识库</div>', unsafe_allow_html=True)

    with col2:
        selected_kb = st.session_state.get("selected_kb")
        render_divider()
        with st.container(border=True):

            if selected_kb:
                kb_info = fetch_data(f"/knowledge-base/{selected_kb}")
                kb_name = kb_info.get("name", "知识库") if kb_info else "知识库"
                st.markdown(f'<div class="card-title">📄 {kb_name} · 文档管理</div>', unsafe_allow_html=True)

                uploaded_file = st.file_uploader("上传文档", type=["txt", "md", "markdown", "pdf", "docx", "json"], key="kb_upload")
                if uploaded_file is not None:
                    if st.button("📤 上传文档", key="upload_doc", use_container_width=True, type="primary"):
                        r = call_api("POST", f"/knowledge-base/{selected_kb}/documents",
                                     files={"file": (uploaded_file.name, uploaded_file.getvalue())})
                        if r and r.status_code == 200:
                            st.success(f"文档 {uploaded_file.name} 上传成功")
                            st.rerun()

                render_divider()

                documents = fetch_data(f"/knowledge-base/{selected_kb}/documents")
                if documents:
                    for doc in documents:
                        st.markdown(f"""
                        <div class="doc-item">
                            <div>
                                <div class="doc-name">{doc.get('filename', '未命名')}</div>
                                <div class="doc-type">类型: {doc.get('file_type', '-')} · {doc.get('created_at', '')[:10]}</div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                        d_col1, d_col2 = st.columns([1, 1])
                        with d_col1:
                            if st.button("⬇ 下载", key=f"dl_{doc['id']}", use_container_width=True, type="secondary"):
                                r = call_api("GET", f"/knowledge-base/{selected_kb}/documents/{doc['id']}/download")
                                if r and r.status_code == 200:
                                    st.download_button("保存文件", data=r.content, file_name=doc.get("filename", "doc"), key=f"save_{doc['id']}")
                        with d_col2:
                            if st.button("🗑 删除", key=f"del_{doc['id']}", use_container_width=True, type="secondary"):
                                r = call_api("DELETE", f"/knowledge-base/{selected_kb}/documents/{doc['id']}")
                                if r and r.status_code == 200:
                                    st.success("删除成功")
                                    st.rerun()
                else:
                    st.markdown('<div class="session-empty">暂无文档，请上传</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="card-title">📄 文档管理</div>', unsafe_allow_html=True)
                st.markdown('<div class="session-empty">请在左侧选择一个知识库<br/>或新建知识库后上传文档</div>', unsafe_allow_html=True)


# ============================================================
# 接口测试：仅素材管理 + 列表点击回填表单查看编辑
# ============================================================

