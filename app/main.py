import json
import os
import threading
import time
from datetime import datetime

import requests
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from werkzeug.utils import secure_filename

from app.importer import (
    ImportManager,
    list_import_files,
    validate_direct_upload,
    validate_google_takeout,
    validate_icloud_export,
)

base_dir = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__,
            static_folder=os.path.join(base_dir, "static"),
            template_folder=os.path.join(base_dir, "templates"))
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

IMMICH_URL = os.environ.get("IMMICH_URL", "http://immich:80")
IMPORT_PATH = os.environ.get("IMPORT_PATH", "/import")

import_manager = ImportManager()
merge_status = {}  # key: "{upload_id}/{filename}" → status dict
_merge_lock = threading.Lock()
_merging_keys = set()


@app.route("/health")
def health():
    return "PhotoBridge OK", 200


@app.route("/")
def index():
    resp = app.make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/api/config")
def get_config():
    return jsonify({
        "immich_url": IMMICH_URL,
        "import_path": IMPORT_PATH,
    })


@app.route("/api/config/test", methods=["POST"])
def test_connection():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    immich_url = data.get("immich_url", "").rstrip("/")
    api_key = data.get("api_key", "")

    if not immich_url or not api_key:
        return jsonify({"error": "Both immich_url and api_key are required"}), 400

    if not immich_url.startswith(("http://", "https://")):
        immich_url = f"http://{immich_url}"

    try:
        resp = requests.get(
            f"{immich_url}/api/users/me",
            headers={"x-api-key": api_key},
            timeout=10,
        )
        if resp.status_code == 200:
            user_data = resp.json()
            return jsonify({
                "success": True,
                "user": user_data.get("name", user_data.get("email", "Unknown")),
                "email": user_data.get("email", ""),
            })
        elif resp.status_code == 401:
            return jsonify({"success": False, "error": "Invalid API key"}), 401
        else:
            return jsonify({
                "success": False,
                "error": f"Immich returned status {resp.status_code}",
            }), 502
    except requests.ConnectionError:
        return jsonify({
            "success": False,
            "error": f"Cannot connect to {immich_url}. Is Immich running?",
        }), 502
    except requests.Timeout:
        return jsonify({
            "success": False,
            "error": "Connection timed out",
        }), 504
    except requests.RequestException as e:
        return jsonify({
            "success": False,
            "error": f"Request error: {str(e)}",
        }), 502


@app.route("/api/files")
def get_files():
    files = list_import_files(IMPORT_PATH)
    return jsonify({"files": files})


@app.route("/api/upload/delete", methods=["POST"])
def delete_uploaded_file():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    upload_id = data.get("upload_id", "")
    filename = data.get("filename", "")
    if not upload_id or not filename:
        return jsonify({"error": "upload_id and filename are required"}), 400

    from app.importer import safe_path
    try:
        upload_dir = safe_path(upload_id, IMPORT_PATH)
        filepath = os.path.join(upload_dir, secure_filename(filename))
        if os.path.isfile(filepath):
            os.remove(filepath)
            return jsonify({"success": True})
        return jsonify({"error": "File not found"}), 404
    except (ValueError, OSError) as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/upload", methods=["POST"])
def upload_files():
    if "files" not in request.files:
        return jsonify({"error": "No files provided"}), 400

    uploaded = request.files.getlist("files")
    if not uploaded:
        return jsonify({"error": "No files selected"}), 400

    from app.importer import human_size

    # Reuse existing upload dir or create new one
    upload_id = request.form.get("upload_id", "")
    if not upload_id:
        upload_id = "uploads/" + datetime.now().strftime("%Y%m%d_%H%M%S")

    upload_dir = os.path.join(IMPORT_PATH, upload_id)
    os.makedirs(upload_dir, exist_ok=True)

    saved_files = []
    for f in uploaded:
        if not f.filename:
            continue
        filename = secure_filename(f.filename)
        if not filename:
            continue
        filepath = os.path.join(upload_dir, filename)
        f.save(filepath)
        size = os.path.getsize(filepath)
        saved_files.append({
            "name": filename,
            "size": size,
            "size_human": human_size(size),
        })

    if not saved_files:
        return jsonify({"error": "No valid files uploaded"}), 400

    return jsonify({
        "success": True,
        "upload_dir": upload_id,
        "files": saved_files,
        "count": len(saved_files),
    })


@app.route("/api/upload/chunk", methods=["POST"])
def upload_chunk():
    chunk = request.files.get("chunk")
    if not chunk:
        return jsonify({"error": "No chunk provided"}), 400

    filename = request.form.get("filename", "")
    chunk_index = int(request.form.get("chunk_index", 0))
    total_chunks = int(request.form.get("total_chunks", 1))
    upload_id = request.form.get("upload_id", "")

    if not upload_id:
        upload_id = "uploads/" + datetime.now().strftime("%Y%m%d_%H%M%S")

    safe_name = secure_filename(filename)
    if not safe_name:
        return jsonify({"error": "Invalid filename"}), 400

    upload_dir = os.path.join(IMPORT_PATH, upload_id)
    chunks_dir = os.path.join(upload_dir, ".chunks", safe_name)
    os.makedirs(chunks_dir, exist_ok=True)

    # Save this chunk
    chunk_path = os.path.join(chunks_dir, f"{chunk_index:06d}")
    chunk.save(chunk_path)

    # If all chunks received, start background merge (with lock for parallel safety)
    received = len([f for f in os.listdir(chunks_dir) if not f.startswith(".")])
    if received >= total_chunks:
        key = f"{upload_id}/{safe_name}"
        should_merge = False
        with _merge_lock:
            if key not in _merging_keys:
                _merging_keys.add(key)
                should_merge = True
        if should_merge:
            merge_status[key] = {"status": "merging"}
            threading.Thread(
                target=_merge_chunks,
                args=(upload_dir, safe_name, total_chunks, chunks_dir, key),
                daemon=True,
            ).start()
        return jsonify({
            "complete": False,
            "merging": True,
            "upload_dir": upload_id,
        })

    return jsonify({
        "complete": False,
        "upload_dir": upload_id,
        "chunk_index": chunk_index,
    })


