"""Classification des questions de l'Assistant IA (Exigence 13.2).

Ce module implémente :class:`QuestionType` (l'énumération des quatre catégories
attendues) et :class:`QuestionClassifier`, première étape du :class:`RagPipeline`
(Exigence 13.3). La classification oriente la réécriture de requête
(:class:`~app.rag.rewriter.QueryRewriter`) et le cadrage de la génération.

Catégories (Exigence 13.2) :

* :attr:`QuestionType.GENERAL` — question générale, sans rattachement à une
  Proposition ni intention documentaire ou comparative marquée ;
* :attr:`QuestionType.ON_PROPOSITION` — question portant sur une Proposition
  précise (``proposal_id`` fourni, ou formulation renvoyant explicitement à « cette
  proposition/mesure ») ;
* :attr:`QuestionType.DOCUMENTARY` — demande d'éléments sourcés/chiffrés (« que
  disent les sources », « quel est le coût estimé », « d'après l'étude ») ;
* :attr:`QuestionType.COMPARATIVE` — demande de comparaison entre deux mesures ou
  options (« comparer A et B », « différence entre… », « plutôt X ou Y »).

La classification est **heuristique et déterministe** (marqueurs lexicaux
normalisés), sans appel LLM : elle est ainsi rapide, testable hors-ligne et sans
coût externe. L'ordre de priorité est explicite pour lever les ambiguïtés :
**comparative > documentaire > sur-proposition > générale** — une question qui
compare deux mesures reste comparative même si elle mentionne une Proposition ou
des Sources.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Final


class QuestionType(str, Enum):
    """Catégorie d'une question posée à l'Assistant IA (Exigence 13.2)."""

    GENERAL = "GENERAL"
    ON_PROPOSITION = "ON_PROPOSITION"
    DOCUMENTARY = "DOCUMENTARY"
    COMPARATIVE = "COMPARATIVE"


# Marqueurs (sans accents, minuscules) déclenchant chaque catégorie. Les
# expressions comparatives sont recherchées en priorité (cf. ordre ci-dessous).
_COMPARATIVE_MARKERS: Final[tuple[str, ...]] = (
    "comparer",
    "comparaison",
    "compare",
    "difference entre",
    "differences entre",
    "par rapport a",
    "versus",
    "plutot que",
    "ou bien",
    "laquelle est",
    "lequel est",
    "mieux que",
    "avantages et inconvenients",
)

_DOCUMENTARY_MARKERS: Final[tuple[str, ...]] = (
    "source",
    "sources",
    "etude",
    "etudes",
    "rapport",
    "document",
    "reference",
    "references",
    "chiffre",
    "chiffres",
    "cout estime",
    "cout",
    "budget",
    "statistique",
    "statistiques",
    "donnees",
    "que disent",
    "d'apres",
    "selon",
    "citer",
    "citation",
)

_ON_PROPOSITION_MARKERS: Final[tuple[str, ...]] = (
    "cette proposition",
    "cette mesure",
    "cette proposition-ci",
    "la proposition",
    "la mesure",
    "ce projet",
    "cette reforme",
)

# Découpe une expression comparative « X et Y » / « X ou Y » explicite.
_COMPARATIVE_CONNECTORS: Final = re.compile(
    r"\b(?:vs|versus)\b|\bcompar", flags=re.IGNORECASE
)


def _normalize(text: str) -> str:
    """Normalise ``text`` pour la comparaison : minuscules sans accents.

    Décompose les caractères accentués (NFKD) puis retire les diacritiques, afin
    que « étude » et « etude » ou « coût » et « cout » soient reconnus de la même
    manière, quel que soit le clavier de l'Utilisateur.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    """Indique si ``haystack`` contient l'un des ``needles`` (déjà normalisés)."""
    return any(needle in haystack for needle in needles)


class QuestionClassifier:
    """Classe une question parmi les quatre :class:`QuestionType` (Exigence 13.2).

    La méthode :meth:`classify` est pure et déterministe : elle n'effectue aucun
    appel réseau ni accès base, ce qui la rend directement testable.
    """

    def classify(self, message: str, proposal_id: int | None) -> QuestionType:
        """Retourne la :class:`QuestionType` de ``message`` (Exigence 13.2).

        La présence d'un ``proposal_id`` ancre la question sur une Proposition,
        mais ne prime pas sur une intention **comparative** ou **documentaire**
        explicite : on privilégie l'intention la plus spécifique. Ordre de
        priorité : comparative > documentaire > sur-proposition > générale.

        Une question vide (ou uniquement des blancs) est classée
        :attr:`QuestionType.ON_PROPOSITION` si un ``proposal_id`` est fourni,
        sinon :attr:`QuestionType.GENERAL`.
        """
        normalized = _normalize(message).strip()
        if not normalized:
            return (
                QuestionType.ON_PROPOSITION
                if proposal_id is not None
                else QuestionType.GENERAL
            )

        if _contains_any(normalized, _COMPARATIVE_MARKERS):
            return QuestionType.COMPARATIVE

        if _contains_any(normalized, _DOCUMENTARY_MARKERS):
            return QuestionType.DOCUMENTARY

        if proposal_id is not None or _contains_any(
            normalized, _ON_PROPOSITION_MARKERS
        ):
            return QuestionType.ON_PROPOSITION

        return QuestionType.GENERAL


__all__ = ["QuestionType", "QuestionClassifier"]
