"""Service des résultats de Consultation (Exigences 11.1 à 11.7, 11.9 à 11.12).

* :meth:`ConsultationResultsService.build_results` (pur) agrège les réponses :
  décomptes, distribution 1..5 et moyenne à 2 décimales, textes ``approved``
  seulement, retenue sous 20 répondants ou sous 20 réponses par Question ;
* :meth:`ConsultationResultsService.results` : 404 si la consultation n'est pas
  clôturée, cache Redis de 30 s (échec ouvert) ;
* :func:`write_csv` / :func:`read_csv` : export ``question,option_ou_valeur,decompte``
  bijectif, avec échappement des préfixes de formule et de l'apostrophe ;
* :meth:`ConsultationResultsService.export_csv` : réservé à ``content_admin`` et
  ``super_admin`` ; 422 si non clôturée ou retenue.

Les résultats portent toujours la mention « participants volontaires, résultats
non représentatifs ».
"""

from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.models.consultation import (
    Consultation,
    ConsultationAnswer,
    ConsultationQuestion,
    ConsultationResponse,
)
from app.models.roles import Role
from app.schemas.consultation import (
    REPRESENTATIVITY_NOTICE,
    ConsultationResults,
    CsvRow,
    OptionCount,
    QuestionResult,
)
from app.services.consultation_service import (
    ConsultationNotFoundError,
    ConsultationValidationError,
    effective_status,
    utcnow,
)

logger = logging.getLogger(__name__)

MIN_RESPONDENTS: Final = 20
MIN_ANSWERS_PER_QUESTION: Final = 20
CACHE_TTL_SECONDS: Final = 30
CACHE_KEY_PREFIX: Final = "consultation:results:"
SCALE_VALUES: Final = (1, 2, 3, 4, 5)
CSV_HEADER: Final = ("question", "option_ou_valeur", "decompte")
FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")
ESCAPE_CHAR: Final = "'"
EXPORT_ROLES: Final = frozenset({Role.CONTENT_ADMIN.value, Role.SUPER_ADMIN.value})


class ConsultationResultsForbiddenError(PermissionError):
    """Rôle insuffisant pour l'export CSV (⇒ 403)."""


# --------------------------------------------------------------------------- #
# Aide au CSV (bijectif)                                                       #
# --------------------------------------------------------------------------- #
def _escape_cell(value: str) -> str:
    if value.startswith((*FORMULA_PREFIXES, ESCAPE_CHAR)):
        return ESCAPE_CHAR + value
    return value


def _unescape_cell(value: str) -> str:
    return value[1:] if value.startswith(ESCAPE_CHAR) else value


