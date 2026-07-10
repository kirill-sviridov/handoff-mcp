FROM python:3.12-slim

# git powers the optional multi-device sync layer (handoff-sync)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# The vault lives outside the image; mount a volume at /data to persist memory.
ENV HANDOFF_VAULT=/data/vault
VOLUME /data

ENTRYPOINT ["handoff-mcp"]
