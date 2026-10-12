"""测试报告页"""
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

def render_report():
    st.markdown("""
    <h1 class="page-title"><span style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;background:#eff6ff;border-radius:8px;font-size:18px;">📊</span> 测试报告</h1>
    <p class="page-subtitle">查看 AI 对话触发执行产生的运行记录</p>
    """, unsafe_allow_html=True)
    st.markdown('<div class="banner">📌 本页面展示 AI 对话触发执行产生的运行记录</div>', unsafe_allow_html=True)

    reports = fetch_data("/reports/")
    if reports:
        stat_cols = st.columns(3)
        total = len(reports)
        success = sum(1 for r in reports if r.get("status") == "completed")
        with stat_cols[0]:
            st.markdown(f'<div class="stat-card"><div class="stat-label">报告总数</div><div class="stat-value">{total}</div></div>', unsafe_allow_html=True)
        with stat_cols[1]:
            st.markdown(f'<div class="stat-card"><div class="stat-label">已完成</div><div class="stat-value" style="color:#10b981;">{success}</div></div>', unsafe_allow_html=True)
        with stat_cols[2]:
            st.markdown(f'<div class="stat-card"><div class="stat-label">运行中</div><div class="stat-value" style="color:#f59e0b;">{total - success}</div></div>', unsafe_allow_html=True)

        render_divider()

        for report in reports:
            report_id = report.get("id")
            with st.expander(f"📄 {report.get('test_name') or report.get('title','未命名报告')}  —  {report.get('created_at', '')[:16]}"):
                st.markdown(f"<p><strong>状态：</strong> {report.get('status', '-')}</p>", unsafe_allow_html=True)
                st.markdown(f"<p><strong>类型：</strong> {report.get('test_type', '-')}</p>", unsafe_allow_html=True)
                st.markdown(f"<p><strong>通过/总数：</strong> {report.get('passed_cases',0)} / {report.get('total_cases',0)}</p>", unsafe_allow_html=True)
                if report.get("logs"):
                    try:
                        logs = json.loads(report["logs"]) if isinstance(report["logs"], str) else report["logs"]
                        st.json(logs)
                    except Exception:
                        st.text(report["logs"])

                render_divider()
                d_col1, d_col2, d_col3 = st.columns([3, 1, 3])
                with d_col2:
                    if st.button("🗑 删除结果", key=f"del_report_{report_id}", use_container_width=True,
                                 type="secondary", help="删除这条执行记录"):
                        r = call_api("DELETE", f"/reports/{report_id}")
                        if r is not None and r.status_code == 200:
                            # Web 自动化页若还挂着这条结果，一并清掉，避免展示已删除的记录
                            st.session_state.web_run_results = [
                                item for item in st.session_state.get("web_run_results") or []
                                if item.get("report_id") != report_id
                            ]
                            st.rerun()
                        elif r is not None:
                            st.error(f"删除失败（HTTP {r.status_code}）：{_api_detail(r)}")
    else:
        st.markdown('<div class="card"><div class="session-empty" style="padding:60px 0;">暂无测试报告<br/>在【AI 助手中心】执行用例后会自动生成报告</div></div>', unsafe_allow_html=True)


# ============================================================
# 用户管理
# ============================================================

