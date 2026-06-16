# Preview Image Fallback System Design

**Date:** 2026-06-16  
**Author:** Claude (with user approval)  
**Status:** Approved

## Context

Currently, game preview images are embedded as base64 strings in `conf.json` files within downloaded game archives. This has several issues:

1. **Missing previews:** Some games don't include `conf.json` or have empty/invalid `preview` fields
2. **Large JSON files:** Base64 encoding makes conf.json unnecessarily large
3. **No updates:** Once a game is downloaded, preview images can't be updated

This design adds a fallback system that fetches preview images from the community API when they're missing or invalid in local game files, with intelligent caching and background validation.

## Goals

- Load game lists instantly with cached preview images
- Fetch missing previews from community API without blocking UI
- Keep cached images fresh through HTTP validation (ETag/If-Modified-Since)
- Handle network failures gracefully with retry logic and placeholders
- Minimize API requests through smart caching

## Non-Goals

- Replacing the existing base64 preview system (remains as primary source)
- Real-time preview updates (24-hour cache expiry is acceptable)
- Bandwidth optimization beyond standard HTTP caching

## Architecture Overview

The system consists of four main components:

### 1. Preview Cache Manager (`preview_cache.py`)

Handles reading/writing cached images and metadata.

**Cache directory structure:**
```
~/.dartsnut/preview_cache/
├── {game_id}.png          # Cached preview image
└── {game_id}.meta.json    # {"etag": "...", "last_modified": "...", "fetch_time": 123456}
```

**Key functions:**
- `get_cached_preview(game_id) -> Optional[bytearray]` - Returns cached image if valid
- `save_cached_preview(game_id, image_data, etag, last_modified)` - Saves image + metadata
- `get_cache_metadata(game_id) -> Optional[dict]` - Returns validation headers
- `is_cache_expired(game_id, max_age_hours=24) -> bool` - Checks cache freshness

### 2. Community API Client (`community_api.py`)

Fetches game metadata and preview images from remote community API.

**Configuration file: `~/.dartsnut/community_api.conf`**
```json
{
  "api_base_url": "https://api.dartsnut.com",
  "image_base_url": "https://cdn.dartsnut.com",
  "timeout_seconds": 10,
  "cache_expiry_hours": 24,
  "retry_intervals_seconds": [30, 120, 600]
}
```

Created with defaults if missing. User can override by editing directly.

**Key functions:**
- `fetch_game_metadata(game_id) -> dict` - GET `/api/v1/games/{game_id}/metadata`
- `fetch_preview_image(image_url, etag=None, last_modified=None) -> (status, data, headers)`
  - Supports conditional requests (If-None-Match, If-Modified-Since)
  - Returns: `(200, image_bytes, {etag, last_modified})` or `(304, None, None)`
- `load_config() -> dict` - Loads config with defaults
- `save_config(config)` - Persists config changes

**API endpoints used:**
- `GET /api/v1/games/{game_id}/metadata` - Returns `{"id", "name", "main_cover", "preview_urls": [...]}`
- Image URLs constructed from: `{image_base_url}/{preview_path}`

**HTTP features:**
- Connection pooling via `requests.Session`
- User-Agent: `dartsnut-rpi/{version}`
- Configurable timeout (default 10s)
- Proper error handling for 404, 429, 5xx

### 3. Background Validation Worker (`validation_worker.py`)

Thread-based worker that validates cached images and retries failed fetches.

**Worker architecture:**
- `ValidationWorker` class managing a `ThreadPoolExecutor` (max 2 workers)
- Priority queue: `queue.PriorityQueue` with task priorities
- Task types:
  - `FETCH_MISSING (priority 1)`: No cache exists, user waiting
  - `VALIDATE_EXPIRED (priority 2)`: Cache expired, needs revalidation
  - `VALIDATE_FRESH (priority 3)`: Proactive validation on startup

**Worker lifecycle:**
```python
# Initialize on app startup
worker = ValidationWorker(max_workers=2)

# Submit tasks
worker.submit(game_id, priority=FETCH_MISSING, callback=on_preview_updated)

# Graceful shutdown
worker.shutdown(wait=True, timeout=5)
```

**Task execution flow:**
1. Pull task from priority queue
2. Get cached metadata (etag, last_modified)
3. Fetch from API with conditional headers
4. Handle response:
   - 304: Update fetch_time, keep image
   - 200: Save new image + metadata
   - Error: Schedule retry
5. Call callback if image changed

### 4. Modified Game Loading (`game_lifecycle.py`)

Update `load_game_list()` to integrate cache-first fallback.

**New flow:**
1. Read `apps/{game_id}/conf.json`
2. Check if `preview` array has valid base64 strings
3. **If valid:** Decode and return (existing behavior)
4. **If missing/invalid:**
   - Call `preview_cache.get_cached_preview(game_id)`
   - **Cache hit + fresh:** Return cached image
   - **Cache hit + expired:** Return cached image + schedule background validation
   - **Cache miss:** Return placeholder + schedule background fetch

## Data Flow

### Loading a game's preview (cache-first)

```
load_game_list()
  ↓
Read apps/{game_id}/conf.json
  ↓
Has valid preview base64? ──YES──→ Decode and return (current behavior)
  ↓ NO
Check cache: preview_cache.get_cached_preview(game_id)
  ↓
Cache hit? ──YES──→ Expired? ──NO──→ Return cached image
  ↓              ↓ YES
  NO            Return cached + schedule VALIDATE_EXPIRED
  ↓
Return placeholder + schedule FETCH_MISSING
  ↓
validation_worker.submit(game_id, priority=...)
```

### Background validation/fetch

