# Koom YouTube Music Data API

Separate Railway service for YouTube Music metadata, search, discovery and recommendation-style data. It is **not** the audio streaming/download API.

Powered by `ytmusicapi`, an unofficial library that emulates YouTube Music web requests. It is not supported or endorsed by Google.

## Features

- Search: songs, videos, albums, artists, playlists and more
- Large-result search with `limit` + `page`
- Search suggestions
- Home/discovery rows
- Quick picks / recommendations
- Trending / charts / popular by country
- New-release discovery from dynamic YouTube Music home rows
- Song metadata
- Artist details + full album/release list
- Album details
- Playlist details, all tracks option, related playlists and suggestions
- Radio / quick-play / watch queue
- Related songs
- Lyrics
- Moods and mood playlists
- In-memory TTL cache for faster repeated requests
- Optional API key
- CORS enabled for web frontends

## Railway deployment

Create a **new service** in the same Railway project/account. Upload/push this folder as a separate repository/service. Do not replace the existing audio API service.

Recommended environment variables:

```text
API_KEY=your_private_api_key
YTMUSIC_COUNTRY=IN
CACHE_TTL=120
CACHE_MAX=256
```

`API_KEY` is optional. If set, all endpoints except `/health` require `X-API-Key`.

### Test

```bash
curl https://YOUR-RAILWAY-DOMAIN/health
curl -H "X-API-Key: your_private_api_key" "https://YOUR-RAILWAY-DOMAIN/search?q=Arijit%20Singh&filter=songs&limit=20"
curl -H "X-API-Key: your_private_api_key" "https://YOUR-RAILWAY-DOMAIN/suggestions?q=arijit"
curl -H "X-API-Key: your_private_api_key" "https://YOUR-RAILWAY-DOMAIN/charts?country=IN"
```

Open `/docs` for the interactive Swagger API.

## Search pagination

Example:

```text
/search?q=Arijit%20Singh&filter=songs&limit=50&page=1
/search?q=Arijit%20Singh&filter=songs&limit=50&page=2
/search?q=Arijit%20Singh&filter=songs&limit=100&page=3
```

The API does not impose the normal 20-result default; `ytmusicapi` handles upstream continuation requests for the requested limit. There is no literal guarantee of infinite results because the upstream YouTube Music catalogue/request limits still apply.

## Main endpoints

```text
GET /health
GET /search?q=...
GET /suggestions?q=...
GET /home
GET /quick-picks
GET /recommendations
GET /trending
GET /charts
GET /popular
GET /new-releases
GET /song/{videoId}
GET /song/{videoId}/lyrics
GET /related/{videoId}
GET /radio/{videoId}
GET /quick-play/{videoId}
GET /watch/{videoId}
GET /playlist/{playlistId}
GET /artist/{channelId}
GET /artist/{channelId}/albums
GET /album/{browseId}
GET /album-id/{audioPlaylistId}
GET /lyrics/{browseId}
GET /moods
GET /mood-playlists
GET /clear-cache
```

## Authentication file

Public catalogue/search endpoints can use `YTMusic()` without account authentication. Account-specific functions require authentication. If you later add authenticated features, keep the auth JSON outside Git and mount it securely in Railway; never commit Google/YouTube cookies or auth files to a public repository.

## Important

This service returns **metadata/discovery data**. Use the separate audio API for your streaming layer. The two services can be called independently by the same frontend.
