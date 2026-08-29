"""固定历史研究因子插件边界。

当前只注册一个可复现的 ``fixed_v0`` 插件。插件接口刻意保持很小：它只接收
信号日前的历史截面并返回逐股票结果，不接触未来行情、组合或产物存储。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pandas as pd


class ResearchFactorPlugin(Protocol):
    plugin_id: str
    version: str
    role: str
    supported_markets: tuple[str, ...]
    required_fields: tuple[str, ...]
    min_history_days: int
    score_direction: str
    parameter_schema: dict[str, Any]

    def score(self, before: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
        """对历史截面执行过滤和评分。"""

    def source_hash(self) -> str:
        """返回用于实验复现的规则源码哈希。"""


@dataclass(frozen=True)
class FixedV0Plugin:
    """第一版固定选股规则的插件适配器。"""

    plugin_id: str = "fixed_v0"
    version: str = "1.0.0"
    role: str = "filter_and_score"
    supported_markets: tuple[str, ...] = ("a_share",)
    required_fields: tuple[str, ...] = ("close", "amount", "adjustment")
    min_history_days: int = 120
    score_direction: str = "higher_is_better"
    parameter_schema: dict[str, Any] = field(default_factory=dict)

    def score(self, before: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
        # 延迟导入避免 engine 与插件 registry 之间形成循环依赖。
        from .engine import _score_fixed_v0

        return _score_fixed_v0(before)

    def source_hash(self) -> str:
        from .engine import _fixed_rule_hash

        return _fixed_rule_hash()


def default_plugins() -> dict[str, ResearchFactorPlugin]:
    """返回默认 registry；调用方可以注入自己的显式 registry。"""
    plugin = FixedV0Plugin()
    return {plugin.plugin_id: plugin}


def resolve_plugin(
    plugin_id: str,
    plugins: dict[str, ResearchFactorPlugin] | None = None,
) -> ResearchFactorPlugin:
    registry = plugins if plugins is not None else default_plugins()
    plugin = registry.get(str(plugin_id).strip())
    if plugin is None:
        raise ValueError(f"未知因子插件: {plugin_id}")
    if not getattr(plugin, "plugin_id", None):
        raise ValueError("因子插件缺少 plugin_id")
    if str(plugin.plugin_id).strip() != str(plugin_id).strip():
        raise ValueError(f"因子插件 registry key 与 plugin_id 不一致: {plugin_id}")
    if not callable(getattr(plugin, "score", None)):
        raise ValueError(f"因子插件不可执行: {plugin_id}")
    if not callable(getattr(plugin, "source_hash", None)):
        raise ValueError(f"因子插件缺少 source_hash: {plugin_id}")
    factor_definition(plugin)
    return plugin


def factor_definition(plugin: ResearchFactorPlugin) -> dict[str, Any]:
    """校验并返回插件的可审计因子契约。"""

    plugin_id = str(getattr(plugin, "plugin_id", "")).strip()
    version = str(getattr(plugin, "version", "")).strip()
    role = str(getattr(plugin, "role", "")).strip()
    supported_markets = getattr(plugin, "supported_markets", ())
    required_fields = getattr(plugin, "required_fields", ())
    min_history_days = getattr(plugin, "min_history_days", None)
    score_direction = str(getattr(plugin, "score_direction", "")).strip()
    parameter_schema = getattr(plugin, "parameter_schema", {})
    if not plugin_id or not version or not role:
        raise ValueError("因子插件必须声明 plugin_id、version 和 role")
    if isinstance(supported_markets, str):
        supported_markets = (supported_markets,)
    if not supported_markets or any(not str(value).strip() for value in supported_markets):
        raise ValueError(f"因子插件 {plugin_id} 必须声明 supported_markets")
    if isinstance(required_fields, str):
        required_fields = (required_fields,)
    if not required_fields or any(not str(value).strip() for value in required_fields):
        raise ValueError(f"因子插件 {plugin_id} 必须声明 required_fields")
    if not isinstance(min_history_days, int) or min_history_days <= 0:
        raise ValueError(f"因子插件 {plugin_id} 的 min_history_days 必须为正整数")
    if score_direction not in {"higher_is_better", "lower_is_better"}:
        raise ValueError(f"因子插件 {plugin_id} 的 score_direction 无效")
    if parameter_schema is None:
        parameter_schema = {}
    if not isinstance(parameter_schema, dict):
        raise ValueError(f"因子插件 {plugin_id} 的 parameter_schema 必须是对象")
    if any(not isinstance(rule, dict) for rule in parameter_schema.values()):
        raise ValueError(f"因子插件 {plugin_id} 的 parameter_schema 条目必须是对象")
    return {
        "plugin_id": plugin_id,
        "version": version,
        "role": role,
        "supported_markets": [str(value).strip() for value in supported_markets],
        "required_fields": [str(value).strip() for value in required_fields],
        "min_history_days": min_history_days,
        "score_direction": score_direction,
        "parameter_schema": parameter_schema,
    }


def validate_plugin_output(
    result: pd.DataFrame,
    before: pd.DataFrame,
    *,
    plugin_id: str,
    min_history_days: int | None = None,
) -> pd.DataFrame:
    """校验插件逐股票输出，不替插件静默修复结果。"""

    if not isinstance(result, pd.DataFrame):
        raise ValueError(f"因子插件 {plugin_id} 必须返回 pandas.DataFrame")
    required = {"symbol", "eligible", "total_score"}
    missing = sorted(required - set(result.columns))
    if missing:
        raise ValueError(f"因子插件 {plugin_id} 输出缺少列: {missing}")
    output = result.copy()
    output["symbol"] = output["symbol"].astype(str)
    if output["symbol"].duplicated().any():
        raise ValueError(f"因子插件 {plugin_id} 输出包含重复股票")
    expected = set(before["symbol"].astype(str))
    actual = set(output["symbol"])
    outside = sorted(actual - expected)
    missing_symbols = sorted(expected - actual)
    if outside:
        raise ValueError(f"因子插件 {plugin_id} 输出包含 universe 外股票: {outside[:5]}")
    if missing_symbols:
        raise ValueError(f"因子插件 {plugin_id} 未返回完整 universe: {missing_symbols[:5]}")
    if output["eligible"].isna().any() or not output["eligible"].map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ValueError(f"因子插件 {plugin_id} 的 eligible 必须是非空布尔值")
    scores = pd.to_numeric(output["total_score"], errors="coerce")
    if scores[output["eligible"].astype(bool)].isna().any() or not np.isfinite(
        scores[output["eligible"].astype(bool)].to_numpy(dtype=float)
    ).all():
        raise ValueError(f"因子插件 {plugin_id} 的 eligible 分数必须是有限数字")
    if min_history_days is not None:
        history_counts = before.groupby(before["symbol"].astype(str))["date"].nunique()
        eligible_symbols = output.loc[output["eligible"].astype(bool), "symbol"]
        short_symbols = sorted(
            symbol for symbol in eligible_symbols if int(history_counts.get(symbol, 0)) < int(min_history_days)
        )
        if short_symbols:
            raise ValueError(f"因子插件 {plugin_id} 将历史不足的股票标记为 eligible: {short_symbols[:5]}")
    return output
