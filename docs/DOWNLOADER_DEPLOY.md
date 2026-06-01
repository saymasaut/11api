# Downloader module deployment

The `/api/v1/downloader/*` endpoints use **yt-dlp** (1000+ supported sites) and **ffmpeg** on the server.

Sites are handled by yt-dlp extractors — not custom per-site scrapers. Keep `yt-dlp` updated:

```bash
pip install -U yt-dlp
```

## Requirements

1. **Python package** (from `requirements.txt`):
   ```bash
   pip install yt-dlp==2025.1.26
   ```

2. **ffmpeg** on `PATH` (required for merging video+audio and many HLS/DASH formats):
   ```bash
   ffmpeg -version
   ```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DOWNLOADER_TEMP_DIR` | `./downloader_temp` | Writable directory for job outputs |
| `DOWNLOADER_MAX_FILE_MB` | `2048` | Reject outputs larger than this |
| `DOWNLOADER_JOB_TIMEOUT_SEC` | `3600` | Soft limit for long jobs |
| `DOWNLOADER_FILE_TOKEN_TTL_SEC` | `3600` | Signed download link lifetime |

## Health check

```http
GET /api/v1/downloader/health
```

Returns `yt_dlp_version`, `ffmpeg_available`, and `temp_dir_writable`.

## Cleanup

Completed job folders live under `{DOWNLOADER_TEMP_DIR}/{job_id}/`. Restart or cron:

- Delete directories older than 24h under `downloader_temp/`
- The API also drops in-memory job records after 1 hour (ready/failed/canceled)

## Security

- Only `http`/`https` URLs accepted
- Private/loopback hosts and DNS-resolved private IPs are blocked (SSRF)
- File tokens are HMAC-signed with `SECRET_KEY`
