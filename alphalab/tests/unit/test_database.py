import pytest

from alphalab.storage import PaperDatabase


def test_initialize_idempotent(tmp_path):
    db = PaperDatabase(tmp_path / "a.db")
    assert db.initialize() is True
    assert db.initialize() is False
    assert db.is_initialized()


def test_init_account_refuses_duplicate(tmp_db):
    with pytest.raises(RuntimeError):
        tmp_db.init_account(200000.0, "2026-01-02")


def test_get_cash_falls_back_to_initial_capital(tmp_db):
    # 初始资金入账日期晚于查询日时，仍应返回可用现金
    assert tmp_db.get_cash("2000-01-01") == 100000.0


def test_unique_order_key(tmp_db):
    from datetime import date

    from alphalab.portfolio.order_planner import PlannedOrder

    key = "etf_rotation_v0|2026-06-05|2026-06-08|510300.SH|BUY|v1"
    order = PlannedOrder(
        order_key=key,
        strategy_id="etf_rotation_v0",
        signal_date=date(2026, 6, 5),
        execution_date=date(2026, 6, 8),
        symbol="510300.SH",
        side="BUY",
        planned_quantity=8000,
        reference_price=2.5,
        planned_value=20000.0,
        target_weight=0.19,
    )
    assert tmp_db.insert_orders("run1", [order]) == 1
    assert tmp_db.insert_orders("run2", [order]) == 0  # 唯一键去重
    assert len(tmp_db.get_orders(execution_date="2026-06-08")) == 1


def test_cash_ledger_continuity(tmp_db):
    tmp_db.insert_cash_ledger(
        trade_date="2026-06-08",
        entry_type="BUY",
        amount=-5000.0,
        cash_before=100000.0,
        cash_after=95000.0,
        description="测试",
    )
    rows = tmp_db.cash_ledger_rows()
    assert len(rows) == 2
    assert rows[-1]["cash_after"] == 95000.0


def test_run_status_roundtrip(tmp_db):
    run_id = tmp_db.start_run("PREPARE", "2026-06-05")
    assert tmp_db.has_successful_run("PREPARE", "2026-06-05") is False
    tmp_db.finish_run(run_id, "SUCCESS")
    assert tmp_db.has_successful_run("PREPARE", "2026-06-05") is True

