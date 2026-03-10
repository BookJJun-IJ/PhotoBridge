FROM python:3.12-slim

ARG TARGETARCH
ARG IMMICH_GO_VERSION=0.31.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN ARCH="" && \
    case "${TARGETARCH}" in \
      "amd64") ARCH="x86_64" ;; \
      "arm64") ARCH="arm64" ;; \
      *) ARCH="x86_64" ;; \
    esac && \
    curl -fSL "https://github.com/simulot/immich-go/releases/download/v${IMMICH_GO_VERSION}/immich-go_Linux_${ARCH}.tar.gz" \
      -o /tmp/immich-go.tar.gz && \
    tar -xzf /tmp/immich-go.tar.gz -C /usr/local/bin/ immich-go && \
    chmod +x /usr/local/bin/immich-go && \
    rm /tmp/immich-go.tar.gz

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

RUN mkdir -p /import

EXPOSE 5000

ENV IMMICH_URL=http://immich:3000
ENV IMPORT_PATH=/import

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--worker-class", "gthread", "--threads", "4", "--timeout", "86400", "--access-logfile", "-", "app.main:app"]
