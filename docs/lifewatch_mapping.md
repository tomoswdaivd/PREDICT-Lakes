# LifeWatch ERIC portability mapping

The cited [RStudio Workflow Component Builder tutorial](https://gitlab.lifewatch.dev/workflows/rstudio-component-builder/-/wikis/home) was accessible during setup. It describes repository-based components, an `annotation.json` metadata workflow, component exploration in RStudio, versioned releases, and publication to the MyLifeWatch platform. This repository does not claim exact LifeWatch formal compatibility: the official component metadata specification and required field set have not been independently verified here.

## Local mapping

Each local component has a single CLI entry point, ordinary Python-callable core functions, explicit arguments, file-based outputs, JSON contracts, and deterministic processing where the source is fixed.

| Local component | Conceptual workflow role | Inputs | Outputs |
| --- | --- | --- | --- |
| `ingest_observations` | acquire/register observations | URL or local source, dataset ID, paths, licence | source-preserving raw file and JSON provenance manifest |
| `standardise_observations` | canonicalise observations and inventory source | source CSV, dataset ID, optional availability time | canonical long CSV and JSON inventory |
| `target_depth_audit` | assess common near-surface target | canonical 1 m/2 m observation CSVs, date range, completeness threshold | JSON metrics summary, daily comparison CSV, PNG plot |
| `build_forecast_state` | create issue-time-limited model input | canonical observations, issue time, availability mode, completeness threshold | JSON state and daily-history CSV |
| `build_forecast_targets` | construct verification-side observed targets | canonical observations, forecast-state record, daily/window completeness thresholds | JSON containing three non-overlapping 30-day observed target windows |
| `forecast_climatology` | expanding historical seasonal baseline | canonical observations, leakage-safe forecast-state record, completeness and minimum-history parameters | JSON containing three common-schema mean-temperature forecasts |
| `forecast_anomaly_persistence` | unfitted full persistence of the current 30-day thermal anomaly | canonical observations, leakage-safe forecast-state record, completeness and minimum-history parameters | JSON containing three common-schema mean-temperature forecasts plus current-anomaly provenance |
| `forecast_anomaly_relaxation` | fitted lead-dependent relaxation of the current anomaly | canonical observations, leakage-safe forecast-state record, distinct-year and completeness parameters | JSON containing three common-schema forecasts, raw/constrained coefficients, and training-case provenance |
| `verify_forecast` | compare forecasts with later observed targets | climatology forecast records and independently constructed observed targets | CSV forecast-target pairs and JSON diagnostic summary (prototype hindcast implementation) |
| `report` | planned | standard outputs | planned |

The JSON files in each component directory are internal portability aids, not official LifeWatch manifests. A future wrapper can map their explicit inputs, outputs, parameters, and metadata to the formal builder representation (including any verified `annotation.json` schema) without moving scientific logic into R. Relative paths are accepted at the CLI; no notebook state or interactive input is required.

The intended branches are explicitly separate: `standardise_observations -> build_forecast_state -> forecast` supplies only issue-time-legal information, while `standardise_observations -> build_forecast_targets -> verify_forecast` supplies future observations only to verification. `build_forecast_targets` output must not be connected to the forecast component input.

The implemented forecast branch is `standardise_observations -> build_forecast_state -> forecast_climatology`. Its wrapper accepts canonical CSV and forecast-state JSON, but no target artifact. It filters canonical rows to calendar years strictly earlier than the issue year before daily aggregation and provenance hashing. The JSON output carries model configuration, exact target windows, training years and window means, inherited availability assumptions, source identifiers, and hashes. The weekly hindcast runner is reproducible experiment orchestration around this component; its verification join occurs only after all forecast records have been created.

`forecast_anomaly_persistence` is swappable with `forecast_climatology` at the same conceptual forecast step and emits the same per-horizon forecast fields. It additionally records the legally available recent 30-day window, its matched historical climatology, and the fixed anomaly adjustment. Its standalone wrapper filters recent observations by issue-time availability and historical climatologies by issue year before aggregation and hashing. Paired hindcast diagnostics remain verification-side outputs and are not component inputs.

`forecast_anomaly_relaxation` uses the same ports and common forecast fields. It internally constructs completed prior-year weekly training cases from canonical observations, fits one constrained coefficient per horizon, and records raw and constrained coefficients, row counts, distinct years, years used, and case hashes. The current issue's verification target is not an input. This remains an internal portability design rather than an official LifeWatch manifest.

## Unresolved compatibility questions

- Which exact `annotation.json` fields and value types are required by the current builder?
- How should Python runtime/dependency information and CSV/JSON ports be declared in the builder?
- What packaging or container conventions are required for a Python component in MyLifeWatch?
- Which provenance fields are required by the platform beyond the local manifest?
