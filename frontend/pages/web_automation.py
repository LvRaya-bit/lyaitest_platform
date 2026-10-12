"""Web自动化页（含探索/录制/执行/批量运行）"""
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
from frontend.lib.constants import _WEB_RUN_POLL_INTERVAL, _WEB_RUN_POLL_TIMEOUT, _WEB_RUN_MAX_LOG_CHARS


def _render_exploration_artifacts(task_id: str):
    """列出并下载探索产生的 JSON、截图、DOM 和 storage_state。"""
    response = call_api("GET", f"/web-automation/explorations/{task_id}/artifacts")
    if not response or response.status_code != 200:
        return
    artifacts = response.json() or []
    if not artifacts:
        st.caption("暂无可下载的探索产物")
        return
    artifact_names = [item.get("name") for item in artifacts if item.get("name")]
    selected = st.selectbox(
        "选择探索产物",
        artifact_names,
        key=f"explore_artifact_select_{task_id}",
        format_func=lambda name: next(
            (f"{name}（{item.get('size', 0)} bytes）" for item in artifacts if item.get("name") == name),
            name,
        ),
    )
    if st.button("准备下载产物", key=f"prepare_explore_artifact_{task_id}", use_container_width=True):
        item = next((value for value in artifacts if value.get("name") == selected), None)
        if item and item.get("download"):
            downloaded = call_api("GET", item["download"])
            if downloaded and downloaded.status_code == 200:
                suffix = os.path.splitext(selected)[1].lower()
                mime = {
                    ".json": "application/json", ".html": "text/html", ".png": "image/png",
                }.get(suffix, "application/octet-stream")
                st.session_state[f"explore_artifact_{task_id}"] = {
                    "data": downloaded.content, "name": selected, "mime": mime,
                }
            elif downloaded:
                st.error(f"下载失败（HTTP {downloaded.status_code}）")
    cached = st.session_state.get(f"explore_artifact_{task_id}")
    if cached and cached.get("name") == selected:
        st.download_button(
            "下载已准备的产物", data=cached["data"], file_name=cached["name"],
            mime=cached["mime"], key=f"download_explore_artifact_{task_id}", use_container_width=True,
        )


def _login_storage_state(login_job: dict) -> str:
    """从 Runner 返回的完成结果取真实 storage_state 路径。"""
    result = login_job.get("result") or {}
    return str(result.get("storage_state") or login_job.get("storage_state") or "").strip()


def _web_case_exploration_id(case: dict) -> str:
    """从探索生成用例的描述中提取来源探索任务 ID。

    历史数据没有单独的 exploration_id 字段，但生成用例会写入稳定的来源行，
    通过它分组可以兼容已有数据，也不会影响手工创建的用例。
    """
    description = str(case.get("description") or "")
    matched = re.search(r"^来源：Web 自动探索\s+([^\s（(]+)", description, flags=re.MULTILINE)
    return matched.group(1).strip() if matched else ""


def _group_web_cases(web_cases: list, explorations: list) -> list:
    """按探索任务把 Web 用例聚合成可折叠合集。"""
    task_map = {str(item.get("id")): item for item in (explorations or []) if item.get("id")}
    groups = {}
    for case in web_cases or []:
        exploration_id = _web_case_exploration_id(case)
        if exploration_id:
            task = task_map.get(exploration_id, {})
            group_key = f"exploration:{exploration_id}"
            if group_key not in groups:
                status = task.get("status", "-")
                status_icon = {"completed": "✅", "running": "⏳", "failed": "❌"}.get(status, "❔")
                groups[group_key] = {
                    "key": group_key,
                    "title": (
                        f"{status_icon} {exploration_id} · "
                        f"{task.get('start_url', 'Web 探索任务')} · {status}"
                    ),
                    "description": "同一次 Web 探索生成的用例",
                    "cases": [],
                }
            groups[group_key]["cases"].append(case)
        else:
            group_key = "manual"
            if group_key not in groups:
                groups[group_key] = {
                    "key": group_key,
                    "title": "📝 手工维护用例",
                    "description": "录制或手工新建的 Web 用例",
                    "cases": [],
                }
            groups[group_key]["cases"].append(case)
    return list(groups.values())


