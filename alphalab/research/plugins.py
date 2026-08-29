"""固定历史研究因子插件边界。

当前只注册一个可复现的 ``fixed_v0`` 插件。插件接口刻意保持很小：它只接收
信号日前的历史截面并返回逐股票结果，不接触未来行情、组合或产物存储。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd


class ResearchFactorPlugin(Protocol):
    plugin_id: str

    def score(self, before: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
        """对历史截面执行过滤和评分。"""

    def source_hash(self) -> str:
        """返回用于实验复现的规则源码哈希。"""


@dataclass(frozen=True)
class FixedV0Plugin:
    """第一版固定选股规则的插件适配器。"""

    plugin_id: str = "fixed_v0"

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
    if not callable(getattr(plugin, "score", None)):
        raise ValueError(f"因子插件不可执行: {plugin_id}")
    if not callable(getattr(plugin, "source_hash", None)):
        raise ValueError(f"因子插件缺少 source_hash: {plugin_id}")
    return plugin
