"""Tests unitaires du ``PrivacyService`` (Exigences 28.4, 28.5 ; tâche 10.3).

Couvre les fonctions RGPD sans base de données :

* :meth:`PrivacyService.retention_policy` — politique de rétention explicite
  couvrant comptes, Votes, journaux, conversations IA et signalements
  (Exigence 28.5) ;
* :meth:`PrivacyService.get_consents` / :meth:`update_consents` — lecture et
  remplacement du registre de consentement, avec normalisation en booléens
  (Exigence 28.4) ;
* :meth:`PrivacyService.export_user_data` — export du compte et des
  contributions, sans ``password_hash`` ni profil politique (Exigences 28.4,
  28.1, 28.2) ;
* :meth:`PrivacyService.delete_account` — suppression des contributions
  personnelles et anonymisation des Propositions rédigées (Exigence 28.4).

La couche d'accès aux données est remplacée par une session factice qui
**interprète les vraies constructions SQLAlchemy** (``select``/``delete``/``update``)
produites par le Service, sans monkeypatch des colonnes ORM : elle lit l'entité
ciblée et la clause ``WHERE`` (colonne + valeur liée) pour filtrer des objets en
mémoire. La logique métier (agrégation, normalisation, anonymisation) est ainsi
exercée telle quelle.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.privacy_service import (
    _ANONYMIZED_AUTHOR_EMAIL,
    PrivacyError,
    PrivacyService,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


class _Obj:
    """Objet léger imitant une entité ORM (attributs libres)."""

    def __init__(self, **attrs: object) -> None:
        self.__dict__.update(attrs)


def _user(user_id: int = 1, **overrides: object) -> _Obj:
    base: dict[str, object] = dict(
        id=user_id,
        email="citoyen@example.org",
        password_hash="secret-hash",
        display_name="Citoyen",
        is_active=True,
        is_verified=True,
        is_admin=False,
        consents={},
        created_at=_NOW,
        updated_at=_NOW,
    )
    base.update(overrides)
    return _Obj(**base)


def _select_entity(stmt: object) -> str:
    """Nom de l'entité ciblée par un ``select(Model)``."""
    return stmt.column_descriptions[0]["entity"].__name__  # type: ignore[attr-defined]


def _dml_table(stmt: object) -> str:
    """Nom de table (``__tablename__``) d'un ``delete``/``update``."""
    return stmt.table.name  # type: ignore[attr-defined]


# Correspondance ``__tablename__`` → nom de modèle utilisé comme clé de table.
_TABLE_TO_MODEL = {
    "users": "User",
    "proposals": "Proposal",
    "proposal_versions": "ProposalVersion",
    "votes": "Vote",
    "arguments": "Argument",
    "comments": "Comment",
}


def _where(stmt: object) -> tuple[str, object] | None:
    """Extrait ``(colonne, valeur)`` de la clause ``WHERE`` (égalité simple)."""
    clause = stmt.whereclause  # type: ignore[attr-defined]
    if clause is None:
        return None
    return clause.left.key, clause.right.value


class _ScalarsResult:
    def __init__(self, rows: list[_Obj]) -> None:
        self._rows = rows

    def all(self) -> list[_Obj]:
        return list(self._rows)


class _FakeSession:
    """Session factice interprétant les vraies constructions SQLAlchemy."""

    def __init__(self, tables: dict[str, list[_Obj]]) -> None:
        self.tables = {name: list(rows) for name, rows in tables.items()}
        self._next_id = 1000

    async def get(self, model: type, ident: int) -> _Obj | None:
        for row in self.tables.get(model.__name__, []):
            if row.id == ident:
                return row
        return None

    def _rows_for_select(self, stmt: object) -> list[_Obj]:
        rows = self.tables.get(_select_entity(stmt), [])
        where = _where(stmt)
        if where is None:
            return list(rows)
        column, value = where
        return [r for r in rows if getattr(r, column) == value]

    async def scalars(self, stmt: object) -> _ScalarsResult:
        return _ScalarsResult(self._rows_for_select(stmt))

    async def scalar(self, stmt: object) -> _Obj | None:
        rows = self._rows_for_select(stmt)
        return rows[0] if rows else None

    async def execute(self, stmt: object) -> None:
        kind = type(stmt).__name__
        model = _TABLE_TO_MODEL[_dml_table(stmt)]
        where = _where(stmt)
        rows = self.tables.get(model, [])
        matched = (
            rows
            if where is None
            else [r for r in rows if getattr(r, where[0]) == where[1]]
        )
        if kind == "Delete":
            self.tables[model] = [r for r in rows if r not in matched]
        elif kind == "Update":
            values = {c.key: getattr(v, "value", v) for c, v in stmt._values.items()}  # type: ignore[attr-defined]
            for row in matched:
                for key, value in values.items():
                    setattr(row, key, value)

    def add(self, obj: object) -> None:
        self.tables.setdefault("User", []).append(obj)  # type: ignore[arg-type]

    async def flush(self) -> None:
        for row in self.tables.get("User", []):
            if getattr(row, "id", None) is None:
                row.id = self._next_id
                self._next_id += 1


