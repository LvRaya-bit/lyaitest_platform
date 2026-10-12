from pathlib import Path

from fastapi import APIRouter, HTTPException, Body, Depends
from fastapi.responses import FileResponse
from app.database import get_connection
from app.dependencies.auth import get_current_user
from app.services.local_runner_client import (
    RunnerUnavailable,
    runner_cancel_web_login,
    runner_delete_job,
    runner_get_active_record,
    runner_get_job,
    runner_save_web_login,
    runner_start_record,
    runner_start_web_explore,
    runner_start_web_login,
    runner_stop_web_explore,
    runner_stop_record,
)
from app.services.web_run_service import (
    collect_web_run,
    create_running_report,
    submit_web_run,
)
import json
import uuid
from datetime import datetime

router = APIRouter(prefix="/api/v1/web-automation", tags=["Web自动化"])

@router.get("/cases")
def list_cases(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM web_cases WHERE user_id = ? ORDER BY created_at DESC", (current_user["id"],))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@router.post("/cases")
def create_case(data: dict = Body(...), current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    case_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO web_cases (id, user_id, name, description, steps, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        case_id,
        current_user["id"],
        data.get("name"),
        data.get("description"),
        json.dumps(data.get("steps", [])),
        now,
        now
    ))
    conn.commit()
    conn.close()
    return {"id": case_id, "message": "创建成功"}

@router.get("/cases/{case_id}")
def get_case(case_id: str, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM web_cases WHERE id = ? AND user_id = ?", (case_id, current_user["id"]))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="用例不存在")
    result = dict(row)
    result["steps"] = json.loads(result.get("steps", "[]"))
    return result

@router.put("/cases/{case_id}")
def update_case(case_id: str, data: dict = Body(...), current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    updates = []
    params = []
    if "name" in data:
        updates.append("name = ?")
        params.append(data["name"])
    if "description" in data:
        updates.append("description = ?")
        params.append(data["description"])
    if "steps" in data:
        updates.append("steps = ?")
        params.append(json.dumps(data["steps"]))
    if updates:
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(case_id)
        params.append(current_user["id"])
        cursor.execute(f"UPDATE web_cases SET {', '.join(updates)} WHERE id = ? AND user_id = ?", params)
        conn.commit()
    conn.close()
    return {"message": "更新成功"}

@router.delete("/cases/{case_id}")
def delete_case(case_id: str, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM web_cases WHERE id = ? AND user_id = ?", (case_id, current_user["id"]))
    conn.commit()
    conn.close()
    return {"message": "删除成功"}

@router.post("/cases/{case_id}/run")
def run_case(case_id: str, data: dict = Body(default=None),
             current_user: dict = Depends(get_current_user)):
    """提交真实执行：由本机 Runner 用 Playwright 打开浏览器逐步执行用例。

    请求体可选 {"headless": bool}：false 时弹出浏览器窗口方便观察操作，
    不传时默认无头。后端只做编排，立即返回 report_id（同时作为 Runner 的
    job_id），执行进度与结果通过 GET /runs/{report_id} 轮询获取。
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM web_cases WHERE id = ? AND user_id = ?", (case_id, current_user["id"]))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="用例不存在")

    case = dict(row)
    steps = json.loads(case.get("steps") or "[]")
    if not steps:
        raise HTTPException(status_code=400, detail="用例没有可执行的步骤")

    headless = bool((data or {}).get("headless", True))
    report_id = str(uuid.uuid4())[:8]
    try:
        submit_web_run(report_id, [{"name": case["name"], "steps": steps}], headless=headless)
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    create_running_report(current_user["id"], "web", case["name"], len(steps), report_id=report_id)

    return {
        "report_id": report_id,
        "runner_job_id": report_id,
        "test_name": case["name"],
        "total_cases": len(steps),
        "headless": headless,
        "status": "running",
    }

@router.get("/runs/{report_id}")
def get_run(report_id: str, current_user: dict = Depends(get_current_user)):
    """轮询执行结果；执行完成后把真实通过/失败数写回 reports 表。"""
    try:
        return collect_web_run(report_id, current_user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

@router.get("/recordings/active")
def get_active_recording(current_user: dict = Depends(get_current_user)):
    """查询本机 Runner 上是否还有未结束的录制，便于页面刷新后恢复状态。"""
    try:
        return runner_get_active_record()
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/recordings")
def start_recording(data: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """开始屏幕录制：本机 Runner 用 playwright codegen 打开浏览器录制真实操作。

    录制必须有头运行，只能在本机桌面会话里使用；关闭浏览器窗口即结束录制。
    """
    url = str(data.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="请填写起始 URL")

    recording_id = str(uuid.uuid4())[:8]
    try:
        runner_start_record({"job_id": recording_id, "url": url})
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"recording_id": recording_id, "status": "recording", "start_url": url}


@router.get("/recordings/{recording_id}")
def get_recording(recording_id: str, current_user: dict = Depends(get_current_user)):
    """轮询录制进度；完成后返回转换好的 steps 与需要人工确认的提示。"""
    try:
        job = runner_get_job(recording_id)
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    result = job.get("result") or {}
    return {
        "recording_id": recording_id,
        "status": job.get("status", "unknown"),
        "error": job.get("error"),
        "steps": result.get("steps") or [],
        "warnings": result.get("warnings") or [],
        "start_url": result.get("start_url"),
    }


@router.post("/recordings/{recording_id}/stop")
def stop_recording(recording_id: str, current_user: dict = Depends(get_current_user)):
    """强制终止录制。

    codegen 只在浏览器窗口被正常关闭时才写出脚本，强制终止会丢失本次录制内容，
    所以这个接口只用于浏览器窗口卡住时的兜底。
    """
    try:
        runner_stop_record(recording_id)
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"recording_id": recording_id, "message": "已请求停止录制"}


def _sync_web_exploration_task(item: dict) -> dict:
    """同步仍在运行中的探索任务，避免 Runner 停止后数据库永久停留在 running。"""
    if not item.get("runner_task_id") or item.get("status") not in {"starting", "running", "stopping"}:
        return item

    try:
        remote = runner_get_job(item["runner_task_id"])
        item["status"] = remote.get("status", item["status"])
        if remote.get("result") is not None:
            item["summary"] = json.dumps(remote["result"], ensure_ascii=False)
            item["result_path"] = (remote.get("result") or {}).get("output_dir")
        if remote.get("error"):
            item["error"] = remote["error"]
    except RunnerUnavailable as exc:
        item["status"] = "interrupted"
        item["error"] = item.get("error") or f"本地 Runner 不可用，探索任务已中断：{exc}"

    conn = get_connection()
    conn.execute(
        "UPDATE web_exploration_tasks SET status = ?, summary = ?, error = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (item["status"], item.get("summary", "{}"), item.get("error"), datetime.now().isoformat(),
         item["id"], item["user_id"]),
    )
    conn.commit()
    conn.close()
    return item


@router.get("/explorations")
def list_web_explorations(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM web_exploration_tasks WHERE user_id = ? ORDER BY created_at DESC",
        (current_user["id"],),
    ).fetchall()
    conn.close()
    return [_sync_web_exploration_task(dict(row)) for row in rows]


@router.post("/explorations")
def start_web_exploration(data: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """启动网页自动探索：本机 Runner 无头打开站点，同源、只读地点可交互元素。

    只做编排：立即返回任务 id（同时作为 Runner 的 job_id），进度通过
    GET /explorations/{id} 轮询。探索产物（截图、结果 JSON）留在 Runner 工作区。
    """
    start_url = str(data.get("start_url") or "").strip()
    if not start_url:
        raise HTTPException(status_code=400, detail="请填写起始 URL")
    if not start_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="起始 URL 必须以 http:// 或 https:// 开头")

    max_depth = max(1, min(int(data.get("max_depth", 2)), 8))
    max_actions = max(1, min(int(data.get("max_actions", 100)), 1000))
    max_pages = max(1, min(int(data.get("max_pages", 80)), 80))
    max_actions_per_page = max(1, min(int(data.get("max_actions_per_page", 25)), 25))
    # 默认只读；开启后允许 Runner 点击登录等表单提交按钮，用于越过登录页。
    allow_form_submit = bool(data.get("allow_form_submit", False))
    headless = bool(data.get("headless", True))
    storage_state = str(data.get("storage_state") or "").strip()
    task_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conn = get_connection()
    conn.execute(
        """INSERT INTO web_exploration_tasks
        (id, user_id, start_url, status, max_depth, max_actions, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (task_id, current_user["id"], start_url, "starting", max_depth, max_actions, now, now),
    )
    conn.commit()
    conn.close()

    try:
        runner_job = runner_start_web_explore({
            "job_id": task_id,
            "start_url": start_url,
            "max_depth": max_depth,
            "max_actions": max_actions,
            "max_pages": max_pages,
            "max_actions_per_page": max_actions_per_page,
            "allow_form_submit": allow_form_submit,
            "headless": headless,
            "allowed_form_actions": data.get("allowed_form_actions") or [],
            "storage_state": storage_state,
        })
    except RunnerUnavailable as exc:
        conn = get_connection()
        conn.execute(
            "UPDATE web_exploration_tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            ("failed", str(exc), datetime.now().isoformat(), task_id),
        )
        conn.commit()
        conn.close()
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    conn = get_connection()
    conn.execute(
        "UPDATE web_exploration_tasks SET status = ?, runner_task_id = ?, updated_at = ? WHERE id = ?",
        (runner_job.get("status", "running"), runner_job.get("job_id"), datetime.now().isoformat(), task_id),
    )
    conn.commit()
    conn.close()
    return {"id": task_id, "runner_job_id": runner_job.get("job_id"), "status": runner_job.get("status", "running")}


def _exploration_output_dir(task: dict) -> Path:
    """从 Runner 返回的结果中取产物目录，并拒绝目录穿越。"""
    summary = task.get("summary")
    if isinstance(summary, str):
        try:
            summary = json.loads(summary or "{}")
        except (TypeError, ValueError):
            summary = {}
    output_dir = Path(str((summary or {}).get("output_dir") or "")).expanduser().resolve()
    runner_id = str(task.get("runner_task_id") or "").strip()
    if not runner_id or not output_dir.is_dir() or runner_id not in output_dir.parts:
        raise HTTPException(status_code=404, detail="探索产物目录不存在")
    return output_dir


@router.get("/explorations/{task_id}/artifacts")
def list_web_exploration_artifacts(task_id: str, current_user: dict = Depends(get_current_user)):
    """列出探索产物；文件仍保留在本机 Runner，平台只提供受保护的下载入口。"""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM web_exploration_tasks WHERE id = ? AND user_id = ?",
        (task_id, current_user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="探索任务不存在")
    output_dir = _exploration_output_dir(dict(row))
    result = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        result.append({"name": path.name, "size": path.stat().st_size,
                       "download": f"/api/v1/web-automation/explorations/{task_id}/artifacts/{path.name}"})
    return result


@router.get("/explorations/{task_id}/artifacts/{artifact_name}")
def download_web_exploration_artifact(task_id: str, artifact_name: str,
                                      current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM web_exploration_tasks WHERE id = ? AND user_id = ?",
        (task_id, current_user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="探索任务不存在")
    if Path(artifact_name).name != artifact_name or artifact_name in {".", ".."}:
        raise HTTPException(status_code=400, detail="非法产物名称")
    output_dir = _exploration_output_dir(dict(row))
    artifact = (output_dir / artifact_name).resolve()
    if output_dir not in artifact.parents or not artifact.is_file():
        raise HTTPException(status_code=404, detail="探索产物不存在")
    return FileResponse(str(artifact), filename=artifact.name)


@router.post("/login")
def start_web_login(data: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """启动预登录：Runner 有头浏览器打开站点，用户手动登录后保存 storageState。

    返回 job_id，前端轮询 GET /login/{job_id} 查状态（login_waiting → completed/cancelled）。
    保存后 storageState 文件留在 Runner 工作区，探索时传 storage_state 参数复用。
    """
    start_url = str(data.get("start_url") or "").strip()
    if not start_url or not start_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="请填写有效的 http(s) 起始 URL")
    task_id = str(uuid.uuid4())[:8]
    try:
        runner_job = runner_start_web_login({"job_id": task_id, "start_url": start_url})
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"id": task_id, "status": runner_job.get("status", "login_waiting")}


@router.get("/login/{job_id}")
def get_web_login(job_id: str, current_user: dict = Depends(get_current_user)):
    """查预登录状态：直接问 Runner 的 JOBS dict。"""
    try:
        remote = runner_get_job(job_id)
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return remote


@router.post("/login/{job_id}/save")
def save_web_login(job_id: str, current_user: dict = Depends(get_current_user)):
    """保存预登录态：Runner 把 cookie + localStorage 写入 storage_state.json。"""
    try:
        result = runner_save_web_login(job_id)
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result


@router.post("/login/{job_id}/cancel")
def cancel_web_login(job_id: str, current_user: dict = Depends(get_current_user)):
    """取消预登录：直接关闭浏览器，不保存任何状态。"""
    try:
        result = runner_cancel_web_login(job_id)
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result


@router.get("/explorations/{task_id}")
def get_web_exploration(task_id: str, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM web_exploration_tasks WHERE id = ? AND user_id = ?",
        (task_id, current_user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="探索任务不存在")
    item = _sync_web_exploration_task(dict(row))
    try:
        item["summary"] = json.loads(item.get("summary") or "{}")
    except (TypeError, ValueError):
        item["summary"] = {}
    return item


@router.post("/explorations/{task_id}/stop")
def stop_web_exploration(task_id: str, current_user: dict = Depends(get_current_user)):
    """请求停止探索，保留 Runner 已生成的中间结果和截图。"""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM web_exploration_tasks WHERE id = ? AND user_id = ?",
        (task_id, current_user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="探索任务不存在")
    task = dict(row)
    if task.get("status") not in {"starting", "running", "stopping"}:
        return {"id": task_id, "status": task.get("status"), "message": "探索任务已结束"}
    if not task.get("runner_task_id"):
        raise HTTPException(status_code=409, detail="Runner 任务尚未创建")
    try:
        result = runner_stop_web_explore(task["runner_task_id"])
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    conn = get_connection()
    conn.execute(
        "UPDATE web_exploration_tasks SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (result.get("status", "stopping"), datetime.now().isoformat(), task_id, current_user["id"]),
    )
    conn.commit()
    conn.close()
    return {"id": task_id, "status": result.get("status", "stopping"), "message": "已请求停止探索"}


@router.delete("/explorations/{task_id}")
def delete_web_exploration(task_id: str, current_user: dict = Depends(get_current_user)):
    """删除探索记录，并清理本机 Runner 上的截图与结果 JSON。"""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM web_exploration_tasks WHERE id = ? AND user_id = ?",
        (task_id, current_user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="探索任务不存在")

    task = dict(row)
    cleanup_warning = None
    runner_task_id = task.get("runner_task_id")
    if runner_task_id:
        remote_available = False
        try:
            remote = runner_get_job(runner_task_id)
            remote_available = True
            if remote.get("status") in {"starting", "running"}:
                raise HTTPException(status_code=409, detail="探索任务仍在 Runner 中运行，请完成后再删除")
        except RunnerUnavailable as exc:
            cleanup_warning = str(exc)
        if remote_available:
            try:
                runner_delete_job(runner_task_id)
            except RunnerUnavailable as exc:
                cleanup_warning = str(exc)
        else:
            # Runner 可能只是重启过、内存中没有旧任务，但仍保留了结果目录；尝试直接清理。
            try:
                runner_delete_job(runner_task_id)
                cleanup_warning = None
            except RunnerUnavailable:
                pass

    conn = get_connection()
    conn.execute("DELETE FROM web_exploration_tasks WHERE id = ? AND user_id = ?", (task_id, current_user["id"]))
    conn.commit()
    conn.close()
    result = {"id": task_id, "message": "探索结果已删除"}
    if cleanup_warning:
        result["warning"] = f"数据库记录已删除，但 Runner 结果目录未确认清理：{cleanup_warning}"
    return result


@router.get("/suites")
def list_suites(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM web_suites WHERE user_id = ? ORDER BY created_at DESC", (current_user["id"],))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@router.post("/suites")
def create_suite(data: dict = Body(...), current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    suite_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO web_suites (id, user_id, name, description, case_ids, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        suite_id,
        current_user["id"],
        data.get("name"),
        data.get("description"),
        json.dumps(data.get("case_ids", [])),
        now,
        now
    ))
    conn.commit()
    conn.close()
    return {"id": suite_id, "message": "创建成功"}

@router.get("/suites/{suite_id}")
def get_suite(suite_id: str, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM web_suites WHERE id = ? AND user_id = ?", (suite_id, current_user["id"]))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="套件不存在")
    result = dict(row)
    result["case_ids"] = json.loads(result.get("case_ids", "[]"))
    return result

@router.put("/suites/{suite_id}")
def update_suite(suite_id: str, data: dict = Body(...), current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    updates = []
    params = []
    if "name" in data:
        updates.append("name = ?")
        params.append(data["name"])
    if "description" in data:
        updates.append("description = ?")
        params.append(data["description"])
    if "case_ids" in data:
        updates.append("case_ids = ?")
        params.append(json.dumps(data["case_ids"]))
    if updates:
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(suite_id)
        params.append(current_user["id"])
        cursor.execute(f"UPDATE web_suites SET {', '.join(updates)} WHERE id = ? AND user_id = ?", params)
        conn.commit()
    conn.close()
    return {"message": "更新成功"}

@router.delete("/suites/{suite_id}")
def delete_suite(suite_id: str, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM web_suites WHERE id = ? AND user_id = ?", (suite_id, current_user["id"]))
    conn.commit()
    conn.close()
    return {"message": "删除成功"}

@router.post("/suites/{suite_id}/run")
def run_suite(suite_id: str, current_user: dict = Depends(get_current_user)):
    """把套件内所有用例合并成一个真实执行任务提交给本机 Runner。"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM web_suites WHERE id = ? AND user_id = ?", (suite_id, current_user["id"]))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="套件不存在")

    suite = dict(row)
    case_ids = json.loads(suite.get("case_ids") or "[]")
    cases = []
    total_steps = 0
    for case_id in case_ids:
        cursor.execute("SELECT * FROM web_cases WHERE id = ? AND user_id = ?", (case_id, current_user["id"]))
        case_row = cursor.fetchone()
        if not case_row:
            continue
        case = dict(case_row)
        steps = json.loads(case.get("steps") or "[]")
        if not steps:
            continue
        total_steps += len(steps)
        cases.append({"name": case["name"], "steps": steps})
    conn.close()

    if not cases:
        raise HTTPException(status_code=400, detail="套件内没有可执行的用例")

    report_id = str(uuid.uuid4())[:8]
    try:
        submit_web_run(report_id, cases)
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    create_running_report(current_user["id"], "web_suite", suite["name"], total_steps, report_id=report_id)

    return {
        "report_id": report_id,
        "runner_job_id": report_id,
        "test_name": suite["name"],
        "total_cases": total_steps,
        "case_count": len(cases),
        "status": "running",
    }

@router.get("/tasks")
def list_tasks(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM web_tasks WHERE user_id = ? ORDER BY created_at DESC", (current_user["id"],))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@router.post("/tasks")
def create_task(data: dict = Body(...), current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    task_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO web_tasks (id, user_id, name, suite_id, task_type, cron_expression, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        task_id,
        current_user["id"],
        data.get("name"),
        data.get("suite_id"),
        data.get("task_type", "manual"),
        data.get("cron_expression"),
        data.get("status", "pending"),
        now,
        now
    ))
    conn.commit()
    conn.close()
    return {"id": task_id, "message": "创建成功"}

@router.get("/tasks/{task_id}")
def get_task(task_id: str, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM web_tasks WHERE id = ? AND user_id = ?", (task_id, current_user["id"]))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return dict(row)

@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM web_tasks WHERE id = ? AND user_id = ?", (task_id, current_user["id"]))
    conn.commit()
    conn.close()
    return {"message": "删除成功"}