def render_web_automation():
    # pending-load：在表单 widget 渲染前设置值，避免 "widget already rendered" 错误
    if st.session_state.pop("_pending_load_web_case", None):
        detail = st.session_state.get("editing_web_case", {})
        st.session_state.wc_name = detail.get("name", "")
        st.session_state.wc_desc = detail.get("description", "")
        _set_web_steps_rows(detail.get("steps", []))

    st.markdown("""
    <h1 class="page-title"><span style="display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;background:#eff6ff;border-radius:8px;font-size:18px;">🌐</span> Web 自动化</h1>
    <p class="page-subtitle">录制真实操作生成用例，或手工维护用例后一键执行</p>
    """, unsafe_allow_html=True)
    st.markdown(
        '<div class="banner">📌 🎬 录制：本机弹出浏览器记录操作，关闭窗口即生成步骤；▶ 执行：由本机 Runner 用 Playwright 真实执行，可选<b>无头</b>（后台静默）或<b>有头</b>（弹出浏览器，可观察每一步操作）。'
        f'<br>首次使用请先在项目根目录启动本机 Runner：<code>{_RUNNER_START_COMMAND}</code></div>',
        unsafe_allow_html=True,
    )

    _render_web_recording()
    _render_web_exploration()

    col1, col2 = st.columns([1, 1], gap="medium")

    editing = st.session_state.get("editing_web_case")

    with col1:
        render_divider()
        with st.container(border=True):
            if editing:
                st.markdown(f'<div class="card-title">✏️ 编辑用例<span class="editing-tag">编辑中：{editing.get("name","")}</span></div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="card-title">➕ 新建用例</div>', unsafe_allow_html=True)

            wc_name = st.text_input("用例名称", key="wc_name", placeholder="请输入用例名称")
            wc_desc = st.text_area("用例描述", key="wc_desc", placeholder="简要描述用例场景")
            steps_list = _render_web_steps_editor()

            f_col1, f_col2, f_col3 = st.columns(3)
            with f_col1:
                save_new = st.button("💾 新建保存", key="wc_save_new", use_container_width=True, type="primary")
            with f_col2:
                update_btn = st.button("🔄 更新", key="wc_update", use_container_width=True, type="secondary", disabled=(not editing))
            with f_col3:
                cancel_btn = st.button("✖ 取消编辑", key="wc_cancel", use_container_width=True, type="secondary")

            if save_new:
                if wc_name:
                    r = call_api("POST", "/web-automation/cases", json={
                        "name": wc_name, "description": wc_desc, "steps": steps_list,
                    })
                    if r and r.status_code == 200:
                        st.success("保存成功")
                        _reset_web_case_form()
                        st.rerun()
                else:
                    st.error("请填写用例名称")

            if update_btn and editing:
                r = call_api("PUT", f"/web-automation/cases/{editing.get('id')}", json={
                    "name": wc_name, "description": wc_desc, "steps": steps_list,
                })
                if r and r.status_code == 200:
                    st.success("更新成功")
                    _reset_web_case_form()
                    st.rerun()

            if cancel_btn:
                _reset_web_case_form()
                st.rerun()

    with col2:
        render_divider()
        with st.container(border=True):
            st.markdown('<div class="card-title">📋 用例合集（按探索任务归组）</div>', unsafe_allow_html=True)
            # 执行模式必须在 ▶ 按钮之前渲染，否则同一次 rerun 里读不到用户的选择
            run_mode = st.radio(
                "执行模式",
                ["⚡ 无头（后台静默执行）", "👁 有头（弹出浏览器，可观察操作）"],
                index=0, horizontal=True, key="wc_run_mode",
                help="有头模式会在本机弹出浏览器窗口，需要桌面会话，适合观察每一步操作；无头模式不弹窗，适合批量执行。",
            )
            headless = run_mode.startswith("⚡")
            if st.button("🔄 刷新列表", key="refresh_web_cases", use_container_width=True, type="secondary"):
                st.rerun()
            web_cases = fetch_data("/web-automation/cases")
            explorations = fetch_data("/web-automation/explorations")
            case_groups = _group_web_cases(web_cases, explorations)
            if case_groups:
                for group in case_groups:
                    group_case_ids = {item["id"] for item in group["cases"]}
                    group_selected = st.session_state.get("selected_web_case") in group_case_ids
                    with st.expander(
                        f"{group['title']} · {len(group['cases'])} 条",
                        expanded=group_selected,
                    ):
                        st.caption(group["description"])
                        for case in group["cases"]:
                            is_selected = st.session_state.get("selected_web_case") == case["id"]
                            row_c1, row_c2, row_c3 = st.columns([4, 1, 1])
                            with row_c1:
                                if st.button(
                                    f"{'✅ ' if is_selected else '🌐 '}{case.get('name', '未命名用例')}",
                                    key=f"web_case_{case['id']}",
                                    use_container_width=True,
                                    type="primary" if is_selected else "secondary",
                                    help="选择此用例并载入左侧编辑器",
                                ):
                                    _load_web_case_to_form(case["id"])
                            with row_c2:
                                if st.button(
                                    "▶", key=f"run_wc_{case['id']}",
                                    use_container_width=True, type="secondary",
                                    help="执行用例",
                                ):
                                    # 不清空历史结果：新结果会追加在列表最前面
                                    _execute_web_case(case["id"], case.get("name", ""), headless)
                            with row_c3:
                                if st.button(
                                    "🗑", key=f"del_wc_{case['id']}",
                                    use_container_width=True, type="secondary",
                                    help="删除此用例",
                                ):
                                    r = call_api("DELETE", f"/web-automation/cases/{case['id']}")
                                    if r and r.status_code == 200:
                                        if st.session_state.get("selected_web_case") == case["id"]:
                                            _reset_web_case_form()
                                        st.success("已删除")
                                        st.rerun()
            else:
                st.markdown('<div class="session-empty">暂无用例<br/>请在左侧新建</div>', unsafe_allow_html=True)

    _render_web_run_results()