# --------------------------------------------------------------------------- #
# Politique de rétention (Exigence 28.5)                                       #
# --------------------------------------------------------------------------- #
def test_retention_policy_covers_all_required_categories() -> None:
    """La politique couvre comptes, Votes, journaux, conversations IA, signalements (Exigence 28.5)."""
    policy = PrivacyService.retention_policy()
    categories = {rule.category for rule in policy.rules}

    assert "Comptes" in categories
    assert "Votes" in categories
    assert any("Journ" in c for c in categories)
    assert any("IA" in c for c in categories)
    assert "Signalements" in categories
    for rule in policy.rules:
        assert rule.retention.strip()
        assert rule.basis.strip()


# --------------------------------------------------------------------------- #
# Gestion du consentement (Exigence 28.4)                                      #
# --------------------------------------------------------------------------- #
async def test_get_consents_returns_current_state() -> None:
    """La lecture renvoie le registre de consentement courant (Exigence 28.4)."""
    session = _FakeSession({"User": [_user(consents={"analytics": True})]})
    state = await PrivacyService(session).get_consents(1)  # type: ignore[arg-type]

    assert state.consents == {"analytics": True}


async def test_update_consents_replaces_and_normalizes() -> None:
    """La mise à jour remplace le registre et normalise en booléens (Exigence 28.4)."""
    user = _user(consents={"old": True})
    session = _FakeSession({"User": [user]})

    state = await PrivacyService(session).update_consents(  # type: ignore[arg-type]
        1, {"analytics": False, "newsletter": True}
    )

    assert state.consents == {"analytics": False, "newsletter": True}
    assert user.consents == {"analytics": False, "newsletter": True}


async def test_consents_missing_user_raises() -> None:
    """Un compte inexistant lève une :class:`PrivacyError`."""
    session = _FakeSession({"User": []})
    with pytest.raises(PrivacyError):
        await PrivacyService(session).get_consents(999)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Export des données (Exigence 28.4)                                           #
# --------------------------------------------------------------------------- #
async def test_export_gathers_account_and_contributions() -> None:
    """L'export rassemble compte + contributions, sans password_hash (Exigences 28.4, 1.3)."""
    session = _FakeSession(
        {
            "User": [_user(1)],
            "Proposal": [
                _Obj(
                    id=5,
                    author_id=1,
                    slug="ma-proposition",
                    title="Ma proposition",
                    status="PUBLISHED",
                    version=2,
                    created_at=_NOW,
                )
            ],
            "Vote": [_Obj(proposal_id=5, user_id=1, value=1, created_at=_NOW)],
            "Argument": [
                _Obj(
                    id=3,
                    proposal_id=5,
                    author_id=1,
                    position="FOR",
                    content="Pour",
                    created_at=_NOW,
                )
            ],
            "Comment": [
                _Obj(
                    id=9,
                    proposal_id=5,
                    author_id=1,
                    parent_id=None,
                    content="Un commentaire",
                    status="VISIBLE",
                    created_at=_NOW,
                )
            ],
        }
    )

    export = await PrivacyService(session).export_user_data(1)  # type: ignore[arg-type]

    assert export.account.email == "citoyen@example.org"
    assert len(export.proposals) == 1 and export.proposals[0].slug == "ma-proposition"
    assert len(export.votes) == 1 and export.votes[0].value == 1
    assert len(export.arguments) == 1 and export.arguments[0].position == "FOR"
    assert len(export.comments) == 1
    assert "password" not in export.model_dump_json()


# --------------------------------------------------------------------------- #
# Suppression de compte (Exigence 28.4)                                        #
# --------------------------------------------------------------------------- #
async def test_delete_account_removes_contributions_and_anonymizes_proposals() -> None:
    """La suppression retire les contributions et anonymise les Propositions (Exigence 28.4)."""
    proposal = _Obj(
        id=5, author_id=1, slug="p", title="P", status="PUBLISHED", version=1, created_at=_NOW
    )
    version = _Obj(id=1, proposal_id=5, version=1, edited_by=1)
    session = _FakeSession(
        {
            "User": [_user(1)],
            "Proposal": [proposal],
            "ProposalVersion": [version],
            "Vote": [_Obj(proposal_id=5, user_id=1, value=1, created_at=_NOW)],
            "Argument": [
                _Obj(id=3, proposal_id=5, author_id=1, position="FOR", content="x", created_at=_NOW)
            ],
            "Comment": [
                _Obj(
                    id=9,
                    proposal_id=5,
                    author_id=1,
                    parent_id=None,
                    content="c",
                    status="VISIBLE",
                    created_at=_NOW,
                )
            ],
        }
    )

    result = await PrivacyService(session).delete_account(1)  # type: ignore[arg-type]

    assert result.deleted is True
    assert result.deleted_votes == 1
    assert result.deleted_arguments == 1
    assert result.deleted_comments == 1
    assert result.anonymized_proposals == 1

    # Le compte de l'Utilisateur a disparu ; un compte système anonyme le remplace.
    remaining_emails = {u.email for u in session.tables["User"]}
    assert "citoyen@example.org" not in remaining_emails
    assert _ANONYMIZED_AUTHOR_EMAIL in remaining_emails

    # La Proposition est réattribuée au compte système ; l'éditeur de version dissocié.
    assert proposal.author_id != 1
    assert version.edited_by is None
    # Les contributions personnelles sont supprimées.
    assert session.tables["Vote"] == []
    assert session.tables["Argument"] == []
    assert session.tables["Comment"] == []


async def test_delete_missing_account_raises() -> None:
    """Supprimer un compte inexistant lève une :class:`PrivacyError`."""
    session = _FakeSession({"User": []})
    with pytest.raises(PrivacyError):
        await PrivacyService(session).delete_account(404)  # type: ignore[arg-type]
