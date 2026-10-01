import os
import ssl
import json
import random
import logging
import asyncio
import urllib.request
import urllib.parse
from typing import List, Dict, Optional, Any, Tuple
from app.models import MovieItem, RoomFilter

logger = logging.getLogger(__name__)

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
BASE_URL = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"
BACKDROP_BASE = "https://image.tmdb.org/t/p/w1280"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

GENRES_BY_ID: Dict[int, str] = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
    80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
    14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
    9648: "Mystery", 10749: "Romance", 878: "Sci-Fi",
    10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western"
}

POPULAR_PROVIDERS: Dict[str, List[Dict[str, Any]]] = {
    "IN": [
        {"id": 8, "name": "Netflix"},
        {"id": 119, "name": "Prime Video"},
        {"id": 2336, "name": "JioHotstar"},
        {"id": 122, "name": "Disney+ Hotstar"},
        {"id": 350, "name": "Apple TV+"},
        {"id": 220, "name": "JioCinema"},
        {"id": 232, "name": "Zee5"},
        {"id": 237, "name": "SonyLIV"},
    ],
    "US": [
        {"id": 8, "name": "Netflix"},
        {"id": 9, "name": "Prime Video"},
        {"id": 337, "name": "Disney+"},
        {"id": 1899, "name": "Max"},
        {"id": 15, "name": "Hulu"},
        {"id": 350, "name": "Apple TV+"},
        {"id": 531, "name": "Paramount+"},
        {"id": 386, "name": "Peacock"},
    ],
    "GB": [
        {"id": 8, "name": "Netflix"},
        {"id": 9, "name": "Prime Video"},
        {"id": 337, "name": "Disney+"},
        {"id": 350, "name": "Apple TV+"},
        {"id": 39, "name": "NOW"},
        {"id": 103, "name": "BBC iPlayer"},
    ],
}

# ─── IN-MEMORY GENRE CACHE ─────────────────────────────────────────────────
# Populated at server startup; maps genre_id -> list of MovieItem
_genre_cache: Dict[int, List[MovieItem]] = {}
_cache_ready = False

# ─── LOW-LEVEL TMDB FETCH (urllib, no httpx) ───────────────────────────────
def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl._create_unverified_context()
    return ctx

def _tmdb_get(endpoint: str, params: Dict[str, str]) -> Optional[Dict]:
    """Synchronous urllib call to TMDB. Returns parsed JSON or None on error."""
    params.setdefault("language", "en-US")
    url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"TMDB {endpoint} failed: {e}")
        return None

def _raw_to_movie_item(m: Dict, providers: List[str]) -> Optional[MovieItem]:
    """Convert a raw TMDB discover result dict to a MovieItem."""
    if not m.get("poster_path"):
        return None
    genres = [GENRES_BY_ID.get(gid, "") for gid in m.get("genre_ids", []) if gid in GENRES_BY_ID]
    genres = [g for g in genres if g][:4]
    release_date = m.get("release_date", "")
    return MovieItem(
        id=m["id"],
        title=m["title"],
        poster_path=f"{IMG_BASE}{m['poster_path']}",
        backdrop_path=f"{BACKDROP_BASE}{m['backdrop_path']}" if m.get("backdrop_path") else None,
        release_date=release_date,
        release_year=release_date[:4] if release_date else "",
        vote_average=round(float(m.get("vote_average", 0.0)), 1),
        vote_count=int(m.get("vote_count", 0)),
        overview=m.get("overview", ""),
        genres=genres,
        providers=providers or ["Streaming"],
    )

# ─── STARTUP CACHE POPULATION ──────────────────────────────────────────────
async def warmup_cache():
    """
    Called once at server startup in a background task.
    Fetches top-voted movies for each genre (up to 3 pages) from TMDB,
    stores them in _genre_cache for instant, accurate local genre filtering.
    Uses generous delays + per-page retry with backoff to handle TMDB CDN throttling.
    """
    global _genre_cache, _cache_ready
    logger.info("TMDB cache warmup starting (this runs in background)...")

    loop = asyncio.get_event_loop()

    for genre_id in GENRES_BY_ID.keys():
        genre_name = GENRES_BY_ID[genre_id]
        genre_movies: List[MovieItem] = []

        for page in [1, 2, 3]:
            # Polite delay between every request to avoid CDN rate-limit resets
            await asyncio.sleep(3.0)

            params = {
                "api_key": TMDB_API_KEY,
                "page": str(page),
                "sort_by": "vote_count.desc",
                "with_genres": str(genre_id),
            }

            data = None
            # Retry up to 3 times with increasing backoff on failure
            for attempt in range(3):
                data = await loop.run_in_executor(None, _tmdb_get, "discover/movie", params)
                if data:
                    break
                wait = 5.0 * (attempt + 1)
                logger.warning(f"  [{genre_name} p{page}] attempt {attempt+1} failed, retrying in {wait}s...")
                await asyncio.sleep(wait)

            if data:
                for m in data.get("results", []):
                    item = _raw_to_movie_item(m, [])
                    if item and item.vote_count >= 100 and item.vote_average >= 5.0:
                        genre_movies.append(item)

        # Store per-genre results immediately so partial cache is usable
        _genre_cache[genre_id] = genre_movies
        logger.info(f" Cached {len(genre_movies)} movies for genre '{genre_name}' ({genre_id})")

    _cache_ready = True
    total = sum(len(v) for v in _genre_cache.values())
    logger.info(f"TMDB cache warmup COMPLETE. {total} total entries across {len(_genre_cache)} genres.")

# ─── PUBLIC API ────────────────────────────────────────────────────────────
async def get_genres() -> Dict[int, str]:
    return GENRES_BY_ID

