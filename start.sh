#!/bin/sh
set -e

echo "[PhotoBridge] Starting gunicorn on 127.0.0.1:8000..."
gunicorn \
    --bind 127.0.0.1:8000 \
    --worker-class gthread \
    --threads 4 \
    --timeout 86400 \
    --access-logfile - \
    app.main:app &

echo "[PhotoBridge] Starting nginx on 0.0.0.0:80..."
exec nginx -g 'daemon off;'
