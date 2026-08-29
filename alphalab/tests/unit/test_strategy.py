from datetime import date

import pandas as pd
import pytest

from alphalab.data.loader import load_etf_metadata, load_market_data
from alphalab.strategies.base import FutureDataError
from alphalab.strategies.etf_rotation_v0 import generate_target_portfolio


def _market_data(config):
    symbols = ["510300.SH", "510500.SH", "159915.SZ", "588000.SH", "518880.SH", "513100.SH"]
    cfg = dict(config)
    cfg.setdefault("data", {})["force_synthetic_symbols"] = list(symbols)
    md, _ = load_market_data(symbols, "2026-05-01", "2026-06-12", cfg)
    return md


def test_future_data_raises(forced_synthetic_config):
    md = _market_data(forced_synthetic_config)
    meta = load_etf_metadata(["510300.SH"], forced_synthetic_config, md)
    with pytest.raises(FutureDataError):
        generate_target_portfolio("2026-06-01", md, meta, {}, 100000.0, forced_synthetic_config)


def test_rebalance_day_generates_targets(forced_synthetic_config):
    md = _market_data(forced_synthetic_config)
    mdd = md[pd.to_datetime(md["date"]) <= pd.Timestamp("2026-06-05")]  # 周五
    meta = load_etf_metadata(md["symbol"].unique().tolist(), forced_synthetic_config, mdd)
    res = generate_target_portfolio("2026-06-05", mdd, meta, {}, 100000.0, forced_synthetic_config)
    assert len(res.targets) > 0
    assert all(t.signal in ("BUY", "HOLD") for t in res.targets)
    total = sum(t.target_weight for t in res.targets)
    assert total <= 1.0 + 1e-9
    assert total > 0.0
    assert all(t.target_weight <= 0.20 + 1e-9 for t in res.targets)


def test_non_rebalance_day_holds(forced_synthetic_config):
    md = _market_data(forced_synthetic_config)
    mdd = md[pd.to_datetime(md["date"]) <= pd.Timestamp("2026-06-04")]  # 周四，非调仓日
    meta = load_etf_metadata(md["symbol"].unique().tolist(), forced_synthetic_config, mdd)
    res = generate_target_portfolio("2026-06-04", mdd, meta, {}, 100000.0, forced_synthetic_config)
    assert res.targets == []
    assert res.diagnostics.get("hold") is True


def test_strategy_deterministic(forced_synthetic_config):
    md = _market_data(forced_synthetic_config)
    mdd = md[pd.to_datetime(md["date"]) <= pd.Timestamp("2026-06-05")]
    meta = load_etf_metadata(md["symbol"].unique().tolist(), forced_synthetic_config, mdd)
    r1 = generate_target_portfolio("2026-06-05", mdd, meta, {}, 100000.0, forced_synthetic_config)
    r2 = generate_target_portfolio("2026-06-05", mdd, meta, {}, 100000.0, forced_synthetic_config)
    assert [(t.symbol, t.target_weight, t.rank) for t in r1.targets] == [
        (t.symbol, t.target_weight, t.rank) for t in r2.targets
    ]


def test_exit_signal_for_dropped_symbol(forced_synthetic_config):
    md = _market_data(forced_synthetic_config)
    mdd = md[pd.to_datetime(md["date"]) <= pd.Timestamp("2026-06-05")]
    meta = load_etf_metadata(md["symbol"].unique().tolist(), forced_synthetic_config, mdd)
    # 持有任意不在目标组合中的标的 → 应生成 EXIT
    positions = {"000300.SH": {"quantity": 1000, "available_quantity": 1000, "average_cost": 1.0}}
    res = generate_target_portfolio("2026-06-05", mdd, meta, positions, 100000.0, forced_synthetic_config)
    exits = [t for t in res.targets if t.signal == "EXIT"]
    assert any(t.symbol == "000300.SH" for t in exits)
