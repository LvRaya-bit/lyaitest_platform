"""Android Appium 自动化能力的增量 API。"""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from app.database import get_connection
from app.dependencies.auth import get_current_user
from app.services.local_runner_client import (
    RunnerUnavailable,
    runner_get_job,
    runner_delete_job,
    runner_health,
    runner_preflight,
    runner_start_exploration,
    runner_start_pytest,
)


router = APIRouter(prefix="/api/v1/app-automation", tags=["Android APP自动化"])


def _now() -> str:
    return datetime.now().isoformat()


def _json(value: Any, default: Any):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _device(device_id: str, user_id: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM app_devices WHERE id = ? AND user_id = ?",
        (device_id, user_id),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="APP设备配置不存在")
    result = dict(row)
    result["capabilities"] = _json(result.get("capabilities"), {})
    return result


def _runner_config(device: Dict[str, Any], data: Optional[Dict[str, Any]] = None):
    data = data or {}
    return {
        "server_url": device["server_url"],
        "device_udid": device["device_udid"],
        "app_package": device["app_package"],
        "app_activity": device["app_activity"],
        "test_phone": device.get("test_phone") or "",
        "test_password_env": device.get("test_password_env") or "TEST_PASSWORD",
        "capabilities": device.get("capabilities") or {},
        **data,
    }


