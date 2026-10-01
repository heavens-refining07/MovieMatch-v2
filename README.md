# MovieMatch

MovieMatch is a mobile-first group movie picker. A signed-in host creates a room, guests join with a four-digit code or link and a nickname, everyone finishes the same swipe deck, and one of the highest-liked movies becomes the winner. Ties are resolved randomly.

## What changed in v2

- Host-only accounts and server-side sessions. Guests never create accounts.
- Authenticated room ownership and signed room connection passes; browser-controlled IDs no longer grant host powers.
- Host dashboard with room history and reusable filter presets.
- Persistent SQLite data locally, configurable for Postgres in deployment.
- A clean white responsive interface for joining, hosting, lobbies, 2:3 poster swiping, and an animated winner reveal.
- Retired Disney+ Hotstar/JioCinema/Zee5/SonyLIV choices are removed, including from stale saved presets; Hulu, HBO Max, and Paramount+ are available.
- Fixed 10/15/25-card options plus a custom 1–100 card deck size.
- Aggregate room vote totals and rankings stay out of the UI.
- No API keys in source code or documentation.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the data model, security boundaries, flows, and rollout plan.

## Security notice

An earlier revision exposed a TMDB API key in both `app/tmdb.py` and `README.md`. Removing it from the current branch does **not** make that key private because it remains in Git history. Revoke or rotate it in TMDB immediately, store the replacement only as a deployment secret, and consider rewriting repository history if required by your security policy.

Never commit `.env`, database files, session secrets, or API keys.

## Local setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Set these values in your environment (the app does not automatically load `.env`):

```text
APP_SECRET_KEY=<long random value>
TMDB_API_KEY=<new rotated key>
DATABASE_URL=sqlite:///./moviematch.db
COOKIE_SECURE=false
```

Run the app:

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Deployment

For Render or another HTTPS host:

- Set `ENVIRONMENT=production`.
- Set `APP_SECRET_KEY` to a long random secret; startup intentionally fails without it in production.
- Set `TMDB_API_KEY` to a newly rotated key.
- Set `COOKIE_SECURE=true`.
- Use a managed Postgres URL for `DATABASE_URL`; `postgres://` and `postgresql://` URLs are normalized for `psycopg`.
- Set `ALLOWED_ORIGINS` only if the frontend is hosted on a different origin.

SQLite is ideal for local development. A single-instance deployment can use a persistent disk, but Postgres is recommended for production durability and horizontal scaling.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests cover registration/login, session revocation, preset persistence, authenticated room creation, account-free guest joining, rejection of host-token use without a host session, provider cleanup, custom deck validation, wait-for-everyone result timing, randomized top-vote ties, duplicate/late vote handling, and result UI safeguards.

## Current architecture

- **Backend:** FastAPI, WebSockets, SQLAlchemy
- **Data:** SQLite by default; Postgres through `DATABASE_URL`
- **Frontend:** semantic HTML, responsive CSS, vanilla JavaScript
- **Movies:** TMDB discovery and metadata
- **Authentication:** opaque random server sessions in HttpOnly cookies, scrypt password hashing
- **Realtime authorization:** signed, room-scoped connection passes plus an authenticated host session for host role

## Operational note

Active WebSocket room state remains in process memory to preserve the existing architecture. Durable room metadata, presets, and results are stored in the database. A later scaling phase should move live room coordination to Redis before running multiple application replicas.
