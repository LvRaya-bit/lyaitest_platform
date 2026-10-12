import uuid
from datetime import datetime
from app.database import get_connection


def save_report(report_data: dict):
    """保存测试报告到 SQLite"""
    report_id = report_data.get("id") or str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO reports (
            id, user_id, session_id, task_id, test_type, test_name, url, status_code,
            response_time, title, screenshot, error, status, total_cases,
            passed_cases, failed_cases, duration, logs, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        report_id,
        report_data.get("user_id", "unknown"),
        report_data.get("session_id", "unknown"),
        report_data.get("task_id"),
        report_data.get("test_type", "unknown"),
        report_data.get("test_name", ""),
        report_data.get("url", ""),
        report_data.get("status_code"),
        report_data.get("response_time"),
        report_data.get("title"),
        report_data.get("screenshot"),
        report_data.get("error"),
        report_data.get("status", "completed"),
        report_data.get("total_cases", 0),
        report_data.get("passed_cases", 0),
        report_data.get("failed_cases", 0),
        report_data.get("duration"),
        report_data.get("logs"),
        now,
        now
    ))

    conn.commit()
    conn.close()
    return report_id


def get_all_reports(user_id: str = None):
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("SELECT * FROM reports WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    else:
        cursor.execute("SELECT * FROM reports ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_reports_by_session(session_id: str, user_id: str = None):
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute(
            "SELECT * FROM reports WHERE session_id = ? AND user_id = ? ORDER BY created_at DESC",
            (session_id, user_id)
        )
    else:
        cursor.execute(
            "SELECT * FROM reports WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,)
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_report_by_id(report_id: str, user_id: str = None):
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("SELECT * FROM reports WHERE id = ? AND user_id = ?", (report_id, user_id))
    else:
        cursor.execute("SELECT * FROM reports WHERE id = ?", (report_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_report(report_id: str, user_id: str = None) -> bool:
    """删除一条执行结果记录；返回是否真的删到了（用于区分 404）。"""
    conn = get_connection()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("DELETE FROM reports WHERE id = ? AND user_id = ?", (report_id, user_id))
    else:
        cursor.execute("DELETE FROM reports WHERE id = ?", (report_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted
