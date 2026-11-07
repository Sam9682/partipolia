"""Profils de charge Locust (Exigences 16.4 à 16.7, 18.1).

Sélection du profil par la variable d'environnement ``LOADTEST_PROFILE`` :

* ``public_read``        : accueil, tableaux de bord national et régional, catalogue, consultations
                           (200 VU, palier >= 120 s, 300 s par défaut) ;
* ``compute``            : simulateurs personnel et national anonymes (100 VU, palier >= 120 s) ;
* ``auth_write``         : revendications, soutiens, réponses aux consultations, engagements
                           (50 VU, palier >= 120 s) ;
* ``participation_spike``: montée linéaire de 0 à 500 VU en 60 s puis maintien de 60 s ;
* ``smoke``              : 20 VU pendant 60 s sur les cinq points d'accès de lecture (CI).

Autres variables : ``LOADTEST_SEED`` (graine des comptes, défaut 1), ``LOADTEST_USERS`` et
``LOADTEST_DURATION`` (paliers des trois premiers profils, durée plancher 120 s).

Garde-fou (16.11) : l'hôte cible doit figurer dans ``loadtest/config.toml``, sinon aucune requête
n'est émise et le code de sortie est non nul. Les réponses 429 explicites (limitation de débit)
sont marquées comme réussies afin que le taux d'erreur de Locust corresponde à la définition de
l'Exigence 17.1 ; les 5xx, erreurs réseau et délais dépassés restent des échecs.

Exemple : ``LOADTEST_PROFILE=smoke locust -f loadtest/locustfile.py --headless -H http://localhost:8080``
"""

from __future__ import annotations

import itertools
import os
import random
import sys
from typing import Any

from locust import HttpUser, LoadTestShape, between, events, task

from loadtest.data import LoadAccount, generate_dataset
from loadtest.guard import TargetNotAllowedError, ensure_allowed

PROFILES = ("public_read", "compute", "auth_write", "participation_spike", "smoke")
PROFILE = os.environ.get("LOADTEST_PROFILE", "smoke")
if PROFILE not in PROFILES:
    raise SystemExit(f"ERREUR : profil inconnu {PROFILE!r} (attendu : {', '.join(PROFILES)})")

SEED = int(os.environ.get("LOADTEST_SEED", "1"))
MIN_TIER_S = 120

# (utilisateurs virtuels, durée par défaut du palier en secondes)
_TIERS = {"public_read": (200, 300), "compute": (100, 120), "auth_write": (50, 120)}

# Constantes du profil « pic de participation » (16.7) et « fumée » (18.1).
SPIKE_USERS, SPIKE_RAMP_S, SPIKE_HOLD_S = 500, 60, 60
SMOKE_USERS, SMOKE_DURATION_S = 20, 60

REGION_CODES = ("11", "24", "27", "28", "32", "44", "52", "53", "75", "76", "84", "93", "94")
CATEGORY = "restaurants"  # code de catégorie de référence du simulateur


# --------------------------------------------------------------------------- garde-fou
def _check_target(host: str | None) -> bool:
    if not host:
        return True  # hôte encore inconnu (interface web) : revérifié au démarrage du test
    try:
        ensure_allowed(host)
    except TargetNotAllowedError as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return False
    return True


@events.init.add_listener
def _guard_on_init(environment, **_kw: Any) -> None:
    if not _check_target(environment.host):
        sys.exit(1)


@events.test_start.add_listener
def _guard_on_start(environment, **_kw: Any) -> None:
    if not _check_target(environment.host):
        environment.process_exit_code = 1
        if environment.runner:
            environment.runner.quit()
        sys.exit(1)


# --------------------------------------------------------------------------- utilitaires
def _ok_or_rate_limited(resp, accepted: tuple[int, ...] = ()) -> None:
    """429 explicite (et statuts attendus) = succès ; 5xx / autres = échec."""
    code = resp.status_code
    if code == 429 or code in accepted or 200 <= code < 300:
        resp.success()
    else:
        resp.failure(f"HTTP {code}")


class _Base(HttpUser):
    abstract = True
    wait_time = between(0.5, 2)

    def get_ok(self, path: str, name: str | None = None, **kw: Any):
        with self.client.get(path, name=name or path, catch_response=True, **kw) as resp:
            _ok_or_rate_limited(resp)
            return resp

    def post_ok(self, path: str, payload: dict, name: str | None = None, accepted=(), **kw: Any):
        with self.client.post(
            path, json=payload, name=name or path, catch_response=True, **kw
        ) as resp:
            _ok_or_rate_limited(resp, accepted)
            return resp


def _json(resp) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - corps absent ou non JSON
        return None


