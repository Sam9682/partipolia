"""Liste versionnée des Régions françaises (INSEE COG 2024). Module pur."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

DATA_PATH = Path(__file__).parent / "data" / "regions_insee_2024.json"


@dataclass(frozen=True, slots=True)
class Region:
    code: str
    label: str


@dataclass(frozen=True, slots=True)
class RegionDataset:
    version: str
    source: str
    as_of: date
    confidence: Literal["official", "provisional", "low"]
    regions: tuple[Region, ...]


@lru_cache(maxsize=1)
def load_regions() -> RegionDataset:
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return RegionDataset(
        version=raw["version"],
        source=raw["source"],
        as_of=date.fromisoformat(raw["as_of"]),
        confidence=raw["confidence"],
        regions=tuple(Region(code=r["code"], label=r["label"]) for r in raw["regions"]),
    )


def is_valid_region(code: str | None) -> bool:
    if not isinstance(code, str):
        return False
    return any(r.code == code for r in load_regions().regions)
