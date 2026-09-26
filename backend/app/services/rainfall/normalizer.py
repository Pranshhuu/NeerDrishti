"""Rainfall normalization for NeerDrishti.

Converts provider-specific Open-Meteo historical JSON into the
provider-independent rainfall record used by downstream processing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class RainfallRecord:
    """Normalized rainfall value with provenance."""

    timestamp: str
    rainfall_mm: float | None
    latitude: float
    longitude: float
    source: str
    source_type: str
    dataset: str
    temporal_resolution: str
    quality: str


def normalize_open_meteo(data: dict[str, Any]) -> list[RainfallRecord]:
    """Normalize an Open-Meteo hourly precipitation response."""

    hourly = data.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("Open-Meteo response is missing 'hourly' data")

    times = hourly.get("time")
    precipitation = hourly.get("precipitation")

    if not isinstance(times, list) or not isinstance(precipitation, list):
        raise ValueError("Open-Meteo hourly time/precipitation arrays are invalid")

    if len(times) != len(precipitation):
        raise ValueError("Hourly time and precipitation arrays have different lengths")

    timezone_name = data.get("timezone", "UTC")

    try:
        timezone = ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError(f"Invalid source timezone: {timezone_name}") from exc

    latitude = float(data["latitude"])
    longitude = float(data["longitude"])

    records: list[RainfallRecord] = []

    for timestamp, rainfall in zip(times, precipitation):
        parsed_timestamp = datetime.fromisoformat(timestamp)

        if parsed_timestamp.tzinfo is None:
            parsed_timestamp = parsed_timestamp.replace(tzinfo=timezone)

        if rainfall is None:
            rainfall_mm = None
            quality = "missing"
        else:
            rainfall_mm = float(rainfall)

            if rainfall_mm < 0:
                raise ValueError(
                    f"Negative precipitation value at {timestamp}: {rainfall_mm}"
                )

            quality = "valid"

        records.append(
            RainfallRecord(
                timestamp=parsed_timestamp.isoformat(),
                rainfall_mm=rainfall_mm,
                latitude=latitude,
                longitude=longitude,
                source="Open-Meteo",
                source_type="reanalysis",
                dataset="ERA5-Land",
                temporal_resolution="1h",
                quality=quality,
            )
        )

    return records


def normalize_open_meteo_file(path: str | Path) -> list[RainfallRecord]:
    """Load and normalize an Open-Meteo JSON file."""

    source_path = Path(path)

    with source_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError("Rainfall source file must contain a JSON object")

    return normalize_open_meteo(data)


def records_to_dicts(records: list[RainfallRecord]) -> list[dict[str, Any]]:
    """Convert normalized records into JSON-serializable dictionaries."""

    return [asdict(record) for record in records]
