#!/usr/bin/env bash
cd ~/workspace/forgejo/partipolia || exit 1
echo "python: $(python3 --version 2>&1)"
echo "uv: $(command -v uv || echo none)"
echo "ruff: $(command -v ruff || echo none)"
echo "mypy: $(command -v mypy || echo none)"
echo "pytest: $(command -v pytest || echo none)"
echo "--- imports ---"
python3 -c 'import argon2, jose, redis, sqlalchemy, pydantic; print("core ok")' 2>&1 | tail -1
python3 -c 'import psycopg; print("psycopg ok")' 2>&1 | tail -1
python3 -c 'import pytest, pytest_asyncio; print("pytest ok")' 2>&1 | tail -1
python3 -c 'import bcrypt; print("bcrypt ok")' 2>&1 | tail -1
