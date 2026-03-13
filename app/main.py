import json
import os
import time
from datetime import datetime

import requests
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from app.importer import (
    ImportManager,
    list_import_files,
    validate_google_takeout,
    validate_icloud_export,
)

base_dir = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__,
            static_folder=os.path.join(base_dir, "static"),
            template_folder=os.path.join(base_dir, "templates"))
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

IMMICH_URL = os.environ.get("IMMICH_URL", "http://immich:3000")
IMPORT_PATH = os.environ.get("IMPORT_PATH", "/import")

import_manager = ImportManager()


@app.route("/health")
def health():
    return "PhotoBridge OK", 200


@app.route("/")
def index():
    return render_template("index.html")


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


@app.route("/api/files")
def get_files():
    files = list_import_files(IMPORT_PATH)
    return jsonify({"files": files})


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
    if source_type not in ("google-photos", "icloud"):
        return jsonify({"error": "source_type must be 'google-photos' or 'icloud'"}), 400

    if source_type == "google-photos":
        result = validate_google_takeout(files, IMPORT_PATH)
    else:
        result = validate_icloud_export(files, IMPORT_PATH)

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

    if data["source_type"] not in ("google-photos", "icloud"):
        return jsonify({"error": "source_type must be 'google-photos' or 'icloud'"}), 400

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
