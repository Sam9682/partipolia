"""Partipolia public web pages (SSR, French).

This router replaces the legacy political-platform pages in the user experience.
The legacy router (:mod:`app.web.router`) is kept importable but is no longer
mounted by :mod:`app.main` ("archive and hide").
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional
from app.core.database import get_session
from app.economics.categories import DEFAULT_CATEGORIES, TOTAL_REFERENCE_EXPENDITURE
from app.economics.defaults import bootstrap_snapshot
from app.economics.net_balance.parameters import load_net_balance_parameters
from app.economics.sector_labels import SECTOR_LABELS
from app.economics.types import Confidence
from app.services.privacy_service import PrivacyService
from app.web.formatting import format_eur_fr

TEMPLATES_DIR: Path = Path(__file__).resolve().parent.parent / "templates"
templates: Jinja2Templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

logger: logging.Logger = logging.getLogger(__name__)



async def resolve_header_identity(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
) -> None:
    """Resolve the header identity and store it on ``request.state``.

    Never raises: any unexpected error is logged and the visitor is treated as
    anonymous.
    """
    try:
        user = await get_current_user_optional(request, session)
    except Exception:  # noqa: BLE001 - the header must never break the page
        logger.exception("header identity resolution failed")
        user = None
    request.state.identity_email = user.email if user is not None else None


public_router: APIRouter = APIRouter(dependencies=[Depends(resolve_header_identity)])

# Single source of truth for the main navigation (French labels, order is display order).
NAV_ITEMS: list[dict[str, str]] = [
    {"label": "Accueil", "href": "/"},
    {"label": "Comprendre", "href": "/comprendre"},
    {"label": "Simulateur", "href": "/simulateur"},
    {"label": "Simulateur national", "href": "/simulateur-national"},
    {"label": "Impact national", "href": "/impact-national"},
    {"label": "Nos revendications", "href": "/nos-revendications"},
    {"label": "Revendications citoyennes", "href": "/revendications"},
    {"label": "Consultations", "href": "/consultations"},
    {"label": "Méthodologie", "href": "/methodologie"},
    {"label": "Participer", "href": "/participer"},
]

POLICY_DEMANDS: list[str] = [
    "Geler les hausses d'impôts et les nouveaux prélèvements obligatoires.",
    "Établir une trajectoire crédible de réduction de la dépense publique.",
    "Réduire la complexité administrative et la bureaucratie inefficace.",
    "Améliorer l'efficacité de la dépense publique.",
    "Publier des indicateurs transparents et mesurables de performance budgétaire.",
    "Protéger le pouvoir d'achat des ménages et l'activité économique productive.",
]


def page_context(request: Request, **extra: Any) -> dict[str, Any]:
    """Shared template context: navigation, current path and header identity."""
    return {
        "nav_items": list(NAV_ITEMS),
        "current_path": request.url.path,
        "identity_email": getattr(request.state, "identity_email", None),
        **extra,
    }


def _info(request: Request, title: str, lead: str, sections: list[tuple[str, str]]) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "public/info.html",
        page_context(request, title=title, lead=lead, sections=sections),
    )


@public_router.get("/", response_class=HTMLResponse, name="accueil", include_in_schema=False)
async def home(request: Request) -> HTMLResponse:
    categories = [
        {
            "code": c.code,
            "name": c.name,
            "description": c.description,
            "reference_annual_expenditure_fr": format_eur_fr(c.reference_annual_expenditure),
        }
        for c in DEFAULT_CATEGORIES
    ]
    return templates.TemplateResponse(
        request,
        "public/home.html",
        page_context(
            request,
            categories=categories,
            total_fr=format_eur_fr(TOTAL_REFERENCE_EXPENDITURE),
            demands=POLICY_DEMANDS,
        ),
    )


@public_router.get(
    "/comprendre", response_class=HTMLResponse, name="comprendre", include_in_schema=False
)
async def understand(request: Request) -> HTMLResponse:
    return _info(
        request,
        "Comprendre",
        "Une mobilisation économique citoyenne, volontaire, pacifique et mesurable.",
        [
            (
                "Le principe",
                "Chaque citoyen peut choisir de réduire volontairement certaines dépenses non essentielles, "
                "mesurer ses économies personnelles et visualiser l'effet collectif estimé de ces décisions.",
            ),
            (
                "Ce que nous ne faisons pas",
                "PARTIPOLIA n'encourage ni l'évasion fiscale illégale, ni le non-paiement des impôts obligatoires, "
                "ni le harcèlement, la menace ou la contrainte.",
            ),
            (
                "Engagement déclaré, pas consommation vérifiée",
                "Les engagements enregistrés sont déclaratifs. Aucune transaction ni donnée bancaire n'est collectée.",
            ),
        ],
    )


@public_router.get(
    "/simulateur", response_class=HTMLResponse, name="simulateur", include_in_schema=False
)
async def simulator(request: Request) -> HTMLResponse:
    categories = [
        {"code": c.code, "name": c.name, "reference": str(c.reference_annual_expenditure)}
        for c in DEFAULT_CATEGORIES
    ]
    return templates.TemplateResponse(
        request, "public/simulator.html", page_context(request, categories=categories)
    )


@public_router.get(
    "/impact-national", response_class=HTMLResponse, name="impact_national", include_in_schema=False
)
async def national_impact(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "public/national.html", page_context(request, sector_labels=SECTOR_LABELS)
    )


@public_router.get(
    "/nos-revendications",
    response_class=HTMLResponse,
    name="revendications",
    include_in_schema=False,
)
async def demands(request: Request) -> HTMLResponse:
    return _info(
        request,
        "Nos revendications",
        "Six demandes claires, portées par une mobilisation non violente et démocratique.",
        [(f"{i}.", text) for i, text in enumerate(POLICY_DEMANDS, start=1)]
        + [
            (
                "Portée",
                "Les consultations organisées sur la plateforme ne sont ni des élections ni des référendums "
                "juridiquement contraignants, et les participants ne représentent pas la population française dans son ensemble.",
            )
        ],
    )


SOURCES: list[dict[str, str]] = [
    {
        "what": "Tableau entrées-sorties A38 : production, consommations intermédiaires, importations",
        "source": "INSEE, comptes nationaux base 2020 (TES symétrique)",
        "url": "https://www.insee.fr/fr/statistiques/8574722",
        "year": "2023",
        "status": "officiel",
    },
    {
        "what": "Valeur ajoutée, rémunérations, salaires, excédent brut d'exploitation par branche",
        "source": "INSEE, comptes nationaux annuels (tableaux 6.201, 6.204, 6.205, 6.206)",
        "url": "https://www.insee.fr/fr/statistiques/8988821",
        "year": "2023",
        "status": "officiel",
    },
    {
        "what": "Taux de TVA (20 %, 10 %, 5,5 %, 2,1 %)",
        "source": "service-public.gouv.fr, économie.gouv.fr",
        "url": "https://entreprendre.service-public.gouv.fr/vosdroits/F37605",
        "year": "2026",
        "status": "officiel",
    },
    {
        "what": "Accise sur les produits énergétiques (ex-TICPE), consommation des véhicules",
        "source": "douane.gouv.fr, SDES",
        "url": "https://www.douane.gouv.fr",
        "year": "à confirmer",
        "status": "provisoire (non vérifié)",
    },
]


@public_router.get(
    "/methodologie", response_class=HTMLResponse, name="methodologie", include_in_schema=False
)
async def methodology(request: Request) -> HTMLResponse:
    snapshot = bootstrap_snapshot()
    seen: dict[str, dict[str, str]] = {}
    for (ptype, _category, _key), parameter in sorted(
        snapshot.parameters.items(), key=lambda kv: kv[0][0]
    ):
        if parameter.confidence in (Confidence.PROVISIONAL, Confidence.LOW):
            seen.setdefault(
                ptype,
                {"type": ptype, "confidence": parameter.confidence.value, "note": parameter.note},
            )
    return templates.TemplateResponse(
        request,
        "public/methodology.html",
        page_context(
            request,
            sources=SOURCES,
            provisional=list(seen.values()),
            net_balance_provisional=load_net_balance_parameters().provisional_rows(),
            model_version=snapshot.model_version,
            dataset_versions=list(snapshot.dataset_versions),
        ),
    )


@public_router.get(
    "/participer", response_class=HTMLResponse, name="participer", include_in_schema=False
)
async def join(request: Request) -> HTMLResponse:
    categories = [
        {
            "code": c.code,
            "name": c.name,
            "reference": str(c.reference_annual_expenditure),
            "reference_fr": format_eur_fr(c.reference_annual_expenditure),
        }
        for c in DEFAULT_CATEGORIES
    ]
    return templates.TemplateResponse(
        request, "public/join.html", page_context(request, categories=categories)
    )


@public_router.get("/login", response_class=HTMLResponse, name="login", include_in_schema=False)
async def login(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "public/login.html", page_context(request))


# ---------------------------------------------------------------------------
# Legal pages (kept at their historical URLs)
# ---------------------------------------------------------------------------
@public_router.get(
    "/mentions-legales",
    response_class=HTMLResponse,
    name="mentions_legales",
    include_in_schema=False,
)
async def legal_notice(request: Request) -> HTMLResponse:
    return _info(
        request,
        "Mentions légales",
        "Informations légales sur l'éditeur du site.",
        [
            (
                "Editeur:",
                "Association loi 1901 - Hypervisia France",
            )
        ],
    )


@public_router.get(
    "/confidentialite", response_class=HTMLResponse, name="confidentialite", include_in_schema=False
)
async def privacy(request: Request) -> HTMLResponse:
    policy = PrivacyService.retention_policy()
    sections = [
        ("Minimisation", "Nous ne collectons ni transactions de consommation ni données bancaires.")
    ]
    sections += [(rule.category, f"{rule.retention} ({rule.basis})") for rule in policy.rules]
    return _info(request, "Confidentialité", "Protection de vos données personnelles.", sections)


@public_router.get("/cookies", response_class=HTMLResponse, name="cookies", include_in_schema=False)
async def cookies(request: Request) -> HTMLResponse:
    return _info(
        request,
        "Cookies",
        "Seuls des cookies strictement nécessaires sont utilisés.",
        [("Session", "Des cookies HttpOnly servent uniquement à maintenir votre connexion.")],
    )


@public_router.get(
    "/conditions", response_class=HTMLResponse, name="conditions", include_in_schema=False
)
async def terms(request: Request) -> HTMLResponse:
    return _info(
        request,
        "Conditions d'utilisation",
        "Règles d'usage de la plateforme.",
        [
            (
                "Usage",
                "La plateforme est non violente. Toute incitation à l'illégalité, au harcèlement ou à la menace est interdite.",
            )
        ],
    )
