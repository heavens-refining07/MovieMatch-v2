import os
import unittest
from pathlib import Path


TEST_DB = Path(__file__).parent / "moviematch-test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["APP_SECRET_KEY"] = "test-secret-key-that-is-long-and-private"
os.environ["TMDB_API_KEY"] = ""

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.database import Base, engine
from app.main import app
from app.rooms import room_manager


class MovieMatchApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    def setUp(self):
        room_manager.rooms.clear()
        self.host = TestClient(app)
        self.guest = TestClient(app)
        response = self.host.post(
            "/api/auth/register",
            json={"email": "host@example.com", "display_name": "Alex", "password": "correct-horse-battery"},
        )
        if response.status_code == 409:
            response = self.host.post(
                "/api/auth/login",
                json={"email": "host@example.com", "password": "correct-horse-battery"},
            )
        self.assertEqual(response.status_code, 201 if response.request.url.path.endswith("register") else 200)

    def test_host_auth_preset_room_and_guest_flow(self):
        me = self.host.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["display_name"], "Alex")

        preset = self.host.post(
            "/api/presets",
            json={"name": "Family night", "filters": {"region": "IN", "genre_ids": [16], "card_count": 10}},
        )
        self.assertEqual(preset.status_code, 201)

        room_response = self.host.post(
            "/api/rooms",
            json={"title": "Friday films", "preset_id": preset.json()["id"]},
        )
        self.assertEqual(room_response.status_code, 201)
        room = room_response.json()
        self.assertEqual(len(room["room_code"]), 4)
        self.assertNotIn("host_id", room)

        unauthorized_create = self.guest.post("/api/rooms", json={"title": "Not allowed"})
        self.assertEqual(unauthorized_create.status_code, 401)

        join = self.guest.post(f"/api/rooms/{room['room_code']}/join", json={"nickname": "Sam"})
        self.assertEqual(join.status_code, 200)
        guest_pass = join.json()
        self.assertEqual(guest_pass["nickname"], "Sam")

        with self.guest.websocket_connect(f"/ws/{room['room_code']}?token={guest_pass['ws_token']}") as websocket:
            lobby = websocket.receive_json()
            self.assertEqual(lobby["type"], "lobby_update")
            guest_rows = [p for p in lobby["data"]["participants"] if p["name"] == "Sam"]
            self.assertEqual(len(guest_rows), 1)
            self.assertFalse(guest_rows[0]["is_host"])

        with self.assertRaises(WebSocketDisconnect) as rejected:
            with self.guest.websocket_connect(f"/ws/{room['room_code']}?token={room['ws_token']}"):
                pass
        self.assertEqual(rejected.exception.code, 4001)

        dashboard = self.host.get("/api/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.json()["rooms"][0]["title"], "Friday films")
        self.assertEqual(dashboard.json()["presets"][0]["name"], "Family night")

    def test_logout_revokes_session(self):
        self.assertEqual(self.host.post("/api/auth/logout").status_code, 204)
        self.assertEqual(self.host.get("/api/auth/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
