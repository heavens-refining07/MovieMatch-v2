import asyncio
import json
import logging
import os
import secrets
from typing import Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

import app.tmdb as tmdb
from app.auth import (
    SESSION_COOKIE,
    clear_session_cookie,
    current_user,
    delete_session,
    hash_password,
    new_session,
    set_session_cookie,
    verify_password,
    websocket_user,
)
from app.database import RoomPreset, RoomRecord, User, get_db, init_database, session_scope, utcnow
from app.models import RoomFilter
from app.rooms import room_manager


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("moviematch")

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY")
if not APP_SECRET_KEY:
    if ENVIRONMENT == "production":
        raise RuntimeError("APP_SECRET_KEY must be set in production.")
    APP_SECRET_KEY = "local-development-only-change-me"
    logger.warning("Using a development-only APP_SECRET_KEY. Set APP_SECRET_KEY before deployment.")

room_token_signer = URLSafeTimedSerializer(APP_SECRET_KEY, salt="moviematch-room")
ROOM_TOKEN_MAX_AGE = int(os.getenv("ROOM_TOKEN_MAX_AGE_SECONDS", "86400"))
app = FastAPI(title="MovieMatch", version="2.0.0")

allowed_origins = [item.strip() for item in os.getenv("ALLOWED_ORIGINS", "").split(",") if item.strip()]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def startup_event():
    init_database()
    if tmdb.is_configured():
        asyncio.create_task(tmdb.warmup_cache())
    else:
        logger.warning("TMDB_API_KEY is not set; movie discovery will remain unavailable.")


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=2, max_length=60)
    password: str = Field(min_length=10, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class CreateRoomRequest(BaseModel):
    title: str = Field(default="Movie night", min_length=1, max_length=100)
    preset_id: Optional[int] = None


class JoinRoomRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=24)


class PresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    filters: RoomFilter = Field(default_factory=RoomFilter)


def user_json(user: User) -> dict:
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


def preset_json(preset: RoomPreset) -> dict:
    return {
        "id": preset.id,
        "name": preset.name,
        "filters": preset.filters,
        "created_at": preset.created_at.isoformat(),
        "updated_at": preset.updated_at.isoformat(),
    }


def room_json(record: RoomRecord) -> dict:
    return {
        "id": record.id,
        "room_code": record.code,
        "title": record.title,
        "status": record.status,
        "filters": record.filters,
        "result": record.result,
        "participant_count": record.participant_count,
        "created_at": record.created_at.isoformat(),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "ended_at": record.ended_at.isoformat() if record.ended_at else None,
        "preset_id": record.preset_id,
    }


def issue_room_token(code: str, user_id: str, role: str, nickname: str) -> str:
    return room_token_signer.dumps({"room": code, "user_id": user_id, "role": role, "nickname": nickname})


def read_room_token(token: str, code: str) -> dict:
    try:
        payload = room_token_signer.loads(token, max_age=ROOM_TOKEN_MAX_AGE)
    except SignatureExpired as exc:
        raise ValueError("Room pass expired.") from exc
    except BadSignature as exc:
        raise ValueError("Invalid room pass.") from exc
    if payload.get("room") != code:
        raise ValueError("Room pass does not match this room.")
    return payload


def persist_room_result(room, result: dict) -> None:
    with session_scope() as db:
        record = db.get(RoomRecord, room.record_id)
        if record:
            record.status = "results"
            record.ended_at = utcnow()
            record.participant_count = len(room.participants)
            record.result_json = json.dumps(result)


@app.get("/")
async def serve_home():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/health")
async def health():
    return {"status": "ok", "tmdb_configured": tmdb.is_configured()}


@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    email = req.email.lower().strip()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account already exists for this email.")
    user = User(email=email, display_name=req.display_name.strip(), password_hash=hash_password(req.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    set_session_cookie(response, new_session(db, user))
    return {"user": user_json(user)}


@app.post("/api/auth/login")
def login(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == req.email.lower().strip()))
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    set_session_cookie(response, new_session(db, user))
    return {"user": user_json(user)}


@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
):
    delete_session(db, session_token)
    clear_session_cookie(response)


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return {"user": user_json(user)}


@app.get("/api/genres")
async def get_genres():
    return [{"id": gid, "name": name} for gid, name in (await tmdb.get_genres()).items()]


