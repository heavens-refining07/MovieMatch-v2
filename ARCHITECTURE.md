# MovieMatch v2 architecture and delivery plan

## Goals and boundaries

Only hosts have accounts. An authenticated host creates and owns rooms, controls filters, removes guests, starts voting, and explicitly restarts a round. Guests join through a room code or share link, choose a nickname, and receive a short-lived signed connection pass. They do not create accounts and cannot obtain host permissions by changing browser storage or URL values.

The existing FastAPI, WebSocket, TMDB, and vanilla frontend structure remains intact. Durable host data lives in SQL; live swipe coordination remains in memory for now.

## Components

1. **HTTP API** handles host registration/login/logout, dashboard data, presets, room creation, guest admission, and host reconnection.
2. **Session service** issues opaque random session tokens. Only a SHA-256 digest is stored in `auth_sessions`; the raw token is kept in a SameSite, HttpOnly cookie.
3. **Room-pass signer** creates time-limited, room-scoped WebSocket passes with a participant ID, nickname, and role. A host pass is accepted only when the socket also carries a valid host session belonging to the room owner.
4. **Room manager** preserves the current in-memory low-latency state for participants, connections, votes, and results.
5. **Persistence layer** uses SQLAlchemy and `DATABASE_URL`, defaulting to SQLite and supporting Postgres with `psycopg`.
6. **TMDB adapter** reads its key only from the environment and safely degrades when the key is missing.

## Data model

### `users`

- `id`: random stable identifier
- `email`: unique normalized host email
- `display_name`: host name shown in lobbies
- `password_hash`: scrypt hash with a per-password random salt
- `created_at`

### `auth_sessions`

- `id`: SHA-256 digest of an opaque browser token
- `user_id`: owning host
- `expires_at`, `created_at`

### `room_presets`

- `id`, `owner_id`, `name`
- `filters_json`: region, providers, genres, rating, year range, and card count
- `created_at`, `updated_at`

### `rooms`

- `id`, four-digit `code`, `owner_id`, optional `preset_id`
- `title`, `status`
- `filters_json`, optional `result_json`
- `participant_count`
- `created_at`, `started_at`, `ended_at`

Guests are intentionally not durable user records. A completed room stores aggregate result data and participant count, not guest credentials.

## Authentication and session flow

1. The host registers or logs in over HTTPS.
2. The server verifies the scrypt password hash and creates an opaque database-backed session.
3. The browser receives the raw session token in an HttpOnly, SameSite cookie. JavaScript cannot read it.
4. Host-only HTTP routes resolve the session and reject anonymous callers with `401`.
5. Logout deletes the server-side session and clears the cookie.
6. Production requires `APP_SECRET_KEY` and should set `COOKIE_SECURE=true`.

Future hardening: email verification, password reset, login throttling, session management UI, CSRF tokens if the API is split across origins, and background pruning of expired sessions.

## Room ownership and realtime flow

1. An authenticated host creates a room. The database record stores `owner_id`; the live room stores the same owner and its database record ID.
2. The host receives a signed room pass with role `host`. The WebSocket accepts it only if the host session cookie belongs to the room owner.
3. A guest submits a nickname to `/api/rooms/{code}/join` and receives a signed room pass with role `guest` and a random participant ID.
4. The WebSocket reads identity and role from the signed pass, never from a client-controlled local-storage ID.
5. Every privileged socket event checks the server-established host role. Filter changes, kicks, start, and restart are host-only.
6. Votes stay private while every participant completes the full deck. A disconnected participant remains part of the round and can reconnect to finish rather than causing an early result.
7. The final accepted vote calculates every movie's like total while holding the room vote lock. One movie is selected randomly from the highest-liked candidates, and the result is locked so concurrent, duplicate, and late votes cannot replace it.
8. Starting and completing a round updates the durable room record. The dashboard reads this history even after the live connection closes, and reconnecting participants receive the same finalized result.

## UI information architecture

- **Welcome:** two-column desktop hero, focused guest join form, compact benefits, and three-step explanation.
- **Guest join:** room code and nickname in one short form; shared links prefill the code.
- **Host auth:** focused sign-in/create-account screen explaining that accounts are host-only.
- **Host dashboard:** create-room card, preset library, and recent room history with winner summaries.
- **Lobby:** prominent share code, participant presence, room settings, and host controls.
- **Filter editor:** region, streaming services, genres, rating, year range, fixed deck sizes, and a custom 1–100 card count; reused for presets and live rooms.
- **Swipe arena:** centered 2:3 poster stack, personal deck progress, participant presence, accessible buttons, pointer gestures, and reduced-motion support.
- **Waiting state:** confirms that the local deck is complete while the remaining participants finish.
- **Results:** one 2:3 winner poster, metadata, overview, confetti/card reveal animation, and host restart without exposing aggregate vote totals or rankings.

The visual system uses white and light-gray surfaces, strong black typography, thin neutral borders, restrained shadows, 44px minimum controls, responsive single-column layouts, and safe-area-friendly spacing.

## Phased implementation plan

### Phase 0 — Immediate security response

- Revoke and rotate the exposed TMDB key.
- Keep the replacement in Render/environment secrets only.
- Review Git history and access logs; rewrite history if policy requires it.

### Phase 1 — Secure host foundation (implemented)

- Users, password hashing, opaque sessions, host-only API dependencies.
- Persistent room ownership, dashboard records, presets, and results.
- Signed guest/host WebSocket passes and server-side host authorization.
- SQLite local default and Postgres-ready database URL.

### Phase 2 — Product UI and lifecycle (implemented baseline)

- Mobile-first welcome, auth, dashboard, lobby, swipe, waiting, and results screens.
- Share links, nickname-only guest entry, filter editing, room history, preset create/edit.
- Responsive and reduced-motion behavior.

### Phase 3 — Production hardening

- Alembic migrations instead of startup `create_all`.
- Rate limits for login, registration, join, and room-code enumeration.
- Email verification and password reset.
- CSRF defense for any future cross-origin cookie setup, strict security headers, structured audit logs, and automated session cleanup.
- Preset deletion confirmation and account/session management.

### Phase 4 — Scale and resilience

- Redis-backed live rooms/pub-sub so multiple FastAPI replicas can share WebSocket state.
- Reconnection grace periods and resumable guest passes.
- Background TMDB cache refresh, request timeouts, metrics, error tracking, and health/readiness checks.
- Replace four-digit codes or add expiration/rate limiting as traffic grows.

### Phase 5 — Quality and experience

- Browser end-to-end tests for host and multi-guest rounds.
- Accessibility audit, keyboard swipe alternatives, focus trapping in modals, and screen-reader announcements.
- Optional invite QR codes, preset duplication, analytics, and richer post-game history.
