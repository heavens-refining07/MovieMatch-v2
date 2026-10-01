import random
import string
import asyncio
import logging
from typing import Dict, List, Optional, Set
from fastapi import WebSocket
from app.models import MovieItem, Participant, RoomFilter, GameResult

logger = logging.getLogger(__name__)

AVATAR_COLORS = [
    "#8B5CF6", "#EC4899", "#3B82F6", "#10B981", 
    "#F59E0B", "#EF4444", "#06B6D4", "#6366F1"
]

class Room:
    def __init__(self, code: str, host_id: str, host_name: str, owner_id: str, record_id: str):
        self.code: str = code
        self.host_id: str = host_id
        self.owner_id: str = owner_id
        self.record_id: str = record_id
        self.state: str = "lobby"  # "lobby", "voting", "results"
        self.filters: RoomFilter = RoomFilter()
        self.participants: Dict[str, Participant] = {}
        self.connections: Dict[str, WebSocket] = {}
        self.movies: List[MovieItem] = []
        # movie_id -> set of user_ids who liked
        self.likes: Dict[int, Set[str]] = {}
        # movie_id -> set of user_ids who passed
        self.dislikes: Dict[int, Set[str]] = {}
        # user_id -> dict of {movie_id: bool}
        self.user_votes: Dict[str, Dict[int, bool]] = {}
        self.round_participant_ids: Set[str] = set()
        self.last_result: Optional[GameResult] = None
        self.vote_lock = asyncio.Lock()

        # Add initial host participant
        self.participants[host_id] = Participant(
            id=host_id,
            name=host_name,
            is_host=True,
            avatar_color=AVATAR_COLORS[0],
            is_connected=True
        )

    def add_connection(self, user_id: str, ws: WebSocket, name: Optional[str] = None, is_host: bool = False):
        if user_id == self.host_id and not is_host:
            raise ValueError("Host identity requires an authenticated host token.")
        self.connections[user_id] = ws
        if user_id in self.participants:
            self.participants[user_id].is_connected = True
            if name:
                self.participants[user_id].name = name
        else:
            color = AVATAR_COLORS[len(self.participants) % len(AVATAR_COLORS)]
            self.participants[user_id] = Participant(
                id=user_id,
                name=name or f"Player {len(self.participants) + 1}",
                is_host=is_host,
                avatar_color=color,
                is_connected=True
            )

    def remove_connection(self, user_id: str):
        if user_id in self.connections:
            del self.connections[user_id]
        if user_id in self.participants:
            self.participants[user_id].is_connected = False

    async def kick_participant(self, user_id: str) -> bool:
        if user_id == self.host_id:
            return False
        if user_id in self.connections:
            ws = self.connections[user_id]
            try:
                await ws.send_json({"type": "kicked", "message": "You have been removed by the host."})
                await ws.close()
            except Exception:
                pass
            del self.connections[user_id]
        if user_id in self.participants:
            del self.participants[user_id]
        self.round_participant_ids.discard(user_id)
        return True

    def start_game(self, movies: List[MovieItem]):
        self.movies = movies
        self.state = "voting"
        self.likes = {m.id: set() for m in movies}
        self.dislikes = {m.id: set() for m in movies}
        self.round_participant_ids = {
            uid for uid, participant in self.participants.items() if participant.is_connected
        }
        self.user_votes = {uid: {} for uid in self.round_participant_ids}
        self.last_result = None

        for uid, p in self.participants.items():
            p.voted_count = 0
            p.total_count = len(movies) if uid in self.round_participant_ids else 0
            p.has_finished = False

    def cast_vote(self, user_id: str, movie_id: int, liked: bool) -> dict:
        """Record one immutable vote and finalize only after every player finishes."""
        if self.state != "voting" or user_id not in self.round_participant_ids or movie_id not in self.likes:
            return {"accepted": False, "result": self.last_result, "all_finished": False}

        if movie_id in self.user_votes.get(user_id, {}):
            return {"accepted": False, "result": self.last_result, "all_finished": False}

        if liked:
            self.likes[movie_id].add(user_id)
            self.dislikes[movie_id].discard(user_id)
        else:
            self.dislikes[movie_id].add(user_id)
            self.likes[movie_id].discard(user_id)

        if user_id not in self.user_votes:
            self.user_votes[user_id] = {}
        self.user_votes[user_id][movie_id] = liked

        p = self.participants[user_id]
        p.voted_count = len(self.user_votes[user_id])
        if p.voted_count >= len(self.movies):
            p.has_finished = True

        all_finished = bool(self.round_participant_ids) and all(
            self.participants[user_id].has_finished for user_id in self.round_participant_ids
        )
        result = self.calculate_results() if all_finished else None
        return {"accepted": True, "result": result, "all_finished": all_finished}

    def calculate_results(self) -> GameResult:
        if self.last_result is not None:
            return self.last_result
        if self.state != "voting" or not self.round_participant_ids or not all(
            self.participants[user_id].has_finished for user_id in self.round_participant_ids
        ):
            raise RuntimeError("Results are available only after every participant finishes voting.")

        participant_ids = set(self.round_participant_ids)
        like_counts = {
            movie.id: len(self.likes.get(movie.id, set()) & participant_ids)
            for movie in self.movies
        }
        max_likes = max(like_counts.values(), default=0)
        tied_candidates = [movie for movie in self.movies if like_counts.get(movie.id, 0) == max_likes]
        if not tied_candidates:
            raise RuntimeError("No movies are available for the result.")

        winner = random.choice(tied_candidates)
        self.last_result = GameResult(
            winner=winner,
            is_tie_break=len(tied_candidates) > 1,
            tied_candidates=tied_candidates if len(tied_candidates) > 1 else [],
            total_voters=len(participant_ids),
            max_likes=max_likes,
        )
        self.state = "results"
        return self.last_result

    def restart_game(self):
        self.state = "lobby"
        self.movies = []
        self.likes = {}
        self.dislikes = {}
        self.user_votes = {}
        self.round_participant_ids = set()
        self.last_result = None
        for p in self.participants.values():
            p.voted_count = 0
            p.total_count = 0
            p.has_finished = False

    async def broadcast(self, event_type: str, data: dict, exclude: Optional[str] = None):
        payload = {"type": event_type, "data": data}
        dead_connections = []
        for uid, ws in list(self.connections.items()):
            if exclude and uid == exclude:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead_connections.append(uid)
        
        for uid in dead_connections:
            self.remove_connection(uid)

    def get_lobby_data(self) -> dict:
        data = {
            "code": self.code,
            "host_id": self.host_id,
            "state": self.state,
            "filters": self.filters.model_dump(),
            "participants": [p.model_dump() for p in self.participants.values()]
        }
        if self.last_result is not None:
            data["result"] = self.last_result.model_dump()
        return data

class RoomManager:
    def __init__(self):
        self.rooms: Dict[str, Room] = {}

    def generate_code(self) -> str:
        for _ in range(100):
            code = "".join(random.choices(string.digits, k=4))
            if code not in self.rooms:
                return code
        return str(random.randint(1000, 9999))

    def create_room(self, host_name: str, owner_id: str, record_id: str) -> tuple[Room, str]:
        code = self.generate_code()
        uid = f"host_{owner_id}"
        room = Room(
            code=code,
            host_id=uid,
            host_name=host_name,
            owner_id=owner_id,
            record_id=record_id,
        )
        self.rooms[code] = room
        return room, uid

    def get_room(self, code: str) -> Optional[Room]:
        return self.rooms.get(code.strip())

    def remove_room(self, code: str):
        if code in self.rooms:
            del self.rooms[code]

room_manager = RoomManager()
