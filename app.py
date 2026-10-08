import asyncio
import copy
import hashlib
import json
import os
import time
from collections import OrderedDict
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from ytmusicapi import YTMusic

APP_NAME = "Koom YouTube Music Data API"
VERSION = "1.0.0"
API_KEY = os.getenv("API_KEY", "").strip()
AUTH_FILE = os.getenv("YTMUSIC_AUTH_FILE", "").strip()
DEFAULT_COUNTRY = os.getenv("YTMUSIC_COUNTRY", "IN").upper()
CACHE_TTL = int(os.getenv("CACHE_TTL", "120"))
CACHE_MAX = int(os.getenv("CACHE_MAX", "256"))

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="Unofficial YouTube Music metadata/search API powered by ytmusicapi.",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
_cache_lock = asyncio.Lock()
_yt: Optional[YTMusic] = None
_yt_lock = asyncio.Lock()


def require_api_key(x_api_key: Optional[str] = Header(default=None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


async def cached(key: str, fn):
    now = time.monotonic()
    async with _cache_lock:
        item = _cache.get(key)
        if item and now - item[0] < CACHE_TTL:
            _cache.move_to_end(key)
            return copy.deepcopy(item[1])
        if item:
            _cache.pop(key, None)

    value = await asyncio.to_thread(fn)
    async with _cache_lock:
        _cache[key] = (time.monotonic(), copy.deepcopy(value))
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return value


async def yt():
    global _yt
    if _yt is not None:
        return _yt
    async with _yt_lock:
        if _yt is None:
            # Public catalogue/search works without authentication.
            # For private/account features, point YTMUSIC_AUTH_FILE at a secure
            # Railway-mounted auth JSON file. Never commit that file to Git.
            _yt = YTMusic(AUTH_FILE) if AUTH_FILE else YTMusic()
    return _yt


def cache_key(prefix: str, *parts: Any) -> str:
    raw = json.dumps([prefix, *parts], sort_keys=True, default=str)
    return prefix + ":" + hashlib.sha256(raw.encode()).hexdigest()


def ok(data: Any, **meta):
    return {"status": "ok", **meta, "data": data}


def clean_home_rows(rows: list[dict[str, Any]], keywords: tuple[str, ...]):
    out = []
    for row in rows:
        title = str(row.get("title", ""))
        if any(k in title.lower() for k in keywords):
            out.append(row)
    return out


@app.get("/", dependencies=[Depends(require_api_key)])
async def root():
    return {
        "name": APP_NAME,
        "version": VERSION,
        "status": "ok",
        "docs": "/docs",
        "engine": "ytmusicapi",
        "country": DEFAULT_COUNTRY,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": APP_NAME, "version": VERSION}


@app.get("/search", dependencies=[Depends(require_api_key)])
async def search(
    q: str = Query(..., min_length=1, max_length=300),
    filter: Optional[str] = Query(default=None),
    scope: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=1000),
    page: int = Query(default=1, ge=1, le=100),
    ignore_spelling: bool = False,
):
    allowed = {
        "songs", "videos", "albums", "artists", "playlists",
        "community_playlists", "featured_playlists", "profiles",
        "podcasts", "episodes",
    }
    if filter and filter not in allowed:
        raise HTTPException(400, f"Invalid filter. Use one of: {', '.join(sorted(allowed))}")
    if scope and scope not in {"library", "uploads"}:
        raise HTTPException(400, "scope must be library or uploads")

    # ytmusicapi handles continuation requests internally for the requested limit.
    # page is supported by requesting enough results and slicing, so clients can
    # paginate without the API imposing a fixed 20-result ceiling.
    requested = min(limit * page, 1000)
    key = cache_key("search", q, filter, scope, requested, ignore_spelling)
    y = await yt()
    results = await cached(
        key,
        lambda: y.search(q, filter=filter, scope=scope, limit=requested, ignore_spelling=ignore_spelling),
    )
    start = (page - 1) * limit
    page_items = results[start:start + limit]
    return ok(
        page_items,
        query=q,
        filter=filter,
        scope=scope,
        page=page,
        limit=limit,
        total_returned=len(page_items),
        has_more=len(results) > start + len(page_items),
        note="Search depth is limited by YouTube Music/ytmusicapi upstream availability; this API does not impose a 20-result fixed limit.",
    )


@app.get("/suggestions", dependencies=[Depends(require_api_key)])
async def suggestions(
    q: str = Query(..., min_length=1, max_length=200),
    detailed: bool = False,
):
    y = await yt()
    data = await cached(
        cache_key("suggestions", q, detailed),
        lambda: y.get_search_suggestions(q, detailed_runs=detailed),
    )
    return ok(data, query=q, detailed=detailed)


@app.get("/home", dependencies=[Depends(require_api_key)])
async def home(limit: int = Query(default=10, ge=1, le=30)):
    y = await yt()
    data = await cached(cache_key("home", limit), lambda: y.get_home(limit=limit))
    return ok(data, rows=len(data))


@app.get("/quick-picks", dependencies=[Depends(require_api_key)])
async def quick_picks(limit: int = Query(default=10, ge=1, le=30)):
    y = await yt()
    rows = await cached(cache_key("home", limit), lambda: y.get_home(limit=limit))
    picks = clean_home_rows(rows, ("quick pick", "quick picks", "listen again", "your mix", "for you"))
    if not picks:
        # Home content is dynamic; if no explicitly named quick-pick row exists,
        # return the first suggestion rows rather than inventing data.
        picks = rows[:min(3, len(rows))]
    return ok(picks, rows=len(picks), source="home")


@app.get("/recommendations", dependencies=[Depends(require_api_key)])
async def recommendations(limit: int = Query(default=10, ge=1, le=30)):
    y = await yt()
    data = await cached(cache_key("home", limit), lambda: y.get_home(limit=limit))
    return ok(data, source="youtube_music_home")


@app.get("/trending", dependencies=[Depends(require_api_key)])
@app.get("/charts", dependencies=[Depends(require_api_key)])
async def charts(country: str = Query(default=DEFAULT_COUNTRY, min_length=2, max_length=2)):
    country = country.upper()
    y = await yt()
    data = await cached(cache_key("charts", country), lambda: y.get_charts(country=country))
    return ok(data, country=country)


@app.get("/popular", dependencies=[Depends(require_api_key)])
async def popular(country: str = Query(default=DEFAULT_COUNTRY, min_length=2, max_length=2)):
    country = country.upper()
    y = await yt()
    data = await cached(cache_key("charts", country), lambda: y.get_charts(country=country))
    return ok(data, country=country, source="youtube_music_charts")


@app.get("/new-releases", dependencies=[Depends(require_api_key)])
async def new_releases(limit: int = Query(default=15, ge=1, le=50)):
    y = await yt()
    rows = await cached(cache_key("home", 20), lambda: y.get_home(limit=20))
    releases = clean_home_rows(rows, ("new release", "new releases", "new music", "new albums", "recent"))
    # Home titles vary by account/region. Return matched rows; fallback keeps the
    # raw home rows so the endpoint remains useful instead of fabricating releases.
    if releases:
        for row in releases:
            row["contents"] = row.get("contents", [])[:limit]
    else:
        releases = rows[:3]
    return ok(releases, source="youtube_music_home", matched=bool(clean_home_rows(rows, ("new release", "new releases", "new music", "new albums", "recent"))))


@app.get("/song/{video_id}", dependencies=[Depends(require_api_key)])
async def song(video_id: str):
    y = await yt()
    data = await cached(cache_key("song", video_id), lambda: y.get_song(video_id))
    return ok(data, video_id=video_id)


@app.get("/related/{video_id}", dependencies=[Depends(require_api_key)])
async def related(video_id: str):
    y = await yt()
    watch = await cached(cache_key("watch", video_id, 25, False), lambda: y.get_watch_playlist(videoId=video_id, limit=25, radio=False))
    browse_id = watch.get("related")
    if not browse_id:
        return ok([], video_id=video_id, source="watch_playlist", message="No related browse id was returned")
    data = await cached(cache_key("related", browse_id), lambda: y.get_song_related(browse_id))
    return ok(data, video_id=video_id, browse_id=browse_id)


@app.get("/radio/{video_id}", dependencies=[Depends(require_api_key)])
@app.get("/quick-play/{video_id}", dependencies=[Depends(require_api_key)])
async def radio(video_id: str, limit: int = Query(default=25, ge=1, le=100), shuffle: bool = False):
    y = await yt()
    data = await cached(
        cache_key("radio", video_id, limit, shuffle),
        lambda: y.get_watch_playlist(videoId=video_id, limit=limit, radio=True, shuffle=False),
    )
    return ok(data, video_id=video_id, radio=True)


@app.get("/playlist/{playlist_id}", dependencies=[Depends(require_api_key)])
async def playlist(
    playlist_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    all: bool = False,
    related: bool = False,
    suggestions_limit: int = Query(default=0, ge=0, le=50),
):
    y = await yt()
    effective = None if all else limit
    data = await cached(
        cache_key("playlist", playlist_id, effective, related, suggestions_limit),
        lambda: y.get_playlist(playlist_id, limit=effective, related=related, suggestions_limit=suggestions_limit),
    )
    return ok(data, playlist_id=playlist_id, all=all)


@app.get("/artist/{channel_id}", dependencies=[Depends(require_api_key)])
async def artist(channel_id: str):
    y = await yt()
    data = await cached(cache_key("artist", channel_id), lambda: y.get_artist(channel_id))
    return ok(data, channel_id=channel_id)


@app.get("/artist/{channel_id}/albums", dependencies=[Depends(require_api_key)])
async def artist_albums(
    channel_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    order: Optional[str] = Query(default=None),
):
    if order and order not in {"Recency", "Popularity", "Alphabetical order"}:
        raise HTTPException(400, "order must be Recency, Popularity, or Alphabetical order")
    y = await yt()
    artist_data = await cached(cache_key("artist", channel_id), lambda: y.get_artist(channel_id))
    albums = artist_data.get("albums") or {}
    browse_id = albums.get("browseId")
    params = albums.get("params")
    if not browse_id or not params:
        return ok([], channel_id=channel_id, message="This artist did not expose an album continuation")
    data = await cached(
        cache_key("artist_albums", channel_id, params, limit, order),
        lambda: y.get_artist_albums(channel_id, params, limit=limit, order=order),
    )
    return ok(data, channel_id=channel_id, count=len(data))


@app.get("/album/{browse_id}", dependencies=[Depends(require_api_key)])
async def album(browse_id: str):
    y = await yt()
    data = await cached(cache_key("album", browse_id), lambda: y.get_album(browse_id))
    return ok(data, browse_id=browse_id)


@app.get("/album-id/{audio_playlist_id}", dependencies=[Depends(require_api_key)])
async def album_id(audio_playlist_id: str):
    y = await yt()
    data = await cached(cache_key("album_id", audio_playlist_id), lambda: y.get_album_browse_id(audio_playlist_id))
    return ok({"browseId": data}, audio_playlist_id=audio_playlist_id)


@app.get("/moods", dependencies=[Depends(require_api_key)])
async def moods():
    y = await yt()
    data = await cached("moods", lambda: y.get_mood_categories())
    return ok(data)


@app.get("/mood-playlists", dependencies=[Depends(require_api_key)])
async def mood_playlists():
    y = await yt()
    data = await cached("mood_playlists", lambda: y.get_mood_playlists())
    return ok(data)


@app.get("/lyrics/{browse_id}", dependencies=[Depends(require_api_key)])
async def lyrics(browse_id: str, timestamps: bool = False):
    y = await yt()
    data = await cached(cache_key("lyrics", browse_id, timestamps), lambda: y.get_lyrics(browse_id, timestamps=timestamps))
    return ok(data, browse_id=browse_id, timestamps=timestamps)


@app.get("/song/{video_id}/lyrics", dependencies=[Depends(require_api_key)])
async def song_lyrics(video_id: str, timestamps: bool = False):
    y = await yt()
    watch = await cached(cache_key("watch", video_id, 1, False), lambda: y.get_watch_playlist(videoId=video_id, limit=1, radio=False))
    browse_id = watch.get("lyrics")
    if not browse_id:
        return ok(None, video_id=video_id, message="Lyrics are not available for this track")
    data = await cached(cache_key("lyrics", browse_id, timestamps), lambda: y.get_lyrics(browse_id, timestamps=timestamps))
    return ok(data, video_id=video_id, browse_id=browse_id, timestamps=timestamps)


@app.get("/watch/{video_id}", dependencies=[Depends(require_api_key)])
async def watch(video_id: str, limit: int = Query(default=25, ge=1, le=100), radio: bool = False):
    y = await yt()
    data = await cached(
        cache_key("watch", video_id, limit, radio),
        lambda: y.get_watch_playlist(videoId=video_id, limit=limit, radio=radio),
    )
    return ok(data, video_id=video_id, radio=radio)


@app.get("/clear-cache", dependencies=[Depends(require_api_key)])
async def clear_cache():
    if not API_KEY:
        raise HTTPException(403, "Set API_KEY before enabling cache management")
    async with _cache_lock:
        count = len(_cache)
        _cache.clear()
    return {"status": "ok", "cleared": count}


@app.exception_handler(Exception)
async def generic_error(request, exc):
    # Keep upstream details useful for API clients, but do not expose tracebacks.
    return JSONResponse(
        status_code=502,
        content={"status": "error", "error": str(exc) or "Upstream request failed"},
    )