_WEB_STEP_COLUMNS = ["action", "element", "value", "expected"]

_WEB_STEP_ACTIONS = ["navigate", "click", "input", "assert", "wait", "screenshot"]

# 历史用例里写过 tap/fill/type 等别名，进编辑器前统一成规范动作名，否则下拉框显示不出来

_WEB_ACTION_ALIASES = {
    "open": "navigate", "goto": "navigate",
    "tap": "click",
    "fill": "input", "type": "input", "send_keys": "input",
    "verify": "assert", "check": "assert",
    "wait_for": "wait",
    "capture": "screenshot",
}



def _cell_str(value) -> str:
    """单元格取值转字符串；顺带把 pandas 的空值 NaN 收敛成空串。"""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text



def _normalize_web_action(action) -> str:
    value = _cell_str(action).lower()
    return _WEB_ACTION_ALIASES.get(value, value)



def _web_steps_to_records(steps) -> list:
    records = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        records.append({
            "action": _normalize_web_action(step.get("action")),
            "element": _cell_str(step.get("element")),
            "value": _cell_str(step.get("value")),
            "expected": _cell_str(step.get("expected")),
        })
    return records



def _set_web_steps_rows(steps):
    """把 steps 灌进步骤编辑器。

    编辑器用带版本号的 key，所以改数据必须同时换 key，
    否则 Streamlit 会继续沿用上一次的行数据。
    """
    st.session_state.wc_steps_rows = _web_steps_to_records(steps)
    st.session_state.wc_steps_version = st.session_state.get("wc_steps_version", 0) + 1



def _render_web_steps_editor() -> list:
    """渲染步骤列表编辑器，返回当前 steps 数组（整行空白视为残留行丢弃）。"""
    version = st.session_state.get("wc_steps_version", 0)
    frame = pd.DataFrame(st.session_state.get("wc_steps_rows") or [], columns=_WEB_STEP_COLUMNS)
    edited = st.data_editor(
        frame,
        key=f"wc_steps_editor_{version}",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "action": st.column_config.SelectboxColumn("动作", options=_WEB_STEP_ACTIONS, width="small"),
            "element": st.column_config.TextColumn("元素 / URL", width="medium"),
            "value": st.column_config.TextColumn("输入值", width="medium"),
            "expected": st.column_config.TextColumn("期望值", width="medium"),
        },
    )
    st.caption('定位器写法：CSS（#login-submit）｜ role=button[name="登录"] ｜ text=欢迎回来 ｜ testid=login-submit。'
               '断言时 element 可填 url / title / status，也可直接填元素定位器。')

    steps = []
    for row in edited.to_dict("records"):
        action = _normalize_web_action(row.get("action"))
        element = _cell_str(row.get("element"))
        value = _cell_str(row.get("value"))
        expected = _cell_str(row.get("expected"))
        if not (action or element or value or expected):
            continue
        steps.append({"action": action, "element": element, "value": value, "expected": expected})
    return steps



