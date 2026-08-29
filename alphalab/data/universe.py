"""目标标的池管理：增删查，持久化到 paper_universe.yaml。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

import yaml

from ..config import DEFAULT_UNIVERSE_CONFIG
from ..utils import iter_unique, normalize_symbol


class Universe:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else DEFAULT_UNIVERSE_CONFIG
        self.path = self.path.expanduser()
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict) or "symbols" not in data:
                raise ValueError(f"标的池配置格式错误: {self.path}")
            return data
        return {"symbols": []}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, sort_keys=False)

    def symbols(self, active_only: bool = True) -> list[str]:
        out = []
        for item in self.data.get("symbols", []):
            if active_only and not item.get("enabled", True):
                continue
            out.append(normalize_symbol(item["symbol"]))
        return out

    def items(self) -> list[dict]:
        return list(self.data.get("symbols", []))

    def add(
        self,
        symbols: Iterable[str],
        *,
        names: dict[str, str] | None = None,
        synthetic: bool = False,
        asset_classes: dict[str, str] | None = None,
    ) -> list[dict]:
        """添加目标标的；重复添加返回现有条目。"""
        existing = {normalize_symbol(x["symbol"]): x for x in self.data["symbols"]}
        added: list[dict] = []
        for raw in symbols:
            sym = normalize_symbol(raw)
            if sym in existing:
                added.append(existing[sym])
                continue
            item = {
                "symbol": sym,
                "name": (names or {}).get(sym, ""),
                "asset_class": (asset_classes or {}).get(sym, "OTHER"),
                "synthetic": bool(synthetic),
                "enabled": True,
                "added_at": date.today().isoformat(),
            }
            self.data["symbols"].append(item)
            existing[sym] = item
            added.append(item)
        self._save()
        return added

    def remove(self, symbols: Iterable[str]) -> list[str]:
        targets = {normalize_symbol(s) for s in symbols}
        kept = [x for x in self.data["symbols"] if normalize_symbol(x["symbol"]) not in targets]
        removed = [
            normalize_symbol(x["symbol"])
            for x in self.data["symbols"]
            if normalize_symbol(x["symbol"]) in targets
        ]
        self.data["symbols"] = kept
        self._save()
        return removed

    def disable(self, symbols: Iterable[str]) -> None:
        targets = {normalize_symbol(s) for s in symbols}
        for item in self.data["symbols"]:
            if normalize_symbol(item["symbol"]) in targets:
                item["enabled"] = False
        self._save()

    def enable(self, symbols: Iterable[str]) -> None:
        targets = {normalize_symbol(s) for s in symbols}
        for item in self.data["symbols"]:
            if normalize_symbol(item["symbol"]) in targets:
                item["enabled"] = True
        self._save()

    @staticmethod
    def merge_metadata(universe: "Universe", meta: dict[str, dict]) -> None:
        """用行情元数据回填名称与资产类别（仅填充空字段）。"""
        changed = False
        for item in universe.data["symbols"]:
            sym = normalize_symbol(item["symbol"])
            info = meta.get(sym)
            if not info:
                continue
            if not item.get("name") and info.get("name"):
                item["name"] = info["name"]
                changed = True
            if item.get("asset_class", "OTHER") == "OTHER" and info.get("asset_class"):
                item["asset_class"] = info["asset_class"]
                changed = True
        if changed:
            universe._save()

    @staticmethod
    def unique_symbols(symbols: Iterable[str]) -> list[str]:
        return iter_unique(normalize_symbol(s) for s in symbols)
