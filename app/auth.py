import base64
import hashlib
import hmac
import os
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, WebSocket, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import AuthSession, User, get_db, utcnow


SESSION_COOKIE = "moviematch_session"
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "14"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_b64, digest_b64 = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
        expected = base64.urlsafe_b64decode(digest_b64 + "=" * (-len(digest_b64) % 4))
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    record = AuthSession(
        id=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        user_id=user.id,
        expires_at=utcnow() + timedelta(days=SESSION_DAYS),
    )
    db.add(record)
    db.commit()
    return token


def delete_session(db: Session, token: Optional[str]) -> None:
    if not token:
        return
    session_id = hashlib.sha256(token.encode("utf-8")).hexdigest()
    record = db.get(AuthSession, session_id)
    if record:
        db.delete(record)
        db.commit()


def user_from_token(db: Session, token: Optional[str]) -> Optional[User]:
    if not token:
        return None
    session_id = hashlib.sha256(token.encode("utf-8")).hexdigest()
    record = db.scalar(select(AuthSession).where(AuthSession.id == session_id))
    if not record:
        return None
    if record.expires_at.replace(tzinfo=record.expires_at.tzinfo or utcnow().tzinfo) <= utcnow():
        db.delete(record)
        db.commit()
        return None
    return record.user


def current_user(
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> User:
    user = user_from_token(db, session_token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Host login required.")
    return user


def optional_user(
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> Optional[User]:
    return user_from_token(db, session_token)


def websocket_user(websocket: WebSocket, db: Session) -> Optional[User]:
    return user_from_token(db, websocket.cookies.get(SESSION_COOKIE))


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