def _render_web_exploration():
    """网页自动探索入口：无头遍历同源页面并记录元素清单与截图。

    探索只读且限同源，产物留在本机 Runner 工作区，供第 2 步「根据探索结果生成用例」使用。
    需要登录的内网系统可先点「预登录」保存登录态，探索时自动复用。
    """
    render_divider()
    with st.container(border=True):
        st.markdown('<div class="card-title">🧭 网页自动探索</div>', unsafe_allow_html=True)
        st.markdown(
            '<p style="color:#64748b;font-size:13px;margin:0 0 10px 0;">'
            '在本机 Runner 里<b>无头</b>打开站点，自动遍历<b>同源</b>页面并点击可交互元素，'
            '记录每页截图与元素清单（含候选定位器）。默认<b>只读同源</b>：'
            '只点链接、按钮、页签等导航型控件，<b>不提交任何表单</b>，'
            '删除、支付、提交订单、退出等危险动作也不会点击。'
            '内网系统需要登录时，先点下方「预登录」保存登录态，再开始探索。</p>',
            unsafe_allow_html=True,
        )

        # ── 预登录区域 ──
        login_state = st.session_state.get("web_login_state")  # {"job_id":..., "status":...}
        login_url = st.text_input("预登录 URL", key="we_login_url",
                                  placeholder="与探索起始 URL 一致",
                                  help="有头浏览器打开此 URL，手动登录后保存登录态，探索时自动复用")

        if not login_state or login_state.get("status") not in {"login_waiting", "login_started"}:
            lc1, lc2 = st.columns([1, 3])
            with lc1:
                if st.button("🔐 预登录", key="we_pre_login", type="secondary",
                             use_container_width=True,
                             disabled=not (login_url or "").strip()):
                    with st.spinner("正在打开浏览器……"):
                        r = call_api("POST", "/web-automation/login",
                                     json={"start_url": login_url.strip()})
                    if r and r.status_code == 200:
                        st.session_state["web_login_state"] = {
                            "job_id": r.json().get("id"), "status": "login_waiting",
                        }
                        st.rerun()
                    elif r:
                        st.error(_api_detail(r))
        else:
            st.info("🔓 浏览器已打开，请手动完成登录。登录完成后点「保存登录态」。")
            sc1, sc2, sc3 = st.columns([1, 1, 2])
            with sc1:
                if st.button("💾 保存登录态", key="we_save_login", type="primary",
                             use_container_width=True):
                    with st.spinner("正在保存登录态……"):
                        r = call_api("POST", f"/web-automation/login/{login_state['job_id']}/save")
                    if r and r.status_code == 200:
                        time.sleep(1)
                        login_job = fetch_data(f"/web-automation/login/{login_state['job_id']}") or {}
                        storage_state = _login_storage_state(login_job)
                        st.session_state["web_login_state"] = {
                            "job_id": login_state["job_id"],
                            "status": "completed" if login_job.get("status") == "completed" and storage_state
                            else "login_waiting",
                            "storage_state": storage_state,
                        }
                        st.success("登录态已保存，探索时将自动复用。" if storage_state else "正在保存登录态，请稍候。")
                        st.rerun()
                    elif r:
                        st.error(_api_detail(r))
            with sc2:
                if st.button("取消", key="we_cancel_login", type="secondary",
                             use_container_width=True):
                    call_api("POST", f"/web-automation/login/{login_state['job_id']}/cancel")
                    st.session_state["web_login_state"] = None
                    st.rerun()

        if login_state and login_state.get("status") == "completed":
            st.caption("✅ 已保存登录态，下方开始探索时会自动携带")
        elif login_state and login_state.get("status") == "login_waiting":
            # 轮询 Runner 查登录状态（浏览器被关 / 登录已保存 / 出错）
            login_job = fetch_data(f"/web-automation/login/{login_state['job_id']}")
            if login_job and login_job.get("status") in {"completed", "cancelled", "failed"}:
                if login_job.get("status") == "completed":
                    storage_state = _login_storage_state(login_job)
                    if storage_state:
                        st.session_state["web_login_state"] = {
                            "job_id": login_state["job_id"], "status": "completed",
                            "storage_state": storage_state,
                        }
                    else:
                        st.session_state["web_login_state"] = None
                else:
                    st.session_state["web_login_state"] = None
                st.rerun()

        st.markdown("<hr style='margin:8px 0;border:none;border-top:1px solid #e2e8f0;'>",
                    unsafe_allow_html=True)

        with st.form("web_explore_form"):
            e_col1, e_col2, e_col3, e_col4, e_col5 = st.columns([3, 1, 1, 1, 1])
            with e_col1:
                explore_url = st.text_input("起始 URL", key="we_url", placeholder="https://example.com")
            with e_col2:
                explore_depth = st.number_input("最大深度", min_value=1, max_value=8, value=2, key="we_depth")
            with e_col3:
                explore_actions = st.number_input("动作上限", min_value=1, max_value=1000, value=100, key="we_actions",
                                                  step=10)
            with e_col4:
                explore_pages = st.number_input("页面上限", min_value=1, max_value=80, value=80, key="we_pages",
                                                step=5,
                                                help="标准探索最多 80 页；每页最多点击 25 个元素。")
            with e_col5:
                explore_mode = st.radio(
                    "浏览器模式", ["无头", "有头"], index=0, horizontal=True, key="we_browser_mode",
                    help="有头模式会弹出浏览器，便于观察探索过程；无头模式后台运行。",
                )
            allow_submit = st.checkbox(
                "允许提交表单（越过登录页；开启后不再是只读探索）",
                key="we_allow_submit",
                help="默认不点任何表单提交按钮。内网系统需要登录时开启，Runner 会真实点击登录等提交按钮；"
                     "删除、支付、提交订单等危险动作仍然不会点击。",
            )
            allowed_actions_text = st.text_input(
                "允许的表单提交白名单（逗号分隔）",
                value="登录,查询,搜索,筛选,校验",
                key="we_allowed_form_actions",
                help="只允许控件名称/文本命中这些词的提交按钮；删除、支付、导出等危险操作始终拦截。",
            )
            if allow_submit:
                st.warning("已开启表单提交：探索会真实点击登录/查询等提交按钮，可能产生写操作，请确认测试环境可接受。")
            start_explore = st.form_submit_button("▶ 开始探索", use_container_width=True, type="primary")

        if start_explore:
            if not (explore_url or "").strip():
                st.error("请先填写起始 URL")
            else:
                explore_body = {
                    "start_url": explore_url.strip(),
                    "max_depth": int(explore_depth),
                    "max_actions": int(explore_actions),
                    "max_pages": int(explore_pages),
                    "max_actions_per_page": 25,
                    "headless": explore_mode == "无头",
                    "allow_form_submit": bool(allow_submit),
                    "allowed_form_actions": [
                        item.strip() for item in re.split(r"[,，;；\n]+", allowed_actions_text or "")
                        if item.strip()
                    ],
                }
                ls = st.session_state.get("web_login_state")
                if ls and ls.get("status") == "completed" and ls.get("storage_state"):
                    explore_body["storage_state"] = ls["storage_state"]
                with st.spinner("正在启动探索……"):
                    r = call_api("POST", "/web-automation/explorations", json=explore_body)
                if r and r.status_code == 200:
                    st.success(f"探索任务已启动：{r.json().get('id')}")
                    st.rerun()
                elif r:
                    hint = f"\n\n{_RUNNER_HINT}" if r.status_code == 503 else ""
                    st.error(f"探索启动失败：{_api_detail(r)}{hint}")

        explorations = fetch_data("/web-automation/explorations")
        if explorations:
            for task in explorations[:10]:
                task_status = task.get("status", "-")
                status_icon = {
                    "starting": "⏳", "running": "⏳", "completed": "✅",
                    "stopping": "⏹️", "stopped": "⏹️", "interrupted": "⏹️", "failed": "❌",
                }.get(task_status, "❔")
                with st.expander(f"{status_icon} {task.get('id')} · {task.get('start_url', '-')} · {task_status}"):
                    detail = fetch_data(f"/web-automation/explorations/{task['id']}")
                    if detail:
                        detail_status = detail.get("status", task_status)
                        if detail_status in {"starting", "running", "stopping"}:
                            st.info("⏳ 加载中……探索任务正在执行，请稍候。")
                        summary = detail.get("summary") or {}
                        st.write(f"页面数：{summary.get('total_pages', '-')}，操作数：{summary.get('actions', '-')}，"
                                 f"耗时：{summary.get('elapsed_seconds', '-')}s")
                        if summary.get("console_errors") or summary.get("request_failures"):
                            st.warning(
                                f"发现控制台错误 {len(summary.get('console_errors') or [])} 条，"
                                f"网络失败 {len(summary.get('request_failures') or [])} 条，"
                                "可在探索产物中查看详情。"
                            )
                        artifacts = summary.get("artifacts") or {}
                        if artifacts:
                            st.caption("探索产物：" + "、".join(
                                str(value) for value in artifacts.values() if value
                            ))
                        if detail_status == "completed":
                            _render_exploration_artifacts(task["id"])
                        if summary:
                            browser_mode = "无头" if summary.get("headless", True) else "有头"
                            mode = "允许提交表单（非只读）" if summary.get("allow_form_submit") else "同源 + 只读"
                            mode = f"{mode} · {browser_mode}浏览器"
                            st.caption(f"探索模式：{mode}")
                        pages = summary.get("pages") or []
                        if pages:
                            titles = [p.get("title") or p.get("url", "") for p in pages[:8]]
                            st.caption("探索到的页面：" + "、".join(t for t in titles if t))
                            blocked_total = sum(
                                1 for p in pages for el in (p.get("elements") or []) if el.get("blocked")
                            )
                            if blocked_total:
                                st.caption(f"未点击 {blocked_total} 个元素（表单提交 / 危险动作 / 跨域）。")
                        if detail.get("error"):
                            st.error(detail["error"])

                        act_col1, act_col2 = st.columns(2)
                        with act_col1:
                            if detail_status in {"starting", "running"} and st.button(
                                "⏹ 停止探索", key=f"stop_wexp_{task['id']}",
                                type="secondary", use_container_width=True,
                            ):
                                with st.spinner("正在停止探索……"):
                                    stop_response = call_api(
                                        "POST", f"/web-automation/explorations/{task['id']}/stop"
                                    )
                                if stop_response and stop_response.status_code == 200:
                                    st.success("已请求停止探索，已生成的产物会保留。")
                                    time.sleep(0.5)
                                    st.rerun()
                                elif stop_response:
                                    st.error(_api_detail(stop_response))
                            if detail_status in {"completed", "stopped"} and st.button(
                                "✨ 根据探索结果生成用例", key=f"gen_wexp_{task['id']}",
                                type="secondary", use_container_width=True,
                            ):
                                with st.spinner("正在生成用例……"):
                                    r = call_api("POST", f"/case-generation/web-exploration/{task['id']}",
                                                 params={"name": f"Web探索用例-{task['id']}"})
                                if r and r.status_code == 200:
                                    payload = r.json()
                                    ai_count = payload.get("ai_case_count", 0)
                                    replaced = payload.get("replaced") or {}
                                    replaced_hint = ""
                                    if replaced.get("web_cases") or replaced.get("case_sets"):
                                        replaced_hint = (f"；已覆盖上一次生成的 {replaced.get('web_cases', 0)} 条用例")
                                    st.success(
                                        f"已生成 {payload.get('case_count', 0)} 条可执行用例"
                                        f"（其中 AI 增强 {ai_count} 条），可在右侧用例列表查看并执行"
                                        f"{replaced_hint}"
                                    )
                                    if not ai_count:
                                        st.info("本次未使用 AI 增强（未配置模型 Key 或模型未返回可用步骤），"
                                                "已生成规则用例，均可在右侧直接执行。")
                                    st.rerun()
                                elif r:
                                    st.error(_api_detail(r))
                        with act_col2:
                            can_delete = detail_status not in {"starting", "running"}
                            if st.button("🗑 删除探索结果", key=f"delete_wexp_{task['id']}", type="secondary",
                                         use_container_width=True, disabled=not can_delete,
                                         help="运行中的任务完成后才可删除"):
                                with st.spinner("正在删除探索结果……"):
                                    r = call_api("DELETE", f"/web-automation/explorations/{task['id']}")
                                if r and r.status_code == 200:
                                    result = r.json()
                                    st.success(result.get("message", "探索结果已删除"))
                                    if result.get("warning"):
                                        st.warning(result["warning"])
                                    st.rerun()
                                elif r:
                                    st.error(_api_detail(r))



