"""集成测试：prepare → execute → reconcile → report 全链路。"""

from datetime import date

import pandas as pd
import pytest

from alphalab.data.loader import load_market_data
from alphalab.paper.execute import AlreadyExecutedError, execute
from alphalab.paper.prepare import AlreadyPreparedError, prepare
from alphalab.paper.reconcile import reconcile
from alphalab.paper.replay import replay
from alphalab.paper.report import build_report


def test_full_loop_single_rebalance(forced_synthetic_config, tmp_db, tmp_universe):
    res = prepare("2026-06-05", forced_synthetic_config, tmp_db, tmp_universe)
    assert len(res.orders) > 0
    assert all(o.side == "BUY" for o in res.orders)  # 空仓首轮全部买入
    assert all(o.execution_date.isoformat() == "2026-06-08" for o in res.orders)

    ex = execute("2026-06-08", forced_synthetic_config, tmp_db, tmp_universe)
    assert len(ex.fills) == len(res.orders)
    assert ex.anomalies == []
    assert ex.total_equity > 0

    rc = reconcile("2026-06-08", forced_synthetic_config, tmp_db, tmp_universe)
    assert rc.anomalies == []

    report = build_report("2026-06-08", tmp_db, tmp_universe, forced_synthetic_config)
    assert "账户摘要" in report
    assert "今日成交" in report
    assert "当前持仓" in report


def test_rerun_protection(forced_synthetic_config, tmp_db, tmp_universe):
    prepare("2026-06-05", forced_synthetic_config, tmp_db, tmp_universe)
    with pytest.raises(AlreadyPreparedError):
        prepare("2026-06-05", forced_synthetic_config, tmp_db, tmp_universe)
    execute("2026-06-08", forced_synthetic_config, tmp_db, tmp_universe)
    with pytest.raises(AlreadyExecutedError):
        execute("2026-06-08", forced_synthetic_config, tmp_db, tmp_universe)
    # 重复执行不产生重复成交
    assert len(tmp_db.get_fills("2026-06-08")) == 5


def test_missing_open_price_rejects_order(forced_synthetic_config, tmp_db, tmp_universe):
    prepare("2026-06-05", forced_synthetic_config, tmp_db, tmp_universe)
    # 构造缺失开盘价的数据：把执行日某标的 open 置为 NaN
    md, _ = load_market_data(
        tmp_universe.symbols(), "2026-06-08", "2026-06-08", forced_synthetic_config
    )
    sym = tmp_universe.symbols()[0]
    md.loc[md["symbol"] == sym, "open"] = float("nan")
    ex = execute("2026-06-08", forced_synthetic_config, tmp_db, tmp_universe, market_data=md)
    rejected = [r for r in ex.rejected if r.symbol == sym]
    assert any("NO_PRICE" in r.reason for r in rejected)


def test_multi_week_replay_clean(forced_synthetic_config, tmp_db, tmp_universe):
    res = replay(
        "2026-06-01",
        "2026-06-26",
        forced_synthetic_config,
        tmp_db,
        tmp_universe,
    )
    assert res.total_fills > 0
    assert res.total_rejected == 0
    assert res.anomalies == 0
    navs = tmp_db.nav_series()
    assert len(navs) == len(res.trading_days)
    # 资产恒等式逐日成立（账本已在对账中校验，这里再抽查）
    for nav in navs:
        assert nav["cash"] >= 0
        assert nav["total_equity"] > 0


def test_replay_deterministic(forced_synthetic_config, tmp_universe):
    from alphalab.storage import PaperDatabase

    db1 = PaperDatabase(tmp_universe.path.parent / "d1.db")
    db2 = PaperDatabase(tmp_universe.path.parent / "d2.db")
    r1 = replay("2026-06-01", "2026-06-26", forced_synthetic_config, db1, tmp_universe, reset_account=True)
    r2 = replay("2026-06-01", "2026-06-26", forced_synthetic_config, db2, tmp_universe, reset_account=True)
    n1 = [(n["trade_date"], n["cash"], n["total_equity"]) for n in db1.nav_series()]
    n2 = [(n["trade_date"], n["cash"], n["total_equity"]) for n in db2.nav_series()]
    assert n1 == n2
    assert abs(r1.final_equity - r2.final_equity) < 0.01


def test_nav_continuity(forced_synthetic_config, tmp_db, tmp_universe):
    replay("2026-06-01", "2026-06-26", forced_synthetic_config, tmp_db, tmp_universe)
    navs = tmp_db.nav_series()
    for prev, cur in zip(navs, navs[1:]):
        expected = cur["total_equity"] - prev["total_equity"]
        assert abs(expected - cur["daily_pnl"]) < 0.02