@app.get("/api/providers")
async def get_providers(region: str = "IN"):
    return tmdb.get_popular_providers_for_region(region)


@app.get("/api/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    presets = db.scalars(select(RoomPreset).where(RoomPreset.owner_id == user.id).order_by(RoomPreset.updated_at.desc())).all()
    rooms = db.scalars(
        select(RoomRecord).where(RoomRecord.owner_id == user.id).order_by(desc(RoomRecord.created_at)).limit(30)
    ).all()
    return {"user": user_json(user), "presets": [preset_json(p) for p in presets], "rooms": [room_json(r) for r in rooms]}


@app.post("/api/presets", status_code=status.HTTP_201_CREATED)
def create_preset(req: PresetRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    preset = RoomPreset(owner_id=user.id, name=req.name.strip(), filters_json=json.dumps(req.filters.model_dump()))
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return preset_json(preset)


@app.patch("/api/presets/{preset_id}")
def update_preset(preset_id: int, req: PresetRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    preset = db.get(RoomPreset, preset_id)
    if not preset or preset.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Preset not found.")
    preset.name = req.name.strip()
    preset.filters_json = json.dumps(req.filters.model_dump())
    preset.updated_at = utcnow()
    db.commit()
    db.refresh(preset)
    return preset_json(preset)


@app.delete("/api/presets/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preset(preset_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    preset = db.get(RoomPreset, preset_id)
    if not preset or preset.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Preset not found.")
    db.delete(preset)
    db.commit()


@app.post("/api/rooms", status_code=status.HTTP_201_CREATED)
def create_room(req: CreateRoomRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    preset = None
    filters = RoomFilter()
    if req.preset_id is not None:
        preset = db.get(RoomPreset, req.preset_id)
        if not preset or preset.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Preset not found.")
        filters = RoomFilter(**preset.filters)

    record = RoomRecord(
        id=secrets.token_hex(16), code="0000", owner_id=user.id,
        preset_id=preset.id if preset else None, title=req.title.strip(),
        filters_json=json.dumps(filters.model_dump()),
    )
    room, host_id = room_manager.create_room(user.display_name, user.id, record.id)
    room.filters = filters
    record.code = room.code
    db.add(record)
    db.commit()
    return {**room_json(record), "ws_token": issue_room_token(room.code, host_id, "host", user.display_name)}


@app.get("/api/rooms/{code}")
def check_room(code: str, db: Session = Depends(get_db)):
    room = room_manager.get_room(code)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found or no longer active.")
    record = db.get(RoomRecord, room.record_id)
    return {
        "room_code": room.code,
        "title": record.title if record else "Movie night",
        "state": room.state,
        "is_locked": room.state != "lobby",
        "participants_count": len([p for p in room.participants.values() if p.is_connected]),
    }


@app.post("/api/rooms/{code}/join")
def join_room(code: str, req: JoinRoomRequest):
    room = room_manager.get_room(code)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found or no longer active.")
    if room.state != "lobby":
        raise HTTPException(status_code=409, detail="This room has already started.")
    guest_id = f"guest_{secrets.token_urlsafe(9)}"
    nickname = req.nickname.strip()
    return {"room_code": room.code, "user_id": guest_id, "nickname": nickname, "ws_token": issue_room_token(room.code, guest_id, "guest", nickname)}


@app.post("/api/rooms/{code}/host-token")
def host_room_token(code: str, user: User = Depends(current_user)):
    room = room_manager.get_room(code)
    if not room or room.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Active room not found.")
    return {"room_code": room.code, "user_id": room.host_id, "nickname": user.display_name, "ws_token": issue_room_token(room.code, room.host_id, "host", user.display_name)}


@app.websocket("/ws/{code}")
async def websocket_endpoint(websocket: WebSocket, code: str, token: str = Query(...)):
    room = room_manager.get_room(code)
    if not room:
        await websocket.close(code=4004, reason="Room does not exist.")
        return
    try:
        pass_data = read_room_token(token, code)
    except ValueError as exc:
        await websocket.close(code=4001, reason=str(exc))
        return

    user_id, role, nickname = pass_data["user_id"], pass_data["role"], pass_data["nickname"]
    if role == "host":
        with session_scope() as db:
            user = websocket_user(websocket, db)
            if not user or user.id != room.owner_id or user_id != room.host_id:
                await websocket.close(code=4001, reason="Host login required.")
                return
    elif role != "guest":
        await websocket.close(code=4001, reason="Invalid room role.")
        return
    if room.state != "lobby" and user_id not in room.participants:
        await websocket.close(code=4003, reason="Game in progress. Room is locked.")
        return

    await websocket.accept()
    try:
        room.add_connection(user_id, websocket, nickname, is_host=(role == "host"))
    except ValueError:
        await websocket.close(code=4001, reason="Invalid participant identity.")
        return
    if room.state == "results" and room.last_result is not None:
        await websocket.send_json({"type": "game_results", "data": room.last_result.model_dump()})
    else:
        await room.broadcast("lobby_update", room.get_lobby_data())
        if room.state == "voting":
            voted_movie_ids = set(room.user_votes.get(user_id, {}))
            remaining_movies = [movie for movie in room.movies if movie.id not in voted_movie_ids]
            await websocket.send_json({
                "type": "game_started",
                "data": {
                    "movies": [movie.model_dump() for movie in remaining_movies],
                    "total_cards": len(room.movies),
                    "swiped_count": len(voted_movie_ids),
                },
            })

    try:
        while True:
            data = await websocket.receive_json()
            event_type, payload = data.get("type"), data.get("data", {})
            is_host = role == "host" and user_id == room.host_id

            if event_type == "update_filters" and is_host and room.state == "lobby":
                try:
                    room.filters = RoomFilter(**payload)
                    with session_scope() as db:
                        record = db.get(RoomRecord, room.record_id)
                        if record:
                            record.filters_json = json.dumps(room.filters.model_dump())
                    await room.broadcast("filters_updated", room.filters.model_dump())
                except Exception as exc:
                    logger.warning("Rejected room filters: %s", exc)

            elif event_type == "kick_player" and is_host:
                target_id = payload.get("target_id")
                if target_id and await room.kick_participant(target_id):
                    await room.broadcast("lobby_update", room.get_lobby_data())

            elif event_type == "start_game" and is_host and room.state == "lobby":
                movies = await tmdb.discover_movies(room.filters)
                if not movies:
                    await websocket.send_json({"type": "error", "data": {"message": "No movies matched. Check TMDB configuration or loosen the filters."}})
                    continue
                room.start_game(movies)
                with session_scope() as db:
                    record = db.get(RoomRecord, room.record_id)
                    if record:
                        record.status, record.started_at = "voting", utcnow()
                        record.participant_count = len(room.participants)
                await room.broadcast("game_started", {"movies": [m.model_dump() for m in movies], "total_cards": len(movies)})

            elif event_type == "cast_vote" and room.state == "voting":
                async with room.vote_lock:
                    outcome = room.cast_vote(user_id, payload.get("movie_id"), bool(payload.get("liked")))
                    result = outcome["result"]
                    if outcome["accepted"] and result is not None:
                        results = result.model_dump()
                        persist_room_result(room, results)
                    else:
                        results = None
                if not outcome["accepted"]:
                    continue
                if results is not None:
                    await room.broadcast("game_results", results)
                else:
                    await room.broadcast("progress_update", {"participants": [p.model_dump() for p in room.participants.values()]})

            elif event_type == "end_session" and is_host and room.state == "voting":
                await websocket.send_json({"type": "error", "data": {"message": "Everyone must finish their deck before the winner is revealed."}})

            elif event_type == "restart_game" and is_host:
                room.restart_game()
                with session_scope() as db:
                    record = db.get(RoomRecord, room.record_id)
                    if record:
                        record.status, record.started_at, record.ended_at, record.result_json = "lobby", None, None, None
                await room.broadcast("game_restarted", room.get_lobby_data())

    except WebSocketDisconnect:
        room.remove_connection(user_id)
        if room.state == "lobby":
            await room.broadcast("lobby_update", room.get_lobby_data())
        elif room.state == "voting":
            await room.broadcast("progress_update", {"participants": [p.model_dump() for p in room.participants.values()]})
    except Exception as exc:
        logger.exception("WebSocket error: %s", exc)
        room.remove_connection(user_id)
