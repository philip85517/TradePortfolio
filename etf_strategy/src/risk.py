from __future__ import annotations

import pandas as pd


def max_drawdown(equity: pd.Series) -> tuple[float, pd.Timestamp | None, pd.Timestamp | None]:
    if equity.empty:
        return 0.0, None, None
    peak = equity.cummax()
    drawdown = equity / peak - 1
    end = drawdown.idxmin()
    start = equity.loc[:end].idxmax()
    return float(drawdown.loc[end]), start, end

