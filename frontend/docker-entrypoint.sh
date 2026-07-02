#!/usr/bin/env sh
# D4: dev-server entrypoint. The node_modules named volume is created root-owned, so the non-root
# `node` user can't write Vite's caches to it until we fix ownership. Do it here — while still root —
# then drop privileges. Guarded so the recursive chown effectively runs only on first start.
set -e

if [ -d /app/frontend/node_modules ] && \
   [ "$(stat -c %u /app/frontend/node_modules)" != "$(id -u node)" ]; then
    chown -R node:node /app/frontend/node_modules
fi

exec gosu node "$@"
