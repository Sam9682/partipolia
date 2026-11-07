"""Indirect fiscal impact: Leontief input-output model on INSEE A38 data.

Economic sectors are nodes and intermediate purchases are weighted edges:

    A[i][j] = domestic intermediate purchases of product i by branch j / output of j
    x = (I - A)^-1 f          (output needed to meet final demand f)

The shock ``f`` is the household spending *no longer made*, excluding VAT, spread
over products by the ``demand_share`` parameters and reduced by the import share of
each product (spending that leaks abroad creates no domestic activity).

Which taxes this module counts (the others are counted elsewhere, so nothing is
counted twice):

* counted: net production taxes and corporate tax on the value added lost in
  every affected branch (first round included);
* NOT counted: VAT on final goods (``direct``), VAT on intermediate purchases
  (deductible for taxable businesses, hence fiscally neutral), fuel excise
  (``fuel``), and every labour-related flow (``employment``).

Reported indicators are kept distinct: reduced turnover (output), reduced value
added, and reduced tax revenue.

Determinism: all arithmetic is ``Decimal`` with a fixed precision; the inverse is
computed by Gauss-Jordan elimination with partial pivoting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.economics.decimal_utils import ONE, ZERO, engine_context, money
from app.economics.types import ModelSnapshot

_INVERSE_CACHE: dict[
    tuple[str, tuple[str, ...], str], tuple[tuple[str, ...], list[list[Decimal]]]
] = {}
_CACHE_MAX = 16


@dataclass(frozen=True, slots=True)
class IndirectImpact:
    """Indirect (supplier chain) impact of one household plan, one scenario."""

    final_demand: Mapping[str, Decimal]  # domestic demand shock by product (EUR)
    import_leakage: Decimal  # spending that goes to imports (EUR)
    output_change: Mapping[str, Decimal]  # reduced turnover by branch (EUR)
    value_added_change: Mapping[str, Decimal]  # reduced value added by branch (EUR)
    turnover_change: Decimal
    value_added_total: Decimal
    value_added_exact: Mapping[str, Decimal]  # unrounded, input of the employment model
    production_tax: Decimal
    corporate_tax: Decimal

    @property
    def tax_revenue_loss(self) -> Decimal:
        return self.production_tax + self.corporate_tax


def invert(matrix: list[list[Decimal]]) -> list[list[Decimal]]:
    """Invert a square matrix by Gauss-Jordan elimination (partial pivoting)."""
    n = len(matrix)
    with localcontext(engine_context()):
        aug = [row[:] + [ONE if i == j else ZERO for j in range(n)] for i, row in enumerate(matrix)]
        for col in range(n):
            pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
            if aug[pivot][col] == ZERO:
                raise ValueError("singular matrix: the economy is not productive")
            aug[col], aug[pivot] = aug[pivot], aug[col]
            scale = aug[col][col]
            aug[col] = [v / scale for v in aug[col]]
            for r in range(n):
                if r != col and aug[r][col] != ZERO:
                    factor = aug[r][col]
                    aug[r] = [a - factor * b for a, b in zip(aug[r], aug[col], strict=True)]
        return [row[n:] for row in aug]


def _technical_coefficients(
    branches: tuple[str, ...],
    output: Mapping[str, Decimal],
    flows: Mapping[tuple[str, str], Decimal],
) -> list[list[Decimal]]:
    with localcontext(engine_context()):
        matrix: list[list[Decimal]] = []
        for i in branches:
            row = []
            for j in branches:
                z = flows.get((i, j), ZERO)
                row.append(z / output[j] if output[j] else ZERO)
            matrix.append(row)
        return matrix


def leontief_inverse(
    snapshot: ModelSnapshot, scenario: str
) -> tuple[tuple[str, ...], list[list[Decimal]]]:
    """(I - A)^-1 for the snapshot's input-output data (cached per model version)."""
    cache_key = (snapshot.model_version, snapshot.dataset_versions, scenario)
    cached = _INVERSE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    output = {k: v.value(scenario) for k, v in snapshot.keyed("io_output").items()}
    branches = tuple(sorted(output))
    flows: dict[tuple[str, str], Decimal] = {}
    for key, value in snapshot.keyed("io_flow").items():
        product, branch = key.split(">")
        flows[(product, branch)] = value.value(scenario)
    a = _technical_coefficients(branches, output, flows)
    n = len(branches)
    with localcontext(engine_context()):
        i_minus_a = [[(ONE if i == j else ZERO) - a[i][j] for j in range(n)] for i in range(n)]
    result = (branches, invert(i_minus_a))
    if len(_INVERSE_CACHE) >= _CACHE_MAX:
        _INVERSE_CACHE.pop(next(iter(_INVERSE_CACHE)))
    _INVERSE_CACHE[cache_key] = result
    return result


def compute_indirect_impact(
    snapshot: ModelSnapshot,
    turnover_ht_by_category: Mapping[str, Decimal],
    scenario: str,
) -> IndirectImpact:
    """Indirect impact of the avoided spending (excl. VAT) of each category."""
    branches, inverse = leontief_inverse(snapshot, scenario)
    index = {b: i for i, b in enumerate(branches)}
    import_share = {k: v.value(scenario) for k, v in snapshot.keyed("io_import_share").items()}

    with localcontext(engine_context()):
        demand: dict[str, Decimal] = dict.fromkeys(branches, ZERO)
        leakage = ZERO
        for category, turnover in turnover_ht_by_category.items():
            for product, share in snapshot.keyed("demand_share", category).items():
                if product not in index:
                    raise KeyError(f"demand_share targets unknown branch {product!r}")
                spent = turnover * share.value(scenario)
                imported = spent * import_share.get(product, ZERO)
                demand[product] += spent - imported
                leakage += imported

        f = [demand[b] for b in branches]
        x = [sum((inverse[i][j] * f[j] for j in range(len(f))), ZERO) for i in range(len(f))]
        output = {k: v.value(scenario) for k, v in snapshot.keyed("io_output").items()}
        added = {k: v.value(scenario) for k, v in snapshot.keyed("branch_value_added").items()}
        ebe = {k: v.value(scenario) for k, v in snapshot.keyed("branch_ebe").items()}

        output_change: dict[str, Decimal] = {}
        va_change: dict[str, Decimal] = {}
        profit_base = ZERO
        for b, dx in zip(branches, x, strict=True):
            output_change[b] = dx
            dva = dx * added[b] / output[b] if output[b] else ZERO
            va_change[b] = dva
            if added[b]:
                profit_base += dva * ebe[b] / added[b]
        va_total = sum(va_change.values(), ZERO)
        production_tax = va_total * snapshot.param("production_tax_rate_on_va").value(scenario)
        corporate_tax = (
            profit_base
            * snapshot.param("ebe_to_taxable_profit").value(scenario)
            * snapshot.param("corporate_tax_rate").value(scenario)
        )
        return IndirectImpact(
            final_demand={b: money(v) for b, v in demand.items() if v},
            import_leakage=money(leakage),
            output_change={b: money(v) for b, v in output_change.items() if money(v)},
            value_added_change={b: money(v) for b, v in va_change.items() if money(v)},
            turnover_change=money(sum(output_change.values(), ZERO)),
            value_added_total=money(va_total),
            value_added_exact=dict(va_change),
            production_tax=money(production_tax),
            corporate_tax=money(corporate_tax),
        )
