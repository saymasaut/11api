# Downloader API (yt-dlp)

## Endpoint

- `GET /api/v1/downloader/extract?url=<video_page_url>`

Returns metadata and downloadable format URLs for any site supported by [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Server requirements

1. Install Python dependency: `pip install -r requirements.txt` (includes `yt-dlp`).
2. Optional: install **ffmpeg** on the server if you later add server-side processing (the Flutter app merges on-device).
3. Optional: set `YTDLP_COOKIES_FILE` to a Netscape cookies file path for login-gated sites (Instagram, Facebook, etc.).

## Security

- Only `http`/`https` URLs are accepted; private/localhost targets are blocked (SSRF guard).
- Use existing API rate limits in production.

## Errors (`detail` field)

| Code | Meaning |
|------|---------|
| `unsupported_url` | URL not supported or extract failed |
| `private_content` | Login or private video |
| `geo_blocked` | Region blocked |
| `rate_limited` | Too many requests |
