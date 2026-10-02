"""Auth API: login, logout, session info, password change."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth import passwords as pwd
from app.auth import sessions as session_svc
from app.auth.deps import CSRF, CurrentUser, DB, client_ip, get_session_token
from app.config import get_settings
from app.db.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class SessionInfo(BaseModel):
    username: str
    must_change_password: bool


def _auth_cookie_params():
    settings = get_settings()
    return {
        "key": settings.cookie_name,
        "httponly": True,
        "secure": not settings.insecure_cookies,
        "samesite": "strict",
        "path": "/",
    }


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        **_auth_cookie_params(),
    )


def _set_csrf_cookie(response: Response) -> str:
    settings = get_settings()
    token = secrets.token_urlsafe(24)
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=False,
        secure=not settings.insecure_cookies,
        samesite="strict",
        path="/",
    )
    return token


def ensure_bootstrap_admin(db) -> None:
    """Create the initial admin from environment-provided bootstrap credentials."""

    settings = get_settings()
    existing = db.scalar(select(User).where(User.username == settings.bootstrap_admin))
    if existing is not None:
        return
    if not settings.bootstrap_password:
        raise RuntimeError(
            "首次启动未找到管理员账户，且未设置 TAB_BOOTSTRAP_PASSWORD 环境变量，无法创建初始管理员"
        )
    db.add(
        User(
            username=settings.bootstrap_admin,
            password_hash=pwd.hash_password(settings.bootstrap_password),
            must_change_password=True,
        )
    )
    db.commit()


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, db: DB) -> dict:
    ip = client_ip(request)
    backoff = session_svc.backoff_seconds(db, payload.username, ip)
    if backoff > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"尝试过于频繁，请 {backoff} 秒后再试",
            headers={"Retry-After": str(backoff)},
        )

    user = db.scalar(select(User).where(User.username == payload.username))
    ok = user is not None and pwd.verify_password(payload.password, user.password_hash)
    session_svc.record_attempt(db, payload.username, ip, ok)
    # 防用户名枚举：统一错误信息。
    if not ok:
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    token, _row = session_svc.create_session(db, user.id, ip=ip, user_agent=request.headers.get("user-agent", ""))
    db.commit()
    _set_session_cookie(response, token)
    _set_csrf_cookie(response)
    return {
        "ok": True,
        "username": user.username,
        "must_change_password": user.must_change_password,
    }


@router.post("/logout", dependencies=[CSRF])
def logout(request: Request, response: Response, db: DB, user: CurrentUser) -> dict:
    token = get_session_token(request)
    session_svc.revoke_session(db, token)
    db.commit()
    settings = get_settings()
    response.delete_cookie(settings.cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return {"ok": True}


@router.get("/session")
def session_info(db: DB, user: CurrentUser) -> SessionInfo:
    return SessionInfo(username=user.username, must_change_password=user.must_change_password)


@router.post("/change-password", dependencies=[CSRF])
def change_password(
    payload: ChangePasswordRequest, request: Request, response: Response, db: DB, user: CurrentUser
) -> dict:
    if not pwd.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前密码错误")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新密码不能与当前密码相同")
    user.password_hash = pwd.hash_password(payload.new_password)
    user.must_change_password = False
    # 废除其他所有会话（保留当前会话）。
    current_token = get_session_token(request)
    session_svc.revoke_all_user_sessions(db, user.id, except_token=current_token)
    # 重新签发会话（登录后重新生成）。
    ip = client_ip(request)
    token, _row = session_svc.create_session(db, user.id, ip=ip, user_agent=request.headers.get("user-agent", ""))
    db.commit()
    _set_session_cookie(response, token)
    _set_csrf_cookie(response)
    return {"ok": True}


@router.get("/sessions")
def list_sessions(db: DB, user: CurrentUser) -> dict:
    from app.db.models import Session as SessionRow

    session_svc.purge_expired_sessions(db)
    db.commit()
    rows = db.scalars(select(SessionRow)).all()
    return {"count": len(rows)}
