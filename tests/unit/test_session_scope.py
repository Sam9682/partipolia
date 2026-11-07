"""Regression guard: the DB session must be committed BEFORE the response is sent.

FastAPI runs the code after ``yield`` of a dependency *after* the response has been
sent unless ``scope="function"`` is requested. With the default scope the commit
happens after the client already has its response, so an immediate follow-up request
(e.g. login right after registration, or reading back a saved simulation) can miss the
write. This was observed on the running application (28 misses out of 40 writes) and
fixed by using ``Depends(get_session, scope="function")`` everywhere.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
PLAIN_USE = re.compile(r"Depends\(\s*get_session\s*\)")


def test_every_session_dependency_uses_function_scope() -> None:
    offenders = [
        f"{path.relative_to(APP_DIR.parent)}:{number}"
        for path in APP_DIR.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if PLAIN_USE.search(line) and not line.lstrip().startswith(("#", "``", "La ", "reçoivent"))
    ]
    assert not offenders, (
        "Use Depends(get_session, scope='function') so the commit happens before the "
        f"response is sent: {offenders}"
    )


def test_function_scope_is_documented_on_get_session() -> None:
    source = (APP_DIR / "core" / "database.py").read_text(encoding="utf-8")
    assert 'scope="function"' in source
