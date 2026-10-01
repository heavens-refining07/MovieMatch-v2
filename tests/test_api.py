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
from app.models import MovieItem, Participant, RoomFilter
from app.rooms import Room, room_manager
import app.tmdb as tmdb


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

    def test_retired_streaming_providers_are_removed_from_saved_filters(self):
        filters = RoomFilter(provider_ids=[8, 122, 220, 232, 237, 2336, 119])
        self.assertEqual(filters.provider_ids, [8, 119])
        provider_names = [provider["name"].lower().replace(" ", "") for provider in tmdb.get_popular_providers_for_region("IN")]
        self.assertNotIn("disney+hotstar", provider_names)
        self.assertNotIn("jiohotstar", provider_names)
        self.assertNotIn("jiocinema", provider_names)
        self.assertNotIn("zee5", provider_names)
        self.assertNotIn("sonyliv", provider_names)
        self.assertIn("hulu", provider_names)
        self.assertIn("hbomax", provider_names)
        self.assertIn("paramount+", provider_names)

    def test_winner_waits_for_everyone_to_finish_and_result_is_locked(self):
        room = Room("4821", "host_owner", "Alex", "owner", "record")
        room.participants["guest_sam"] = Participant(id="guest_sam", name="Sam", is_connected=True)
        first = MovieItem(id=1, title="First")
        second = MovieItem(id=2, title="Second")
        room.start_game([first, second])

        host_vote = room.cast_vote("host_owner", 1, True)
        self.assertTrue(host_vote["accepted"])
        self.assertIsNone(host_vote["result"])
        self.assertEqual(room.state, "voting")

        unanimous_but_early = room.cast_vote("guest_sam", 1, True)
        self.assertIsNone(unanimous_but_early["result"])
        self.assertEqual(room.state, "voting")

        room.cast_vote("host_owner", 2, False)
        final_vote = room.cast_vote("guest_sam", 2, False)
        self.assertTrue(final_vote["all_finished"])
        self.assertEqual(final_vote["result"].winner.id, 1)
        self.assertEqual(room.state, "results")

        late_vote = room.cast_vote("guest_sam", 2, True)
        self.assertFalse(late_vote["accepted"])
        self.assertEqual(room.last_result.winner.id, 1)
        self.assertEqual(room.get_lobby_data()["result"]["winner"]["id"], 1)

    def test_duplicate_vote_is_immutable_and_top_vote_ties_are_randomized(self):
        room = Room("4822", "host_owner", "Alex", "owner", "record")
        room.participants["guest_sam"] = Participant(id="guest_sam", name="Sam", is_connected=True)
        first = MovieItem(id=9, title="First choice")
        second = MovieItem(id=10, title="Second choice")
        third = MovieItem(id=11, title="Third choice")
        room.start_game([first, second, third])

        room.cast_vote("host_owner", 9, True)
        duplicate = room.cast_vote("host_owner", 9, False)
        self.assertFalse(duplicate["accepted"])
        room.cast_vote("host_owner", 10, False)
        room.cast_vote("host_owner", 11, False)
        room.cast_vote("guest_sam", 9, False)
        room.cast_vote("guest_sam", 10, True)
        final_vote = room.cast_vote("guest_sam", 11, False)

        self.assertTrue(final_vote["all_finished"])
        self.assertIn(final_vote["result"].winner.id, {9, 10})
        self.assertTrue(final_vote["result"].is_tie_break)
        self.assertEqual({movie.id for movie in final_vote["result"].tied_candidates}, {9, 10})
        self.assertEqual(room.state, "results")

    def test_custom_card_count_accepts_one_to_one_hundred(self):
        self.assertEqual(RoomFilter(card_count=37).card_count, 37)
        with self.assertRaises(ValueError):
            RoomFilter(card_count=0)
        with self.assertRaises(ValueError):
            RoomFilter(card_count=101)

    def test_results_ui_hides_vote_totals_and_posters_use_two_by_three_ratio(self):
        project_root = Path(__file__).parents[1]
        html = (project_root / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (project_root / "static" / "js" / "app.js").read_text(encoding="utf-8")
        css = (project_root / "static" / "css" / "style.css").read_text(encoding="utf-8")

        self.assertNotIn("Group ranking", html)
        self.assertNotIn("winner-score", html)
        self.assertNotIn("result.max_likes", javascript)
        self.assertNotIn("result.leaderboard", javascript)
        self.assertIn("aspect-ratio: 2 / 3", css)
        self.assertIn('data-count="custom"', html)
        self.assertIn('id="custom-card-count"', html)
        self.assertIn("poster-orbit.svg", html)
        self.assertIn("how-illustration", html)
        self.assertIn("result-confetti", html)


if __name__ == "__main__":
    unittest.main()
