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

## Forecast-state semantics

`build_forecast_state` creates the model-agnostic observed 2 m daily history available at an explicit forecast issue timestamp. Historical hindcasts use `availability_mode=observation_time_proxy`: because true historical `data_available_time` is unknown, an observation is eligible only when `observation_time <= forecast_issue_time`. This is an explicitly labelled pseudo-operational assumption, not a reconstruction of actual historical operational availability.

The component preserves source timestamps, excludes later observations before applying the daily completeness rule, and does not turn an intraday partial day into a state unless that cutoff-limited day has at least 18 valid observations. The state records the issue time, last legal raw observation time, last eligible daily date, source identifiers and a hash of only the legally available observation slice. A future `data_available_time` mode is supported in the interface and will require known availability at or before the issue time.

## Forecast-target semantics

The primary targets are three non-overlapping future 30-day means of 2 m water temperature: `month_1` is lead days +1 through +30, `month_2` is +31 through +60, and `month_3` is +61 through +90. Lead days begin after `last_complete_daily_state_date`, not after the raw forecast issue timestamp. These are fixed lead-time windows, not calendar months, cumulative means, or single target days.

Each target requires at least 27 of its 30 expected daily means (90% completeness). Each daily mean must first satisfy the 18-hour rule. Up to three missing days are tolerated to retain windows affected by short gaps while ensuring that the window mean represents nearly the full interval; missing days are not imputed. The target output records actual valid-day counts and completeness.

`build_forecast_targets` is verification-side only. Future observed temperatures are deliberately excluded from `build_forecast_state` and from forecasting inputs. The target builder consumes the state date only to define lead windows and is run independently against observations that occur after that date.

Across 4,003 valid candidate state dates in 2008-2018, 3,949 support `month_1`, 3,889 support both months 1-2, and 3,829 support all three windows under the 27-of-30 rule. These are nested counts; exclusively, 60 dates support month 1 but not month 2, 60 support months 1-2 but not all three, and 3,829 support all three.

## Expanding climatology baseline

The first forecast model is an expanding prior-year window climatology. For an issue in calendar year Y, it uses only observations whose calendar year is strictly less than Y. For each horizon it calculates the same 30-day month/day window in each eligible earlier year using the established 18-hour daily and 27-of-30-day window rules, then averages the valid yearly window means. At least three valid historical years are required. No seasonal smoother is applied, and the historical-window standard deviation is descriptive rather than a calibrated uncertainty forecast.

Calendar alignment preserves the current target window's start month and day and then takes 30 consecutive UTC dates. A 29 February start has no equivalent in a non-leap historical year, so that candidate is recorded and excluded rather than silently shifted. The strict calendar-year training rule also means a historical window crossing into the issue year cannot use its issue-year days.

The initial hindcast uses valid Monday state dates with issue time at 00:00 UTC on Tuesday. Daily issues would have month-1 targets overlapping by 29 of 30 days; weekly issues reduce this redundancy approximately sevenfold while retaining year-round seasonal coverage. There are 571 scheduled issue times in the 2008-2018 archive. Verification is performed only after forecasts have been generated and never supplies target artifacts to the model.

With a minimum of three valid prior years, the verification sample contains 404, 400, and 395 forecasts for months 1, 2, and 3. Forecast-minus-observed bias is -0.403, -0.422, and -0.420 degC; MAE is 0.957, 0.972, and 0.971 degC; RMSE is 1.229, 1.240, and 1.242 degC; and correlation is 0.9723, 0.9716, and 0.9713. High aggregate correlations mainly reflect the shared seasonal cycle and are not, alone, evidence of useful forecast skill. Errors vary substantially with issue month: the largest RMSE values occur for month 1 issued in June (1.750 degC), month 2 issued in May (1.795 degC), and month 3 issued in April (1.746 degC), all of which place much of the target during the warming season. The smallest corresponding RMSE values occur for month 1 issued in August (0.628 degC), month 2 issued in July (0.644 degC), and month 3 issued in June (0.621 degC).

Training history grows visibly through the experiment: no forecasts are valid initially, while most 2011 windows first reach three valid years and 2018 windows generally use nine or ten valid years depending on window completeness and year boundaries. These climatology results are the reference against which anomaly persistence should next be defined and tested.
