from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_datetime(df, column: str = "date"):
    out = df.copy()
    out[column] = out[column].pipe(lambda s: __import__("pandas").to_datetime(s))
    return out


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

