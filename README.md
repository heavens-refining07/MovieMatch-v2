# MovieMatch

MovieMatch is a real-time group movie recommendation and voting platform. A host creates a private room, friends join without creating accounts, and everyone swipes through the same set of movies. After every participant finishes voting, MovieMatch selects a winner from the movies with the highest number of likes.

**Live application:** [moviematch-v2.onrender.com](https://moviematch-v2.onrender.com/)  
**Release:** [MovieMatch v2.1](https://github.com/heavens-refining07/Test-1/releases/tag/v2.1)

## Project objective

Choosing a movie in a group often takes longer than watching one. MovieMatch replaces repeated suggestions and group-chat debates with a simple shared process:

1. The host signs in and creates a room.
2. Guests join using a four-digit code or invitation link and a nickname.
3. The host chooses genres, streaming services, rating, year range, and deck size.
4. Every participant swipes left or right on the same movie deck.
5. When everyone finishes, the application reveals one of the highest-voted movies. Ties are resolved randomly.

Only hosts need accounts. Guests can participate immediately without registration.

## Main features

- Host registration, login, logout, and secure browser sessions
- Guest access through room code or invitation link
- Real-time lobby and voting updates using WebSockets
- TMDB movie discovery with genres, posters, ratings, and release details
- Filters for genre, streaming provider, minimum rating, and release year
- Fixed or custom deck sizes from 1 to 100 movies
- Voting that waits until every participant has completed the deck
- Random winner selection when multiple movies share the highest score
- Animated winner reveal and responsive swipe interface
- Host dashboard with room history and reusable filter presets
- SQLite support for local development and PostgreSQL support for deployment
- Automated API, authentication, room, voting, and result tests

## System architecture

```text
Browser interface
  HTML + CSS + JavaScript
          |
          | REST API and WebSocket events
          v
FastAPI application
  Authentication | Rooms | Voting | Presets | Dashboard
          |
          +-------------------+
          |                   |
          v                   v
SQLAlchemy database       TMDB API
Users, sessions,          Movie metadata,
presets and history       posters and filters
```

FastAPI serves both the frontend and backend. REST endpoints handle authentication, room creation, presets, and dashboard data. WebSockets keep participants, voting progress, and results synchronized in real time.

## Technology stack

| Area | Technology | Purpose |
| --- | --- | --- |
| Backend | Python 3.11, FastAPI | API routes and application logic |
| Real-time communication | WebSockets | Live room, participant, and voting updates |
| Data validation | Pydantic | Request and response validation |
| Database | SQLAlchemy | Database models and persistence |
| Local database | SQLite | Simple local development |
| Production database | PostgreSQL with Psycopg | Durable hosted data |
| Authentication | HttpOnly sessions, scrypt, ItsDangerous | Host sessions, password hashing, and signed room passes |
| Movie data | TMDB API | Movie metadata, posters, genres, and ratings |
| Frontend | HTML5, CSS3, vanilla JavaScript | Responsive interface without a frontend framework |
| Deployment | Docker, Uvicorn, Render | Containerized web deployment |
| Testing | Python `unittest`, FastAPI test tools | Automated application verification |

## Project structure

```text
Test-1/
|-- app/
|   |-- main.py          # FastAPI routes, WebSocket endpoint, and startup
|   |-- auth.py          # Password hashing, sessions, and signed passes
|   |-- database.py      # SQLAlchemy models and database configuration
|   |-- models.py        # Pydantic request and response models
|   |-- rooms.py         # Room state, participants, voting, and results
|   `-- tmdb.py          # TMDB integration, filters, and movie cache
|-- static/
|   |-- index.html       # Application screens and components
|   |-- css/style.css    # Responsive design and animations
|   |-- js/app.js        # Navigation, authentication, rooms, and dashboard
|   |-- js/swipe.js      # Swipe gestures and vote submission
|   `-- assets/          # Local illustrations and poster artwork
|-- tests/test_api.py    # Automated API and room-flow tests
|-- run.py               # Production server entry point
|-- requirements.txt     # Python dependencies
|-- Dockerfile           # Container configuration
|-- Procfile             # Alternative hosting start command
|-- ARCHITECTURE.md      # Detailed data model and security design
`-- .env.example         # Environment-variable template
```

## Security design

- Host passwords are stored as secure hashes, never as plain text.
- Host sessions use opaque values stored in HttpOnly cookies.
- Guests receive signed, room-specific connection passes.
- Host-only actions are checked by the server rather than trusted from browser data.
- API keys and application secrets are loaded from environment variables.
- `.env`, database, and local development files are excluded from Git.

Never place a real `TMDB_API_KEY` or `APP_SECRET_KEY` in source code. If a key has previously been committed, removing it from the latest file is not sufficient because it may remain in Git history; the exposed key should be rotated.

## Local installation

### Requirements

- Python 3.11 or later
- A free TMDB API key
- Git (optional)

### 1. Create and activate a virtual environment

```bash
python -m venv .venv
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

The application reads operating-system environment variables. In Windows PowerShell, set them for the current terminal session:

```powershell
$env:ENVIRONMENT="development"
$env:APP_SECRET_KEY="replace-with-a-long-random-value"
$env:TMDB_API_KEY="replace-with-your-tmdb-api-key"
$env:DATABASE_URL="sqlite:///./moviematch.db"
$env:COOKIE_SECURE="false"
```

`.env.example` is provided as a reference template, but the application does not automatically load a `.env` file. Never commit a completed `.env` file.

### 4. Start the application

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) on the same computer. Devices connected to the same Wi-Fi can use `http://YOUR-COMPUTER-IP:8000` while the server is running and the firewall permits access.

## Important API endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/` | GET | Load the MovieMatch interface |
| `/api/health` | GET | Check application and TMDB configuration status |
| `/api/auth/register` | POST | Create a host account |
| `/api/auth/login` | POST | Sign in a host |
| `/api/dashboard` | GET | Load host rooms, history, and presets |
| `/api/rooms` | POST | Create a host-owned room |
| `/api/rooms/{code}/join` | POST | Join as a guest with a nickname |
| `/ws/{code}` | WebSocket | Synchronize the lobby, voting, and result |

## Testing

Run the automated test suite:

```bash
python -m unittest discover -s tests -v
```

The tests cover authentication, session revocation, room ownership, account-free guest joining, preset persistence, provider filtering, custom deck validation, duplicate and late votes, wait-for-everyone result timing, randomized ties, and result privacy.

## Deployment

The project is deployed as a Docker web service on Render. The service uses the `moviematch-v2` branch and automatically deploys new commits.

Production environment variables:

```text
ENVIRONMENT=production
APP_SECRET_KEY=<long-random-secret>
TMDB_API_KEY=<private-tmdb-key>
DATABASE_URL=<managed-postgresql-url>
COOKIE_SECURE=true
```

SQLite is suitable for local development. PostgreSQL is recommended on Render so room history and presets survive restarts and future scaling.

## Current limitations

- Active WebSocket room state is stored in application memory.
- A server restart closes active rooms and live connections.
- Multi-server scaling requires a shared real-time store such as Redis.
- TMDB availability and rate limits affect movie discovery.
- Render free services may take additional time to start after inactivity.

## Future scope

- Redis-backed room and WebSocket coordination
- Email verification and password recovery for hosts
- Personal watchlists and favourite movies
- Trailer integration and richer movie details
- Push notifications for room invitations and results
- Progressive Web App support
- Improved analytics for hosts without exposing individual guest votes

## Academic relevance

MovieMatch demonstrates concepts commonly required in a college software project:

- Client-server web architecture
- REST API design
- Real-time communication with WebSockets
- Authentication and authorization
- Relational database design
- Third-party API integration
- Responsive user-interface design
- Automated testing
- Containerization and cloud deployment

Detailed ownership rules, database entities, authentication flows, and scaling guidance are available in [ARCHITECTURE.md](ARCHITECTURE.md).

## Attribution

This product uses the TMDB API but is not endorsed or certified by TMDB.

Movie data and images are provided by [The Movie Database (TMDB)](https://www.themoviedb.org/).
