# Panel Stack

Local comic downloader, catalog, and reader for a personal library.

Panel Stack is intentionally undeployed for now. It runs as a local Vite + FastAPI app, stores catalog data in SQLite, and keeps downloaded archives in `~/Documents/panelstack-downloads` by default.

## Quick Start

```bash
npm install
npm run dev
```

The dev launcher starts both services:

- Web UI: `http://127.0.0.1:5173`
- API: `http://127.0.0.1:8000`

Use another frontend port with:

```bash
npm run dev -- --port 5174
```

## Downloader

`comics.py` can download from GetComics posts, search titles, supported mirror links, or direct archive URLs.

Install the downloader dependencies first:

```bash
uv venv
uv pip install -r requirements.txt
```

If you do not use `uv`, the standard Python flow is:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Interactive terminal flow:

```bash
python3 comics.py --tui
```

Direct command:

```bash
python3 comics.py 'https://getcomics.org/dc/absolute-batman-17-2026/'
python3 comics.py 'Batman - Detective Comics Vol. 1 - Mercy of the Father (TPB)'
```

Useful options:

```bash
python3 comics.py --choose '<getcomics-post-url>'
python3 comics.py --host pixeldrain.com '<getcomics-post-url>'
python3 comics.py --dry-run '<getcomics-post-url>'
python3 comics.py --no-extract '<getcomics-post-url>'
```

Supported automated mirrors are best-effort. Some hosts still require browser-only, premium, or anti-automation flows.

## App Shape

- `backend/`: FastAPI, SQLite, SQLAlchemy, local ingest, reading-path catalog, archive page streaming
- `frontend/`: React + Vite library UI, All/Search, detail pages, and image viewer
- `comics.py`: standalone downloader TUI/CLI
- `scripts/dev.py`: combined local dev launcher
- `backend/data/curation/reading_paths.json`: checked-in seed catalog

## Hostineer Deployment

The hosted app runs FastAPI under Passenger through an ASGI-to-WSGI adapter. Build and deploy to `/panels` with:

```bash
APP_PASSWORD_HASH='<pbkdf2 hash>' ./deploy_panels.sh
```

The password hash is written to a remote `.env` on the first deploy and is not tracked by git. Passenger uses the remote virtualenv created from Python 3.11.11 because Hostineer's current Passenger WSGI loader imports Python's removed `imp` module and cannot start under Python 3.12.

Generated data is ignored by git: local app settings, local SQLite databases, cached provider data, virtualenvs, `node_modules/`, and frontend build output.