# --------------------------------------------------------------------------- lecture publique
class _ReadMixin:
    """Lecture des cinq pages/points d'accès publics (16.4, 18.1)."""

    def read_home(self) -> None:
        self.get_ok("/", name="GET /")

    def read_national(self) -> None:
        self.get_ok("/api/v1/dashboard/national", name="GET /api/v1/dashboard/national")

    def read_regional(self) -> None:
        code = random.choice(REGION_CODES)
        self.get_ok("/api/v1/dashboard/regions", name="GET /api/v1/dashboard/regions")
        self.get_ok(
            f"/api/v1/dashboard/regions/{code}", name="GET /api/v1/dashboard/regions/{code}"
        )

    def read_catalog(self) -> None:
        page = random.randint(1, 3)
        sort = random.choice(("recent", "supports", "category"))
        self.get_ok(f"/api/v1/claims?sort={sort}&page={page}", name="GET /api/v1/claims")

    def read_consultations(self) -> None:
        self.get_ok("/api/v1/consultations", name="GET /api/v1/consultations")


class PublicReadUser(_ReadMixin, _Base):
    @task(2)
    def home(self) -> None:
        self.read_home()

    @task(3)
    def national(self) -> None:
        self.read_national()

    @task(3)
    def regional(self) -> None:
        self.read_regional()

    @task(3)
    def catalog(self) -> None:
        self.read_catalog()

    @task(2)
    def consultations(self) -> None:
        self.read_consultations()


class SmokeUser(_ReadMixin, _Base):
    wait_time = between(0.2, 1)

    @task
    def read_all(self) -> None:
        self.read_home()
        self.read_national()
        self.read_regional()
        self.read_catalog()
        self.read_consultations()


# --------------------------------------------------------------------------- calcul anonyme
class ComputeUser(_Base):
    @task(3)
    def personal(self) -> None:
        self.post_ok(
            "/api/v1/simulations/calculate",
            {
                "reductions": {CATEGORY: str(random.choice((10, 25, 50, 100)))},
                "adults": random.randint(1, 4),
                "horizon": random.choice(("short", "medium", "long")),
            },
            name="POST /api/v1/simulations/calculate",
        )

    @task(2)
    def national(self) -> None:
        self.post_ok(
            "/api/v1/national-simulations/calculate",
            {
                "participation_rate": str(random.choice((1, 5, 10, 25))),
                "reductions": {CATEGORY: str(random.choice((10, 25, 50)))},
                "horizon": random.choice(("short", "medium", "long")),
            },
            name="POST /api/v1/national-simulations/calculate",
        )


# --------------------------------------------------------------------------- écriture authentifiée
_ACCOUNT_COUNTER = itertools.count()
_ACCOUNTS: tuple[LoadAccount, ...] = ()


def _accounts() -> tuple[LoadAccount, ...]:
    global _ACCOUNTS
    if not _ACCOUNTS:
        _ACCOUNTS = generate_dataset(SEED, accounts=max(SPIKE_USERS, 500), objects_per_account=0).accounts
    return _ACCOUNTS


class AuthWriteUser(_Base):
    """Citoyen authentifié : revendications, soutiens, réponses, engagements (16.6)."""

    token: str | None = None
    claim_ids: list[Any]

    def on_start(self) -> None:
        accounts = _accounts()
        self.account = accounts[next(_ACCOUNT_COUNTER) % len(accounts)]
        self.claim_ids = []
        self.token = None
        self._login()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _login(self) -> None:
        acc = self.account
        self.client.post(
            "/api/v1/auth/register",
            json={"email": acc.email, "password": acc.password, "display_name": acc.pseudonym},
            name="POST /api/v1/auth/register",
        )  # 409 si déjà inscrit : sans importance (mise en place hors mesure)
        with self.client.post(
            "/api/v1/auth/login",
            json={"email": acc.email, "password": acc.password},
            name="POST /api/v1/auth/login",
            catch_response=True,
        ) as resp:
            _ok_or_rate_limited(resp)
            body = _json(resp) if resp.status_code == 200 else None
            self.token = (body or {}).get("access_token")

    @task(2)
    def create_claim(self) -> None:
        if not self.token:
            return self._login()
        n = random.getrandbits(48)
        resp = self.post_ok(
            "/api/v1/claims",
            {
                "title": f"{self.account.pseudonym} revendication {n}",
                "body": f"Revendication de charge {n} : réduire certaines dépenses et le déclarer.",
            },
            name="POST /api/v1/claims",
            headers=self._headers(),
        )
        data = _json(resp) if resp.status_code in (200, 201) else None
        if data and data.get("id") is not None:
            with self.client.post(
                f"/api/v1/claims/{data['id']}/publish",
                name="POST /api/v1/claims/{id}/publish",
                headers=self._headers(),
                catch_response=True,
            ) as pub:
                _ok_or_rate_limited(pub, accepted=(422,))  # 422 = quota atteint : refus métier

    @task(4)
    def support_claim(self) -> None:
        if not self.token:
            return
        if not self.claim_ids:
            data = _json(self.get_ok("/api/v1/claims?sort=recent&page=1", name="GET /api/v1/claims"))
            items = (data or {}).get("items", data if isinstance(data, list) else []) or []
            self.claim_ids = [c["id"] for c in items if isinstance(c, dict) and "id" in c]
            if not self.claim_ids:
                return
        cid = random.choice(self.claim_ids)
        with self.client.put(
            f"/api/v1/claims/{cid}/support",
            name="PUT /api/v1/claims/{id}/support",
            headers=self._headers(),
            catch_response=True,
        ) as resp:
            _ok_or_rate_limited(resp, accepted=(404, 422))  # soutien de sa propre revendication, etc.

    @task(2)
    def respond_consultation(self) -> None:
        if not self.token:
            return
        listing = _json(self.get_ok("/api/v1/consultations", name="GET /api/v1/consultations"))
        items = (listing or {}).get("items", listing if isinstance(listing, list) else []) or []
        open_ones = [c for c in items if isinstance(c, dict) and c.get("status", "published") == "published"]
        if not open_ones:
            return
        cid = open_ones[0]["id"]
        detail = _json(
            self.get_ok(f"/api/v1/consultations/{cid}", name="GET /api/v1/consultations/{id}")
        )
        answers = _build_answers((detail or {}).get("questions", []))
        # 409 = participation déjà enregistrée (doublon refusé) : comportement attendu.
        with self.client.post(
            f"/api/v1/consultations/{cid}/responses",
            json={"answers": answers},
            name="POST /api/v1/consultations/{id}/responses",
            headers=self._headers(),
            catch_response=True,
        ) as resp:
            _ok_or_rate_limited(resp, accepted=(409,))

    @task(2)
    def commitment(self) -> None:
        if not self.token:
            return
        self.post_ok(
            "/api/v1/commitments",
            {
                "category_code": CATEGORY,
                "reduction_percentage": str(random.choice((5, 10, 20))),
                "duration_months": random.choice((1, 3, 6, 12)),
            },
            name="POST /api/v1/commitments",
            accepted=(409, 422),  # engagement déjà déclaré pour la catégorie / quota
            headers=self._headers(),
        )


