# PhotoBridge by Yundera

**A Google Photos & iCloud to Immich Importer**

Import your Google Photos or iCloud Photos archive directly into your Yundera server.

## What it does

PhotoBridge provides a simple web interface to migrate your photos from Google Photos or iCloud into [Immich](https://immich.app/) on your Yundera PCS. It wraps the [immich-go](https://github.com/simulot/immich-go) tool with a clean UI that handles validation, configuration, and real-time progress tracking.

### Features

- **Google Photos Takeout** — Import directly from Takeout ZIP files (no extraction needed)
- **iCloud Photos** — Import from iCloud export directories or ZIPs
- **Validation** — Checks your export files before importing
- **Album preservation** — Recreates your Google Photos albums in Immich
- **Metadata handling** — Preserves dates, locations, and descriptions via JSON sidecars
- **Dry-run mode** — Preview what will be imported without uploading
- **Real-time logs** — Watch the import progress live in your browser
- **Duplicate detection** — Skips files already in your Immich library

## Quick Start (Yundera PCS)

### 1. Prepare your export

**Google Photos:**
1. Go to [Google Takeout](https://takeout.google.com/)
2. Deselect all, then select only **Google Photos**
3. Choose `.zip` format, max 8 GB per file
4. Download the ZIP file(s)

**iCloud:**
1. Go to [icloud.com](https://www.icloud.com/) → Photos
2. Select all photos (Ctrl+A / Cmd+A)
3. Download as ZIP

### 2. Upload to your PCS

Place your Takeout ZIP files in `/DATA/Gallery/Import/` on your PCS using the **Files** app or **Samba**.

### 3. Run PhotoBridge

```bash
docker compose up -d
```

Then open `http://your-pcs:3100` in your browser.

### 4. Import

1. Enter your Immich API key (get it from Immich → Account Settings → API Keys)
2. Select your Takeout ZIP files
3. Click **Validate**, then **Start Import**
4. Watch the progress in real-time

## Docker Compose

```yaml
version: "3.8"

services:
  photobridge:
    image: yundera/photobridge:latest
    container_name: photobridge
    restart: unless-stopped
    ports:
      - "3100:5000"
    volumes:
      - /DATA/Gallery/Import:/import:ro
    environment:
      - IMMICH_URL=http://immich:3000
    networks:
      - pcs

networks:
  pcs:
    external: true
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IMMICH_URL` | `http://immich:3000` | Immich server URL (internal Docker network) |
| `IMPORT_PATH` | `/import` | Path inside container where exports are mounted |

## Building from Source

```bash
# Build for current platform
docker build -t yundera/photobridge .

# Build multi-arch (amd64 + arm64)
docker buildx build --platform linux/amd64,linux/arm64 -t yundera/photobridge:latest --push .
```

## How It Works

1. **You** export your photos from Google Photos (Takeout) or iCloud
2. **You** place the ZIP files on your PCS
3. **PhotoBridge** validates the export structure
4. **immich-go** handles the actual upload to Immich, preserving albums and metadata
5. **Immich** detects duplicates automatically

## Credits

- [immich-go](https://github.com/simulot/immich-go) by simulot — the CLI tool that powers the import
- [Immich](https://immich.app/) — the self-hosted photo management solution
- [PixelUnion](https://pixelunion.eu/) — inspiration for the migration workflow

## License

MIT License — see [LICENSE](LICENSE)
