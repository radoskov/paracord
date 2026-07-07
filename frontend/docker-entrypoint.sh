#!/usr/bin/env sh
# D4: dev-server entrypoint. Docker-created paths can be owned by root or by an old container UID,
# so fix ownership while still root, then drop privileges.
set -e

current_lock_sha="$(sha256sum /app/frontend/package-lock.json | awk '{print $1}')"
installed_lock_sha=""
if [ -f /app/frontend/node_modules/.paracord-package-lock.sha256 ]; then
    installed_lock_sha="$(cat /app/frontend/node_modules/.paracord-package-lock.sha256)"
fi

if [ "$installed_lock_sha" != "$current_lock_sha" ]; then
    if [ -f /opt/paracord-frontend/package-lock.sha256 ] && \
       [ "$(cat /opt/paracord-frontend/package-lock.sha256)" = "$current_lock_sha" ]; then
        find /app/frontend/node_modules -mindepth 1 -maxdepth 1 -exec rm -rf {} +
        cp -a /opt/paracord-frontend/node_modules/. /app/frontend/node_modules/
    else
        npm ci
    fi
    printf '%s\n' "$current_lock_sha" > /app/frontend/node_modules/.paracord-package-lock.sha256
fi

if [ -d /app/frontend/node_modules ] && \
   [ "$(stat -c %u /app/frontend/node_modules)" != "$(id -u node)" ]; then
    chown -R node:node /app/frontend/node_modules
fi

if [ -d /app/frontend/dist ] && \
   [ "$(stat -c %u /app/frontend/dist)" != "$(id -u node)" ]; then
    chown -R node:node /app/frontend/dist
fi

exec gosu node "$@"
