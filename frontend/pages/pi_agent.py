"""pi-Agent 自愈页"""
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

_PI_AGENT_POLL_INTERVAL = 2

_PI_AGENT_MAX_LOG_CHARS = 6000

_PI_AGENT_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")



def _clean_ansi(text: str) -> str:
    """pi-agent 的日志带 ANSI 颜色码，直接塞进 Streamlit 会显示成乱码。"""
    return _PI_AGENT_ANSI_RE.sub("", text or "")



def _start_pi_agent_task(case_id: str, start_url: str, storage_state: str):
    payload = {"case_id": case_id, "start_url": start_url}
    if storage_state.strip():
        payload["storage_state"] = storage_state.strip()
    resp = call_api("POST", "/pi-agent/tasks", json=payload)
    if resp is None:
        st.error("提交失败，请确认后端服务已启动")
        return None
    if resp.status_code == 503:
        st.error(f"本地 Runner 不可用：{_api_detail(resp)}\n\n{_RUNNER_HINT}")
        return None
    if resp.status_code != 200:
        st.error(f"提交失败（HTTP {resp.status_code}）：{_api_detail(resp)}")
        return None
    return resp.json().get("id")



def _render_pi_agent_active() -> bool:
    """渲染正在运行的自愈任务；返回 True 表示本次运行已被它接管。

    这里刻意不用「while 循环里 sleep 轮询」的阻塞写法：那样整段脚本卡在循环里，
    页面上的「终止」按钮永远点不动——而模型静默空转恰恰是最需要人工叫停的场景。
    改成「渲染一轮 → sleep → st.rerun()」，每 2 秒自动刷新，按钮点击才会被处理。
    """
    task_id = st.session_state.get("pi_agent_active")
    if not task_id:
        return False

    def _back_button(key: str):
        if st.button("↩ 返回", key=key, use_container_width=False):
            st.session_state.pi_agent_active = None
            st.rerun()

    poll = call_api("GET", f"/pi-agent/tasks/{task_id}")
    if poll is None:
        st.error("获取任务状态失败，请确认后端与 Runner 是否在线")
        _back_button("pi_agent_back_err")
        return True
    if poll.status_code != 200:
        st.error(f"查询任务失败（HTTP {poll.status_code}）：{_api_detail(poll)}")
        _back_button("pi_agent_back_http")
        return True

    task = poll.json()
    if task.get("status") in {"pending", "running"}:
        st.info("⏳ pi-agent 正在自愈…（模型会反复 run_test → inspect_dom → heal_selector）"
                f"　运行状态：{task.get('status')}")
        log_text = _clean_ansi(task.get("log") or "")
        st.code(log_text[-_PI_AGENT_MAX_LOG_CHARS:] if log_text else "（等待 pi-agent 输出日志…）", language="text")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 立即刷新", key="pi_agent_refresh", use_container_width=True):
                st.rerun()
        with col2:
            if st.button("⏹ 终止自愈", key="pi_agent_stop", use_container_width=True, type="secondary"):
                call_api("POST", f"/pi-agent/tasks/{task_id}/stop")
                st.rerun()
        time.sleep(_PI_AGENT_POLL_INTERVAL)
        st.rerun()
        return True

    # 终态：交给正常渲染路径展示，避免被下一次 rerun 覆盖掉
    st.session_state.pi_agent_active = None
    st.session_state.pi_agent_last = task
    st.rerun()
    return True



def _render_pi_agent_task(task: dict):
    """展示一次自愈任务的最终产物：退出码、修复后的测试文件、写入的记忆。"""
    status = task.get("status")
    if status == "completed":
        st.success(f"✅ 自愈完成，pi-agent 退出码 {task.get('exit_code')}")
    elif status in {"pending", "running"}:
        st.warning("⏱ 轮询超时，任务仍在运行，可稍后刷新本页查看")
    else:
        st.error(f"❌ 自愈未成功（{status}）：{task.get('error') or '未知原因'}")

    meta_cols = st.columns(3)
    meta_cols[0].metric("最终页面地址", task.get("final_url") or "-")
    meta_cols[1].metric("页面标题", task.get("page_title") or "-")
    meta_cols[2].metric("退出码", task.get("exit_code") if task.get("exit_code") is not None else "-")

    if task.get("fixed_test"):
        with st.expander("🛠 pi-agent 修复后的测试文件（tests/test_web_case.py）", expanded=True):
            st.code(task["fixed_test"], language="python")

    if task.get("memory"):
        with st.expander("🧠 本次写入的持久化记忆（下次任务会作为 context file 加载）", expanded=True):
            st.markdown(task["memory"])



