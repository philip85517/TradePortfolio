"""回测与回放一致性（SPEC 第 23 节）。"""

from alphalab.backtest.engine import run_backtest
from alphalab.paper.replay import replay
from alphalab.storage import PaperDatabase
from alphalab.validation.parity_checks import compare_nav_series, compare_positions, format_parity_report


def test_backtest_replay_parity(forced_synthetic_config, tmp_universe, tmp_path):
    symbols = tmp_universe.symbols()
    bt = run_backtest(symbols, "2026-06-01", "2026-06-26", forced_synthetic_config, 100000.0)
    db = PaperDatabase(tmp_path / "paper.db")
    rp = replay("2026-06-01", "2026-06-26", forced_synthetic_config, db, tmp_universe, reset_account=True)

    replay_nav = __import__("pandas").DataFrame([dict(r) for r in db.nav_series()])
    replay_positions = [
        {"trade_date": p["trade_date"], "symbol": p["symbol"], "quantity": p["quantity"]}
        for p in db.all_positions()
    ]
    diffs = compare_nav_series(bt.nav, replay_nav) + compare_positions(
        bt.position_snapshots, replay_positions
    )
    assert diffs == [], format_parity_report(diffs)
    assert abs(bt.final_equity - rp.final_equity) < 0.01