```
Worker pulls task from queue
  ↓
Get cached metadata (etag, last_modified)
  ↓
HTTP GET with conditional headers:
  If-None-Match: {etag}
  If-Modified-Since: {last_modified}
  ↓
Response:
  304 Not Modified ──→ Update fetch_time, keep image
  200 OK ──→ Save new image + metadata
  Error ──→ Retry with exponential backoff (30s, 2m, 10m)
  ↓
Call callback: on_preview_updated(game_id, preview_data)
  ↓
UI refreshes if game visible
```

## Error Handling & Retry Strategy

### Network errors
(Connection timeout, DNS failure, connection refused)

- Retry with exponential backoff: 30s → 2m → 10m
- Keep placeholder with game name + "Retrying..." hint
- After exhausting retries, schedule next attempt on game list reload

### HTTP errors

- **404 Not Found:** Don't retry, use permanent placeholder (game has no preview)
- **429 Rate Limited:** Respect Retry-After header, or wait 5 minutes
- **5xx Server Errors:** Retry with standard backoff
- **401/403 Auth errors:** Log warning, don't retry (config issue)

### Malformed responses
(Invalid image data, corrupt PNG)

- Log error with details
- Don't cache the bad response
- Retry once after 2 minutes (maybe temporary CDN issue)

### Placeholder generation

Create a simple 128x160 black frame with white text using PIL:
- Top: Game name (truncated if needed, centered)
- Bottom: Status hint ("Loading preview..." or "Preview unavailable")
- Convert to same RGB bytearray format as normal previews
- Generated on-demand, not cached

Function: `generate_placeholder_preview(game_name, status_hint) -> bytearray`

## Configuration

### Config file: `~/.dartsnut/community_api.conf`

```json
{
  "api_base_url": "https://api.dartsnut.com",
  "image_base_url": "https://cdn.dartsnut.com",
  "timeout_seconds": 10,
  "cache_expiry_hours": 24,
  "retry_intervals_seconds": [30, 120, 600]
}
```

### Game identification

Games must have a `community_id` or `id` field in `conf.json` to map to community API.

**Fallback strategy if ID missing:**
1. Try to match by exact name via API search endpoint: `GET /api/v1/games/search?name={name}`
2. If multiple matches or no match, log warning and skip preview fetch
3. Consider prompting user to add community_id to conf.json

## Integration Points

### Startup
```python
# In game_lifecycle.py module initialization
validation_worker = ValidationWorker(max_workers=2)
validation_worker.start()

# Register callback for UI updates
validation_worker.register_callback(on_preview_updated)
```

### Game list loading
```python
# Modified load_game_list() in game_lifecycle.py
for conf in game_configs:
    preview = _decode_game_preview_frames(conf.get("preview"), conf["name"])
    
    if not preview or preview == [blank_frame]:
        # Fallback to cache/API
        game_id = conf.get("community_id") or conf.get("id")
        cached = preview_cache.get_cached_preview(game_id)
        
        if cached and not preview_cache.is_cache_expired(game_id):
            preview = cached
        else:
            preview = generate_placeholder_preview(conf["name"], "Loading...")
            priority = FETCH_MISSING if not cached else VALIDATE_EXPIRED
            validation_worker.submit(game_id, priority=priority)
    
    conf["preview"] = preview
```

### Shutdown
```python
# In app exit handler
validation_worker.shutdown(wait=True, timeout=5)
```

### UI notification
```python
def on_preview_updated(game_id, preview_data):
    """Called when background worker completes fetch/validation."""
    # Update in-memory game list
    game = find_game_by_id(game_id)
    if game:
        game["preview"] = preview_data
        # Trigger display refresh if game currently visible
        if is_game_visible(game_id):
            refresh_game_display(game_id)
```

## Logging

All operations log to existing logger with appropriate levels:

- **Info:** Cache hits, successful fetches, 304 responses, cache validation
- **Warning:** Retries, rate limits, expired cache served during outage, missing community_id
- **Error:** Exhausted retries, malformed data, config issues, API auth failures

Log format includes game_id for traceability:
```python
logger.info(f"[Preview] Cache hit for game {game_id}")
logger.warning(f"[Preview] Retrying fetch for game {game_id} (attempt 2/3)")
logger.error(f"[Preview] Failed to fetch preview for game {game_id}: {error}")
```

## Testing Strategy

### Unit tests
- `test_preview_cache.py`: Cache read/write, metadata handling, expiry logic
- `test_community_api.py`: API client, conditional requests, error handling
- `test_validation_worker.py`: Task queue, priority handling, retry logic
- `test_placeholder_generation.py`: Text rendering, format conversion

### Integration tests
- Mock API server returning 200, 304, 404, 5xx
- Test full flow: cache miss → fetch → cache → reload → 304
- Test failure scenarios: network timeout → retry → eventual success
- Test concurrent fetches for multiple games

### Manual testing
- Delete cache, load game list (should fetch from API)
- Disconnect network, load game list (should show placeholders + retry)
- Modify cached image timestamp to force expiry (should revalidate)

## Performance Considerations

- **Memory:** Cached images stored on disk, only loaded when needed
- **Startup time:** No blocking operations, validation runs in background
- **API load:** Max 2 concurrent requests, respects rate limits
- **Cache size:** No automatic cleanup initially (can add LRU eviction later if needed)

## Future Enhancements

(Not in scope for initial implementation)

- LRU cache eviction when cache directory exceeds size limit
- Proactive prefetch for recently installed games
- Support for multiple preview images per game (carousel)
- Bandwidth optimization: progressive JPEG, WebP support
- Cache statistics and monitoring dashboard
