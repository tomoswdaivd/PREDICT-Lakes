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
| `forecast` | planned | forecast state and model configuration | planned |
| `verify_forecast` | planned | forecasts and later targets | planned |
| `report` | planned | standard outputs | planned |

The JSON files in each component directory are internal portability aids, not official LifeWatch manifests. A future wrapper can map their explicit inputs, outputs, parameters, and metadata to the formal builder representation (including any verified `annotation.json` schema) without moving scientific logic into R. Relative paths are accepted at the CLI; no notebook state or interactive input is required.

## Unresolved compatibility questions

- Which exact `annotation.json` fields and value types are required by the current builder?
- How should Python runtime/dependency information and CSV/JSON ports be declared in the builder?
- What packaging or container conventions are required for a Python component in MyLifeWatch?
- Which provenance fields are required by the platform beyond the local manifest?
