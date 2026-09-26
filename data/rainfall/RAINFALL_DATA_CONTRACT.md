# NeerDrishti Rainfall Data Contract

## Purpose

Define the normalized rainfall representation used by the NeerDrishti
rainfall and runoff pipeline.

The contract allows multiple rainfall providers to feed the same downstream
runoff engine without changing the core processing logic.

## Normalized Rainfall Record

Each rainfall observation or forecast value must contain:

- `timestamp`
  - ISO 8601 timestamp
  - Preserve the source timezone when available

- `rainfall_mm`
  - Precipitation amount in millimetres
  - Must be non-negative
  - `null` is allowed only when the source explicitly reports missing data

- `latitude`
  - Latitude of the source/grid location in decimal degrees

- `longitude`
  - Longitude of the source/grid location in decimal degrees

- `source`
  - Provider name
  - Examples: `Open-Meteo`, `IMD`, `NASA GPM IMERG`

- `source_type`
  - `observation`
  - `reanalysis`
  - `forecast`
  - `scenario`

- `dataset`
  - Specific dataset/model/product name
  - Example: `ERA5-Land`

- `temporal_resolution`
  - Duration represented by each rainfall value
  - Example: `1h`, `30min`, `daily`

- `quality`
  - Data-quality/provenance state
  - `valid`
  - `missing`
  - `estimated`
  - `scenario`

## Provenance Requirements

The normalized layer must preserve enough metadata to identify:

1. Where the rainfall came from
2. Whether it was observed, reanalysis, forecast, or simulated
3. Which dataset/model produced it
4. Where the value applies spatially
5. What time interval it represents

Source semantics must never be silently changed during normalization.

## Current Historical Dataset

Current prototype dataset:

- Provider: `Open-Meteo`
- Dataset: `ERA5-Land`
- Source type: `reanalysis`
- Requested location: `19.0760, 72.8777`
- Returned grid location: `19.086115, 72.85291`
- Timezone: `Asia/Kolkata`
- Temporal resolution: `1h`
- Variable: `precipitation`
- Unit: `mm`
- Period: `2024-06-01` to `2024-06-30`

The requested coordinates and returned grid coordinates must both be
preserved where available. The returned grid location must not be presented
as an exact observation at the requested coordinate.

## Provider Independence

Planned providers:

- Open-Meteo / ERA5-Land — historical reanalysis
- Open-Meteo / ECMWF IFS HRES — forecast
- IMD — Indian observations/reference data when accessible
- NASA GPM IMERG — satellite precipitation when integrated
- Controlled scenario rainfall — simulation/testing

## Scientific Honesty

Reanalysis, forecast, observation, satellite estimate, and controlled
scenario data must remain distinguishable throughout the pipeline.

Simulated rainfall must never be presented as observed rainfall.

Forecast rainfall must never be presented as measured rainfall.

Reanalysis rainfall must never be presented as a rain-gauge observation.

## Downstream Contract

The runoff engine receives normalized rainfall records and must not need
provider-specific parsing logic.

Pipeline:

Rainfall Source
    ->
Normalization
    ->
Quality / Provenance Validation
    ->
Normalized Rainfall Records
    ->
Runoff Engine
    ->
Terrain / Drainage Coupling
    ->
Flood Risk
