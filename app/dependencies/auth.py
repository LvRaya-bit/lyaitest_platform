from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.services.auth_service import (
    DEFAULT_ADMIN_USERNAME,
    decode_token,
    get_user,
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = get_user(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_admin(current_user: dict = Depends(get_current_user)):
    """管理接口专用依赖：仅默认管理员账户可访问。

    返回 403 而非 401，避免前端把「权限不足」误判为登录失效而清空会话。
    """
    if current_user.get("username") != DEFAULT_ADMIN_USERNAME:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可访问该功能")
    return current_user