# PREDICT-Lakes prototype brief

## Purpose and decisions

PREDICT-Lakes is a reproducible prototype for forecasting and analysing the thermal state of Windermere South Basin. The intended observation stream is the UKCEH/EIDC automatic buoy archive. Periodic source blocks are acceptable for this prototype.

- Initial target: near-surface lake temperature, with 1 m preferred when consistently available.
- Intended horizons: configurable 30, 60, and 90 days.
- Later first models: climatology, persistence/anomaly persistence, then a simple seasonal relaxation model.
- Meteorological predictors or forecasts are a second-stage extension.
- Historical testing must be pseudo-operational and obey issue-time discipline.
- LifeWatch portability is an architectural requirement.
- The first milestone is acquisition, inventory, and standardisation, not model sophistication.

## Current facts

The three cited EIDC automatic-buoy releases are CSV packages with two header rows and nominal hourly averages from measurements every four minutes. They cover 2008–2011, 2012–2015, and 2016–2018. The first and third use spaced column labels; the middle release uses underscore-separated labels. The three releases were accessed on 2026-09-14 and are preserved under ignored `data/raw/`.

All three releases describe GMT timestamps, 12 nominal temperature depths (1, 2, 4, 7, 10, 13, 16, 19, 22, 25, 30, and 35 m), air temperature, solar irradiance, and wind speed. The 2016–2018 file has no 1 m observations; this makes shallowest-consistent-depth selection an explicit data decision, not an assumption.

EIDC records identify the licence as the Open Government Licence and provide DOI citations. Historical data-availability timestamps are not provided, so the implementation records them as unknown rather than fabricating them.

EIDC also exposes Windermere South Basin surface-monitoring records for 2019–2025, but these are lower-frequency boat monitoring records, not automatic-buoy blocks. No later automatic-buoy block was identified in the initial catalogue search.

## Proposed daily aggregation rules

These are proposals for the later forecast-state component, not silently applied here:

1. Treat source GMT as UTC and use calendar days in UTC.
2. Calculate a daily mean only when at least 18 distinct hourly values (75% of a 24-hour day) are present for that variable/depth; retain the count and coverage fraction.
3. Exclude blank values; do not impute during aggregation.
4. Detect duplicate timestamps and fail or require an explicit policy rather than averaging them silently.
5. Keep depth as an explicit key and report depth availability changes; do not substitute a different depth without a documented decision.

The threshold and depth-selection policy remain unresolved scientific choices.
