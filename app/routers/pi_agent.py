"""pi-Agent 自愈路由。

平台的职责是「编排 + 存历史」：真正干活的是 Runner 在本机拉起的 pi-agent 子进程。
Runner 的 JOBS 是内存字典，重启即丢，所以任务与产物都落在 pi_agent_tasks 表里。
"""
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException

from app.database import get_connection
from app.dependencies.auth import get_current_user
from app.services.local_runner_client import (
    RunnerUnavailable,
    runner_get_job,
    runner_health,
    runner_start_pi_agent,
    runner_stop_pi_agent,
)
from app.services.web_run_service import read_log_tail

router = APIRouter(prefix="/api/v1/pi-agent", tags=["pi-Agent自愈"])

# Runner 侧已结束的状态：映射到平台侧后就不再轮询
_RUNNER_FINISHED = {"completed", "failed", "cancelled"}

_TASK_COLUMNS = """
    id, name, case_id, case_name, start_url, storage_state, runner_task_id, status,
    exit_code, final_url, page_title, error, created_at, updated_at
"""


@router.get("/runner/health")
def runner_health_check(current_user: dict = Depends(get_current_user)):
    """Runner 是否在线 —— pi-agent 需要本机 Node 与已安装依赖，先探活再提交任务。"""
    try:
        return runner_health()
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/cases")
def list_healable_cases(current_user: dict = Depends(get_current_user)):
    """可作为自愈目标的 Web 用例（只回元信息，步骤由后端读取，前端不必传回）。"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, description, steps FROM web_cases WHERE user_id = ? ORDER BY updated_at DESC",
        (current_user["id"],),
    )
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["step_count"] = len(json.loads(item.pop("steps") or "[]"))
        result.append(item)
    return result


@router.get("/tasks")
def list_tasks(limit: int = 20, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""SELECT {_TASK_COLUMNS} FROM pi_agent_tasks
            WHERE user_id = ? ORDER BY created_at DESC LIMIT ?""",
        (current_user["id"], max(1, min(limit, 100))),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


@router.post("/tasks")
def create_task(data: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """提交自愈：把用例的声明式步骤 + 真实站点地址交给 Runner。"""
    case_id = str(data.get("case_id") or "").strip()
    start_url = str(data.get("start_url") or "").strip()
    if not case_id:
        raise HTTPException(status_code=400, detail="请选择要自愈的 Web 用例")
    if not start_url:
        raise HTTPException(status_code=400, detail="请填写被测页面的真实地址")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM web_cases WHERE id = ? AND user_id = ?", (case_id, current_user["id"]))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="用例不存在")
    case = dict(row)
    steps = json.loads(case.get("steps") or "[]")
    if not steps:
        conn.close()
        raise HTTPException(status_code=400, detail="该用例没有任何步骤，无法自愈")

    task_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    storage_state = str(data.get("storage_state") or "").strip()
    name = str(data.get("name") or "").strip() or f"自愈：{case.get('name') or case_id}"

    try:
        job = runner_start_pi_agent({
            "job_id": task_id,
            "case": {"id": case_id, "name": case.get("name"), "steps": steps},
            "start_url": start_url,
            "storage_state": storage_state,
        })
    except RunnerUnavailable as exc:
        conn.close()
        raise HTTPException(status_code=503, detail=str(exc))

    cursor.execute("""
        INSERT INTO pi_agent_tasks (id, user_id, name, case_id, case_name, start_url,
                                    storage_state, runner_task_id, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (task_id, current_user["id"], name, case_id, case.get("name"), start_url,
          storage_state, job.get("job_id", task_id), "running", now, now))
    conn.commit()
    conn.close()
    return {"id": task_id, "status": "running", "message": "自愈任务已提交"}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, current_user: dict = Depends(get_current_user)):
    """回读任务详情。

    任务未结束时顺带把 Runner 的实时状态与日志同步回平台，
    这样前端只轮询平台这一个接口就够了。
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pi_agent_tasks WHERE id = ? AND user_id = ?", (task_id, current_user["id"]))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="任务不存在")
    task = dict(row)
    task.setdefault("log", "")

    if task["status"] in {"pending", "running"}:
        try:
            job = runner_get_job(task.get("runner_task_id") or task_id)
        except RunnerUnavailable as exc:
            # Runner 不可用不代表任务失败（可能只是后端与 Runner 断开了），如实回报
            conn.close()
            task["runner_error"] = str(exc)
            return task

        log_text, _ = read_log_tail(job)
        runner_status = job.get("status", "unknown")
        updates = {"log": log_text, "updated_at": datetime.now().isoformat()}
        if runner_status in _RUNNER_FINISHED:
            result = job.get("result") or {}
            updates.update({
                "status": runner_status,
                "exit_code": job.get("exit_code"),
                "final_url": result.get("final_url"),
                "page_title": result.get("title"),
                "fixed_test": result.get("fixed_test"),
                "memory": result.get("memory"),
                "error": job.get("error") or "",
            })

        cursor.execute("""
            UPDATE pi_agent_tasks
            SET status = ?, exit_code = ?, final_url = ?, page_title = ?,
                fixed_test = ?, memory = ?, log = ?, error = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
        """, (updates.get("status", task["status"]), updates.get("exit_code", task.get("exit_code")),
              updates.get("final_url", task.get("final_url")), updates.get("page_title", task.get("page_title")),
              updates.get("fixed_test", task.get("fixed_test")), updates.get("memory", task.get("memory")),
              updates["log"], updates.get("error", task.get("error")),
              updates["updated_at"], task_id, current_user["id"]))
        conn.commit()
        task.update(updates)

    conn.close()
    return task


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: str, current_user: dict = Depends(get_current_user)):
    """人工叫停：模型静默空转时要能一键结束，否则会一直占着 Runner。"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, runner_task_id FROM pi_agent_tasks WHERE id = ? AND user_id = ?",
                   (task_id, current_user["id"]))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    task = dict(row)
    if task["status"] not in {"pending", "running"}:
        raise HTTPException(status_code=400, detail=f"任务已结束（{task['status']}），无需终止")

    try:
        runner_stop_pi_agent(task.get("runner_task_id") or task_id)
    except RunnerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"id": task_id, "message": "已发送终止指令，稍后刷新即可看到最终状态"}