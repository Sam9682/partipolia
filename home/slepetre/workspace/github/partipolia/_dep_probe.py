for m in ["fastapi", "pydantic", "jinja2", "httpx", "pytest_asyncio", "asgi_lifespan"]:
    try:
        __import__(m)
        print("OK", m)
    except Exception as e:
        print("MISSING", m, repr(e))
for m in ["psycopg", "pgvector", "argon2", "structlog"]:
    try:
        __import__(m)
        print("OPT-OK", m)
    except Exception:
        print("OPT-MISSING", m)
