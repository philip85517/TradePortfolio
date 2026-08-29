"""行情与元数据加载：本地 CSV/DuckDB 优先，缺失标的回退确定性合成数据。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from ..config import REPO_ROOT, resolve_data_dir
from ..utils import json_hash, normalize_symbol, parse_date, symbol_code
from .synthetic import generate_synthetic_market_data

REQUIRED_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume", "amount"]
STANDARD_COLUMNS = [
    "date",
    "symbol",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "fund_size",
    "premium_discount_rate",
    "asset_class",
    "is_leverage",
    "is_inverse",
    "is_active",
    "is_single_stock",
    "listing_date",
]


def _candidate_data_files(config: dict | None) -> list[Path]:
    cfg = config or {}
    files: list[Path] = []
    market_dir = resolve_data_dir(cfg)
    if market_dir.exists():
        files.extend(sorted(market_dir.glob("*.csv")))
    legacy = REPO_ROOT / "etf_strategy" / "data" / "raw" / "etf_daily_5y.csv"
    if legacy.exists():
        files.append(legacy)
    return files


def _read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV 缺少必需列 {missing}: {path}")
    df["date"] = pd.to_datetime(df["date"])
    return df


def _try_duckdb(config: dict | None) -> pd.DataFrame | None:
    cfg = config or {}
    source = cfg.get("data", {}).get("duckdb")
    if not source:
        return None
    import duckdb

    path = Path(str(source.get("path", ""))).expanduser()
    table = source.get("table", "etf_daily")
    if not path.exists():
        return None
    con = duckdb.connect(str(path), read_only=True)
    try:
        df = con.execute(f"SELECT * FROM {table}").fetchdf()
    except Exception:
        return None
    finally:
        con.close()
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df


def _try_default_duckdb() -> tuple[pd.DataFrame | None, str]:
    """自动发现项目内 etf_strategy 的 DuckDB（无需配置）。"""
    default_path = REPO_ROOT / "etf_strategy" / "data" / "processed" / "etf_strategy.duckdb"
    if not default_path.exists():
        return None, ""
    import duckdb

    con = duckdb.connect(str(default_path), read_only=True)
    try:
        tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        if "etf_daily" not in tables:
            return None, ""
        df = con.execute("SELECT * FROM etf_daily").fetchdf()
    except Exception:
        return None, ""
    finally:
        con.close()
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return None, ""
    df["date"] = pd.to_datetime(df["date"])
    return df, "etf_strategy:duckdb"


def _standardize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["symbol"] = out["symbol"].map(normalize_symbol)
    for col in STANDARD_COLUMNS:
        if col not in out.columns:
            out[col] = None
    return out[STANDARD_COLUMNS].sort_values(["symbol", "date"]).reset_index(drop=True)


def _load_real_data(symbols: list[str], config: dict | None) -> tuple[pd.DataFrame, list[str]]:
    """加载本地真实行情；返回 (数据, 实际来源列表)。"""
    dfs: list[pd.DataFrame] = []
    sources: list[str] = []
    duck = _try_duckdb(config)
    if duck is not None:
        dfs.append(duck)
        sources.append("duckdb")
    default_duck, duck_source = _try_default_duckdb()
    if default_duck is not None:
        dfs.append(default_duck)
        sources.append(duck_source)
    for path in _candidate_data_files(config):
        try:
            dfs.append(_read_csv(path))
            sources.append(str(path))
        except Exception:
            continue
    if not dfs:
        return pd.DataFrame(columns=STANDARD_COLUMNS), sources
    combined = pd.concat(dfs, ignore_index=True)
    combined = _standardize(combined)
    combined = combined[combined["symbol"].isin(symbols)]
    return combined, sources


def load_market_data(
    symbols: Iterable[str],
    start_date: str | date,
    end_date: str | date | None = None,
    config: dict | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """加载目标标的市场数据（含指标 warmup 前置区间）。

    返回 (标准列 DataFrame, 数据快照 dict)。
    缺失的真实标的数据会用确定性合成行情补齐，快照中会注明。
    """
    symbols = sorted({normalize_symbol(s) for s in symbols})
    if not symbols:
        return pd.DataFrame(columns=STANDARD_COLUMNS), {"symbols": []}
    cfg = config or {}
    s = parse_date(start_date)
    e = parse_date(end_date) if end_date else s
    if e < s:
        raise ValueError("end_date 不能早于 start_date")
    warmup = int(cfg.get("data", {}).get("warmup_days", 260))
    warm_start = pd.Timestamp(s) - pd.Timedelta(days=warmup)
    e_ts = pd.Timestamp(e)

    force_synthetic = set(cfg.get("data", {}).get("force_synthetic_symbols", []))
    real_symbols = [s for s in symbols if s not in force_synthetic]
    real, sources = _load_real_data(real_symbols, cfg)
    synthetic_fallback = bool(cfg.get("data", {}).get("synthetic_fallback", True))
    rows_by_symbol: dict[str, pd.DataFrame] = {}
    if not real.empty:
        real = real[real["date"] <= e_ts]
        for sym in symbols:
            part = real[real["symbol"] == sym]
            part = part[(part["date"] >= warm_start) & (part["date"] <= e_ts)]
            rows_by_symbol[sym] = part

    syn_symbols: list[str] = []
    missing_symbols: list[str] = []
    for sym in symbols:
        if sym in force_synthetic:
            missing_symbols.append(sym)
            if synthetic_fallback:
                syn_symbols.append(sym)
            continue
        part = rows_by_symbol.get(sym)
        if part is None or part.empty or len(part) < 5:
            missing_symbols.append(sym)
            if synthetic_fallback:
                syn_symbols.append(sym)

    if syn_symbols:
        seed = int(cfg.get("data", {}).get("synthetic_seed", 42))
        syn = generate_synthetic_market_data(syn_symbols, s, e, seed=seed)
        for sym in syn_symbols:
            part = syn[syn["symbol"] == sym]
            if part.empty:
                continue
            rows_by_symbol[sym] = part

    if not rows_by_symbol:
        return pd.DataFrame(columns=STANDARD_COLUMNS), {
            "symbols": symbols,
            "sources": sources,
            "synthetic_symbols": syn_symbols,
            "missing_symbols": missing_symbols,
            "latest_market_date": None,
            "row_count": 0,
        }

    out = pd.concat(list(rows_by_symbol.values()), ignore_index=True)
    out = out.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"], keep="last").reset_index(drop=True)
    checksum = json_hash(out.fillna("").to_dict("records"))
    snapshot = {
        "symbols": symbols,
        "sources": sources,
        "synthetic_symbols": syn_symbols,
        "missing_symbols": missing_symbols,
        "range": [warm_start.date().isoformat(), e.isoformat()],
        "latest_market_date": pd.to_datetime(out["date"]).max().date().isoformat(),
        "row_count": len(out),
        "checksum": checksum,
    }
    return out, snapshot


def load_etf_metadata(
    symbols: Iterable[str],
    config: dict | None = None,
    market_data: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """加载 ETF 元数据；优先真实元数据，缺失字段用行情/合成默认值回填。"""
    symbols = sorted({normalize_symbol(s) for s in symbols})
    meta_rows: dict[str, dict] = {}
    if market_data is not None and not market_data.empty:
        md = market_data.copy()
        md["_listing_dt"] = pd.to_datetime(md["listing_date"], errors="coerce")
        min_listing = md.groupby("symbol")["_listing_dt"].min()
        last = md.sort_values(["symbol", "date"]).groupby("symbol").tail(1)
        for _, row in last.iterrows():
            meta_rows[row["symbol"]] = {
                "name": row.get("name"),
                "fund_size": row.get("fund_size"),
                "asset_class": row.get("asset_class"),
                "listing_date": min_listing.get(row["symbol"]) or row.get("listing_date"),
                "is_leverage": bool(row.get("is_leverage")),
                "is_inverse": bool(row.get("is_inverse")),
                "is_active": bool(row.get("is_active")),
                "is_single_stock": bool(row.get("is_single_stock")),
            }
    legacy_meta = REPO_ROOT / "etf_strategy" / "data" / "raw" / "etf_meta_latest.csv"
    if legacy_meta.exists():
        try:
            meta = pd.read_csv(legacy_meta)
            for _, row in meta.iterrows():
                sym = normalize_symbol(str(row.get("symbol", "")))
                if sym not in meta_rows:
                    meta_rows[sym] = {}
                for col, key in [
                    ("name", "name"),
                    ("fund_size", "fund_size"),
                    ("asset_class", "asset_class"),
                    ("listing_date", "listing_date"),
                    ("is_leverage", "is_leverage"),
                    ("is_inverse", "is_inverse"),
                    ("is_active", "is_active"),
                    ("is_single_stock", "is_single_stock"),
                ]:
                    if col in meta.columns and row.get(col) is not None and not pd.isna(row.get(col)):
                        meta_rows[sym].setdefault(key, row[col])
        except Exception:
            pass
    # DuckDB 元数据（etf_meta）优先回填
    default_path = REPO_ROOT / "etf_strategy" / "data" / "processed" / "etf_strategy.duckdb"
    if default_path.exists():
        try:
            import duckdb

            con = duckdb.connect(str(default_path), read_only=True)
            try:
                tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
                if "etf_meta" in tables:
                    meta = con.execute("SELECT * FROM etf_meta").fetchdf()
                    for _, row in meta.iterrows():
                        sym = normalize_symbol(str(row.get("symbol", "")))
                        if sym not in meta_rows:
                            meta_rows[sym] = {}
                        for col, key in [
                            ("name", "name"),
                            ("fund_size", "fund_size"),
                            ("asset_class", "asset_class"),
                            ("listing_date", "listing_date"),
                            ("is_leverage", "is_leverage"),
                            ("is_inverse", "is_inverse"),
                            ("is_active", "is_active"),
                            ("is_single_stock", "is_single_stock"),
                        ]:
                            if col in meta.columns and row.get(col) is not None and not pd.isna(row.get(col)):
                                meta_rows[sym].setdefault(key, row[col])
            except Exception:
                pass
            finally:
                con.close()
        except Exception:
            pass

    rows = []
    for sym in symbols:
        info = meta_rows.get(sym, {})
        rows.append(
            {
                "symbol": sym,
                "name": info.get("name") or f"ETF-{symbol_code(sym)}",
                "fund_size": info.get("fund_size") or 5e8,
                "asset_class": info.get("asset_class") or "OTHER",
                "listing_date": pd.Timestamp(info.get("listing_date") or "2000-01-01"),
                "is_leverage": bool(info.get("is_leverage", False)),
                "is_inverse": bool(info.get("is_inverse", False)),
                "is_active": bool(info.get("is_active", False)),
                "is_single_stock": bool(info.get("is_single_stock", False)),
            }
        )
    return pd.DataFrame(rows)
