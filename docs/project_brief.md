# PREDICT-Lakes prototype brief

## Purpose and decisions

PREDICT-Lakes is a reproducible prototype for forecasting and analysing the thermal state of Windermere South Basin. The intended observation stream is the UKCEH/EIDC automatic buoy archive. Periodic source blocks are acceptable for this prototype.

- Initial target: 2 m water temperature as the common near-surface target across 2008-2018.
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

The 2 m temperature series is present across the full 2008-2018 automatic-buoy archive, whereas the 1 m series is absent from the 2016-2018 release. The common-target decision is based on the matched-depth audit documented in `outputs/target_depth_audit_summary.json`.

EIDC also exposes Windermere South Basin surface-monitoring records for 2019–2025, but these are lower-frequency boat monitoring records, not automatic-buoy blocks. No later automatic-buoy block was identified in the initial catalogue search.

## Daily aggregation rules

The target-depth audit applied the following rule to daily comparison values:

1. Treat source GMT as UTC and use calendar days in UTC.
2. Calculate a daily mean only when at least 18 distinct hourly values (75% of a 24-hour day) are present for each depth; exclude blanks and do not impute.
3. Detect duplicate timestamps and fail rather than average them silently.
4. Keep depth as an explicit key and report depth availability changes.

The 1 m versus 2 m audit produced 2,751 matched daily pairs from 66,069 matched hourly timestamps. Bias (1 m minus 2 m) was +0.024 C, MAE 0.063 C, RMSE 0.148 C, and correlation 0.9996. The warmest 10% of matched days had bias +0.097 C, MAE 0.109 C, RMSE 0.214 C, and correlation 0.9895. Differences were seasonally largest in June (+0.131 C mean bias) and July (+0.098 C), while winter biases were negative and below 0.03 C in magnitude. Yearly mean biases ranged from +0.004 to +0.061 C; 2015 has only 207 eligible matched days.

Decision: adopt 2 m as the common 2008-2018 near-surface forecast target. The close daily agreement and much longer complete-depth coverage support its use for the 30-90 day seasonal thermal-state prototype. This does not imply numerical identity: occasional warm-season differences remain relevant to later uncertainty and verification analyses.

Aggregation issue: the 2008-2011 release contains many timestamps at minute :59 rather than exactly on the hour. The audit retained source timestamps and matched depths exactly; exact-hour-grid counts are reported diagnostically and were not used to discard those observations. The daily rule is therefore based on distinct valid timestamps by UTC calendar date, not timestamp minute == 0.
