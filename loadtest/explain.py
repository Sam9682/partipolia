"""Plans d'exécution des 5 requêtes les plus fréquentes (Exigence 17.8).

Exécute ``EXPLAIN (ANALYZE, BUFFERS)`` pour : catalogue des revendications, soutiens d'un
utilisateur, réponses d'une consultation, agrégation régionale et lecture du dernier
instantané. Seules des requêtes ``SELECT`` sont exécutées, dans une transaction en lecture seule
annulée à la fin : aucune donnée n'est modifiée.

Sortie dans ``docs/perf/`` : ``explain.md`` (rapport lisible) et ``explain.json``.

CLI : ``python -m loadtest.explain [--database-url URL] [--out DIR]``
(``DATABASE_URL`` par défaut). Code de sortie 1 si la base est injoignable ou une requête échoue.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_OUT = Path("docs/perf")
DEFAULT_DATABASE_URL = "postgresql+psycopg://partipolia:partipolia@localhost:5432/partipolia"
EXIT_OK = 0
EXIT_FAILED = 1


@dataclass(frozen=True)
class PlanQuery:
    key: str
    title: str
    sql: str
    params: dict[str, Any]
    indexes: str


# Les paramètres sont des valeurs représentatives ; les identifiants absents donnent tout de
# même un plan valide (l'index utilisé reste visible).
QUERIES: tuple[PlanQuery, ...] = (
    PlanQuery(
        "catalogue",
        "Catalogue des revendications publiées (tri par soutiens)",
        "SELECT id, title, category_id, support_count, published_at FROM claims "
        "WHERE status = 'published' ORDER BY support_count DESC, id DESC LIMIT 20",
        {},
        "ix_claims_published_support",
    ),
    PlanQuery(
        "supports",
        "Soutiens d'un utilisateur",
        "SELECT claim_id, created_at FROM claim_supports WHERE user_id = :user_id "
        "ORDER BY claim_id LIMIT 100",
        {"user_id": 1},
        "ix_claim_supports_user",
    ),
    PlanQuery(
        "consultation_responses",
        "Réponses d'une consultation (réponses et leurs réponses aux questions)",
        "SELECT a.question_id, a.scale_value, a.selected_options "
        "FROM consultation_responses r "
        "JOIN consultation_answers a ON a.response_id = r.id "
        "WHERE r.consultation_id = :consultation_id",
        {"consultation_id": 1},
        "ix_consultation_responses_consultation, ix_consultation_answers_question",
    ),
    PlanQuery(
        "regional_aggregation",
        "Agrégation régionale des engagements actifs",
        "SELECT u.region, count(DISTINCT c.user_id) AS participants, "
        "avg(c.reduction_percentage) AS avg_reduction "
        "FROM citizen_commitments c JOIN users u ON u.id = c.user_id "
        "WHERE c.status = 'active' AND u.region IS NOT NULL GROUP BY u.region",
        {},
        "ix_citizen_commitments_user_id",
    ),
    PlanQuery(
        "latest_snapshot",
        "Lecture du dernier instantané agrégé d'une région",
        "SELECT * FROM aggregated_impacts "
        "WHERE geographical_level = :level AND geographical_code = :code "
        "ORDER BY snapshot_date DESC LIMIT 1",
        {"level": "region", "code": "11"},
        "ix_aggregated_impacts_level_code_date",
    ),
)


def run_explain(connection: Any, query: PlanQuery) -> list[str]:
    """Retourne les lignes du plan ``EXPLAIN (ANALYZE, BUFFERS)`` de la requête."""
    from sqlalchemy import text

    rows = connection.execute(text(f"EXPLAIN (ANALYZE, BUFFERS) {query.sql}"), query.params)
    return [str(r[0]) for r in rows]


def collect(connection: Any, queries: tuple[PlanQuery, ...] = QUERIES) -> list[dict[str, Any]]:
    """Exécute les plans en lecture seule ; la transaction est annulée à la fin."""
    from sqlalchemy import text

    connection.execute(text("SET TRANSACTION READ ONLY"))
    try:
        return [
            {
                "key": q.key,
                "title": q.title,
                "sql": q.sql,
                "params": q.params,
                "indexes": q.indexes,
                "plan": run_explain(connection, q),
            }
            for q in queries
        ]
    finally:
        connection.rollback()


def render_markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Plans d'exécution des requêtes les plus fréquentes",
        "",
        "Généré par `python -m loadtest.explain` (`EXPLAIN (ANALYZE, BUFFERS)`). "
        "À exécuter sur l'environnement de référence avec le jeu de départ chargé.",
        "",
    ]
    for i, r in enumerate(results, start=1):
        lines += [
            f"## {i}. {r['title']}",
            "",
            f"Index attendus : `{r['indexes']}`",
            "",
            "```sql",
            r["sql"],
            "```",
            "",
            f"Paramètres : `{json.dumps(r['params'])}`",
            "",
            "```text",
            *r["plan"],
            "```",
            "",
        ]
    return "\n".join(lines)


def write_outputs(results: list[dict[str, Any]], out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    md = out / "explain.md"
    js = out / "explain.json"
    md.write_text(render_markdown(results), encoding="utf-8")
    js.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return md, js


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    try:
        from sqlalchemy import create_engine

        engine = create_engine(args.database_url)
        with engine.connect() as conn:
            results = collect(conn)
    except Exception as exc:  # noqa: BLE001 - toute erreur de connexion/requête est fatale
        print(f"ERREUR : plans d'exécution non produits : {exc}", file=sys.stderr)
        return EXIT_FAILED
    md, js = write_outputs(results, args.out)
    print(f"Plans écrits dans {md} et {js}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
