"""Pages SSR des consultations : rendu des gabarits, accessibilité et gardes d'accès."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.schemas.consultation import (
    REPRESENTATIVITY_NOTICE,
    ConsultationPublic,
    ConsultationResults,
    OptionCount,
    QuestionPublic,
    QuestionResult,
)
from app.web.consultations import (
    STATUS_LABELS,
    TYPE_LABELS,
    _build_payload,
    _read_answers,
    _result_views,
)
from app.web.public import templates
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _consultation(status: str = "open") -> ConsultationPublic:
    return ConsultationPublic(
        id=1,
        title="Budget participatif",
        description="Une description suffisamment longue.",
        status=status,  # type: ignore[arg-type]
        opens_at=NOW,
        closes_at=NOW.replace(year=2031),
        questions=[
            QuestionPublic(
                id=10,
                position=1,
                question_type="single_choice",
                prompt="Votre choix ?",
                required=True,
                options=["Oui", "Non"],
            ),
            QuestionPublic(
                id=11, position=2, question_type="scale", prompt="Votre avis ?", required=False
            ),
            QuestionPublic(
                id=12, position=3, question_type="text", prompt="Un commentaire ?", required=False
            ),
        ],
    )


def _render(name: str, **context: object) -> str:
    base = {"nav_items": [], "current_path": "/"}
    return templates.env.get_template(name).render(**base, **context)


def test_detail_form_has_labels_live_region_and_keeps_input() -> None:
    html = _render(
        "public/consultation_detail.html",
        consultation=_consultation(),
        status_labels=STATUS_LABELS,
        type_labels=TYPE_LABELS,
        authenticated=True,
        values={10: "Non", 12: "Ma saisie <b>"},
        errors={10: "Réponse invalide"},
        banner="Certaines réponses sont invalides.",
        info=None,
        notice=REPRESENTATIVITY_NOTICE,
    )
    assert "<fieldset" in html and "<legend" in html
    assert 'for="q-12"' in html
    assert 'aria-live="polite"' in html
    assert "Ma saisie &lt;b&gt;" in html
    assert 'value="Non" checked' in html
    assert "non représentatifs" in html


def test_results_show_notice_and_equivalent_table() -> None:
    results = ConsultationResults(
        consultation_id=1,
        respondent_count=30,
        questions=[
            QuestionResult(
                question_id=10,
                position=1,
                prompt="Votre choix ?",
                question_type="single_choice",
                answer_count=30,
                counts=[OptionCount(option="Oui", count=20), OptionCount(option="Non", count=10)],
            ),
            QuestionResult(
                question_id=11,
                position=2,
                prompt="Votre avis ?",
                question_type="scale",
                answer_count=0,
                withheld=True,
            ),
        ],
    )
    html = _render(
        "public/consultation_results.html",
        consultation=_consultation("closed"),
        results=results,
        questions=_result_views(results),
        type_labels=TYPE_LABELS,
        notice=results.notice,
    )
    assert "non représentatifs" in html
    assert "<table" in html and "<caption" in html
    assert "retenu" in html
    assert "�" not in html


def test_admin_payload_builder_reports_problems_and_skips_empty_slots() -> None:
    values = {
        "title": "Titre valide",
        "description": "Une description suffisamment longue.",
        "opens_at": "2030-01-01T10:00",
        "closes_at": "2030-02-01T10:00",
        "questions": [
            {
                "prompt": "Votre choix ?",
                "question_type": "single_choice",
                "required": True,
                "options_text": "Oui\nNon",
            },
            {"prompt": "", "question_type": "text", "required": False, "options_text": ""},
        ],
    }
    payload, problems = _build_payload(values)
    assert problems == [] and payload is not None and len(payload.questions) == 1
    values["questions"][0]["options_text"] = "Oui"
    payload, problems = _build_payload(values)
    assert payload is None and problems


def test_read_answers_ignores_empty_and_flags_bad_scale() -> None:
    from starlette.datastructures import FormData

    answers, values, errors = _read_answers(
        _consultation(), FormData([("q_10", "Oui"), ("q_11", "abc"), ("q_12", "  ")])
    )
    assert [a.question_id for a in answers] == [10]
    assert 11 in errors and values[10] == "Oui"


def test_admin_pages_redirect_anonymous_to_login() -> None:
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    response = client.get("/admin/consultations")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
