"""National scenario simulator: glue between the pure engine and the API.

A national scenario is a *hypothesis*, never a forecast. ``calculate`` has no side
effect: any engine failure (missing parameter, missing reference population,
unavailable snapshot) is converted to :class:`ServiceUnavailableError` (HTTP 503)
before anything is written, and no partial result is ever returned.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.economics.aggregate import aggregate_savings
from app.economics.decimal_utils import ZERO, engine_context, pct_to_fraction
from app.economics.defaults import bootstrap_snapshot
from app.economics.engine import run_engine
from app.economics.mobilisation import mobilisation_gap
from app.economics.national import (
    MissingPopulationError,
    NationalScenario,
    avoided_by_category,
    reference_population,
    with_population_fallback,
)
from app.economics.types import ModelSnapshot
from app.schemas.national_simulation import (
    MAX_COMPARED_SCENARIOS,
    MIN_COMPARED_SCENARIOS,
    MobilisationGap,
    NationalComparison,
    NationalScenarioResult,
    PopulationInfo,
    ScenarioEcho,
    ScenarioPreset,
)
from app.services.aggregation_service import MIN_COHORT_SIZE, AggregationService
from app.services.simulation_presenter import DISCLAIMERS, build_fiscal_result
from app.services.simulation_service import SnapshotProvider

logger = get_logger(__name__)

HYPOTHESIS_DISCLAIMER = (
    "Ce scénario est une hypothèse, non une prévision : il ne présume ni de la mobilisation "
    "réelle, ni des engagements effectivement déclarés."
)
DECLARATIVE_MENTION = "engagements déclaratifs non vérifiés"
_WEAK_LEVELS = frozenset({"provisional", "low"})

#: Disclaimers of the personal simulator that do not apply to a national hypothesis.
_PERSONAL_ONLY = "Votre engagement est déclaratif"


class ServiceUnavailableError(Exception):
    """Engine, model version or dataset unavailable (=> HTTP 503, ``SERVICE_UNAVAILABLE``)."""

    status_code = 503
    code = "SERVICE_UNAVAILABLE"


_PRESETS: tuple[ScenarioPreset, ...] = (
    ScenarioPreset(
        label="Restaurants : 10 % de participation, -20 %",
        description="Un dixième de la population réduit de 20 % ses dépenses de restauration.",
        participation_rate=Decimal("10"),
        reductions={"restaurants": Decimal("20")},
    ),
    ScenarioPreset(
        label="Vêtements : 10 % de participation, -50 %",
        description="Un dixième de la population réduit de moitié ses achats de vêtements.",
        participation_rate=Decimal("10"),
        reductions={"clothing": Decimal("50")},
    ),
    ScenarioPreset(
        label="Abonnements numériques : 25 % de participation, -30 %",
        description="Un quart de la population réduit de 30 % ses abonnements numériques.",
        participation_rate=Decimal("25"),
        reductions={"digital_subscriptions": Decimal("30")},
    ),
    ScenarioPreset(
        label="Loisirs et vacances : 15 % de participation, -25 %",
        description="15 % de la population réduit de 25 % ses loisirs et ses séjours.",
        participation_rate=Decimal("15"),
        reductions={"leisure": Decimal("25"), "hotels_holidays": Decimal("25")},
    ),
    ScenarioPreset(
        label="Toutes catégories : 5 % de participation, -10 %",
        description="5 % de la population réduit de 10 % toutes les catégories de dépenses.",
        participation_rate=Decimal("5"),
        reductions={
            "restaurants": Decimal("10"),
            "clothing": Decimal("10"),
            "leisure": Decimal("10"),
            "hotels_holidays": Decimal("10"),
            "electronics": Decimal("10"),
            "digital_subscriptions": Decimal("10"),
        },
    ),
)


class NationalSimulationService:
    def __init__(self, provider: SnapshotProvider, session: AsyncSession | None = None) -> None:
        self._provider = provider
        self._session = session

    # ------------------------------------------------------------------ #
    async def _snapshot(self) -> ModelSnapshot:
        try:
            snapshot = await self._provider.get_snapshot()
        except Exception as exc:  # noqa: BLE001 - converted to a 503
            logger.warning("national_snapshot_unavailable", exc_info=True)
            raise ServiceUnavailableError("model snapshot unavailable") from exc
        try:
            return with_population_fallback(snapshot, bootstrap_snapshot())
        except MissingPopulationError:
            # Unusable fallback: keep the raw snapshot, the engine will raise (=> 503).
            return snapshot

    async def calculate(self, scenario: NationalScenario) -> NationalScenarioResult:
        snapshot = await self._snapshot()
        return self._safe_calculate(snapshot, scenario)

    async def compare(self, scenarios: Sequence[NationalScenario]) -> NationalComparison:
        if not MIN_COMPARED_SCENARIOS <= len(scenarios) <= MAX_COMPARED_SCENARIOS:
            raise ValueError("2 à 4 scénarios")
        snapshot = await self._snapshot()
        # All results are computed before returning: no partial comparison.
        return NationalComparison(
            results=[self._safe_calculate(snapshot, scenario) for scenario in scenarios]
        )

    def presets(self) -> list[ScenarioPreset]:
        return [preset.model_copy(deep=True) for preset in _PRESETS]

    async def mobilisation(self, scenario_rate: Decimal) -> MobilisationGap:
        """Mobilisation gap; any failure degrades to status ``unavailable``."""
        try:
            return await self._mobilisation(scenario_rate)
        except Exception:  # noqa: BLE001 - degraded state, never fails the request
            logger.warning("national_mobilisation_unavailable", exc_info=True)
            return MobilisationGap(
                status="unavailable",
                scenario_rate=scenario_rate,
                message="Mobilisation non disponible : données de référence inaccessibles.",
            )

    async def _mobilisation(self, scenario_rate: Decimal) -> MobilisationGap:
        row = await AggregationService(self._require_session()).latest()
        if row is None:
            return MobilisationGap(
                status="unavailable",
                scenario_rate=scenario_rate,
                message="Mobilisation non disponible : aucun agrégat n'a encore été calculé.",
            )
        common = {
            "scenario_rate": scenario_rate,
            "calculated_at": row.calculated_at,
            "model_version": row.model_version,
        }
        population = await self._population_or_none()
        if population is None:
            return MobilisationGap(
                status="unavailable",
                message="Mobilisation non disponible : population de référence absente.",
                **common,
            )
        result = mobilisation_gap(
            row.participant_count,
            int(population),
            scenario_rate,
            MIN_COHORT_SIZE,
        )
        if result.status == "ok":
            return MobilisationGap(
                status="ok",
                declared_rate=result.declared_rate,
                gap_pp=result.gap_pp,
                participants=result.participants,
                message=f"Taux de mobilisation calculé à partir d'{DECLARATIVE_MENTION}.",
                **common,
            )
        messages = {
            "unavailable": "Mobilisation non disponible : population de référence absente.",
            "suppressed": "Cohorte trop petite : aucun chiffre de mobilisation n'est publié.",
            "inconsistent": "Données incohérentes : le taux déclaré dépasse 100 %.",
        }
        return MobilisationGap(
            status=result.status,
            message=f"{messages[result.status]} ({DECLARATIVE_MENTION.capitalize()}.)",
            **common,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def calculate_with_snapshot(
        snapshot: ModelSnapshot, scenario: NationalScenario
    ) -> NationalScenarioResult:
        """Pure computation: avoided spending -> savings -> engine -> fiscal result."""
        avoided = avoided_by_category(snapshot, scenario)
        savings = aggregate_savings(snapshot, avoided)
        outcome = run_engine(snapshot, savings, scenario.horizon)
        active = {code for code, value in avoided.items() if value > ZERO}
        fiscal = build_fiscal_result(snapshot, outcome, active, scenario.horizon)
        population = reference_population(snapshot)
        with localcontext(engine_context()):
            participants = (
                population.value * pct_to_fraction(scenario.participation_rate)
            ).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)

        warning: str | None = None
        levels = {fiscal.confidence, population.confidence_label}
        if levels & _WEAK_LEVELS or fiscal.provisional_parameters:
            level = "low" if "low" in levels else "provisional"
            warning = (
                f"Niveau de confiance {level} : certains paramètres du modèle sont "
                "provisoires ou peu fiables ; les fourchettes en tiennent compte."
            )
        return NationalScenarioResult(
            scenario=ScenarioEcho(
                participation_rate=scenario.participation_rate,
                reductions=dict(scenario.reductions),
                horizon=scenario.horizon,
            ),
            model_version=snapshot.model_version,
            dataset_versions=list(snapshot.dataset_versions),
            population=PopulationInfo(
                value=population.value,
                source=population.source,
                as_of=population.as_of,
                confidence_label=population.confidence_label,
            ),
            participants=participants,
            avoided_by_category=avoided,
            fiscal=fiscal,
            confidence=fiscal.confidence,
            confidence_warning=warning,
            provisional_parameters=list(fiscal.provisional_parameters),
            disclaimers=[
                *(d for d in DISCLAIMERS if not d.startswith(_PERSONAL_ONLY)),
                HYPOTHESIS_DISCLAIMER,
            ],
        )

    # ------------------------------------------------------------------ #
    def _safe_calculate(
        self, snapshot: ModelSnapshot, scenario: NationalScenario
    ) -> NationalScenarioResult:
        try:
            return self.calculate_with_snapshot(snapshot, scenario)
        except Exception as exc:  # noqa: BLE001 - KeyError, MissingPopulationError, ...
            logger.warning("national_calculation_failed", error=type(exc).__name__)
            raise ServiceUnavailableError("national scenario engine unavailable") from exc

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise ServiceUnavailableError("database session unavailable")
        return self._session

    async def _population_or_none(self) -> Decimal | None:
        try:
            return reference_population(await self._snapshot()).value
        except Exception:  # noqa: BLE001 - absent population => "unavailable"
            return None


__all__ = [
    "DECLARATIVE_MENTION",
    "HYPOTHESIS_DISCLAIMER",
    "NationalSimulationService",
    "ServiceUnavailableError",
]
