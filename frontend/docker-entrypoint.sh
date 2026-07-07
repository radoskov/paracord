#!/usr/bin/env sh
# D4/Batch W: dev-server entrypoint. Docker-created paths can be owned by root or by an old container
# UID, so fix ownership while still root, then drop privileges.
#
# node_modules lives in a named volume that shadows the freshly built image. When package-lock.json
# changes (new deps) the volume goes stale, so self-heal on a lock-hash mismatch:
#   - fresh/empty volume or hash mismatch WITH a matching baked copy -> fast restore from the image
#     copy baked at /opt/paracord-frontend (no network);
#   - hash mismatch with NO matching baked copy -> `npm ci` fallback (needs the registry).
# The marker is written LAST so a crash mid-restore just re-heals on the next start.
set -e

NODE_MODULES=/app/frontend/node_modules
MARKER="$NODE_MODULES/.paracord-package-lock.sha256"
BAKED=/opt/paracord-frontend

current_lock_sha="$(sha256sum /app/frontend/package-lock.json | awk '{print $1}')"
installed_lock_sha=""
if [ -f "$MARKER" ]; then
    installed_lock_sha="$(cat "$MARKER")"
fi

healed=""
if [ "$installed_lock_sha" != "$current_lock_sha" ]; then
    mkdir -p "$NODE_MODULES"
    if [ -f "$BAKED/package-lock.sha256" ] && \
       [ "$(cat "$BAKED/package-lock.sha256")" = "$current_lock_sha" ]; then
        echo "frontend: node_modules is stale — restoring dependencies from the baked image copy"
        find "$NODE_MODULES" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
        cp -a "$BAKED/node_modules/." "$NODE_MODULES/"
    else
        echo "frontend: node_modules is stale and no baked copy matches — running npm ci"
        npm ci
    fi
    printf '%s\n' "$current_lock_sha" > "$MARKER"
    healed=1
fi

# A heal rewrites the tree as root, and a fresh named volume inherits root ownership; in both cases
# hand node_modules back to the unprivileged `node` user. (Checking only the top dir is not enough
# after a restore — its ownership can be unchanged while the new contents are root-owned.)
if [ -n "$healed" ] || \
   { [ -d "$NODE_MODULES" ] && [ "$(stat -c %u "$NODE_MODULES")" != "$(id -u node)" ]; }; then
    chown -R node:node "$NODE_MODULES"
fi

if [ -d /app/frontend/dist ] && \
   [ "$(stat -c %u /app/frontend/dist)" != "$(id -u node)" ]; then
    chown -R node:node /app/frontend/dist
fi

exec gosu node "$@"
