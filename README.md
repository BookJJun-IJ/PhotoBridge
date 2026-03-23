# PhotoBridge by Yundera

**Google Photos, iCloud & Direct Upload to Immich Importer**

Import your Google Photos, iCloud Photos, or any photos/videos directly into [Immich](https://immich.app/) on your Yundera PCS.

## Features

- **Google Photos Takeout** — Import directly from Takeout ZIP files (no extraction needed)
- **iCloud Photos** — Import from iCloud export directories or ZIPs
- **Direct Upload** — Drag & drop photos, videos, or ZIP files from your browser
  - Chunked upload for large files (Cloudflare compatible)
  - Pause / Resume upload
  - Remove individual files before import
  - Auto-extract ZIP files before import
  - Create Immich albums from folder names inside ZIPs
- **Validation** — Checks your export files before importing
- **Album preservation** — Recreates albums in Immich (Google Photos albums, folder names)
- **Metadata handling** — Preserves dates, locations, and descriptions
- **Dry-run mode** — Preview what will be imported without uploading
- **Real-time logs** — Watch the import progress live in your browser
- **Duplicate detection** — Skips files already in your Immich library

## Quick Start

### 1. Install

Install **PhotoBridge** from the Yundera AppStore, or run with Docker:

```yaml
services:
  photobridge:
    image: ghcr.io/bookjjun-ij/photobridge:latest
    container_name: photobridge
    restart: unless-stopped
    user: "0:0"
    expose:
      - "80"
    volumes:
      - /DATA/Gallery/Import:/import
    environment:
      TZ: Asia/Seoul
      IMMICH_URL: http://immich:80
      IMPORT_PATH: /import
    networks:
      - pcs

networks:
  pcs:
    external: true
```

### 2. Prepare your photos

**Google Photos:**
1. Go to [Google Takeout](https://takeout.google.com/)
2. Deselect all, then select only **Google Photos**
3. Choose `.zip` format
4. Download the ZIP file(s)

**iCloud:**
1. Go to [icloud.com](https://www.icloud.com/) → Photos
2. Select all photos
3. Download as ZIP

**Direct Upload:**
No preparation needed — just drag & drop files in the browser.

### 3. Import

1. Open PhotoBridge and enter your Immich API key
2. Choose your source: **Google Photos**, **iCloud**, or **Direct Upload**
3. Select files (or drag & drop for Direct Upload)
4. Click **Validate**, then **Start Import**
5. Watch the progress in real-time

## Architecture

```
Browser → Cloudflare Tunnel → Caddy → nginx → gunicorn → Flask
                                                            ↓
                                                        immich-go → Immich
```

- **Flask + Gunicorn** — Web server and API
- **nginx** — Reverse proxy, static file serving
- **immich-go** — CLI tool that handles the actual upload to Immich
- **SSE** — Server-Sent Events for real-time log streaming

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IMMICH_URL` | `http://immich:80` | Immich server URL |
| `IMPORT_PATH` | `/import` | Path inside container where exports are mounted |
| `TZ` | `UTC` | Timezone |

## Building from Source

```bash
# Build for current platform
docker build -t photobridge .

# Build multi-arch (amd64 + arm64)
docker buildx build --platform linux/amd64,linux/arm64 -t photobridge:latest --push .
```

## Credits

- [immich-go](https://github.com/simulot/immich-go) by simulot — the CLI tool that powers the import
- [Immich](https://immich.app/) — self-hosted photo management

## License

MIT License — see [LICENSE](LICENSE)