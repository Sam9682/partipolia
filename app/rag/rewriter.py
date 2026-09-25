"""Réécriture de requête pour la recherche hybride (Exigence 13.3).

Ce module implémente :class:`QueryRewriter`, deuxième étape du
:class:`RagPipeline` : à partir du message brut de l'Utilisateur, de sa
:class:`~app.rag.classifier.QuestionType` et, le cas échéant, de la
:class:`~app.models.proposal.Proposal` concernée, il produit une **requête
enrichie** destinée à la :class:`~app.rag.retriever.HybridSearch` (Exigence
13.3).

La réécriture est **déterministe et locale** (pas d'appel LLM) : elle contextualise
la requête pour améliorer le rappel documentaire, sans en altérer le sens.

* :attr:`~app.rag.classifier.QuestionType.ON_PROPOSITION` — on injecte le titre
  (et, si disponible, l'énoncé du problème) de la Proposition afin d'ancrer la
  recherche sur son sujet, même si le message emploie des références implicites
  (« cette mesure ») ;
* :attr:`~app.rag.classifier.QuestionType.DOCUMENTARY` — on privilégie les termes
  du message tels quels (recherche de passages sourcés) ;
* :attr:`~app.rag.classifier.QuestionType.COMPARATIVE` — idem, la comparaison
  s'appuie sur les termes explicites des mesures citées ;
* :attr:`~app.rag.classifier.QuestionType.GENERAL` — le message est repris,
  normalisé (espaces).

Aucune interpolation SQL n'a lieu ici : la requête produite est une simple chaîne
transmise ensuite à la couche de recherche paramétrée.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.rag.classifier import QuestionType

if TYPE_CHECKING:  # pragma: no cover - import pour l'annotation de type uniquement
    from app.models.proposal import Proposal


def _collapse_whitespace(text: str) -> str:
    """Réduit toute suite d'espaces de ``text`` à un seul espace, sans bords."""
    return " ".join(text.split())


class QueryRewriter:
    """Réécrit une requête utilisateur pour la recherche hybride (Exigence 13.3).

    :meth:`rewrite` est pure : elle ne dépend que de ses arguments et n'effectue
    ni appel réseau ni accès base.
    """

    def rewrite(
        self,
        message: str,
        question_type: QuestionType,
        proposal: "Proposal | None",
    ) -> str:
        """Retourne la requête enrichie pour ``message`` (Exigence 13.3).

        Pour une question rattachée à une Proposition, le titre (et l'énoncé du
        problème s'il existe) est préfixé au message afin d'ancrer le contexte.
        Pour les autres catégories, le message normalisé est renvoyé tel quel. Un
        message vide donne, au mieux, le contexte de la Proposition (titre), sinon
        une chaîne vide.
        """
        base = _collapse_whitespace(message)

        if question_type is QuestionType.ON_PROPOSITION and proposal is not None:
            return _collapse_whitespace(
                " ".join(part for part in self._proposal_context(proposal, base) if part)
            )

        return base

    @staticmethod
    def _proposal_context(proposal: "Proposal", base: str) -> list[str]:
        """Assemble les fragments de contexte d'une Proposition suivis du message.

        L'ordre place le contexte (titre puis problème) avant le message, de sorte
        que les termes du sujet pèsent dans la recherche même quand le message est
        elliptique (« cette mesure », « ça »).
        """
        title = _collapse_whitespace(proposal.title or "")
        problem = _collapse_whitespace(proposal.problem or "")
        return [title, problem, base]


__all__ = ["QueryRewriter"]
