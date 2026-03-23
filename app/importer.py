import os
import re
import subprocess
import signal
import threading
import uuid
import zipfile
from datetime import datetime
from typing import Optional


MEDIA_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif',
    '.webp', '.bmp', '.tiff', '.tif', '.raw', '.cr2',
    '.nef', '.arw', '.dng', '.mp4', '.mov', '.avi',
    '.mkv', '.wmv', '.3gp', '.m4v', '.mpg', '.mpeg',
    '.mts', '.m2ts', '.webm',
}


def safe_path(filename, base="/import"):
    """Resolve a filename safely within the import directory."""
    full_path = os.path.realpath(os.path.join(base, filename))
    base_real = os.path.realpath(base)
    if not full_path.startswith(base_real + os.sep) and full_path != base_real:
        raise ValueError(f"Path traversal detected: {filename}")
    return full_path


def human_size(size_bytes):
    """Convert bytes to human-readable size."""
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def list_import_files(import_path="/import"):
    """List files and directories in the import mount."""
    items = []
    try:
        for name in sorted(os.listdir(import_path)):
            full = os.path.join(import_path, name)
            if os.path.isfile(full):
                size = os.path.getsize(full)
                items.append({
                    "name": name,
                    "type": "file",
                    "size": size,
                    "size_human": human_size(size),
                })
            elif os.path.isdir(full):
                items.append({
                    "name": name,
                    "type": "directory",
                    "size": None,
                    "size_human": "-",
                })
    except FileNotFoundError:
        pass
    return items


def validate_google_takeout(files, import_path="/import"):
    """Validate Google Photos Takeout zip files."""
    result = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "albums": [],
        "media_count": 0,
        "json_count": 0,
        "total_size": 0,
        "total_size_human": "",
    }

    albums = set()

    for filename in files:
        filepath = safe_path(filename, import_path)

        if not os.path.exists(filepath):
            result["errors"].append(f"File not found: {filename}")
            result["valid"] = False
            continue

        if not zipfile.is_zipfile(filepath):
            result["errors"].append(f"Not a valid zip file: {filename}")
            result["valid"] = False
            continue

        result["total_size"] += os.path.getsize(filepath)

        has_google_photos_dir = False
        entries_checked = 0

        try:
            with zipfile.ZipFile(filepath, 'r') as zf:
                for entry in zf.namelist():
                    if entry.startswith("Takeout/Google Photos/") or \
                       entry.startswith("Takeout/Google Foto") or \
                       entry.startswith("Takeout/Google "):
                        has_google_photos_dir = True

                        parts = entry.split("/")
                        if len(parts) >= 4:
                            album_name = parts[2]
                            if album_name:
                                albums.add(album_name)

                        lower = entry.lower()
                        ext = os.path.splitext(lower)[1]
                        if ext in MEDIA_EXTENSIONS:
                            result["media_count"] += 1
                        elif lower.endswith('.json'):
                            result["json_count"] += 1

                    entries_checked += 1
        except zipfile.BadZipFile:
            result["errors"].append(f"Corrupted zip file: {filename}")
            result["valid"] = False
            continue

        if not has_google_photos_dir and entries_checked > 0:
            result["errors"].append(
                f"{filename}: Missing 'Takeout/Google Photos/' directory structure. "
                "This may not be a Google Photos Takeout archive."
            )
            result["valid"] = False

    if result["media_count"] > 0 and result["json_count"] == 0:
        result["warnings"].append(
            "No JSON metadata files found. Photo dates and album info may be inaccurate."
        )

    if result["json_count"] > 0 and result["json_count"] < result["media_count"] * 0.5:
        result["warnings"].append(
            f"Only {result['json_count']} metadata files found for "
            f"{result['media_count']} media files. Some photos may have missing metadata."
        )

    result["albums"] = sorted(albums)
    result["total_size_human"] = human_size(result["total_size"])
    return result


