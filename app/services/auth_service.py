import os
import secrets
from jose import jwt, JWTError
import bcrypt
from datetime import datetime, timedelta
from app.database import get_connection
import uuid

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

def get_secret_key() -> str:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    cursor.execute("SELECT value FROM config WHERE key = ?", ("jwt_secret_key",))
    row = cursor.fetchone()
    if row:
        conn.close()
        return row[0]
    new_key = secrets.token_hex(32)
    cursor.execute("INSERT INTO config (key, value) VALUES (?, ?)", ("jwt_secret_key", new_key))
    conn.commit()
    conn.close()
    return new_key

SECRET_KEY = os.getenv("JWT_SECRET_KEY", get_secret_key())

def verify_password(plain_password: str, hashed_password: bytes) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password)

def get_password_hash(password: str) -> bytes:
    password_bytes = password.encode('utf-8')[:72]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt())

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

def get_user(username: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "id": row[0],
            "username": row[1],
            "password_hash": row[2],
            "created_at": row[3]
        }
    return None

def create_user(username: str, password: str) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        conn.close()
        return None
    user_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    password_hash = get_password_hash(password)
    cursor.execute("""
        INSERT INTO users (id, username, password_hash, created_at)
        VALUES (?, ?, ?, ?)
    """, (user_id, username, password_hash, now))
    conn.commit()
    conn.close()
    return {"id": user_id, "username": username, "created_at": now}


DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"


def ensure_default_admin() -> None:
    """确保平台存在唯一的默认管理员账户。

    该账户在启动时创建，因此普通用户无法抢注 "admin" 这个用户名：
    注册接口会因用户名已存在而拒绝。
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (DEFAULT_ADMIN_USERNAME,))
    exists = cursor.fetchone()
    conn.close()
    if exists:
        return
    create_user(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)