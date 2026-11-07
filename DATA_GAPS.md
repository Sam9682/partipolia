# Partipolia - Data sources and known gaps

Rule applied: no economic coefficient or tax figure is invented. When no verified
official figure was available, the parameter is shipped as a configurable
**provisional** assumption with an explicit confidence label and a note, and is
listed here. Administrators can replace any value by importing a new dataset and
publishing a new model version (old simulations stay reproducible).

Confidence labels: `high` (official and verified), `medium` (official but to be
re-confirmed), `low`, `provisional` (assumption).

## Verified, official data (confidence `high`)

| Data | Source | Reference |
|------|--------|-----------|
| Input-output table A38: domestic output, intermediate consumption flows, import share by product | INSEE, Comptes nationaux base 2020, TES symetrique A38 (semi-definitive) - https://www.insee.fr/fr/statistiques/8574722 | 2023 |
| Value added, compensation of employees, gross wages, gross operating surplus by A38 branch | INSEE, tables 6.201, 6.204, 6.205, 6.206 - https://www.insee.fr/fr/statistiques/8988821 | 2023 |
| Legal VAT rates 20 %, 10 %, 5.5 %, 2.1 % | service-public.gouv.fr (F22399, F37288, F37605), economie.gouv.fr | 2026 |
| Standard VAT on fuel (20 %) | economie.gouv.fr | 2026 |

Employer contribution rates by branch are *derived* from these INSEE tables
(compensation / wages - 1), so they are official effective rates.

## Missing parameters (all shipped as `provisional` unless noted)

| Parameter (`parameter_type`) | What is missing | Where to find it |
|------------------------------|-----------------|------------------|
| `vat_split` (restaurants, leisure, hotels_holidays) | Share of each category's spending subject to each VAT rate | INSEE household consumption by COICOP; DGFiP |
| `demand_share` | Allocation of consumer spending to A38 products (producer share vs trade margin) | INSEE TES margins (commerce and transport margins by product) |
| `ebe_to_taxable_profit` | Conversion of gross operating surplus into taxable profit | DGFiP corporate tax statistics |
| `production_tax_rate_on_va` | Net production taxes (D29-D39) as a share of value added, by branch | INSEE national accounts, production taxes by branch |
| `corporate_tax_rate` (`medium`) | Rate to re-confirm (25 %) | impots.gouv.fr |
| `employment_elasticity` (short / medium / long) | Sectoral wage-bill response to value added | DARES, INSEE employment and working-time series |
| `job_loss_share`, `unemployment_months` | Share of adjustment through separations and benefit duration | DARES, Unedic |
| `employee_contribution_rate` (`medium`) | Rate to re-confirm (about 22 %) | URSSAF |
| `income_tax_rate_on_wages` | Average income tax rate on wage income | DGFiP |
| `unemployment_replacement_rate` (`medium`) | Rule to re-confirm | Unedic |
| `partial_activity_cost_rate` | Public cost of partial activity per unit of lost wage | service-public.gouv.fr, DARES |
| `respend_share`, `respend_vat_rate` | Household re-spending of savings and its average VAT rate | INSEE household accounts |
| `fuel_l_per_100km` | Average passenger-car consumption | SDES |
| `fuel_price_ttc_per_l` | Average pump price | SDES / ministry price series |
| `fuel_excise_per_l` | **Current excise per litre.** The tax was renamed "accise sur les produits energetiques (ex-TICPE)"; the schedule could not be retrieved and verified | douane.gouv.fr, impots.gouv.fr |
| `car_share`, `km_per_trip`, `trips_per_1000_eur` (per category) | Mobility behaviour linked to each type of spending | SDES mobility surveys (ENTD / EMP) |

## Known modelling limits (also shown on the methodology page)

* Estimates, not measured tax revenue; ranges reflect parameter uncertainty only.
* Re-spent savings only recover VAT; the activity they create is not modelled.
* Allocation of revenue to central government, local government and social security
  is simplified (VAT and excise are attributed to central government).
* The model is linear: it does not capture capacity constraints or price effects.
* Declared commitments are not verified consumption.
