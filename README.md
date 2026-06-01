# AppHub API (Backend)

FastAPI scraper and streaming API for AppHub, including the **yt-dlp downloader** module.

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) on `PATH` (required for merged/HLS downloads)
- `yt-dlp` (installed via `requirements.txt`)

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS

pip install -r requirements.txt
cp .env.example .env       # optional

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API docs: `http://localhost:8000/docs`
- Health: `GET /api/v1/downloader/health`

## Downloader endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/downloader/health` | yt-dlp / ffmpeg / temp dir status |
| GET | `/api/v1/downloader/extract?url=` | List formats for a URL |
| GET | `/api/v1/downloader/resolve?url=&format_id=` | Direct URL or `needs_job` |
| POST | `/api/v1/downloader/jobs` | Server-side download + merge |
| GET | `/api/v1/downloader/jobs/{job_id}` | Job progress |
| DELETE | `/api/v1/downloader/jobs/{job_id}` | Cancel job |
| GET | `/api/v1/downloader/files/{token}` | Download merged file |

Errors return JSON:

```json
{
  "status": "error",
  "error_code": "VIDEO_UNAVAILABLE",
  "message": "…",
  "status_code": 404
}
```

See [docs/DOWNLOADER_DEPLOY.md](docs/DOWNLOADER_DEPLOY.md) for production setup.

## Docker

```bash
docker build -t apphub-api .
docker run -p 8000:8000 --env-file .env apphub-api
```

## Git remote

This folder is its own repository:

```text
origin  https://github.com/milon4999/test.git
```

Push from `backend/` only (not the parent `apphub3` root unless you use a monorepo layout).