def _render_web_recording():
    """屏幕录制入口：本机弹出浏览器录真实操作，结束后转成步骤回填编辑器。"""
    render_divider()
    with st.container(border=True):
        st.markdown('<div class="card-title">🎬 屏幕录制生成用例</div>', unsafe_allow_html=True)
        st.markdown(
            '<p style="color:#64748b;font-size:13px;margin:0 0 10px 0;">'
            '点击「开始录制」后会在<b>本机</b>弹出 Chromium 录制窗口，在其中正常操作即可；'
            '<b>关闭浏览器窗口就算结束录制</b>，随后自动转成步骤回填到左侧编辑器，确认无误再保存。</p>',
            unsafe_allow_html=True,
        )

        r_col1, r_col2 = st.columns([3, 1])
        with r_col1:
            record_url = st.text_input("起始 URL", key="wc_record_url", placeholder="https://example.com/login")
        with r_col2:
            render_divider()
            start_clicked = st.button("🎬 开始录制", key="wc_record_start", use_container_width=True, type="primary")

        if start_clicked:
            if not (record_url or "").strip():
                st.error("请先填写起始 URL")
            else:
                job = _start_web_recording(record_url.strip())
                if job:
                    st.session_state.web_record_job = job

        job = st.session_state.get("web_record_job")
        if job:
            _render_web_recording_status(job)