def _merge_chunks(upload_dir, safe_name, total_chunks, chunks_dir, key):
    from app.importer import human_size
    try:
        final_path = os.path.join(upload_dir, safe_name)
        with open(final_path, "wb") as out:
            for i in range(total_chunks):
                cp = os.path.join(chunks_dir, f"{i:06d}")
                with open(cp, "rb") as cf:
                    while True:
                        data = cf.read(8 * 1024 * 1024)
                        if not data:
                            break
                        out.write(data)
                os.remove(cp)

        try:
            os.rmdir(chunks_dir)
            chunks_parent = os.path.dirname(chunks_dir)
            if os.path.isdir(chunks_parent) and not os.listdir(chunks_parent):
                os.rmdir(chunks_parent)
        except OSError:
            pass

        size = os.path.getsize(final_path)
        merge_status[key] = {
            "status": "done",
            "file": {
                "name": safe_name,
                "size": size,
                "size_human": human_size(size),
            },
        }
    except Exception as e:
        merge_status[key] = {"status": "error", "error": str(e)}
    finally:
        with _merge_lock:
            _merging_keys.discard(key)


@app.route("/api/upload/merge-status")
def get_merge_status():
    upload_id = request.args.get("upload_id", "")
    filename = request.args.get("filename", "")
    key = f"{upload_id}/{secure_filename(filename)}"
    status = merge_status.get(key)
    if not status:
        return jsonify({"status": "not_found"}), 404
    if status["status"] == "done":
        merge_status.pop(key, None)
    return jsonify(status)


@app.route("/api/validate", methods=["POST"])
def validate():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    source_type = data.get("source_type")
    files = data.get("files", [])

    if not source_type:
        return jsonify({"error": "source_type is required"}), 400
    if not files:
        return jsonify({"error": "At least one file must be selected"}), 400
    if source_type not in ("google-photos", "icloud", "direct"):
        return jsonify({"error": "source_type must be 'google-photos', 'icloud', or 'direct'"}), 400

    if source_type == "google-photos":
        result = validate_google_takeout(files, IMPORT_PATH)
    elif source_type == "icloud":
        result = validate_icloud_export(files, IMPORT_PATH)
    else:
        result = validate_direct_upload(files, IMPORT_PATH)

    return jsonify(result)


@app.route("/api/import/start", methods=["POST"])
def start_import():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    required = ["immich_url", "api_key", "source_type", "files"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    if data["source_type"] not in ("google-photos", "icloud", "direct"):
        return jsonify({"error": "source_type must be 'google-photos', 'icloud', or 'direct'"}), 400

    config = {
        "immich_url": data["immich_url"].rstrip("/"),
        "api_key": data["api_key"],
        "source_type": data["source_type"],
        "files": data["files"],
        "dry_run": data.get("dry_run", False),
        "import_path": IMPORT_PATH,
        "options": data.get("options", {}),
    }

    job_id = import_manager.create_and_start(config)
    return jsonify({"job_id": job_id, "status": "started"})


@app.route("/api/import/<job_id>/stream")
def stream_logs(job_id):
    def generate():
        job = import_manager.get_job(job_id)
        if not job:
            yield f"event: error\ndata: {json.dumps({'message': 'Job not found'})}\n\n"
            return

        last_index = 0

        while True:
            current_lines = job.log_lines[last_index:]
            for line in current_lines:
                data = json.dumps({"line": line})
                yield f"event: log\ndata: {data}\n\n"
                last_index += 1

            status_data = json.dumps(job.to_dict())
            yield f"event: status\ndata: {status_data}\n\n"

            if job.status in ("completed", "failed", "cancelled"):
                duration = ""
                if job.end_time and job.start_time:
                    delta = job.end_time - job.start_time
                    minutes, seconds = divmod(int(delta.total_seconds()), 60)
                    hours, minutes = divmod(minutes, 60)
                    if hours:
                        duration = f"{hours}h {minutes}m {seconds}s"
                    elif minutes:
                        duration = f"{minutes}m {seconds}s"
                    else:
                        duration = f"{seconds}s"

                done_data = json.dumps({
                    "status": job.status,
                    "duration": duration,
                    "total_lines": len(job.log_lines),
                })
                yield f"event: done\ndata: {done_data}\n\n"
                return

            time.sleep(0.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/import/<job_id>/cancel", methods=["POST"])
def cancel_import(job_id):
    success = import_manager.cancel_job(job_id)
    if success:
        return jsonify({"status": "cancelling"})
    job = import_manager.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"error": f"Cannot cancel job in '{job.status}' state"}), 400


@app.route("/api/import/<job_id>/status")
def job_status(job_id):
    job = import_manager.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job.to_dict())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("FLASK_PORT", 80)), threaded=True)
