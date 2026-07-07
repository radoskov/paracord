# PaRacORD frontend (Vite + Svelte) image.
#
# Build targets:
#   development  (default) — Vite dev server with HMR, bind-mount overlay
#   builder                — intermediate: compiles the static dist/ bundle
#   production             — nginx serving the compiled static bundle; no Node runtime
#
# docker compose  uses `development` by default via docker-compose.yml.
# docker compose -f docker-compose.yml -f docker-compose.prod.yml  uses `production`.

# ── development: Vite dev server ───────────────────────────────────────────────
FROM node:24-bookworm-slim AS development

# D4: gosu lets the entrypoint drop to the image's built-in non-root `node` user (UID 1000, which
# matches the host developer) after fixing ownership of the root-owned node_modules volume.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci
RUN mkdir -p /opt/paracord-frontend \
    && cp -a node_modules /opt/paracord-frontend/node_modules \
    && sha256sum package-lock.json | awk '{print $1}' > /opt/paracord-frontend/package-lock.sha256

COPY frontend ./
# Baked outside the bind-mounted /app/frontend so it stays stable regardless of the host tree.
COPY frontend/docker-entrypoint.sh /usr/local/bin/paracord-frontend-entrypoint.sh
RUN chmod +x /usr/local/bin/paracord-frontend-entrypoint.sh

EXPOSE 5173
ENTRYPOINT ["/usr/local/bin/paracord-frontend-entrypoint.sh"]
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ── builder: compile static bundle ────────────────────────────────────────────
FROM node:24-bookworm-slim AS builder

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend ./
RUN npm run build

# ── production: nginx serving compiled assets ──────────────────────────────────
FROM nginx:1.27-alpine AS production

COPY --from=builder /app/frontend/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