def _start_web_recording(url: str):
    response = call_api("POST", "/web-automation/recordings", json={"url": url})
    if response is None:
        st.error("提交录制失败，请确认后端服务已启动")
        return None
    if response.status_code != 200:
        hint = f"\n\n{_RUNNER_HINT}" if response.status_code == 503 else ""
        st.error(f"提交录制失败（HTTP {response.status_code}）：{_api_detail(response)}{hint}")
        return None
    return {
        "recording_id": response.json().get("recording_id"),
        "start_url": url,
        "status": "recording",
        "steps": [],
        "warnings": [],
        "error": None,
    }



def _refresh_web_recording(job: dict) -> dict:
    """向 Runner 拉一次录制进度，把结果合并进当前 job。"""
    response = call_api("GET", f"/web-automation/recordings/{job.get('recording_id')}")
    if response is None or response.status_code != 200:
        if response is None:
            detail, hint = "后端服务未响应", ""
        else:
            detail = _api_detail(response)
            hint = f"；{_RUNNER_HINT}" if response.status_code == 503 else ""
        return {**job, "status": "failed", "error": f"查询录制状态失败：{detail}{hint}"}

    data = response.json()
    runtime_status = data.get("status")
    if runtime_status in {"recording", "starting"}:
        return {**job, "status": "recording"}
    if runtime_status == "completed":
        return {**job, "status": "completed",
                "steps": data.get("steps") or [], "warnings": data.get("warnings") or []}
    return {**job, "status": "failed",
            "error": data.get("error") or f"录制没有正常结束（Runner 状态：{runtime_status}）"}



def _stop_web_recording(recording_id):
    """请求 Runner 强制终止录制。

    刻意不在这里提示失败：录制很可能已经正常结束（此时 Runner 回 404），
    调用方紧接着会回查真实状态，一切以 Runner 的结果为准。
    """
    call_api("POST", f"/web-automation/recordings/{recording_id}/stop")



