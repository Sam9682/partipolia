"""Orchestration du pipeline RAG et prompt système avec garde-fous (Exigences 13, 14, 15).

Ce module implémente :class:`RagPipeline`, qui **enchaîne** les briques de
récupération et de génération pour produire une réponse documentée et citée
(Exigence 13.3) :

    classification → réécriture → recherche hybride → fusion → reranking →
    contexte → génération LLM (faible température) → validation des citations →
    réponse ``{answer, sources[], confidence}``

Garde-fous structurels (Exigences 14.1, 14.2, 14.6) :

* **Récupération obligatoire avant génération** — le LLM n'est appelé que si au
  moins un passage a été retenu ; sinon la réponse est « information
  insuffisante » (``confidence = 0``, ``sources = []``) sans appel de génération
  (Exigences 14.1, 14.6, 13.8) ;
* **Faible température** — la génération utilise ``settings.llm_temperature``
  (défaut 0.1), plafonnée, pour limiter les hallucinations (Exigence 14.2) ;
* **Validation des citations** — après génération, les affirmations factuelles
  non sourcées et les citations fabriquées font baisser la ``confidence``
  (Exigences 14.3–14.5).

Garde-fous du **prompt système** (Exigences 13.5–13.10, 15) : répondre uniquement
à partir du contexte fourni ; distinguer faits / estimations / opinions /
hypothèses / désaccords entre Sources ; ne jamais inventer de nombres ; associer
chaque affirmation factuelle à une Source ; ne pas transformer une information
descriptive en recommandation politique ; ne jamais formuler de recommandation de
vote individuelle ; sur question sensible privilégier « Voici les arguments
documentés » ; comparer sur des critères factuels (coût, calendrier, effets
documentés, contraintes juridiques, Sources, incertitudes).

**Découplage IA (Property 10)** : le pipeline est en lecture seule côté domaine —
il ne consomme la base que pour la recherche hybride (SELECT) et n'émet **aucune**
écriture vers ``votes`` ou ``program_proposals``. Il ne dépend que de contrats
(``LLMProvider`` / ``EmbeddingProvider``) et des briques RAG.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from app.rag.classifier import QuestionClassifier, QuestionType
from app.rag.context import CitationValidator, ContextBuilder
from app.rag.providers.base import LLMProvider, Message
from app.rag.ranking import ScoreFusion
from app.rag.reranking import DEFAULT_KEEP, Reranker
from app.rag.retriever import HybridSearch
from app.rag.rewriter import QueryRewriter
from app.rag.types import Citation
from app.schemas.chat import ChatResponse, ChatSource

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.proposal import Proposal
    from app.models.user import User

# Message renvoyé lorsque la récupération ne retourne aucun passage (Exigences
# 13.8, 14.6). Il assume explicitement l'indisponibilité plutôt que d'inventer.
INSUFFICIENT_INFORMATION_MESSAGE: Final = (
    "Information insuffisante : aucune source documentaire pertinente n'a été "
    "trouvée pour répondre à cette question."
)

# Nombre de candidats remontés par la recherche hybride avant fusion/reranking
# (Exigence 12.3 : ~20 → 6). Repli si la configuration ne le précise pas.
_DEFAULT_TOP_K: Final = 20

# Prompt système imposant les garde-fous anti-hallucination et de neutralité
# (Exigences 13.5–13.10, 15.1–15.3). Rédigé en français, langue de la Plateforme.
SYSTEM_PROMPT: Final = (
    "Tu es un assistant documentaire politique neutre. Tu réponds UNIQUEMENT à "
    "partir du contexte documentaire fourni ci-dessous, jamais à partir de "
    "connaissances externes.\n"
    "\n"
    "Règles impératives :\n"
    "1. Réponds exclusivement à partir des passages fournis. Si l'information "
    "nécessaire est absente du contexte, indique explicitement qu'elle est "
    "indisponible ; n'invente rien.\n"
    "2. N'invente JAMAIS de nombres, chiffres, montants, pourcentages ou dates. "
    "Ne cite que ceux présents dans le contexte.\n"
    "3. Associe chaque affirmation factuelle importante à sa source en insérant "
    "le marqueur de citation correspondant [n] (n étant le numéro du passage).\n"
    "4. Distingue clairement les faits, les estimations, les opinions, les "
    "hypothèses et les désaccords entre sources. Qualifie explicitement chaque "
    "élément (par exemple : « selon l'étude [1] », « estimation », « hypothèse », "
    "« les sources divergent »).\n"
    "5. Ne transforme jamais une information descriptive en recommandation "
    "politique.\n"
    "6. Ne formule JAMAIS de recommandation de vote individuelle (ne dis jamais "
    "« vous devriez voter pour/contre »). Sur une question sensible, privilégie "
    "la formulation « Voici les arguments documentés ».\n"
    "7. Pour une comparaison entre mesures, compare uniquement sur des critères "
    "factuels : coût, calendrier, effets documentés, contraintes juridiques, "
    "sources et incertitudes.\n"
)


class RagPipeline:
    """Enchaîne les étapes RAG jusqu'à la réponse citée (Exigence 13.3).

    Le pipeline est instancié par requête avec les briques de récupération/
    classement (``HybridSearch``, ``ScoreFusion``, ``Reranker``, ``ContextBuilder``,
    ``CitationValidator``, ``QuestionClassifier``, ``QueryRewriter``) et un
    ``LLMProvider`` (sélectionné par configuration — Exigence 16.3). Les
    dépendances sont injectées afin de rester testable hors-ligne (providers
    simulés) et indépendant des implémentations concrètes.
    """

    def __init__(
        self,
        *,
        hybrid_search: HybridSearch,
        llm_provider: LLMProvider,
        classifier: QuestionClassifier | None = None,
        rewriter: QueryRewriter | None = None,
        fusion: ScoreFusion | None = None,
        reranker: Reranker | None = None,
        context_builder: ContextBuilder | None = None,
        citation_validator: CitationValidator | None = None,
        temperature: float = 0.1,
        top_k: int = _DEFAULT_TOP_K,
        keep: int = DEFAULT_KEEP,
    ) -> None:
        self._hybrid_search = hybrid_search
        self._llm = llm_provider
        self._classifier = classifier or QuestionClassifier()
        self._rewriter = rewriter or QueryRewriter()
        self._fusion = fusion or ScoreFusion()
        self._reranker = reranker or Reranker()
        self._context_builder = context_builder or ContextBuilder()
        self._citation_validator = citation_validator or CitationValidator()
        # Température plafonnée : la génération RAG doit rester « faible » quelle
        # que soit la configuration transmise (Exigence 14.2).
        self._temperature = max(0.0, min(temperature, 0.3))
        self._top_k = top_k if top_k > 0 else _DEFAULT_TOP_K
        self._keep = keep

    async def answer(
        self,
        message: str,
        proposal_id: int | None,
        user: "User | None" = None,
        *,
        proposal: "Proposal | None" = None,
    ) -> ChatResponse:
        """Produit la réponse documentée de l'Assistant IA (Exigence 13.3).

        Enchaîne classification → réécriture → recherche hybride → fusion →
        reranking → contexte → génération LLM (faible température) → validation des
        citations. La **récupération est obligatoire avant toute génération**
        (Exigence 14.1) : si aucun passage n'est retenu, aucune génération n'a lieu
        et la réponse indique « information insuffisante » (``confidence = 0.0``,
        ``sources = []`` — Exigences 13.8, 14.6).

        Le paramètre ``user`` n'influence pas le contenu (neutralité, Exigence 15)
        mais est disponible pour la journalisation en amont ; ``proposal`` fournit
        le contexte de réécriture pour une question rattachée à une Proposition.
        """
        # 1. Classification de la question (Exigence 13.2).
        question_type = self._classifier.classify(message, proposal_id)

        # 2. Réécriture de la requête pour la recherche (Exigence 13.3).
        query = self._rewriter.rewrite(message, question_type, proposal)
        if not query.strip():
            return self._insufficient_response()

        # 3. Recherche hybride (lexicale + sémantique) — récupération OBLIGATOIRE
        #    avant génération (Exigence 14.1).
        candidates = await self._hybrid_search.search(query, top_k=self._top_k)

        # 4. Fusion des sous-scores puis 5. reranking (20 → 6) (Exigences 12.2, 12.3).
        scored = self._fusion.fuse(candidates)
        top_chunks = self._reranker.rerank(scored, keep=self._keep)

        # Aucun passage récupéré ⇒ pas de génération, « information insuffisante »
        # (Exigences 13.8, 14.1, 14.6).
        if not top_chunks:
            return self._insufficient_response()

        # 6. Construction du contexte cité (citations numérotées [n]) (Exigence 13.4).
        context, citations = self._context_builder.build(top_chunks)

        # 7. Génération LLM à faible température, cadrée par le prompt système
        #    (Exigences 13.3, 13.5–13.10, 14.2, 15).
        messages = self._build_messages(question_type, context, message)
        answer_text = self._llm.generate(messages, self._temperature)

        # 8. Validation des citations : détecte les affirmations non sourcées et
        #    les citations fabriquées (Exigences 14.3–14.5).
        validation = self._citation_validator.validate(answer_text, citations)

        confidence = self._confidence(
            citations=citations,
            is_valid=validation.is_valid,
            unsourced=len(validation.unsourced_claims),
            invalid=len(validation.invalid_citation_numbers),
        )

        return ChatResponse(
            answer=answer_text,
            sources=self._to_sources(citations),
            confidence=confidence,
        )

    # ------------------------------------------------------------------ #
    # Construction des messages transmis au LLM                          #
    # ------------------------------------------------------------------ #
    def _build_messages(
        self, question_type: QuestionType, context: str, message: str
    ) -> list[Message]:
        """Assemble les messages ``system`` + ``user`` pour la génération.

        Le message système porte les garde-fous ; le message utilisateur fournit
        le contexte documentaire numéroté puis la question. Pour une question
        comparative, une consigne de comparaison factuelle est rappelée (Exigence
        15.2).
        """
        system = SYSTEM_PROMPT
        if question_type is QuestionType.COMPARATIVE:
            system += (
                "\nCette question est comparative : structure la réponse en "
                "comparant les options sur coût, calendrier, effets documentés, "
                "contraintes juridiques, sources et incertitudes.\n"
            )

        user_content = (
            "Contexte documentaire (chaque passage est précédé de son numéro de "
            "citation) :\n"
            f"{context}\n\n"
            f"Question : {message.strip()}\n\n"
            "Réponds en français en respectant strictement les règles ci-dessus "
            "et en citant tes sources avec [n]."
        )

        return [
            Message(role="system", content=system),
            Message(role="user", content=user_content),
        ]

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _insufficient_response() -> ChatResponse:
        """Réponse « information insuffisante » sans génération (Exigences 13.8, 14.6)."""
        return ChatResponse(
            answer=INSUFFICIENT_INFORMATION_MESSAGE,
            sources=[],
            confidence=0.0,
        )

    @staticmethod
    def _to_sources(citations: list[Citation]) -> list[ChatSource]:
        """Convertit les citations retenues en Sources affichables (Exigence 13.4)."""
        return [
            ChatSource(
                number=citation.number,
                chunk_id=citation.chunk_id,
                content=citation.content,
                source_id=citation.source_id,
                document_id=citation.document_id,
            )
            for citation in citations
        ]

    @staticmethod
    def _confidence(
        *,
        citations: list[Citation],
        is_valid: bool,
        unsourced: int,
        invalid: int,
    ) -> float:
        """Dérive un indice de confiance ∈ ``[0, 1]`` à partir de la validation.

        La confiance part d'une base élevée lorsqu'un contexte a été récupéré et
        que la réponse est intégralement sourcée (``is_valid``). Chaque affirmation
        factuelle non sourcée et chaque citation fabriquée la fait décroître, sans
        jamais passer sous ``0`` (Exigences 14.3–14.5). Une absence de citation
        donne une confiance nulle (aucun ancrage documentaire).
        """
        if not citations:
            return 0.0
        base = 0.9 if is_valid else 0.5
        penalty = 0.15 * unsourced + 0.2 * invalid
        return max(0.0, min(1.0, base - penalty))


__all__ = [
    "RagPipeline",
    "SYSTEM_PROMPT",
    "INSUFFICIENT_INFORMATION_MESSAGE",
]
