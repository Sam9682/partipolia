#!/usr/bin/env bash
cd ~/workspace/forgejo/partipolia || exit 1
echo "pip: $(python3 -m pip --version 2>&1 | head -1)"
echo "venv module: $(python3 -c 'import venv; print("ok")' 2>&1)"
# try a quick offline check: are argon2/psycopg wheels reachable? (dry, short timeout)
timeout 20 python3 -m pip download --no-deps --dest /tmp/wheeltest argon2-cffi 2>&1 | tail -3