def _render_web_recording_status(job: dict):
    status = job.get("status")

    if status == "recording":
        st.info(
            f"⏳ 录制中：{job.get('start_url', '')}"
            "　在浏览器窗口里操作，**操作完关闭浏览器窗口**，录制会自动结束并写出脚本。"
        )
        b_col1, b_col2 = st.columns(2)
        with b_col1:
            if st.button("🔄 刷新状态", key="wc_record_refresh", use_container_width=True, type="primary"):
                st.session_state.web_record_job = _refresh_web_recording(job)
                st.rerun()
        with b_col2:
            if st.button("⏹ 强制终止录制", key="wc_record_stop", use_container_width=True, type="secondary"):
                _stop_web_recording(job.get("recording_id"))
                # 不臆造失败结果：终止后回查 Runner 的真实状态，已经录到的内容仍然保留
                time.sleep(1.5)
                st.session_state.web_record_job = _refresh_web_recording(job)
                st.rerun()
        return

    if status == "completed":
        steps = job.get("steps") or []
        st.success(f"✅ 录制结束，识别出 {len(steps)} 个动作")
        warnings = job.get("warnings") or []
        if warnings:
            with st.expander(f"⚠️ {len(warnings)} 处动作暂不支持自动转换，需人工确认"):
                for item in warnings:
                    st.markdown(f"- {item}")
        if steps:
            st.dataframe(
                [{"序号": index + 1, "动作": step.get("action", ""), "元素 / URL": step.get("element", ""),
                  "输入值": step.get("value", ""), "期望值": step.get("expected", "")}
                 for index, step in enumerate(steps)],
                use_container_width=True, hide_index=True,
            )
            if st.button("📥 载入左侧编辑器", key="wc_record_load", use_container_width=True, type="primary"):
                _load_recorded_steps(job)
        else:
            st.warning("这次录制没有捕获到可转换的动作，请重新录制。")
        return

    st.error(f"❌ 录制未成功：{job.get('error') or '未知原因'}")
    if st.button("🎬 重新开始", key="wc_record_retry", use_container_width=True):
        st.session_state.web_record_job = None
        st.rerun()



def _load_recorded_steps(job: dict):
    """把录制结果回填到左侧编辑器，由用户确认后再保存成用例。

    这里刻意不自动入库：录制拿到的是操作流水账，顺手点的无关动作也在里面，
    必须过人眼这一步。
    """
    _set_web_steps_rows(job.get("steps") or [])
    if not (st.session_state.get("wc_name") or "").strip():
        st.session_state.wc_name = time.strftime("录制用例 %m-%d %H:%M")
    st.session_state.web_record_job = None
    st.success("已载入左侧编辑器，确认步骤无误后点击「💾 新建保存」")