def write_csv(results: ConsultationResults) -> str:
    """Sérialise les décomptes publiables en CSV (UTF-8, ``\\r\\n``).

    Les Questions retenues n'ont aucune ligne. Un texte libre approuvé figure comme
    ligne ``(question, texte, 1)``.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    if not results.withheld:
        for q in results.questions:
            if q.withheld:
                continue
            if q.question_type == "text":
                for text in q.texts or []:
                    writer.writerow([_escape_cell(q.prompt), _escape_cell(text), 1])
            else:
                for item in q.counts or []:
                    writer.writerow([_escape_cell(q.prompt), _escape_cell(item.option), item.count])
    return buffer.getvalue()


def read_csv(text: str) -> list[CsvRow]:
    """Relit un CSV produit par :func:`write_csv` (retire l'apostrophe d'échappement)."""
    reader = csv.reader(io.StringIO(text, newline=""))
    rows = list(reader)
    if not rows:
        return []
    if tuple(rows[0]) != CSV_HEADER:
        raise ValueError("en-tête CSV inattendu")
    parsed: list[CsvRow] = []
    for row in rows[1:]:
        if not row:
            continue
        if len(row) != 3:
            raise ValueError("ligne CSV invalide")
        parsed.append(
            CsvRow(
                question=_unescape_cell(row[0]),
                option_ou_valeur=_unescape_cell(row[1]),
                decompte=int(row[2]),
            )
        )
    return parsed


# --------------------------------------------------------------------------- #
# Agrégation pure                                                              #
# --------------------------------------------------------------------------- #
def _mean(values: Sequence[int]) -> float:
    mean = Decimal(sum(values)) / Decimal(len(values))
    return float(mean.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _answers_of(response: Any) -> Iterable[Any]:
    """Une réponse est soit un itérable de réponses unitaires, soit porte ``.answers``."""
    answers = getattr(response, "answers", None)
    result: Iterable[Any] = answers if answers is not None else response
    return result


def _aggregate_question(question: Any, answers: Sequence[Any]) -> QuestionResult:
    qtype = question.question_type
    base = {
        "question_id": question.id,
        "position": question.position,
        "prompt": question.prompt,
        "question_type": qtype,
    }
    counts: list[OptionCount] | None = None
    mean: float | None = None
    texts: list[str] | None = None

    if qtype in ("single_choice", "multiple_choice"):
        options = [str(o) for o in (question.options or [])]
        tally = dict.fromkeys(options, 0)
        answer_count = 0
        for a in answers:
            selected = {str(o) for o in (a.selected_options or []) if str(o) in tally}
            if qtype == "single_choice" and len(selected) != 1:
                continue
            if not selected:
                continue
            answer_count += 1
            for option in selected:
                tally[option] += 1
        counts = [OptionCount(option=o, count=tally[o]) for o in options]
    elif qtype == "scale":
        values = [a.scale_value for a in answers if a.scale_value in SCALE_VALUES]
        answer_count = len(values)
        counts = [OptionCount(option=str(v), count=values.count(v)) for v in SCALE_VALUES]
        mean = _mean(values) if values else None
    else:
        given = [a for a in answers if a.text_value and a.text_value.strip()]
        answer_count = len(given)
        texts = [a.text_value for a in given if a.text_review == "approved"]

    if answer_count < MIN_ANSWERS_PER_QUESTION:
        return QuestionResult(**base, answer_count=0, withheld=True)
    return QuestionResult(**base, answer_count=answer_count, counts=counts, mean=mean, texts=texts)


def build_results(
    responses: Sequence[Any],
    questions: Sequence[Any],
    consultation_id: int = 0,
) -> ConsultationResults:
    """Agrège les réponses d'une consultation (fonction pure).

    ``responses`` : une entrée par répondant, chacune étant un itérable de réponses
    unitaires (``question_id``, ``selected_options``, ``scale_value``, ``text_value``,
    ``text_review``) ou un objet exposant ``.answers``.
    """
    respondent_count = len(responses)
    ordered = sorted(questions, key=lambda q: q.position)
    if respondent_count < MIN_RESPONDENTS:
        return ConsultationResults(
            consultation_id=consultation_id,
            respondent_count=respondent_count,
            withheld=True,
            notice=REPRESENTATIVITY_NOTICE,
            questions=[
                QuestionResult(
                    question_id=q.id,
                    position=q.position,
                    prompt=q.prompt,
                    question_type=q.question_type,
                    answer_count=0,
                    withheld=True,
                )
                for q in ordered
            ],
        )
    by_question: dict[int, list[Any]] = defaultdict(list)
    for response in responses:
        for answer in _answers_of(response):
            by_question[answer.question_id].append(answer)
    return ConsultationResults(
        consultation_id=consultation_id,
        respondent_count=respondent_count,
        withheld=False,
        notice=REPRESENTATIVITY_NOTICE,
        questions=[_aggregate_question(q, by_question.get(q.id, [])) for q in ordered],
    )


# --------------------------------------------------------------------------- #
# Service                                                                      #
# --------------------------------------------------------------------------- #
class ConsultationResultsService:
    """Lecture publique des résultats et export CSV."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        redis: Redis | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session = session
        self._redis = redis if redis is not None else get_redis()
        self._clock = clock

    build_results = staticmethod(build_results)
    write_csv = staticmethod(write_csv)
    read_csv = staticmethod(read_csv)

    async def results(self, cid: int) -> ConsultationResults:
        """Résultats d'une consultation clôturée ; 404 sinon (Exigences 11.1, 11.2, 11.9)."""
        consultation = await self._session.get(Consultation, cid)
        if consultation is None or effective_status(consultation, self._clock()) != "closed":
            raise ConsultationNotFoundError("Consultation introuvable ou non clôturée.")

        key = f"{CACHE_KEY_PREFIX}{cid}"
        try:
            cached = await self._redis.get(key)
        except Exception:  # noqa: BLE001 — Redis indisponible : échec ouvert.
            logger.warning("consultation_results_cache_unavailable", exc_info=True)
            cached = None
        if cached:
            try:
                return ConsultationResults.model_validate_json(cached)
            except ValueError:
                logger.warning("consultation_results_cache_corrupt")

        results = await self._compute(cid)
        try:
            await self._redis.set(key, results.model_dump_json(), ex=CACHE_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.warning("consultation_results_cache_unavailable", exc_info=True)
        return results

    async def export_csv(self, actor: Any, cid: int) -> str:
        """CSV des résultats ; 403 hors content/super admin, 422 si non clôturée ou retenue."""
        if getattr(actor, "role", None) not in EXPORT_ROLES:
            raise ConsultationResultsForbiddenError("Export réservé aux administrateurs.")
        consultation = await self._session.get(Consultation, cid)
        if consultation is None:
            raise ConsultationNotFoundError("Consultation introuvable.")
        if effective_status(consultation, self._clock()) != "closed":
            raise ConsultationValidationError("La consultation n'est pas clôturée.")
        results = await self.results(cid)
        if results.withheld:
            raise ConsultationValidationError("Résultats retenus : moins de 20 répondants.")
        return write_csv(results)

    async def _compute(self, cid: int) -> ConsultationResults:
        questions = list(
            (
                await self._session.execute(
                    select(ConsultationQuestion)
                    .where(ConsultationQuestion.consultation_id == cid)
                    .order_by(ConsultationQuestion.position)
                )
            ).scalars()
        )
        response_ids = list(
            (
                await self._session.execute(
                    select(ConsultationResponse.id).where(
                        ConsultationResponse.consultation_id == cid
                    )
                )
            ).scalars()
        )
        grouped: dict[Any, list[ConsultationAnswer]] = {rid: [] for rid in response_ids}
        if response_ids:
            question_ids = [q.id for q in questions]
            rows = (
                await self._session.execute(
                    select(ConsultationAnswer)
                    .join(
                        ConsultationResponse,
                        ConsultationResponse.id == ConsultationAnswer.response_id,
                    )
                    .where(
                        ConsultationResponse.consultation_id == cid,
                        ConsultationAnswer.question_id.in_(question_ids),
                    )
                )
            ).scalars()
            for answer in rows:
                grouped[answer.response_id].append(answer)
        return build_results(list(grouped.values()), questions, cid)


__all__ = [
    "ConsultationResultsForbiddenError",
    "ConsultationResultsService",
    "build_results",
    "read_csv",
    "write_csv",
]
