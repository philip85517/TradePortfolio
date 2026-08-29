"""黄金样本测试（SPEC 24.3）：固定输入 → 固定预期净值。"""

from alphalab.paper.replay import replay
from alphalab.storage import PaperDatabase

# 由确定性合成数据（seed=42）+ 默认配置 + 固定区间（2026-06-01 ~ 2026-06-26）生成并封存
GOLDEN_FINAL_EQUITY = 101067.8


def test_golden_sample_final_equity(forced_synthetic_config, tmp_universe, tmp_path):
    db = PaperDatabase(tmp_path / "golden.db")
    res = replay("2026-06-01", "2026-06-26", forced_synthetic_config, db, tmp_universe, reset_account=True)
    assert abs(res.final_equity - GOLDEN_FINAL_EQUITY) <= 0.01
