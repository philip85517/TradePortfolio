"""pytest 共享夹具。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from alphalab.config import load_account_config, load_config, merge_strategy_account  # noqa: E402
from alphalab.data import Universe  # noqa: E402
from alphalab.data.loader import load_market_data  # noqa: E402
from alphalab.storage import PaperDatabase  # noqa: E402

SYNTH_SYMBOLS = ["510300.SH", "510500.SH", "159915.SZ", "588000.SH", "518880.SH", "513100.SH"]


@pytest.fixture(scope="session")
def strategy_config() -> dict:
    cfg = merge_strategy_account(load_config(), load_account_config())
    cfg["data"]["synthetic_seed"] = 42
    return cfg


@pytest.fixture
def synthetic_market_data(strategy_config) -> object:
    return load_market_data(SYNTH_SYMBOLS, "2026-05-01", "2026-08-21", strategy_config)


@pytest.fixture
def tmp_db(tmp_path) -> PaperDatabase:
    db = PaperDatabase(tmp_path / "paper.db")
    db.initialize()
    db.init_account(100000.0, "2026-01-01")
    return db


@pytest.fixture
def tmp_universe(tmp_path) -> Universe:
    u = Universe(tmp_path / "universe.yaml")
    u.add(SYNTH_SYMBOLS, synthetic=True)
    return u


@pytest.fixture
def forced_synthetic_config(strategy_config) -> dict:
    cfg = dict(strategy_config)
    cfg.setdefault("data", {})["force_synthetic_symbols"] = list(SYNTH_SYMBOLS)
    return cfg

