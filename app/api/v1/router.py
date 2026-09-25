"""Routeur agrégateur de l'API REST versionnée ``/api/v1`` (Exigence 33.1).

Ce module rassemble sous un unique :class:`fastapi.APIRouter` l'ensemble des
sous-routeurs de la couche API (authentification, thèmes, propositions, votes,
arguments, commentaires, sources, chat RAG, programme, statistiques, équipes,
mandat). Il est monté par ``app.main`` sous le préfixe ``/api/v1``.

À ce stade de l'échafaudage (tâche 1.5), l'agrégateur est volontairement **vide** :
les sous-routeurs seront ajoutés au fil des tâches ultérieures (2.x, 3.x, …) via
``api_router.include_router(...)``. Le point d'entrée peut ainsi monter
``/api/v1`` dès maintenant sans dépendre de modules non encore écrits.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    arguments,
    auth,
    chat,
    documents,
    mandate,
    privacy,
    program,
    proposals,
    sources,
    statistics,
    teams,
    themes,
    votes,
)

# Routeur racine de l'API v1. Les tags et le préfixe ``/api/v1`` sont appliqués
# au montage dans ``app.main`` afin de garder ce module découplé du chemin exact.
api_router: APIRouter = APIRouter()

# Authentification — inscription, connexion, refresh, logout, me (Exigence 1 ; tâche 3.3).
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])

# Référentiel_Thématique — points d'accès publics (Exigence 2 ; tâche 4.1).
api_router.include_router(themes.router, prefix="/themes", tags=["themes"])

# Propositions — CRUD (création DRAFT, liste filtrée, détail, modification
# versionnée, archivage) (Exigence 4 ; tâche 4.5).
api_router.include_router(proposals.router, prefix="/proposals", tags=["proposals"])

# Sources documentaires — liste publique et gestion réservée à l'Administrateur
# (Exigence 10 ; tâche 6.1). Le rattachement d'une Source à une Proposition est
# exposé sous ``/proposals/{id}/sources``.
api_router.include_router(sources.router, prefix="/sources", tags=["sources"])
api_router.include_router(
    sources.proposal_sources_router, prefix="/proposals", tags=["sources"]
)

# Ingestion documentaire — déclenchement réservé à l'Administrateur
# (``POST /api/v1/admin/documents/ingest``), émettant la tâche Celery
# ``ingest_document`` (202) sans bloquer l'API (Exigences 11.1, 23.2, 23.3 ;
# tâche 6.7).
api_router.include_router(documents.router, prefix="/admin", tags=["documents"])

# Arguments — débat argumenté FOR/AGAINST (Exigence 8 ; tâche 5.5). La création
# est rattachée à une Proposition (``/proposals/{id}/arguments``) ; la
# modification et la suppression portent sur l'Argument (``/arguments/{id}``).
api_router.include_router(arguments.router, prefix="/arguments", tags=["arguments"])
api_router.include_router(
    arguments.proposal_arguments_router, prefix="/proposals", tags=["arguments"]
)

# Votes — UPSERT et retrait réservés aux Utilisateurs authentifiés (Exigence 6.6)
# et décomptes publics (Exigences 6.5, 7.4) (Exigence 6 ; tâche 5.4). Tous les
# points sont rattachés à une Proposition (``/proposals/{id}/votes``).
api_router.include_router(votes.router, prefix="/proposals", tags=["votes"])

# Statistiques — compteurs globaux agrégés et anonymisés, consultation publique
# (Exigence 22 ; tâche 8.4).
api_router.include_router(statistics.router, prefix="/statistics", tags=["statistics"])

# Programme — Programme et ses Propositions (associations porteuses de priority
# et included_at) ; consultation publique et gestion (ajout/retrait) réservée à
# l'Administrateur (Exigences 18, 19 ; tâche 8.1).
api_router.include_router(program.router, prefix="/program", tags=["program"])

# Équipes — liste publique des Équipes et de leurs membres (Exigences 20.1, 20.2)
# et gestion (création/ajout/retrait) réservée à l'Administrateur, consignée au
# Journal_D_Audit (Exigence 20.3 ; tâche 8.3).
api_router.include_router(teams.router, prefix="/teams", tags=["teams"])

# Mandat — listes publiques des Engagements et Indicateurs (Exigences 21.1, 21.3)
# et mises à jour (statut d'engagement, valeur d'indicateur) réservées à
# l'Administrateur, consignées au Journal_D_Audit (Exigences 21.2, 21.4 ; tâche 8.3).
api_router.include_router(mandate.router, prefix="/mandate", tags=["mandate"])

# Chat RAG — Assistant IA documentaire, réservé aux Utilisateurs authentifiés
# (``POST /api/v1/chat`` → {answer, sources[], confidence}) ; chaque réponse est
# journalisée (request_id, user_id, timestamp, modèle, retrieved_documents)
# (Exigences 13.1, 14.7, 30.2 ; tâche 7.9).
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])

# RGPD — export des données, gestion du consentement et suppression de compte,
# réservés à l'Utilisateur authentifié pour ses propres données ; politique de
# rétention explicite consultable publiquement (Exigences 28.4, 28.5 ; tâche 10.3).
api_router.include_router(privacy.router, prefix="/privacy", tags=["privacy"])

# NOTE : les autres sous-routeurs (commentaires, …) seront enregistrés ici au fil
# des tâches ultérieures via ``api_router.include_router(...)``.
