"""Web 用例真实执行的共享编排逻辑。

真正打开浏览器执行步骤的是本机 Runner（POST /web-run）；这里只负责提交任务、
维护 reports 记录、回读结果并落库。被 web_automation 路由与 agent 服务共同使用，
避免两处重复实现。
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.database import get_connection
from app.services.local_runner_client import runner_get_job, runner_start_web_run

# 执行日志可能很长，只回传尾部，避免接口响应膨胀
_LOG_TAIL_BYTES = 200_000


def read_log_tail(job: Dict[str, Any]):
    """读取 Runner 落在本机磁盘上的执行日志尾部，供页面实时展示。"""
    log_path = job.get("log_path")
    if not log_path:
        return "", False
    path = Path(str(log_path))
    try:
        if not path.is_file():
            return "", False
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _LOG_TAIL_BYTES:
                handle.seek(size - _LOG_TAIL_BYTES)
            text = handle.read().decode("utf-8", errors="replace")
        return text, size > _LOG_TAIL_BYTES
    except OSError:
        return "", False


def flatten_logs(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 Runner 按用例分组的步骤日志拍平，便于报告页统一展示。"""
    logs = []
    for case in summary.get("cases", []):
        for entry in case.get("logs", []):
            item = dict(entry)
            item["case_name"] = case.get("name", "")
            logs.append(item)
    return logs


def create_running_report(user_id: str, test_type: str, test_name: str, total_cases: int,
                          session_id: Optional[str] = None, report_id: Optional[str] = None) -> str:
    """落一条 running 状态的报告并返回 report_id（同时用作 Runner 的 job_id）。"""
    report_id = report_id or str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO reports (id, user_id, session_id, test_type, test_name, status,
                             total_cases, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (report_id, user_id, session_id, test_type, test_name, "running", total_cases, now, now))
    conn.commit()
    conn.close()
    return report_id


def submit_web_run(report_id: str, cases: List[Dict[str, Any]], headless: bool = True) -> None:
    """把用例提交给本机 Runner 真实执行。

    headless=False 时会在本机弹出浏览器窗口，可肉眼观察每一步操作，
    需要桌面会话，适合本地调试；无头模式适合后台批量跑。

    Runner 不可用时抛 RunnerUnavailable，由调用方决定转成 HTTP 503 还是提示文案。
    """
    runner_start_web_run({"job_id": report_id, "cases": cases, "headless": headless})


def persist_run_result(report_id: str, user_id: str, payload: Dict[str, Any]) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE reports SET status = ?, passed_cases = ?, failed_cases = ?, logs = ?, updated_at = ?
        WHERE id = ? AND user_id = ?
    """, (
        payload.get("status"),
        payload.get("passed_cases", 0),
        payload.get("failed_cases", 0),
        json.dumps(payload.get("logs", []), ensure_ascii=False),
        datetime.now().isoformat(),
        report_id,
        user_id,
    ))
    conn.commit()
    conn.close()


def collect_web_run(report_id: str, user_id: str) -> Dict[str, Any]:
    """回读 Runner 的执行结果；完成后把真实通过/失败数写回 reports。

    报告不存在或不属于该 user_id 时抛 ValueError；
    Runner 不可用时由 runner_get_job 抛 RunnerUnavailable。
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reports WHERE id = ? AND user_id = ?", (report_id, user_id))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise ValueError("执行记录不存在")

    report = dict(row)
    job = runner_get_job(report_id)
    log_text, log_truncated = read_log_tail(job)
    payload: Dict[str, Any] = {
        "report_id": report_id,
        "test_name": report.get("test_name"),
        "total_cases": report.get("total_cases"),
        "runner_status": job.get("status", "unknown"),
        "log": log_text,
        "log_truncated": log_truncated,
    }

    if job.get("status") in {"running", "starting", "unknown"}:
        payload.update({
            "status": "running",
            "passed_cases": report.get("passed_cases") or 0,
            "failed_cases": report.get("failed_cases") or 0,
            "logs": [],
        })
        return payload

    # Runner 未能启动执行（例如缺少 playwright），此时没有 result
    if not job.get("result"):
        payload.update({
            "status": "failed",
            "passed_cases": 0,
            "failed_cases": report.get("total_cases") or 0,
            "logs": [{"status": "failed", "message": job.get("error", "执行失败，Runner 未返回结果")}],
        })
        persist_run_result(report_id, user_id, payload)
        return payload

    summary = job.get("result") or {}
    failed = int(summary.get("failed", 0))
    payload.update({
        "status": "completed" if failed == 0 else "failed",
        "passed_cases": int(summary.get("passed", 0)),
        "failed_cases": failed,
        "skipped_cases": int(summary.get("skipped", 0)),
        "elapsed_seconds": summary.get("elapsed_seconds"),
        "logs": flatten_logs(summary),
    })
    persist_run_result(report_id, user_id, payload)
    return payload