@router.get("/runner/health")
def get_runner_health(current_user: dict = Depends(get_current_user)):
    try:
        return runner_health()
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/devices")
def list_devices(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM app_devices WHERE user_id = ? ORDER BY created_at DESC",
        (current_user["id"],),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["capabilities"] = _json(item.get("capabilities"), {})
        result.append(item)
    return result


@router.post("/devices")
def create_device(data: Dict[str, Any] = Body(...), current_user: dict = Depends(get_current_user)):
    required = ["name", "device_udid", "app_package", "app_activity"]
    missing = [key for key in required if not str(data.get(key, "")).strip()]
    if missing:
        raise HTTPException(status_code=400, detail=f"缺少字段: {', '.join(missing)}")

    device_id = str(uuid.uuid4())[:8]
    now = _now()
    conn = get_connection()
    conn.execute(
        """INSERT INTO app_devices
        (id, user_id, name, server_url, device_udid, app_package, app_activity,
         test_phone, test_password_env, capabilities, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            device_id, current_user["id"], data["name"],
            data.get("server_url", "http://127.0.0.1:4723"), data["device_udid"],
            data["app_package"], data["app_activity"], data.get("test_phone", ""),
            data.get("test_password_env", "TEST_PASSWORD"),
            json.dumps(data.get("capabilities", {}), ensure_ascii=False),
            "unknown", now, now,
        ),
    )
    conn.commit()
    conn.close()
    return {"id": device_id, "message": "APP设备配置创建成功"}


@router.get("/devices/{device_id}")
def get_device(device_id: str, current_user: dict = Depends(get_current_user)):
    return _device(device_id, current_user["id"])


@router.put("/devices/{device_id}")
def update_device(device_id: str, data: Dict[str, Any] = Body(...), current_user: dict = Depends(get_current_user)):
    _device(device_id, current_user["id"])
    allowed = {
        "name", "server_url", "device_udid", "app_package", "app_activity",
        "test_phone", "test_password_env", "capabilities",
    }
    updates, params = [], []
    for key, value in data.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(json.dumps(value, ensure_ascii=False) if key == "capabilities" else value)
    if updates:
        updates.append("updated_at = ?")
        params.extend([_now(), device_id, current_user["id"]])
        conn = get_connection()
        conn.execute(f"UPDATE app_devices SET {', '.join(updates)} WHERE id = ? AND user_id = ?", params)
        conn.commit()
        conn.close()
    return {"message": "APP设备配置更新成功"}


@router.delete("/devices/{device_id}")
def delete_device(device_id: str, current_user: dict = Depends(get_current_user)):
    _device(device_id, current_user["id"])
    conn = get_connection()
    conn.execute("DELETE FROM app_devices WHERE id = ? AND user_id = ?", (device_id, current_user["id"]))
    conn.commit()
    conn.close()
    return {"message": "APP设备配置已删除"}


@router.post("/devices/{device_id}/preflight")
def preflight_device(device_id: str, current_user: dict = Depends(get_current_user)):
    device = _device(device_id, current_user["id"])
    try:
        result = runner_preflight(_runner_config(device))
        status = "ready" if result.get("ready", False) else "blocked"
    except RunnerUnavailable as exc:
        result = {"ready": False, "detail": str(exc)}
        status = "offline"
    conn = get_connection()
    conn.execute(
        "UPDATE app_devices SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (status, _now(), device_id, current_user["id"]),
    )
    conn.commit()
    conn.close()
    return result


def _sync_exploration_task(item: Dict[str, Any]) -> Dict[str, Any]:
    """同步仍在运行中的任务，避免 Runner 停止后数据库永久停留在 running。"""
    if not item.get("runner_task_id") or item.get("status") not in {"starting", "running"}:
        return item

    try:
        remote = runner_get_job(item["runner_task_id"])
        item["status"] = remote.get("status", item["status"])
        if remote.get("result") is not None:
            item["summary"] = json.dumps(remote["result"], ensure_ascii=False)
        if remote.get("error"):
            item["error"] = remote["error"]
    except RunnerUnavailable as exc:
        item["status"] = "interrupted"
        item["runner_error"] = str(exc)
        item["error"] = item.get("error") or "本地 Runner 不可用，探索任务已中断"

    conn = get_connection()
    conn.execute(
        "UPDATE app_exploration_tasks SET status = ?, summary = ?, error = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (item["status"], item.get("summary", "{}"), item.get("error"), _now(), item["id"], item["user_id"]),
    )
    conn.commit()
    conn.close()
    return item


@router.get("/explorations")
def list_explorations(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.*, d.name AS device_name
        FROM app_exploration_tasks t LEFT JOIN app_devices d ON t.device_id = d.id
        WHERE t.user_id = ? ORDER BY t.created_at DESC""",
        (current_user["id"],),
    ).fetchall()
    conn.close()
    return [_sync_exploration_task(dict(row)) for row in rows]


@router.post("/explorations")
def start_exploration(data: Dict[str, Any] = Body(...), current_user: dict = Depends(get_current_user)):
    device = _device(str(data.get("device_id", "")), current_user["id"])
    task_id = str(uuid.uuid4())[:8]
    now = _now()
    max_depth = max(1, min(int(data.get("max_depth", 3)), 8))
    max_actions = max(1, min(int(data.get("max_actions", 100)), 1000))
    click_strategy = str(data.get("click_strategy", "auto")).lower()
    if click_strategy not in {"auto", "native", "coordinate"}:
        raise HTTPException(status_code=400, detail="click_strategy 只能是 auto、native 或 coordinate")
    conn = get_connection()
    conn.execute(
        """INSERT INTO app_exploration_tasks
        (id, user_id, device_id, status, max_depth, max_actions, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (task_id, current_user["id"], device["id"], "starting", max_depth, max_actions, now, now),
    )
    conn.commit()
    conn.close()
    try:
        runner_job = runner_start_exploration(_runner_config(device, {
            "max_depth": max_depth, "max_actions": max_actions,
            "click_strategy": click_strategy,
            "job_id": task_id,
        }))
    except RunnerUnavailable as exc:
        conn = get_connection()
        conn.execute(
            "UPDATE app_exploration_tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            ("failed", str(exc), _now(), task_id),
        )
        conn.commit()
        conn.close()
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    conn = get_connection()
    conn.execute(
        "UPDATE app_exploration_tasks SET status = ?, runner_task_id = ?, updated_at = ? WHERE id = ?",
        (runner_job.get("status", "running"), runner_job.get("job_id"), _now(), task_id),
    )
    conn.commit()
    conn.close()
    return {"id": task_id, "runner_job_id": runner_job.get("job_id"), "status": runner_job.get("status", "running")}


@router.get("/explorations/{task_id}")
def get_exploration(task_id: str, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM app_exploration_tasks WHERE id = ? AND user_id = ?",
        (task_id, current_user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="探索任务不存在")
    item = _sync_exploration_task(dict(row))
    item["summary"] = _json(item.get("summary"), {})
    return item


@router.delete("/explorations/{task_id}")
def delete_exploration(task_id: str, current_user: dict = Depends(get_current_user)):
    """删除探索任务记录及本地 Runner 产生的 XML、截图和 JSON 结果。"""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM app_exploration_tasks WHERE id = ? AND user_id = ?",
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
    conn.execute(
        "DELETE FROM app_exploration_tasks WHERE id = ? AND user_id = ?",
        (task_id, current_user["id"]),
    )
    conn.commit()
    conn.close()
    result = {"id": task_id, "message": "探索结果已删除"}
    if cleanup_warning:
        result["warning"] = f"数据库记录已删除，但 Runner 结果目录未确认清理：{cleanup_warning}"
    return result


@router.post("/test-runs")
def start_test_run(data: Dict[str, Any] = Body(...), current_user: dict = Depends(get_current_user)):
    device = _device(str(data.get("device_id", "")), current_user["id"])
    test_dir = str(data.get("test_dir", "")).strip()
    if not test_dir:
        raise HTTPException(status_code=400, detail="test_dir 不能为空")
    run_id = str(uuid.uuid4())[:8]
    now = _now()
    conn = get_connection()
    conn.execute(
        """INSERT INTO app_test_runs (id, user_id, device_id, case_set_id, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, current_user["id"], device["id"], data.get("case_set_id"), "starting", now, now),
    )
    conn.commit()
    conn.close()
    try:
        remote = runner_start_pytest(_runner_config(device, {
            "test_dir": test_dir,
            "marker": data.get("marker", ""),
            "module": data.get("module", ""),
            "job_id": run_id,
        }))
    except RunnerUnavailable as exc:
        conn = get_connection()
        conn.execute("UPDATE app_test_runs SET status = ?, error = ?, updated_at = ? WHERE id = ?", ("failed", str(exc), _now(), run_id))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    conn = get_connection()
    conn.execute("UPDATE app_test_runs SET status = ?, runner_task_id = ?, updated_at = ? WHERE id = ?", (remote.get("status", "running"), remote.get("job_id"), _now(), run_id))
    conn.commit()
    conn.close()
    return {"id": run_id, "runner_job_id": remote.get("job_id"), "status": remote.get("status", "running")}


@router.get("/test-runs")
def list_test_runs(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    rows = conn.execute("SELECT * FROM app_test_runs WHERE user_id = ? ORDER BY created_at DESC", (current_user["id"],)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@router.get("/test-runs/{run_id}")
def get_test_run(run_id: str, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM app_test_runs WHERE id = ? AND user_id = ?",
        (run_id, current_user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="APP测试任务不存在")
    item = dict(row)
    if item.get("runner_task_id") and item.get("status") in {"starting", "running"}:
        try:
            remote = runner_get_job(item["runner_task_id"])
            item["status"] = remote.get("status", item["status"])
            item["report_path"] = remote.get("report_path")
            item["summary"] = json.dumps({
                "exit_code": remote.get("exit_code"),
                "finished_at": remote.get("finished_at"),
            }, ensure_ascii=False)
            item["error"] = remote.get("error")
            conn = get_connection()
            conn.execute(
                "UPDATE app_test_runs SET status = ?, report_path = ?, summary = ?, error = ?, updated_at = ? WHERE id = ?",
                (item["status"], item.get("report_path"), item["summary"], item.get("error"), _now(), run_id),
            )
            conn.commit()
            conn.close()
        except RunnerUnavailable as exc:
            item["runner_error"] = str(exc)
    item["summary"] = _json(item.get("summary"), {})
    return item
