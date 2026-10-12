from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from app.services.auth_service import (
    DEFAULT_ADMIN_USERNAME,
    get_user,
    verify_password,
    create_access_token,
    create_user,
)
from app.dependencies.auth import get_current_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

class RegisterRequest(BaseModel):
    username: str
    password: str

class RegisterResponse(BaseModel):
    id: str
    username: str
    message: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user_id: str
    username: str

class UserResponse(BaseModel):
    id: str
    username: str
    created_at: str

@router.post("/register", response_model=RegisterResponse)
async def register(request: RegisterRequest):
    if not request.username or not request.password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    if request.username.strip().lower() == DEFAULT_ADMIN_USERNAME:
        raise HTTPException(status_code=400, detail="该用户名不可用，请更换其他用户名")
    user = create_user(request.username, request.password)
    if not user:
        raise HTTPException(status_code=400, detail="用户名已存在")
    return RegisterResponse(id=user["id"], username=user["username"], message="注册成功")

@router.post("/login", response_model=LoginResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = get_user(form_data.username)
    if not user or not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    access_token = create_access_token(data={"sub": user["username"], "user_id": user["id"]})
    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user_id=user["id"],
        username=user["username"]
    )

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserResponse(id=current_user["id"], username=current_user["username"], created_at=current_user["created_at"])