def get_popular_providers_for_region(region: str = "IN") -> List[Dict[str, Any]]:
    reg = (region or "IN").upper()
    return POPULAR_PROVIDERS.get(reg, POPULAR_PROVIDERS["IN"])

async def discover_movies(filters: RoomFilter) -> List[MovieItem]:
    """
    Primary entry point: returns a shuffled deck of `filters.card_count` movies
    that strictly match the requested genres, rating, and year range.

    Strategy:
      1. Try live TMDB /discover with the exact genre filter (fast path).
      2. If live fetch returns enough movies -> filter locally by rating/year -> return.
      3. If live fetch fails or returns too few -> serve from warmup cache (already genre-correct).
      4. Apply rating & year filter locally (no TMDB round-trip for these).
      5. Annotate provider names from the requested provider_ids.
    """
    requested_genre_ids = [int(g) for g in (filters.genre_ids or [])]
    min_rating = float(filters.min_rating or 0)
    year_from = int(filters.year_from) if filters.year_from else None
    year_to = int(filters.year_to) if filters.year_to else None
    card_count = int(filters.card_count or 15)

    region_providers = get_popular_providers_for_region(filters.region)
    pid_to_name = {p["id"]: p["name"] for p in region_providers}
    selected_providers = [pid_to_name[pid] for pid in (filters.provider_ids or []) if pid in pid_to_name]

    def annotate_providers(movie: MovieItem) -> MovieItem:
        movie.providers = selected_providers if selected_providers else ["Streaming"]
        return movie

    def passes_local_filters(m: MovieItem) -> bool:
        if min_rating and m.vote_average < min_rating:
            return False
        if year_from and m.release_year and int(m.release_year) < year_from:
            return False
        if year_to and m.release_year and int(m.release_year) > year_to:
            return False
        return True

    # ── 1. Try live TMDB fetch ────────────────────────────────────────────
    candidates: List[MovieItem] = []
    loop = asyncio.get_event_loop()

    for page in [1, 2]:
        params: Dict[str, str] = {
            "api_key": TMDB_API_KEY,
            "page": str(page),
            "sort_by": "vote_count.desc",
        }
        # Only add with_genres if user selected genres — otherwise fetch all popular
        if requested_genre_ids:
            params["with_genres"] = ",".join(str(g) for g in requested_genre_ids)
        if filters.year_from:
            params["primary_release_date.gte"] = f"{filters.year_from}-01-01"
        if filters.year_to:
            params["primary_release_date.lte"] = f"{filters.year_to}-12-31"

        data = await loop.run_in_executor(None, _tmdb_get, "discover/movie", params)
        if data:
            for m in data.get("results", []):
                item = _raw_to_movie_item(m, selected_providers or ["Streaming"])
                if not item:
                    continue
                # Strict genre check only when genres were explicitly selected
                if requested_genre_ids:
                    movie_gids = set(m.get("genre_ids", []))
                    if not movie_gids.intersection(set(requested_genre_ids)):
                        continue
                if passes_local_filters(item):
                    candidates.append(item)
        if len(candidates) >= card_count:
            break
        await asyncio.sleep(0.5)

    # ── 2. Supplement from genre cache if needed ─────────────────────────
    if len(candidates) < card_count and _cache_ready:
        logger.info(f"Live fetch returned {len(candidates)}, supplementing from cache...")
        cache_pool: List[MovieItem] = []

        if requested_genre_ids:
            # Collect cached movies that match ANY of the selected genres
            seen_ids = {m.id for m in candidates}
            for gid in requested_genre_ids:
                for cm in _genre_cache.get(gid, []):
                    if cm.id not in seen_ids and passes_local_filters(cm):
                        cache_pool.append(cm)
                        seen_ids.add(cm.id)
        else:
            # No genre filter: pool all cached movies
            seen_ids = {m.id for m in candidates}
            for gid, movies in _genre_cache.items():
                for cm in movies:
                    if cm.id not in seen_ids and passes_local_filters(cm):
                        cache_pool.append(cm)
                        seen_ids.add(cm.id)

        random.shuffle(cache_pool)
        for cm in cache_pool:
            annotate_providers(cm)
            candidates.append(cm)
            if len(candidates) >= card_count * 2:  # get 2x then sample
                break

    # ── 3. If cache also not ready yet (fresh boot), do blocking multi-page fetch ─
    if len(candidates) < card_count and not _cache_ready:
        logger.info("Cache not ready yet, doing blocking multi-page fetch...")
        for page in [3, 4]:
            params = {"api_key": TMDB_API_KEY, "page": str(page), "sort_by": "vote_count.desc"}
            if requested_genre_ids:
                params["with_genres"] = ",".join(str(g) for g in requested_genre_ids)
            data = await loop.run_in_executor(None, _tmdb_get, "discover/movie", params)
            if data:
                for m in data.get("results", []):
                    item = _raw_to_movie_item(m, selected_providers or ["Streaming"])
                    if item and passes_local_filters(item):
                        if requested_genre_ids:
                            if not set(m.get("genre_ids", [])).intersection(set(requested_genre_ids)):
                                continue
                        candidates.append(item)
            await asyncio.sleep(0.5)

    # Ensure all have correct providers set
    for m in candidates:
        if not m.providers or m.providers == ["Streaming"]:
            annotate_providers(m)

    # Deduplicate by ID
    seen = set()
    unique = []
    for m in candidates:
        if m.id not in seen:
            seen.add(m.id)
            unique.append(m)

    random.shuffle(unique)
    result = unique[:card_count]
    logger.info(f"discover_movies returning {len(result)} movies (genres={requested_genre_ids}, rating>={min_rating})")
    return result
