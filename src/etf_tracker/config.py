from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TrackerConfig:
    raw: dict[str, Any]

    @property
    def total_capital(self) -> float:
        return float(self.raw["total_capital"])

    @property
    def funds(self) -> dict[str, float]:
        return {key: float(value) for key, value in self.raw["funds"].items()}

    @property
    def a500_code(self) -> str:
        return str(self.raw["symbols"]["a500"]["code"])

    @property
    def kc50_code(self) -> str:
        return str(self.raw["symbols"]["kc50"]["code"])

    @property
    def symbols(self) -> dict[str, Any]:
        return self.raw["symbols"]

    @property
    def google_sheet_range(self) -> str:
        return str(self.raw.get("google_sheet", {}).get("range", "Form Responses 1!A:Z"))


def load_config(path: str | Path) -> TrackerConfig:
    with Path(path).open("r", encoding="utf-8") as fh:
        return TrackerConfig(json.load(fh))

