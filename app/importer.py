import os
import subprocess
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

    def _detect_source(self, config):
        """Auto-detect if files contain a Google Takeout structure.

        Checks ZIP contents and directory structure for
        'Takeout/Google Photos' (or localized variants).
        """
        import_base = config.get("import_path", "/import")
        for f in config["files"]:
            fpath = safe_path(f, import_base)

            # Check ZIP files directly
            if os.path.isfile(fpath) and zipfile.is_zipfile(fpath):
                if self._is_takeout_zip(fpath):
                    return "google-photos"
                continue

            if not os.path.isdir(fpath):
                continue

            # Check ZIPs inside the directory
            for name in os.listdir(fpath):
                zpath = os.path.join(fpath, name)
                if name.lower().endswith('.zip') and os.path.isfile(zpath) \
                        and zipfile.is_zipfile(zpath):
                    if self._is_takeout_zip(zpath):
                        return "google-photos"

            # Check extracted directory structure
            for root, dirs, _files in os.walk(fpath):
                depth = root.replace(fpath, "").count(os.sep)
                if depth > 3:
                    dirs.clear()
                    continue
                if os.path.basename(root) == "Takeout":
                    for d in dirs:
                        if d.startswith("Google Photo") or \
                           d.startswith("Google Foto"):
                            return "google-photos"
        return "folder"

    @staticmethod
    def _is_takeout_zip(filepath):
        """Check if a ZIP file contains Google Takeout structure."""
        try:
            with zipfile.ZipFile(filepath, 'r') as zf:
                for entry in zf.namelist()[:200]:
                    if entry.startswith("Takeout/Google Photo") or \
                       entry.startswith("Takeout/Google Foto"):
                        return True
        except (zipfile.BadZipFile, OSError):
            pass
        return False

    def _build_command(self, config, detected_source="folder"):
        """Build the immich-go command from configuration."""
        cmd = ["/usr/local/bin/immich-go"]

        # Logging
        cmd.extend(["--log-level", "INFO"])

        cmd.append("upload")
        if detected_source == "google-photos":
            cmd.append("from-google-photos")
        else:
            cmd.append("from-folder")

        cmd.extend(["--server", config["immich_url"]])
        cmd.extend(["--api-key", config["api_key"]])

        # Disable TUI for plain text output
        cmd.append("--no-ui")

        if config.get("dry_run"):
            cmd.append("--dry-run")

        options = config.get("options", {})

        if detected_source != "google-photos":
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
        """Extract ZIP files in the import directory before importing."""

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
                        total_members = len(members)

                        # Check if ZIP has a common top-level folder
                        top_dirs = set()
                        has_root_files = False
                        for m in members:
                            parts = m.strip('/').split('/')
                            if len(parts) == 1 and not m.endswith('/'):
                                has_root_files = True
                            elif len(parts) > 1:
                                top_dirs.add(parts[0])

                        # If files are at root (no common folder), create
                        # a subfolder named after the ZIP file
                        if has_root_files or len(top_dirs) > 1:
                            zip_stem = os.path.splitext(name)[0]
                            extract_dir = os.path.join(dirpath, zip_stem)
                            os.makedirs(extract_dir, exist_ok=True)
                            job.log_lines.append(
                                f"[PhotoBridge] ZIP has no single root folder, extracting to '{zip_stem}/'"
                            )
                        else:
                            extract_dir = dirpath

                        job.log_lines.append(
                            f"[PhotoBridge] ZIP contains {total_members} entries, extracting..."
                        )
                        for idx, member in enumerate(members, 1):
                            zf.extract(member, extract_dir)
                            if total_members >= 20 and idx % max(1, total_members // 5) == 0:
                                pct = round(idx / total_members * 100)
                                job.log_lines.append(
                                    f"[PhotoBridge] Extracting... {idx}/{total_members} ({pct}%)"
                                )
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
            detected = self._detect_source(job.config)
            if detected != "google-photos":
                self._extract_zips(job)
            if detected == "google-photos":
                job.log_lines.append(
                    "[PhotoBridge] Detected Google Photos Takeout structure "
                    "— using google-photos importer for metadata preservation"
                )
            cmd = self._build_command(job.config, detected)

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