def _execute_web_case(case_id: str, case_name: str, headless: bool = True):
    """提交真实执行并轮询 Runner 结果；结果写入 session_state 供页面展示。"""
    resp = call_api("POST", f"/web-automation/cases/{case_id}/run", json={"headless": headless})
    if resp is None:
        st.error("执行请求失败，请确认后端服务已启动")
        return
    if resp.status_code == 503:
        detail = ""
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            detail = resp.text
        st.error(f"本地 Runner 不可用：{detail}\n\n{_RUNNER_HINT}")
        return
    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            detail = resp.text
        st.error(f"提交执行失败（HTTP {resp.status_code}）：{detail}")
        return

    report_id = resp.json().get("report_id")
    mode_hint = "无头（后台执行）" if headless else "有头（浏览器已弹出，可观察操作）"
    status_box = st.status(f"⏳ 正在执行「{case_name}」…　执行模式：{mode_hint}", expanded=True)
    log_slot = status_box.empty()

    result = None
    deadline = time.time() + _WEB_RUN_POLL_TIMEOUT
    while time.time() < deadline:
        poll = call_api("GET", f"/web-automation/runs/{report_id}")
        if poll is not None and poll.status_code == 200:
            result = poll.json()
            if result.get("log"):
                log_slot.code(result["log"][-_WEB_RUN_MAX_LOG_CHARS:], language="text")
            if result.get("status") != "running":
                break
        time.sleep(_WEB_RUN_POLL_INTERVAL)

    if result is None:
        status_box.update(label="❌ 获取执行结果失败，请检查 Runner 与后端服务", state="error")
        return

    summary = f"通过 {result.get('passed_cases', 0)} / 失败 {result.get('failed_cases', 0)}"
    if result.get("status") == "completed":
        status_box.update(label=f"✅ 执行完成：{summary}", state="complete")
    elif result.get("status") == "running":
        status_box.update(label="⏱ 轮询超时，任务仍在运行，可稍后在【测试报告】查看结果", state="error")
    else:
        status_box.update(label=f"❌ 执行失败：{summary}", state="error")
    # 追加到执行历史而不是覆盖，方便前后几次结果对比
    st.session_state.web_run_results = [
        {**result, "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    ] + (st.session_state.get("web_run_results") or [])



def _render_web_run_results():
    """按时间倒序展示本次会话执行过的所有结果，新结果追加而不覆盖旧结果。"""
    results = st.session_state.get("web_run_results") or []
    if not results:
        return

    render_divider()
    st.markdown(f'<div class="card-title">🧾 执行结果（本次会话共 {len(results)} 条，最新在最前）</div>',
                unsafe_allow_html=True)
    for result in results:
        _render_one_web_run_result(result)



def _render_one_web_run_result(result: dict):
    """渲染单条执行结果卡片。"""
    report_id = result.get("report_id")
    render_divider()
    with st.container(border=True):
        status = result.get("status")
        icon = {"completed": "✅", "failed": "❌", "running": "⏳"}.get(status, "⏳")
        st.markdown(
            f'<div class="card-title">{icon} 执行结果：{result.get("test_name", "")}'
            f'（通过 {result.get("passed_cases", 0)} / 失败 {result.get("failed_cases", 0)}）'
            f'<span class="editing-tag">{result.get("finished_at", "")}</span></div>',
            unsafe_allow_html=True,
        )

        logs = result.get("logs") or []
        if logs:
            rows = []
            for item in logs:
                state = item.get("status")
                mark = {"passed": "✅", "failed": "❌", "skipped": "⏭"}.get(state, "⏭")
                prefix = f"{item.get('case_name')}｜" if item.get("case_name") else ""
                rows.append({
                    "用例/步骤": f"{prefix}步骤 {item.get('step_index', '')}",
                    "动作": item.get("action", ""),
                    "结果": mark,
                    "说明": item.get("message", ""),
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)
        elif status == "running":
            st.markdown('<p style="color:#94a3b8;">执行中，结果尚未生成。</p>', unsafe_allow_html=True)

        if result.get("log"):
            with st.expander("查看原始执行日志"):
                st.code(result["log"], language="text")

        if report_id:
            render_divider()
            d_col1, d_col2, d_col3 = st.columns([3, 1, 3])
            with d_col2:
                if st.button("🗑 删除结果", key=f"wc_run_result_delete_{report_id}",
                             use_container_width=True, type="secondary",
                             help="删除这条执行记录，测试报告页也会同步移除"):
                    r = call_api("DELETE", f"/reports/{report_id}")
                    if r is not None and r.status_code == 200:
                        st.session_state.web_run_results = [
                            item for item in st.session_state.get("web_run_results") or []
                            if item.get("report_id") != report_id
                        ]
                        st.rerun()
                    elif r is not None:
                        st.error(f"删除失败（HTTP {r.status_code}）：{_api_detail(r)}")



def _reset_web_case_form():
    st.session_state.editing_web_case = None
    st.session_state.selected_web_case = None
    for k in ["wc_name", "wc_desc"]:
        if k in st.session_state:
            del st.session_state[k]
    _set_web_steps_rows([])



def _load_web_case_to_form(case_id):
    detail = fetch_data(f"/web-automation/cases/{case_id}")
    if detail:
        st.session_state.editing_web_case = detail
        st.session_state.selected_web_case = case_id
        st.session_state.wc_name = detail.get("name", "")
        st.session_state.wc_desc = detail.get("description", "")
        _set_web_steps_rows(detail.get("steps", []))
        st.rerun()


# ============================================================
# 测试报告
# ============================================================

def _run_generated_cases_batch(case_ids: list, headless: bool) -> None:
    """提交「用例生成」模块的批量执行，并轮询展示真实结果。"""
    r = call_api("POST", "/case-generation/cases/batch-run",
                 json={"case_ids": case_ids, "headless": headless})
    if r is None:
        st.error("执行请求失败，请检查后端与 Runner 服务是否在线")
        return
    if r.status_code != 200:
        st.error(_api_detail(r))
        return

    payload = r.json()
    report_id = payload.get("report_id")
    skipped = payload.get("skipped") or []
    if skipped:
        st.warning(f"以下 {len(skipped)} 条用例没有可执行步骤，已跳过：" + "、".join(skipped[:5]))

    mode_hint = "无头（后台执行）" if headless else "有头（浏览器已弹出，可观察操作）"
    status_box = st.status(
        f"⏳ 正在执行 {payload.get('case_count', 0)} 条用例…　执行模式：{mode_hint}", expanded=True)
    log_slot = status_box.empty()

    result = None
    deadline = time.time() + _WEB_RUN_POLL_TIMEOUT
    while time.time() < deadline:
        poll = call_api("GET", f"/web-automation/runs/{report_id}")
        if poll is not None and poll.status_code == 200:
            result = poll.json()
            if result.get("log"):
                log_slot.code(result["log"][-_WEB_RUN_MAX_LOG_CHARS:], language="text")
            if result.get("status") != "running":
                break
        time.sleep(_WEB_RUN_POLL_INTERVAL)

    if result is None:
        status_box.update(label="❌ 获取执行结果失败，请检查 Runner 与后端服务", state="error")
        return

    summary = f"通过 {result.get('passed_cases', 0)} / 失败 {result.get('failed_cases', 0)}"
    if result.get("status") == "completed":
        status_box.update(label=f"✅ 执行完成：{summary}", state="complete")
    elif result.get("status") == "running":
        status_box.update(label="⏱ 轮询超时，任务仍在运行，可稍后在【测试报告】查看结果", state="error")
    else:
        status_box.update(label=f"❌ 执行失败：{summary}", state="error")

    _render_one_web_run_result({**result, "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")})