def _build_answers(questions: list[Any]) -> list[dict[str, Any]]:
    """Réponses valides pour chaque Question selon son type (échelle 1..5, option, texte)."""
    answers: list[dict[str, Any]] = []
    for q in questions:
        if not isinstance(q, dict) or "id" not in q:
            continue
        kind = str(q.get("kind", ""))
        options = [o.get("id") if isinstance(o, dict) else o for o in (q.get("options") or [])]
        if "scale" in kind:
            answers.append({"question_id": q["id"], "scale": random.randint(1, 5)})
        elif "text" in kind:
            if q.get("required"):
                answers.append({"question_id": q["id"], "text": "Réponse de charge."})
        elif options:
            pick = random.choice(options)
            answers.append({"question_id": q["id"], "options": [pick]})
    return answers


class ParticipationSpikeUser(AuthWriteUser):
    """Pic de participation : tous les VU soumettent une réponse (17.4)."""

    wait_time = between(0.2, 1)

    @task(10)
    def respond_consultation(self) -> None:  # type: ignore[override]
        super().respond_consultation()

    # Les autres tâches héritées restent actives avec un poids moindre.


# --------------------------------------------------------------------------- sélection du profil
_USER_BY_PROFILE: dict[str, type[HttpUser]] = {
    "public_read": PublicReadUser,
    "compute": ComputeUser,
    "auth_write": AuthWriteUser,
    "participation_spike": ParticipationSpikeUser,
    "smoke": SmokeUser,
}
for _cls in (PublicReadUser, ComputeUser, AuthWriteUser, ParticipationSpikeUser, SmokeUser):
    _cls.abstract = _cls is not _USER_BY_PROFILE[PROFILE]
del _cls  # évite qu'une classe soit découverte deux fois par Locust


def _tier_params() -> tuple[int, int]:
    users_default, duration_default = _TIERS[PROFILE]
    users = int(os.environ.get("LOADTEST_USERS", users_default))
    duration = max(MIN_TIER_S, int(os.environ.get("LOADTEST_DURATION", duration_default)))
    return users, duration


class ProfileShape(LoadTestShape):
    """Forme de charge du profil sélectionné (une seule forme par exécution)."""

    def tick(self):
        t = self.get_run_time()
        if PROFILE == "participation_spike":
            if t < SPIKE_RAMP_S:  # montée linéaire 0 -> 500 VU en 60 s
                users = max(1, round(SPIKE_USERS * t / SPIKE_RAMP_S))
                return users, max(1, SPIKE_USERS / SPIKE_RAMP_S)
            if t < SPIKE_RAMP_S + SPIKE_HOLD_S:  # maintien 60 s
                return SPIKE_USERS, SPIKE_USERS
            return None
        if PROFILE == "smoke":
            return (SMOKE_USERS, SMOKE_USERS) if t < SMOKE_DURATION_S else None
        users, duration = _tier_params()
        return (users, max(1, users // 10)) if t < duration else None
