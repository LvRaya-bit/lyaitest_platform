from fastapi import APIRouter, Depends
from app.database import get_connection
from app.dependencies.auth import require_admin

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

@router.get("/users")
async def list_users(current_user: dict = Depends(require_admin)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, created_at FROM users ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [{"id": row[0], "username": row[1], "created_at": row[2]} for row in rows]

@router.get("/users/{user_id}/sessions")
async def get_user_sessions(user_id: str, current_user: dict = Depends(require_admin)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT session_id, name, created_at FROM sessions WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{"session_id": row[0], "name": row[1], "created_at": row[2]} for row in rows]