def validate_icloud_export(files, import_path="/import"):
    """Validate iCloud Photos export directory."""
    result = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "media_count": 0,
        "csv_count": 0,
        "total_size": 0,
        "total_size_human": "",
    }

    for dirname in files:
        dirpath = safe_path(dirname, import_path)

        if os.path.isfile(dirpath):
            # Could be a zip file from iCloud
            if zipfile.is_zipfile(dirpath):
                result["total_size"] += os.path.getsize(dirpath)
                try:
                    with zipfile.ZipFile(dirpath, 'r') as zf:
                        for entry in zf.namelist():
                            ext = os.path.splitext(entry.lower())[1]
                            if ext in MEDIA_EXTENSIONS:
                                result["media_count"] += 1
                            elif ext == '.csv':
                                result["csv_count"] += 1
                except zipfile.BadZipFile:
                    result["errors"].append(f"Corrupted zip file: {dirname}")
                    result["valid"] = False
                continue
            else:
                result["errors"].append(f"Not a directory or zip: {dirname}")
                result["valid"] = False
                continue

        if not os.path.isdir(dirpath):
            result["errors"].append(f"Not found: {dirname}")
            result["valid"] = False
            continue

        for root, _dirs, filenames in os.walk(dirpath):
            for fname in filenames:
                fpath = os.path.join(root, fname)
                ext = os.path.splitext(fname.lower())[1]
                if ext in MEDIA_EXTENSIONS:
                    result["media_count"] += 1
                    try:
                        result["total_size"] += os.path.getsize(fpath)
                    except OSError:
                        pass
                elif ext == '.csv':
                    result["csv_count"] += 1

    if result["media_count"] == 0:
        result["errors"].append("No media files found in the selected files/directory.")
        result["valid"] = False

    if result["csv_count"] == 0 and result["media_count"] > 0:
        result["warnings"].append(
            "No CSV metadata files found. Album information and some dates "
            "may not be imported correctly."
        )

    result["total_size_human"] = human_size(result["total_size"])
    return result


def validate_direct_upload(files, import_path="/import"):
    """Validate directly uploaded files (photos, videos, or ZIPs)."""
    result = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "media_count": 0,
        "zip_count": 0,
        "total_size": 0,
        "total_size_human": "",
    }

    for filename in files:
        filepath = safe_path(filename, import_path)

        if not os.path.exists(filepath):
            result["errors"].append(f"File not found: {filename}")
            result["valid"] = False
            continue

        if os.path.isdir(filepath):
            # Walk directory and count media files (including inside ZIPs)
            for root, _dirs, filenames in os.walk(filepath):
                for fname in filenames:
                    fpath = os.path.join(root, fname)
                    ext = os.path.splitext(fname.lower())[1]
                    if ext in MEDIA_EXTENSIONS:
                        result["media_count"] += 1
                        try:
                            result["total_size"] += os.path.getsize(fpath)
                        except OSError:
                            pass
                    elif ext == '.zip' and zipfile.is_zipfile(fpath):
                        result["zip_count"] += 1
                        try:
                            result["total_size"] += os.path.getsize(fpath)
                            with zipfile.ZipFile(fpath, 'r') as zf:
                                for entry in zf.namelist():
                                    if os.path.splitext(entry.lower())[1] in MEDIA_EXTENSIONS:
                                        result["media_count"] += 1
                        except (zipfile.BadZipFile, OSError):
                            pass
            continue

        file_size = os.path.getsize(filepath)
        result["total_size"] += file_size
        ext = os.path.splitext(filename.lower())[1]

        if ext == '.zip' and zipfile.is_zipfile(filepath):
            result["zip_count"] += 1
            try:
                with zipfile.ZipFile(filepath, 'r') as zf:
                    for entry in zf.namelist():
                        entry_ext = os.path.splitext(entry.lower())[1]
                        if entry_ext in MEDIA_EXTENSIONS:
                            result["media_count"] += 1
            except zipfile.BadZipFile:
                result["errors"].append(f"Corrupted zip file: {filename}")
                result["valid"] = False
        elif ext in MEDIA_EXTENSIONS:
            result["media_count"] += 1
        else:
            result["warnings"].append(f"Unsupported file type: {filename}")

    if result["media_count"] == 0 and not result["errors"]:
        result["errors"].append("No media files found in the selected files.")
        result["valid"] = False

    result["total_size_human"] = human_size(result["total_size"])
    return result


class ImportJob:
    """Represents a single import job."""

    def __init__(self, job_id, config):
        self.job_id = job_id
        self.config = config
        self.status = "pending"
        self.process: Optional[subprocess.Popen] = None
        self.log_lines = []
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "status": self.status,
            "lines_count": len(self.log_lines),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }


