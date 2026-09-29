"""Script d'amorçage des Problèmes_Juridiques « Réparer la loi ».

Exigences couvertes : 1.2, 2.4, 4.1, 4.3, 13.6.

Amorce, de façon **idempotente**, le jeu de démonstration MVP de la
Rubrique_Réparer_La_Loi :

* **≥ 10 Problèmes_Juridiques** concrets (Exigence 1.2), chacun rattaché à un
  Thème existant du Référentiel_Thématique V1 par ``theme_id`` (Exigence 2.4) —
  les Thèmes sont retrouvés par ``slug`` au moment de l'exécution ;
* pour chaque Problème, **3 à 5 Réformes_Proposées** représentées par des
  ``Proposal`` existantes (Exigence 13.6 : aucun modèle redondant), dont
  **exactement une Option_Statu_Quo** (Exigence 4.3), rattachées via la table
  d'association ``legal_problem_reforms`` (Exigence 4.1) ;
* une **Source ``LEGISLATION``** par Problème, reliée à chacune de ses Réformes
  via l'association ``proposal_sources`` (Exigence 10.3, transparence des
  Sources_Juridiques).

La cardinalité des Réformes rattachées (``3 ≤ n ≤ 5`` **et** exactement un statu
quo) est validée à la construction via
``LegalReformService._validate_cardinality`` (Exigences 4.1, 4.3), avant toute
écriture.

Idempotence :

* les Problèmes sont amorcés **par ``slug``** : un ``slug`` déjà présent est
  laissé intact (aucune Réforme ni Source n'est recréée pour lui) ;
* l'Utilisateur auteur du seed et la Source ``LEGISLATION`` sont retrouvés puis
  réutilisés s'ils existent déjà.

Relancer le script sur une base déjà amorcée n'insère pas de doublon et ne remet
pas à zéro les données existantes.

Utilisation :

    uv run python -m scripts.seed_legal_problems
    # ou
    python -m scripts.seed_legal_problems

Prérequis : les 25 Thèmes du Référentiel_Thématique V1 doivent avoir été amorcés
au préalable (``python -m scripts.seed``) ; à défaut, le script s'interrompt en
signalant les slugs de Thèmes manquants.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.core.database import SessionLocal
from app.models import (
    LegalProblem,
    LegalProblemReform,
    Proposal,
    ProposalSource,
    Source,
    Theme,
    User,
)
from app.services.legal_reform_service import LegalReformService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Utilisateur auteur du seed : requis par ``proposals.author_id`` (NOT NULL).
# Retrouvé par email (UNIQUE, Exigence 1.1) puis réutilisé s'il existe déjà.
# ---------------------------------------------------------------------------
SEED_AUTHOR_EMAIL: str = "seed-reparer-la-loi@partipolia.local"
SEED_AUTHOR_DISPLAY_NAME: str = "Amorçage Réparer la loi"

# Niveaux de complexité autorisés par le CHECK ``ck_legal_problems_complexity_level``.
_COMPLEXITY_FAIBLE = "FAIBLE"
_COMPLEXITY_MOYEN = "MOYEN"
_COMPLEXITY_ELEVE = "ELEVE"


@dataclass(frozen=True, slots=True)
class ReformSeed:
    """Une Réforme_Proposée à amorcer (matérialisée par une ``Proposal``)."""

    title: str
    description: str
    is_status_quo: bool = False
    expected_impact: str | None = None


@dataclass(frozen=True, slots=True)
class ProblemSeed:
    """Un Problème_Juridique à amorcer, avec son Thème, sa Source et ses Réformes."""

    slug: str
    title: str
    summary: str
    theme_slug: str
    complexity_level: str
    affected_citizens_count: int
    concerned_legal_texts: list[str]
    jurisprudence_refs: list[str]
    source_title: str
    source_url: str
    reforms: list[ReformSeed] = field(default_factory=list)


def _status_quo(description: str) -> ReformSeed:
    """Construit l'Option_Statu_Quo (« ne rien changer ») d'un Problème (Exigence 4.3)."""
    return ReformSeed(
        title="Statu quo — maintenir le droit en vigueur",
        description=description,
        is_status_quo=True,
    )


# ---------------------------------------------------------------------------
# Jeu de démonstration MVP : ≥ 10 Problèmes_Juridiques (Exigence 1.2).
# Chaque Problème compte 3 à 5 Réformes dont exactement une Option_Statu_Quo.
# Les ``theme_slug`` référencent le Référentiel_Thématique V1 (scripts/seed.py).
# ---------------------------------------------------------------------------
PROBLEMS_V1: list[ProblemSeed] = [
    ProblemSeed(
        slug="delais-de-paiement-tpe-pme",
        title="Délais de paiement subis par les TPE et PME",
        summary=(
            "Malgré le plafond légal, de nombreuses TPE/PME subissent des retards de "
            "paiement qui fragilisent leur trésorerie et provoquent des défaillances."
        ),
        theme_slug="economie",
        complexity_level=_COMPLEXITY_MOYEN,
        affected_citizens_count=1_200_000,
        concerned_legal_texts=["Code de commerce, art. L441-10 et L441-16"],
        jurisprudence_refs=["Cass. com., 10 janv. 2018, n° 16-24.156"],
        source_title="Code de commerce — délais de paiement (Légifrance)",
        source_url="https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000032230773",
        reforms=[
            _status_quo(
                "Conserver le plafond actuel et le régime de sanctions administratives "
                "existant sans modification."
            ),
            ReformSeed(
                title="Renforcer les sanctions automatiques en cas de retard",
                description=(
                    "Rendre l'intérêt de retard exigible de plein droit et relever le "
                    "montant des amendes administratives pour dépassement du plafond."
                ),
                expected_impact="Réduction des retards subis par les petites entreprises.",
            ),
            ReformSeed(
                title="Créer un guichet public de médiation accélérée",
                description=(
                    "Instaurer une procédure de médiation gratuite et bornée dans le "
                    "temps entre donneur d'ordre et fournisseur en cas de litige."
                ),
            ),
        ],
    ),
    ProblemSeed(
        slug="opacite-des-marches-publics-locaux",
        title="Opacité de certains marchés publics locaux",
        summary=(
            "Le suivi des attributions et avenants de marchés publics locaux reste "
            "difficile d'accès pour les citoyens, limitant le contrôle démocratique."
        ),
        theme_slug="finances-publiques",
        complexity_level=_COMPLEXITY_MOYEN,
        affected_citizens_count=500_000,
        concerned_legal_texts=["Code de la commande publique, art. L2196-1 et suivants"],
        jurisprudence_refs=["CE, 30 janv. 2019, n° 421869"],
        source_title="Code de la commande publique — publicité (Légifrance)",
        source_url="https://www.legifrance.gouv.fr/codes/id/LEGITEXT000037701019",
        reforms=[
            _status_quo(
                "Maintenir les obligations de publicité existantes sans portail unifié "
                "supplémentaire."
            ),
            ReformSeed(
                title="Publier en données ouvertes tous les avenants",
                description=(
                    "Imposer la publication en open data des avenants au-delà d'un seuil "
                    "et de leurs motifs, dans un format réutilisable."
                ),
            ),
            ReformSeed(
                title="Créer un portail national des marchés locaux",
                description=(
                    "Regrouper sur un portail unique les attributions locales avec "
                    "moteur de recherche et alertes citoyennes."
                ),
            ),
            ReformSeed(
                title="Étendre le contrôle a posteriori des chambres régionales",
                description=(
                    "Élargir la compétence de contrôle a posteriori et publier des "
                    "synthèses accessibles des observations."
                ),
            ),
        ],
    ),
    ProblemSeed(
        slug="complexite-du-bulletin-de-paie",
        title="Complexité persistante du bulletin de paie",
        summary=(
            "Malgré la simplification engagée, le bulletin de paie reste difficile à "
            "lire pour les salariés, qui peinent à vérifier leurs droits."
        ),
        theme_slug="travail",
        complexity_level=_COMPLEXITY_FAIBLE,
        affected_citizens_count=25_000_000,
        concerned_legal_texts=["Code du travail, art. R3243-1"],
        jurisprudence_refs=["Cass. soc., 20 févr. 2013, n° 11-28.201"],
        source_title="Code du travail — mentions du bulletin de paie (Légifrance)",
        source_url="https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000033769100",
        reforms=[
            _status_quo(
                "Conserver le modèle de bulletin simplifié actuel sans nouvelle "
                "obligation de présentation."
            ),
            ReformSeed(
                title="Imposer un résumé standardisé en tête de bulletin",
                description=(
                    "Rendre obligatoire un bloc de synthèse normalisé : net à payer, "
                    "cotisations agrégées et droits acquis."
                ),
            ),
            ReformSeed(
                title="Fournir un simulateur officiel de vérification",
                description=(
                    "Mettre à disposition un service public permettant de recalculer "
                    "et de vérifier les principaux montants du bulletin."
                ),
            ),
        ],
    ),
    ProblemSeed(
        slug="acces-aux-soins-deserts-medicaux",
        title="Accès aux soins dans les déserts médicaux",
        summary=(
            "De nombreux territoires manquent de médecins, allongeant les délais de "
            "rendez-vous et l'éloignement des soins de premier recours."
        ),
        theme_slug="sante",
        complexity_level=_COMPLEXITY_ELEVE,
        affected_citizens_count=6_000_000,
        concerned_legal_texts=["Code de la santé publique, art. L1434-4"],
        jurisprudence_refs=["CE, 17 mars 2021, n° 440208"],
        source_title="Code de la santé publique — organisation de l'offre (Légifrance)",
        source_url="https://www.legifrance.gouv.fr/codes/id/LEGITEXT000006072665",
        reforms=[
            _status_quo(
                "Maintenir les incitations financières actuelles à l'installation sans "
                "mesure de régulation supplémentaire."
            ),
            ReformSeed(
                title="Conditionner le conventionnement dans les zones sur-dotées",
                description=(
                    "Réguler les nouvelles installations en zones sur-dotées pour "
                    "favoriser un rééquilibrage territorial."
                ),
            ),
            ReformSeed(
                title="Développer les maisons de santé pluriprofessionnelles",
                description=(
                    "Financer le déploiement de structures de soins coordonnés dans les "
                    "zones sous-denses avec appui logistique public."
                ),
            ),
            ReformSeed(
                title="Étendre la télémédecine avec accompagnement de proximité",
                description=(
                    "Généraliser les téléconsultations assistées par des points d'accès "
                    "équipés et un personnel de proximité."
                ),
            ),
        ],
    ),
    ProblemSeed(
        slug="lourdeur-inscription-scolaire-en-ligne",
        title="Lourdeur des démarches d'inscription scolaire en ligne",
        summary=(
            "Les familles font face à des démarches d'inscription fragmentées entre "
            "plusieurs services, source d'erreurs et de non-recours."
        ),
        theme_slug="education",
        complexity_level=_COMPLEXITY_FAIBLE,
        affected_citizens_count=3_500_000,
        concerned_legal_texts=["Code de l'éducation, art. L131-5"],
        jurisprudence_refs=["CE, 13 nov. 2013, n° 359640"],
        source_title="Code de l'éducation — obligation scolaire (Légifrance)",
        source_url="https://www.legifrance.gouv.fr/codes/id/LEGITEXT000006071191",
        reforms=[
            _status_quo(
                "Conserver les téléservices existants gérés indépendamment par chaque collectivité."
            ),
            ReformSeed(
                title="Créer un dossier d'inscription unique et pré-rempli",
                description=(
                    "Unifier les démarches sur un dossier unique pré-rempli à partir des "
                    "données déjà connues de l'administration."
                ),
            ),
            ReformSeed(
                title="Garantir un accompagnement humain systématique",
                description=(
                    "Assurer, en parallèle du numérique, un guichet d'accompagnement "
                    "pour éviter le non-recours."
                ),
            ),
        ],
    ),
    ProblemSeed(
        slug="passoires-thermiques-location",
        title="Location de passoires thermiques",
        summary=(
            "Des logements très énergivores restent proposés à la location, pesant sur "
            "les factures des locataires et sur l'objectif climatique."
        ),
        theme_slug="logement",
        complexity_level=_COMPLEXITY_ELEVE,
        affected_citizens_count=5_200_000,
        concerned_legal_texts=["Loi n° 89-462 du 6 juillet 1989, art. 6"],
        jurisprudence_refs=["Cass. 3e civ., 4 juin 2014, n° 13-17.289"],
        source_title="Loi du 6 juillet 1989 — décence du logement (Légifrance)",
        source_url="https://www.legifrance.gouv.fr/loda/id/JORFTEXT000000509310",
        reforms=[
            _status_quo(
                "Maintenir le calendrier d'interdiction de location déjà prévu sans "
                "accélération ni accompagnement supplémentaire."
            ),
            ReformSeed(
                title="Avancer le calendrier avec aides ciblées",
                description=(
                    "Accélérer l'interdiction de location des logements les plus "
                    "énergivores en renforçant les aides à la rénovation."
                ),
            ),
            ReformSeed(
                title="Créer un tiers-financement public de la rénovation",
                description=(
                    "Proposer un préfinancement public remboursé par les économies "
                    "d'énergie pour lever l'obstacle du reste à charge."
                ),
            ),
            ReformSeed(
                title="Renforcer le contrôle de la décence énergétique",
                description=(
                    "Doter les collectivités de moyens de contrôle et de sanction des "
                    "locations non conformes."
                ),
            ),
        ],
    ),
    ProblemSeed(
        slug="fracture-numerique-demarches-administratives",
        title="Fracture numérique face aux démarches administratives",
        summary=(
            "La dématérialisation des démarches exclut une partie des usagers éloignés "
            "du numérique, générant du non-recours aux droits."
        ),
        theme_slug="ia-numerique",
        complexity_level=_COMPLEXITY_MOYEN,
        affected_citizens_count=13_000_000,
        concerned_legal_texts=[
            "Code des relations entre le public et l'administration, art. L112-8"
        ],
        jurisprudence_refs=["CE, 3 juin 2022, n° 452798"],
        source_title="CRPA — saisine par voie électronique (Légifrance)",
        source_url="https://www.legifrance.gouv.fr/codes/id/LEGITEXT000031366350",
        reforms=[
            _status_quo(
                "Conserver l'offre d'accompagnement numérique actuelle sans garantie "
                "légale d'alternative non dématérialisée."
            ),
            ReformSeed(
                title="Garantir une alternative non numérique opposable",
                description=(
                    "Consacrer un droit à une alternative papier ou en présentiel pour "
                    "toute démarche essentielle."
                ),
            ),
            ReformSeed(
                title="Déployer des médiateurs numériques de proximité",
                description=(
                    "Financer un réseau de médiateurs numériques dans les services "
                    "publics de proximité."
                ),
            ),
        ],
    ),
    ProblemSeed(
        slug="delais-excessifs-de-la-justice-civile",
        title="Délais excessifs de la justice civile",
        summary=(
            "L'allongement des délais de jugement en matière civile porte atteinte au "
            "droit à un procès dans un délai raisonnable."
        ),
        theme_slug="justice",
        complexity_level=_COMPLEXITY_ELEVE,
        affected_citizens_count=2_800_000,
        concerned_legal_texts=["Code de l'organisation judiciaire, art. L111-3"],
        jurisprudence_refs=["CEDH, 26 oct. 2000, Kudła c. Pologne, n° 30210/96"],
        source_title="Code de l'organisation judiciaire (Légifrance)",
        source_url="https://www.legifrance.gouv.fr/codes/id/LEGITEXT000006071164",
        reforms=[
            _status_quo(
                "Maintenir l'organisation juridictionnelle actuelle sans renfort de "
                "moyens ni réforme de procédure."
            ),
            ReformSeed(
                title="Renforcer les effectifs de greffe et de magistrats",
                description=(
                    "Programmer un plan pluriannuel de recrutement pour réduire le stock "
                    "d'affaires en attente."
                ),
            ),
            ReformSeed(
                title="Développer la médiation préalable obligatoire",
                description=(
                    "Étendre la médiation préalable pour certains litiges afin de "
                    "désengorger les audiences."
                ),
            ),
            ReformSeed(
                title="Fixer des délais cibles publiés par juridiction",
                description=(
                    "Publier des objectifs de délais par type de contentieux et un suivi "
                    "transparent de leur respect."
                ),
            ),
        ],
    ),
    ProblemSeed(
        slug="pollution-de-l-air-en-zones-urbaines",
        title="Pollution de l'air persistante en zones urbaines",
        summary=(
            "Plusieurs agglomérations dépassent encore les seuils de qualité de l'air, "
            "avec des conséquences sanitaires importantes."
        ),
        theme_slug="ecologie",
        complexity_level=_COMPLEXITY_ELEVE,
        affected_citizens_count=8_000_000,
        concerned_legal_texts=["Code de l'environnement, art. L221-1"],
        jurisprudence_refs=["CE, 4 août 2021, n° 428409 (Association Les Amis de la Terre)"],
        source_title="Code de l'environnement — qualité de l'air (Légifrance)",
        source_url="https://www.legifrance.gouv.fr/codes/id/LEGITEXT000006074220",
        reforms=[
            _status_quo(
                "Conserver les plans de protection de l'atmosphère existants sans "
                "durcissement des mesures."
            ),
            ReformSeed(
                title="Renforcer les zones à faibles émissions avec aides à la mobilité",
                description=(
                    "Étendre les restrictions de circulation les plus polluantes en les "
                    "accompagnant d'aides à la mobilité propre."
                ),
            ),
            ReformSeed(
                title="Investir massivement dans les transports collectifs",
                description=(
                    "Financer l'offre de transports en commun pour offrir une "
                    "alternative crédible à la voiture individuelle."
                ),
            ),
        ],
    ),
    ProblemSeed(
        slug="revenu-agricole-et-prix-abusifs",
        title="Revenu agricole et prix payés aux producteurs",
        summary=(
            "Malgré l'encadrement des négociations commerciales, une part des "
            "producteurs perçoit des prix inférieurs à leurs coûts de production."
        ),
        theme_slug="agriculture",
        complexity_level=_COMPLEXITY_MOYEN,
        affected_citizens_count=400_000,
        concerned_legal_texts=["Code rural et de la pêche maritime, art. L631-24"],
        jurisprudence_refs=["Cass. com., 8 juill. 2020, n° 18-24.320"],
        source_title="Code rural — contractualisation (Légifrance)",
        source_url="https://www.legifrance.gouv.fr/codes/id/LEGITEXT000006071367",
        reforms=[
            _status_quo(
                "Maintenir le dispositif de contractualisation actuel sans contrôle "
                "renforcé des coûts de production."
            ),
            ReformSeed(
                title="Rendre opposables les indicateurs de coûts de production",
                description=(
                    "Imposer la prise en compte d'indicateurs de coûts publiés lors de "
                    "la construction du prix."
                ),
            ),
            ReformSeed(
                title="Renforcer les contrôles et sanctions des pratiques abusives",
                description=(
                    "Doter l'autorité de contrôle de moyens accrus et publier les "
                    "sanctions prononcées."
                ),
            ),
            ReformSeed(
                title="Soutenir les organisations de producteurs",
                description=(
                    "Financer le regroupement des producteurs pour rééquilibrer le "
                    "rapport de force commercial."
                ),
            ),
        ],
    ),
    ProblemSeed(
        slug="cout-du-logement-etudiant",
        title="Coût et pénurie du logement étudiant",
        summary=(
            "L'insuffisance de logements étudiants abordables pèse sur la réussite et "
            "la mobilité des étudiants, en particulier dans les grandes villes."
        ),
        theme_slug="education",
        complexity_level=_COMPLEXITY_MOYEN,
        affected_citizens_count=1_700_000,
        concerned_legal_texts=["Code de la construction et de l'habitation, art. L631-12"],
        jurisprudence_refs=["CE, 11 déc. 2020, n° 426483"],
        source_title="CCH — résidences universitaires (Légifrance)",
        source_url="https://www.legifrance.gouv.fr/codes/id/LEGITEXT000006074096",
        reforms=[
            _status_quo(
                "Conserver le rythme de construction de logements étudiants actuel sans "
                "objectif contraignant."
            ),
            ReformSeed(
                title="Fixer un objectif contraignant de logements CROUS",
                description=(
                    "Programmer un objectif pluriannuel de places de logement social "
                    "étudiant avec financement dédié."
                ),
            ),
            ReformSeed(
                title="Encadrer les loyers dans les zones tendues universitaires",
                description=(
                    "Étendre l'encadrement des loyers autour des principaux campus en zone tendue."
                ),
            ),
        ],
    ),
]


def _validate_seed_data(problems: list[ProblemSeed]) -> None:
    """Vérifie, hors base, la cohérence du jeu de démonstration (Exigences 1.2, 4.1, 4.3).

    Contrôle qu'il y a au moins 10 Problèmes, des ``slug`` uniques, et — pour
    chaque Problème — une cardinalité de Réformes valide au sens du Service
    (``3 ≤ n ≤ 5`` et exactement une Option_Statu_Quo). La validation de
    cardinalité réutilise ``LegalReformService._validate_cardinality`` sur des
    liens transitoires, sans écriture (Exigences 4.1, 4.3).
    """
    assert len(problems) >= 10, (
        f"Le MVP exige au moins 10 Problèmes_Juridiques (trouvé : {len(problems)})."
    )
    slugs = [p.slug for p in problems]
    assert len(set(slugs)) == len(slugs), "Les slugs de Problèmes_Juridiques doivent être uniques."

    for problem in problems:
        transient_links = [
            LegalProblemReform(problem_id=0, proposal_id=0, is_status_quo=reform.is_status_quo)
            for reform in problem.reforms
        ]
        # Réutilise la validation du Service : lève ReformCardinalityError si invalide.
        LegalReformService._validate_cardinality(transient_links)


async def _get_or_create_seed_author(session: AsyncSession) -> User:
    """Retrouve (par email UNIQUE) ou crée l'Utilisateur auteur du seed.

    ``proposals.author_id`` est NOT NULL : les Réformes amorcées ont besoin d'un
    auteur. Le mot de passe est haché (Exigence 1.3) via ``auth_service`` s'il est
    disponible, avec repli sur ``argon2-cffi``.
    """
    author = await session.scalar(select(User).where(User.email == SEED_AUTHOR_EMAIL))
    if author is not None:
        return author

    author = User(
        email=SEED_AUTHOR_EMAIL,
        password_hash=_hash_seed_password(),
        display_name=SEED_AUTHOR_DISPLAY_NAME,
        is_active=True,
        is_verified=True,
        is_admin=False,
    )
    session.add(author)
    await session.flush()
    return author


def _hash_seed_password() -> str:
    """Hache un mot de passe aléatoire pour le compte de seed (jamais de clair — Exigence 1.3).

    Utilise argon2-cffi (``PasswordHasher``), la même stratégie de hachage que
    :class:`AuthService` (Exigence 1.3), pour rester cohérent avec les comptes
    créés par le Service — sans instancier ce dernier (qui requiert une session et
    un client Redis). Le clair, jamais persisté, est un jeton aléatoire.
    """
    import secrets

    from argon2 import PasswordHasher

    password = secrets.token_urlsafe(32)
    hashed: str = PasswordHasher().hash(password)
    return hashed


async def _load_theme_ids(session: AsyncSession, slugs: set[str]) -> dict[str, int]:
    """Charge les ``id`` des Thèmes par ``slug`` ; interrompt si des Thèmes manquent.

    Les Problèmes rattachent un ``theme_id`` valide (Exigence 2.4) : les Thèmes du
    Référentiel V1 doivent avoir été amorcés au préalable (``scripts.seed``).
    """
    result = await session.execute(select(Theme.slug, Theme.id).where(Theme.slug.in_(slugs)))
    mapping = dict(result.all())
    missing = sorted(slugs - mapping.keys())
    if missing:
        raise SystemExit(
            "Thèmes manquants : "
            + ", ".join(missing)
            + ". Amorcez d'abord le Référentiel_Thématique via « python -m scripts.seed »."
        )
    return mapping


async def _get_or_create_legislation_source(session: AsyncSession, spec: ProblemSeed) -> Source:
    """Retrouve (par titre) ou crée la Source ``LEGISLATION`` d'un Problème.

    Une Source de type ``LEGISLATION`` documente chaque Réforme amorcée
    (transparence des Sources_Juridiques, Exigence 10). L'idempotence s'appuie sur
    le titre de la Source, propre à chaque Problème du jeu de démonstration.
    """
    source = await session.scalar(select(Source).where(Source.title == spec.source_title))
    if source is not None:
        return source

    source = Source(
        title=spec.source_title,
        url=spec.source_url,
        publisher="Légifrance",
        source_type="LEGISLATION",
        is_verified=True,
    )
    session.add(source)
    await session.flush()
    return source


async def _seed_one_problem(
    session: AsyncSession,
    spec: ProblemSeed,
    *,
    theme_id: int,
    author_id: int,
) -> None:
    """Amorce un Problème_Juridique complet : Réformes, liens et Source ``LEGISLATION``.

    Crée le Problème, ses 3 à 5 Réformes (``Proposal``) dont exactement une
    Option_Statu_Quo, les liens ``legal_problem_reforms`` et l'association
    ``proposal_sources`` vers la Source ``LEGISLATION`` du Problème. La cardinalité
    est validée via le Service avant l'écriture des liens (Exigences 4.1, 4.3).
    """
    problem = LegalProblem(
        slug=spec.slug,
        title=spec.title,
        summary=spec.summary,
        theme_id=theme_id,
        affected_citizens_count=spec.affected_citizens_count,
        concerned_legal_texts=list(spec.concerned_legal_texts),
        jurisprudence_refs=list(spec.jurisprudence_refs),
        complexity_level=spec.complexity_level,
        status="PUBLISHED",
    )
    session.add(problem)
    await session.flush()  # matérialise problem.id

    source = await _get_or_create_legislation_source(session, spec)

    links: list[LegalProblemReform] = []
    for index, reform in enumerate(spec.reforms):
        proposal = Proposal(
            theme_id=theme_id,
            author_id=author_id,
            slug=f"{spec.slug}-reforme-{index + 1}",
            title=reform.title,
            problem=spec.summary,
            description=reform.description,
            expected_impact=reform.expected_impact,
            status="PUBLISHED",
            version=1,
        )
        session.add(proposal)
        await session.flush()  # matérialise proposal.id

        # Rattachement documentaire : Source LEGISLATION ↔ Réforme (Exigence 10.3).
        session.add(
            ProposalSource(
                proposal_id=proposal.id,
                source_id=source.id,
                relevance_score=1.0,
                note="Source législative de référence du Problème_Juridique.",
            )
        )

        links.append(
            LegalProblemReform(
                problem_id=problem.id,
                proposal_id=proposal.id,
                is_status_quo=reform.is_status_quo,
            )
        )

    # Défense en profondeur : la cardinalité rattachée doit rester valide (4.1, 4.3).
    LegalReformService._validate_cardinality(links)
    session.add_all(links)


async def seed_legal_problems(session: AsyncSession) -> tuple[int, int]:
    """Amorce les Problèmes_Juridiques manquants ; laisse intacts ceux déjà présents.

    Idempotent par ``slug`` : un Problème dont le ``slug`` existe déjà est ignoré
    (ni Réforme ni Source recréées). Retourne le couple ``(créés, ignorés)``.
    """
    existing_result = await session.execute(
        select(LegalProblem.slug).where(LegalProblem.slug.in_([spec.slug for spec in PROBLEMS_V1]))
    )
    existing_slugs = set(existing_result.scalars().all())

    to_create = [spec for spec in PROBLEMS_V1 if spec.slug not in existing_slugs]
    if not to_create:
        return 0, len(PROBLEMS_V1)

    theme_ids = await _load_theme_ids(session, {spec.theme_slug for spec in to_create})
    author = await _get_or_create_seed_author(session)

    for spec in to_create:
        await _seed_one_problem(
            session,
            spec,
            theme_id=theme_ids[spec.theme_slug],
            author_id=author.id,
        )

    created = len(to_create)
    skipped = len(PROBLEMS_V1) - created
    return created, skipped


async def seed() -> None:
    """Point d'entrée asynchrone : amorce les Problèmes_Juridiques de façon idempotente."""
    _validate_seed_data(PROBLEMS_V1)

    async with SessionLocal() as session:
        created, skipped = await seed_legal_problems(session)
        await session.commit()

    print(
        f"Problèmes_Juridiques : {created} créé(s), {skipped} déjà présent(s) "
        f"(jeu de démonstration : {len(PROBLEMS_V1)})."
    )
    print("Amorçage « Réparer la loi » terminé.")


def main() -> None:
    """Point d'entrée synchrone pour ``python -m scripts.seed_legal_problems``."""
    asyncio.run(seed())


if __name__ == "__main__":
    main()
