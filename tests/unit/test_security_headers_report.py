"""Tests du script ``tools/security_headers_report.py`` (tâche 13.7)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "tools" / "security_headers_report.py"
_spec = importlib.util.spec_from_file_location("security_headers_report", _PATH)
assert _spec and _spec.loader
shr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = shr
_spec.loader.exec_module(shr)


def test_select_headers_filters_and_redacts_cookie() -> None:
    raw = [
        ("content-type", "text/html"),
        ("x-frame-options", "DENY"),
        ("Set-Cookie", "access_token=secret; HttpOnly; Secure; SameSite=lax"),
    ]
    assert shr.select_security_headers(raw) == [
        ("X-Frame-Options", "DENY"),
        ("Set-Cookie", "access_token=<masqué>; HttpOnly; Secure; SameSite=lax"),
    ]


def test_merge_section_replaces_only_marked_block() -> None:
    doc = f"# R\n\n{shr.BEGIN_MARKER}\nancien\n{shr.END_MARKER}\n\n## Dérogations\nX\n"
    caps = [shr.Capture("Page d'accueil", "GET", "/", 200, [("X-Frame-Options", "DENY")])]
    merged = shr.merge_section(doc, shr.render_section(caps, "https://h", "2026-11-07"))
    assert "ancien" not in merged
    assert "| `X-Frame-Options` | `DENY` |" in merged
    assert merged.endswith("## Dérogations\nX\n")


def test_merge_section_inserts_before_derogations_without_markers() -> None:
    doc = "# R\n\n## Dérogations\nX\n"
    merged = shr.merge_section(doc, shr.render_section([], "https://h", "d"))
    assert merged.index(shr.BEGIN_MARKER) < merged.index("## Dérogations")


def test_template_lists_ten_domains() -> None:
    assert len(shr.DOMAINS) == 10
    text = shr.report_template("2026-11-07")
    assert all(name in text for name in shr.DOMAINS)
    assert "SEC-001" in text