class ImportManager:
    """Manages import jobs and their lifecycle."""

    def __init__(self):
        self.jobs = {}
        self._lock = threading.Lock()

    def create_and_start(self, config):
        """Create a new import job and start it."""
        job_id = str(uuid.uuid4())
        job = ImportJob(job_id, config)
        with self._lock:
            self.jobs[job_id] = job
        thread = threading.Thread(target=self._run_job, args=(job,), daemon=True)
        thread.start()
        return job_id

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def cancel_job(self, job_id):
        job = self.jobs.get(job_id)
        if not job or job.status != "running":
            return False
        job.status = "cancelled"
        if job.process:
            try:
                job.process.terminate()
            except OSError:
                pass
        return True

    def _build_command(self, config):
        """Build the immich-go command from configuration."""
        cmd = ["/usr/local/bin/immich-go"]

        # Logging
        cmd.extend(["--log-level", "INFO"])

        cmd.append("upload")

        source_type = config["source_type"]
        if source_type == "google-photos":
            cmd.append("from-google-photos")
        elif source_type == "icloud":
            cmd.append("from-icloud")
        else:
            cmd.append("from-folder")

        cmd.extend(["--server", config["immich_url"]])
        cmd.extend(["--api-key", config["api_key"]])

        # Disable TUI for plain text output
        cmd.append("--no-ui")

        if config.get("dry_run"):
            cmd.append("--dry-run")

        options = config.get("options", {})

        if source_type == "google-photos":
            if not options.get("include_archived", True):
                cmd.append("--include-archived=false")
            if not options.get("include_partner", True):
                cmd.append("--include-partner=false")
            if options.get("include_trashed", False):
                cmd.append("--include-trashed=true")
            if not options.get("sync_albums", True):
                cmd.append("--sync-albums=false")
            if options.get("include_unmatched", False):
                cmd.append("--include-unmatched=true")
        elif source_type == "icloud":
            if options.get("memories", False):
                cmd.append("--memories")
        elif source_type == "direct":
            folder_album = options.get("folder_as_album", "FOLDER")
            if folder_album and folder_album != "NONE":
                cmd.append(f"--folder-as-album={folder_album}")

        # Common options
        if options.get("date_range"):
            cmd.extend(["--date-range", options["date_range"]])

        # Add file paths
        import_base = config.get("import_path", "/import")
        for f in config["files"]:
            cmd.append(safe_path(f, import_base))

        return cmd

    def _extract_zips(self, job):
        """Extract ZIP files in the upload directory for direct uploads."""
        if job.config.get("source_type") != "direct":
            return

        import_base = job.config.get("import_path", "/import")
        for f in job.config["files"]:
            dirpath = safe_path(f, import_base)
            if not os.path.isdir(dirpath):
                job.log_lines.append(f"[PhotoBridge] Not a directory: {dirpath}")
                continue

            all_files = os.listdir(dirpath)
            job.log_lines.append(
                f"[PhotoBridge] Scanning {dirpath}: {len(all_files)} item(s) — {all_files}"
            )

            for name in all_files:
                fpath = os.path.join(dirpath, name)
                if not os.path.isfile(fpath):
                    continue
                if not name.lower().endswith('.zip'):
                    continue
                if not zipfile.is_zipfile(fpath):
                    job.log_lines.append(f"[PhotoBridge] {name} is not a valid ZIP")
                    continue

                job.log_lines.append(f"[PhotoBridge] Extracting {name}...")
                try:
                    with zipfile.ZipFile(fpath, 'r') as zf:
                        members = zf.namelist()
                        job.log_lines.append(
                            f"[PhotoBridge] ZIP contains {len(members)} entries"
                        )
                        zf.extractall(dirpath)
                    os.remove(fpath)
                    after = os.listdir(dirpath)
                    job.log_lines.append(
                        f"[PhotoBridge] Extracted {name} — directory now has: {after}"
                    )
                except Exception as e:
                    job.log_lines.append(f"[PhotoBridge] Failed to extract {name}: {e}")

    def _run_job(self, job):
        """Execute the import in a background thread."""
        job.status = "running"
        job.start_time = datetime.now()

        try:
            self._extract_zips(job)
            cmd = self._build_command(job.config)

            # Log full command with masked API key
            display_cmd = []
            for i, arg in enumerate(cmd):
                if i > 0 and cmd[i - 1] == "--api-key":
                    display_cmd.append("***")
                else:
                    display_cmd.append(arg)
            job.log_lines.append(f"[PhotoBridge] Starting import...")
            job.log_lines.append(f"[PhotoBridge] Command: {' '.join(display_cmd)}")

            # Log files that will be imported
            import_base = job.config.get("import_path", "/import")
            for f in job.config["files"]:
                fpath = safe_path(f, import_base)
                if os.path.isdir(fpath):
                    for root, dirs, files in os.walk(fpath):
                        rel = os.path.relpath(root, fpath)
                        for fn in files:
                            job.log_lines.append(
                                f"[PhotoBridge] File: {rel}/{fn}"
                            )

            job.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            for line in iter(job.process.stdout.readline, ''):
                if job.status == "cancelled":
                    break
                line = line.rstrip('\n\r')
                if line:
                    job.log_lines.append(line)

            job.process.wait(timeout=30)

            if job.status != "cancelled":
                if job.process.returncode == 0:
                    job.status = "completed"
                    job.log_lines.append("[PhotoBridge] Import completed successfully!")
                else:
                    job.status = "failed"
                    job.log_lines.append(
                        f"[PhotoBridge] Import failed with exit code {job.process.returncode}"
                    )

        except Exception as e:
            job.status = "failed"
            job.log_lines.append(f"[PhotoBridge] Error: {str(e)}")

        finally:
            job.end_time = datetime.now()
            if job.status == "cancelled":
                job.log_lines.append("[PhotoBridge] Import cancelled by user.")