def render_pi_agent():
    st.markdown("""
    <h1 class="page-title"><span style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;background:#eff6ff;border-radius:8px;font-size:18px;">🤖</span> pi-Agent 自愈</h1>
    <p class="page-subtitle">把 Web 用例交给 pi-agent：它自己跑测试、探查 DOM、修定位器，并把经验写进持久化记忆</p>
    """, unsafe_allow_html=True)
    st.markdown(
        '<div class="banner">📌 这不是"平台内置一个 LLM 调用"，而是把任务交给本机的 pi-agent 子进程：'
        '它通过<b>扩展系统</b>加载四个自定义工具（run_test / inspect_dom / heal_selector / remember），'
        '并用 <b>tool_result hook</b> 在测试失败时自动追加自愈提示。<br>'
        '前置条件：本机已装 Node 与 <code>pi_demos/selfheal/agents/node_modules</code>，且已配置 <code>ZHIPU_API_KEY</code>。'
        f'<br>首次使用请先在项目根目录启动本机 Runner：<code>{_RUNNER_START_COMMAND}</code></div>',
        unsafe_allow_html=True,
    )

    health = fetch_data("/pi-agent/runner/health")
    if not health or health.get("status") != "ok":
        st.warning(f"本机 Runner 未连接，无法提交自愈任务。{_RUNNER_HINT}")
        return

    # 有任务在跑就整页交给它接管：日志 + 终止按钮
    if _render_pi_agent_active():
        return

    last = st.session_state.get("pi_agent_last")
    if last:
        render_divider()
        with st.container(border=True):
            st.markdown('<div class="card-title">🧪 本次自愈结果</div>', unsafe_allow_html=True)
            _render_pi_agent_task(last)
            if st.button("🧹 清除本次结果", key="pi_agent_clear_last"):
                st.session_state.pi_agent_last = None
                st.rerun()

    cases = fetch_data("/pi-agent/cases")
    if not cases:
        st.info("还没有可自愈的 Web 用例。请先到【Web自动化】页面录制或新建一条用例。")
        return

    render_divider()
    with st.container(border=True):
        st.markdown('<div class="card-title">🎯 选择自愈目标</div>', unsafe_allow_html=True)

        options = {f'{item["name"]}（{item.get("step_count", 0)} 步）': item["id"] for item in cases}
        selected_label = st.selectbox("目标用例", list(options.keys()), key="pi_agent_case")
        start_url = st.text_input(
            "被测页面的真实地址", key="pi_agent_url",
            placeholder="https://example.com/login　（pi-agent 会先预取这个页面作为探查素材）",
        )
        storage_state = st.text_input(
            "登录态文件路径（可选）", key="pi_agent_state",
            placeholder=r"留空表示免登录；需要登录的站点可填写 Playwright storage_state.json 的路径",
        )
        submitted = st.button("🚀 开始自愈", key="pi_agent_submit", use_container_width=True, type="primary")

    if submitted:
        if not start_url.strip():
            st.error("请填写被测页面的真实地址")
        else:
            task_id = _start_pi_agent_task(options[selected_label], start_url.strip(), storage_state)
            if task_id:
                st.session_state.pi_agent_active = task_id
                st.session_state.pi_agent_last = None
                st.rerun()

    tasks = fetch_data("/pi-agent/tasks?limit=10")
    if tasks:
        render_divider()
        with st.container(border=True):
            st.markdown('<div class="card-title">🕘 最近的自愈任务</div>', unsafe_allow_html=True)
            st.dataframe(
                [{"任务": item.get("name", ""), "目标用例": item.get("case_name", ""),
                  "状态": item.get("status", ""), "退出码": item.get("exit_code"),
                  "页面标题": item.get("page_title", ""), "创建时间": item.get("created_at", "")}
                 for item in tasks],
                use_container_width=True, hide_index=True,
            )


# ============================================================
# 主入口
# ============================================================

