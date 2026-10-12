"""APP自动化页"""
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

def render_app_automation():
    st.markdown("""
    <h1 class="page-title"><span style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;background:#eff6ff;border-radius:8px;font-size:18px;">📱</span> APP 自动化</h1>
    <p class="page-subtitle">Android + Appium 自动探索与 Pytest 执行，设备操作由本机 Runner 完成</p>
    """, unsafe_allow_html=True)
    st.markdown(
        '<div class="banner">📌 请先启动 Appium 服务和本地 Runner：<code>appium --port 4723</code>、'
        f'<code>{_RUNNER_START_COMMAND}</code></div>',
        unsafe_allow_html=True,
    )

    health_col, refresh_col = st.columns([5, 1])
    with health_col:
        health = call_api("GET", "/app-automation/runner/health")
        if health and health.status_code == 200:
            st.success("本地 Runner 已连接")
        else:
            st.warning(f"本地 Runner 未连接，配置仍可保存，但暂时不能执行设备操作。{_RUNNER_HINT}")
    with refresh_col:
        if st.button("刷新", key="refresh_runner", use_container_width=True, type="secondary"):
            st.rerun()

    left, right = st.columns([1, 1], gap="medium")
    with left:
        st.markdown('<div class="card"><div class="card-title">➕ 新增 Android 设备</div>', unsafe_allow_html=True)
        with st.form("app_device_form", clear_on_submit=True):
            d_name = st.text_input("设备名称", placeholder="例如：测试机-01")
            d_udid = st.text_input("设备 UDID", placeholder="adb devices 获取")
            d_package = st.text_input("App 包名", placeholder="com.example.app")
            d_activity = st.text_input("App Activity", placeholder="com.example.MainActivity")
            d_server = st.text_input("Appium Server", value="http://127.0.0.1:4723")
            d_phone = st.text_input("测试手机号（可选）")
            d_password_env = st.text_input("密码环境变量名", value="TEST_PASSWORD")
            save_device = st.form_submit_button("保存设备配置", use_container_width=True, type="primary")
        if save_device:
            r = call_api("POST", "/app-automation/devices", json={
                "name": d_name, "device_udid": d_udid, "app_package": d_package,
                "app_activity": d_activity, "server_url": d_server,
                "test_phone": d_phone, "test_password_env": d_password_env,
            })
            if r and r.status_code == 200:
                st.success("设备配置已保存")
                st.rerun()
            elif r:
                st.error(r.json().get("detail", "保存失败"))
        st.markdown('</div>', unsafe_allow_html=True)

    with right:
        st.markdown('<div class="card"><div class="card-title">📋 设备列表</div>', unsafe_allow_html=True)
        devices = fetch_data("/app-automation/devices")
        if devices:
            labels = {f"{d['name']} · {d['device_udid']}": d for d in devices}
            selected_label = st.selectbox("选择设备", list(labels.keys()), key="app_device_selector")
            selected = labels[selected_label]
            st.session_state.selected_app_device = selected["id"]
            status = selected.get("status", "unknown")
            st.markdown(f"状态：`{status}`　包名：`{selected.get('app_package', '-')}`")
            b1, b2 = st.columns(2)
            with b1:
                if st.button("🔎 环境检测", key="preflight_app", use_container_width=True, type="secondary"):
                    r = call_api("POST", f"/app-automation/devices/{selected['id']}/preflight")
                    if r and r.status_code == 200:
                        result = r.json()
                        (st.success if result.get("ready") else st.warning)("检测通过" if result.get("ready") else "检测未通过")
                        st.json(result)
                    elif r:
                        st.error(r.json().get("detail", "检测失败"))
            with b2:
                if st.button("🗑 删除", key="delete_app_device", use_container_width=True, type="secondary"):
                    r = call_api("DELETE", f"/app-automation/devices/{selected['id']}")
                    if r and r.status_code == 200:
                        st.success("已删除")
                        st.rerun()
        else:
            st.markdown('<div class="session-empty">暂无 Android 设备配置</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    render_divider()
    st.markdown('<div class="card"><div class="card-title">🧭 App 页面自动探索</div>', unsafe_allow_html=True)
    st.caption("开始探索前会自动识别登录页并使用 Runner 终端中的 TEST_PHONE 和密码环境变量登录；未配置账号时会保留登录页结果并提示原因。")
    devices = fetch_data("/app-automation/devices")
    if devices:
        device_options = {f"{d['name']} · {d['device_udid']}": d["id"] for d in devices}
        with st.form("app_explore_form"):
            e_label = st.selectbox("探索设备", list(device_options.keys()))
            e_depth = st.number_input("最大探索深度", min_value=1, max_value=8, value=3)
            e_actions = st.number_input("最大点击次数", min_value=1, max_value=1000, value=100, step=10)
            e_strategy = st.selectbox(
                "点击策略",
                ["auto", "native", "coordinate"],
                format_func=lambda x: {
                    "auto": "自动（推荐，兼顾速度和 MuMu 兼容性）",
                    "native": "原生控件（多显示模拟器优先）",
                    "coordinate": "坐标点击（标准单显示设备最快）",
                }[x],
            )
            start_explore = st.form_submit_button("▶ 开始探索", use_container_width=True, type="primary")
        if start_explore:
            with st.spinner("正在启动探索……"):
                r = call_api("POST", "/app-automation/explorations", json={
                    "device_id": device_options[e_label], "max_depth": e_depth, "max_actions": e_actions,
                    "click_strategy": e_strategy,
                })
            if r and r.status_code == 200:
                st.success(f"探索任务已启动：{r.json().get('id')}")
                st.rerun()
            elif r:
                st.error(r.json().get("detail", "探索启动失败"))
    else:
        st.info("请先新增 Android 设备配置")
    explorations = fetch_data("/app-automation/explorations")
    if explorations:
        for task in explorations[:10]:
            task_status = task.get("status", "-")
            status_icon = {
                "starting": "⏳", "running": "⏳", "completed": "✅",
                "interrupted": "⏹️", "failed": "❌",
            }.get(task_status, "❔")
            with st.expander(f"{status_icon} {task.get('id')} · {task.get('device_name', '-') } · {task_status}"):
                detail = fetch_data(f"/app-automation/explorations/{task['id']}")
                if detail:
                    detail_status = detail.get("status", task_status)
                    if detail_status in {"starting", "running"}:
                        st.info("⏳ 加载中……探索任务正在执行，请稍候。")
                    summary = detail.get("summary", {})
                    st.write(f"页面数：{summary.get('total_pages', '-')}，操作数：{summary.get('actions', '-')}")
                    login_info = summary.get("login", {}) if isinstance(summary, dict) else {}
                    if login_info:
                        st.write(f"登录：{login_info.get('message', login_info.get('status', '-'))}")
                    if detail.get("error"):
                        st.error(detail["error"])
                    action_col1, action_col2 = st.columns(2)
                    with action_col1:
                        if detail_status == "completed" and st.button("✨ 根据探索结果生成用例", key=f"gen_exp_{task['id']}", type="secondary", use_container_width=True):
                            with st.spinner("正在生成用例……"):
                                r = call_api("POST", f"/case-generation/exploration/{task['id']}")
                            if r and r.status_code == 200:
                                payload = r.json()
                                replaced = (payload.get("replaced") or {}).get("case_sets", 0)
                                hint = f"；已覆盖上一次生成的 {replaced} 个用例集" if replaced else ""
                                st.success(f"已生成 {payload.get('case_count', 0)} 条用例{hint}")
                                st.rerun()
                    with action_col2:
                        can_delete = detail_status not in {"starting", "running"}
                        if st.button("🗑 删除探索结果", key=f"delete_exp_{task['id']}", type="secondary", use_container_width=True, disabled=not can_delete, help="运行中的任务完成后才可删除"):
                            with st.spinner("正在删除探索结果……"):
                                r = call_api("DELETE", f"/app-automation/explorations/{task['id']}")
                            if r and r.status_code == 200:
                                result = r.json()
                                st.success(result.get("message", "探索结果已删除"))
                                if result.get("warning"):
                                    st.warning(result["warning"])
                                st.rerun()
                            elif r:
                                st.error(r.json().get("detail", "删除失败"))
    st.markdown('</div>', unsafe_allow_html=True)

    render_divider()
    st.markdown('<div class="card"><div class="card-title">▶ 执行 Appium + Pytest 用例</div>', unsafe_allow_html=True)
    st.caption("可直接执行现有 appium_auto_test 或后续生成的测试目录；Runner 会限制目录必须位于允许工作区内。")
    run_devices = fetch_data("/app-automation/devices")
    if run_devices:
        run_options = {f"{d['name']} · {d['device_udid']}": d["id"] for d in run_devices}
        with st.form("app_pytest_form"):
            run_label = st.selectbox("执行设备", list(run_options.keys()), key="run_app_device")
            test_dir = st.text_input("测试目录绝对路径", placeholder="D:\\lvyang\\AI测试平台优化\\appium_auto_test\\testcases")
            marker = st.text_input("Pytest 标记（可选）", placeholder="p0 or p1")
            module = st.text_input("测试文件（可选）", placeholder="test_login.py")
            start_run = st.form_submit_button("开始执行", use_container_width=True, type="primary")
        if start_run:
            r = call_api("POST", "/app-automation/test-runs", json={
                "device_id": run_options[run_label], "test_dir": test_dir,
                "marker": marker, "module": module,
            })
            if r and r.status_code == 200:
                st.success(f"执行任务已启动：{r.json().get('id')}")
                st.rerun()
            elif r:
                st.error(r.json().get("detail", "执行启动失败"))
        runs = fetch_data("/app-automation/test-runs")
        for run in (runs or [])[:10]:
            detail = fetch_data(f"/app-automation/test-runs/{run['id']}")
            detail = detail or run
            st.write(f"`{run['id']}`　状态：`{detail.get('status', '-')}`　报告：{detail.get('report_path') or '-'}")
            if detail.get("error"):
                st.error(detail["error"])
    else:
        st.info("请先新增 Android 设备配置")
    st.markdown('</div>', unsafe_allow_html=True)


# ============================================================
# 测试用例生成：功能文档、探索结果、接口文档三种入口
# ============================================================

