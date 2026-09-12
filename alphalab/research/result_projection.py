"""Read-only projection of frozen portfolio research results.

The research engine stores the facts needed to reproduce a result in a
manifest and a small set of CSV files.  This module only reshapes those facts
for the portfolio review API.  It deliberately does not open a market data
adapter, calculate a new return, or write a derived artifact back to a run.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date
import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


SCHEMA_VERSION = 1

_ACTION_LABELS = {
    "BUY": "买入替补",
    "SELL": "卖出",
    "SELECT": "选择替补",
    "DEFER_SELL": "卖出顺延",
    "DEFER_BUY": "买入顺延",
    "CASH": "保留现金",
    "CANCEL_BUY": "取消替补",
    "UNSETTLED": "未结算",
    "OPEN_POSITION": "保留未平仓",
    "INITIAL_NOT_FILLED": "初始建仓未成交",
}

_REASON_LABELS = {
    "before_trading_resumes": "公告明确复牌前等待",
    "termination_decision": "正式退市决定触发退出",
    "termination_decision_delisting": "退市股份未结算，完整收益不可确定",
    "listing_metadata_delisting": "已到历史上市边界，股份未结算",
    "lot_budget": "预算不足一手，保留现金",
    "no_candidate": "没有合格替补，保留现金",
    "confirmed_suspension": "来源确认停牌，暂缓成交",
    "one_price_bar": "一字行情，保守不成交",
    "terminal": "研究结束日退出",
    "terminal_or_ineligible": "研究结束或候选已不合格",
    "missing_entry_price": "缺少建仓日开盘价，初始建仓未成交",
    "initial_entry": "由冻结建仓记录还原初始成交",
    "replacement_selection": "按冻结排名选择替补",
    "open_position": "研究结束时仍有未平仓持仓",
    "initial_not_filled": "初始建仓未成交",
    "insufficient_cash": "可用资金不足，未完成成交",
    "unknown": "暂无中文解释",
}

_PRICE_BASIS_LABELS = {
    "adjusted_total_return_anchored_at_raw_entry": "研究总回报价格（按建仓原始开盘价锚定）",
    "verified_raw_open_plus_slippage": "已核实原始开盘价加模拟滑点",
    "frozen_entry_price_includes_buy_slippage": "冻结建仓价已含买入滑点",
}

_FILL_ACTIONS = {"BUY", "SELL"}
_DECISION_ACTIONS = {
    "SELECT",
    "DEFER_SELL",
    "DEFER_BUY",
    "CASH",
    "CANCEL_BUY",
    "UNSETTLED",
    "OPEN_POSITION",
    "INITIAL_NOT_FILLED",
}


def build_review_projection(
    run_or_manifest: Any,
    *,
    portfolio_id: str | None = None,
    portfolio_frame: pd.DataFrame | None = None,
    nav_frame: pd.DataFrame | None = None,
    portfolios_frame: pd.DataFrame | None = None,
    portfolio_nav_frame: pd.DataFrame | None = None,
    candidates_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build the versioned portfolio review object from frozen inputs.

    ``run_or_manifest`` may be a :class:`ReviewRun`-like object or a manifest
    mapping.  Frames are optional so callers that only have a manifest still
    receive an honest empty/insufficient-evidence projection.  When a frame
    contains multiple portfolios it is filtered by ``portfolio_id`` without
    changing the caller's frame.
    """

    run = run_or_manifest
    manifest = _mapping(getattr(run, "manifest", run))
    spec = _mapping(manifest.get("spec"))
    diagnostics = _mapping(manifest.get("diagnostics"))
    portfolio_id = _resolve_portfolio_id(manifest, portfolio_id, portfolio_frame, portfolios_frame)

    if portfolio_frame is None:
        portfolio_frame = getattr(run, "portfolio_frame", None)
    if portfolios_frame is None:
        portfolios_frame = getattr(run, "portfolios_frame", None)
    if nav_frame is None:
        nav_frame = getattr(run, "nav_frame", None)
    if portfolio_nav_frame is None:
        portfolio_nav_frame = getattr(run, "portfolio_nav_frame", None)
    if candidates_frame is None:
        candidates_frame = getattr(run, "candidates_frame", None)

    holdings_frame = _portfolio_slice(portfolios_frame, portfolio_id)
    if holdings_frame.empty:
        holdings_frame = _portfolio_slice(portfolio_frame, portfolio_id)
    selected_nav = _portfolio_slice(portfolio_nav_frame, portfolio_id, allow_missing_portfolio=True)
    if selected_nav.empty:
        selected_nav = _portfolio_slice(nav_frame, portfolio_id, allow_missing_portfolio=True)

    all_performance = _portfolio_performance(manifest)
    performance = _mapping(all_performance.get(portfolio_id))
    configs = _portfolio_configs(manifest)
    config = _mapping(configs.get(portfolio_id))
    first_summary = _first_performance(performance)
    initial_cash = _first_value(
        config.get("initial_cash"),
        first_summary.get("initial_cash"),
        spec.get("initial_cash"),
        _mapping(diagnostics.get("portfolios")).get(portfolio_id, {}).get("initial_cash"),
    )
    initial_cash = _number(initial_cash)
    names = _name_index(holdings_frame, candidates_frame)
    name = _safe_text(
        _first_value(
            config.get("name"),
            _mapping(diagnostics.get("portfolios")).get(portfolio_id, {}).get("name"),
            _first_text(holdings_frame, "portfolio_name"),
            _first_text(holdings_frame, "name"),
            portfolio_id,
        )
    ) or portfolio_id
    scope = _scope(manifest, diagnostics, spec, selected_nav)
    quality_mode = _safe_text(
        _first_value(spec.get("data_quality_mode"), _mapping(diagnostics.get("data_quality")).get("mode"), "unknown")
    )

    horizons = _horizons(manifest, performance, selected_nav)
    by_horizon: dict[str, dict[str, Any]] = {}
    for horizon in horizons:
        summary = _mapping(_lookup(performance, horizon))
        horizon_nav = _nav_rows(selected_nav, horizon, initial_cash)
        raw_events = _execution_events(manifest, portfolio_id, horizon, summary)
        initial_holdings = _initial_holdings(holdings_frame)
        explicit_events = _normalise_events(
            raw_events,
            manifest=manifest,
            portfolio_id=portfolio_id,
            horizon=horizon,
            names=names,
        )
        explicit_events = _link_related_events(explicit_events)
        events = _add_initial_events(
            explicit_events,
            initial_holdings,
            manifest=manifest,
            portfolio_id=portfolio_id,
            horizon=horizon,
            entry_date=scope.get("entry_date"),
            names=names,
            diagnostics=diagnostics,
            candidates_frame=candidates_frame,
        )
        events = sorted(events, key=_event_sort_key)
        chains = _chains(events)
        ending_holdings, ending_evidence = _ending_holdings(summary, names, holdings_frame)
        status = _status(summary, manifest, initial_holdings, ending_holdings, horizon_nav)
        capabilities = _capabilities(
            manifest,
            spec,
            diagnostics,
            summary,
            horizon,
            horizon_nav,
            raw_events,
            events,
            ending_evidence,
            selected_nav,
            portfolio_id,
        )
        unavailable_reasons = _unavailable_reasons(
            summary,
            horizon_nav,
            status,
            ending_evidence,
            initial_holdings,
            events,
            initial_cash,
        )
        horizon_payload: dict[str, Any] = {
            "summary": _plain(summary),
            "status": status,
            "nav": horizon_nav,
            "events": events,
            "chains": chains,
            "initial_holdings": initial_holdings,
            "ending_holdings": ending_holdings,
            "capabilities": capabilities,
            "unavailable_reasons": unavailable_reasons,
            "initial_cash": initial_cash,
            "ending_cash": _ending_cash(summary),
            "known_assets_value": _number(summary.get("known_assets_value")),
            "unsettled_symbols": _string_list(summary.get("unsettled_symbols")),
            "transaction_count": int(sum(bool(event.get("filled")) for event in events)),
        }
        by_horizon[str(horizon)] = _plain(horizon_payload)

    return _plain(
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": _safe_text(manifest.get("run_id")),
            "portfolio_id": portfolio_id,
            "name": name,
            "initial_cash": initial_cash,
            "scope": scope,
            "quality_mode": quality_mode,
            "horizons": horizons,
            "by_horizon": by_horizon,
        }
    )


# These aliases keep the projection easy to discover for consumers that use
# the noun from the task brief rather than the verb used by ReviewState.
project_portfolio_review = build_review_projection
project_result_review = build_review_projection


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if is_dataclass(value):
        return _mapping(asdict(value))
    return {}


def _portfolio_performance(manifest: Mapping[str, Any]) -> dict[str, Any]:
    value = manifest.get("portfolio_performance")
    if isinstance(value, Mapping) and value:
        return {str(key): item for key, item in value.items()}
    return {"strategy": manifest.get("performance", {})}


def _portfolio_configs(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = manifest.get("portfolios")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = _mapping(row)
        pid = _safe_text(item.get("portfolio_id"))
        if pid:
            result[pid] = item
    return result


def _resolve_portfolio_id(
    manifest: Mapping[str, Any],
    requested: str | None,
    portfolio_frame: pd.DataFrame | None,
    portfolios_frame: pd.DataFrame | None,
) -> str:
    configured = list(_portfolio_configs(manifest))
    configured.extend(str(key) for key in _portfolio_performance(manifest))
    for frame in [portfolios_frame, portfolio_frame]:
        if isinstance(frame, pd.DataFrame) and "portfolio_id" in frame.columns:
            configured.extend(str(value) for value in frame["portfolio_id"].dropna().tolist())
    available = list(dict.fromkeys(value for value in configured if str(value).strip()))
    value = str(requested or "").strip()
    if value:
        return value
    return available[0] if available else "strategy"


def _portfolio_slice(
    frame: pd.DataFrame | None,
    portfolio_id: str,
    *,
    allow_missing_portfolio: bool = False,
) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        return pd.DataFrame()
    result = frame.copy()
    if "portfolio_id" not in result.columns:
        return result if allow_missing_portfolio else result
    values = result["portfolio_id"].astype(str)
    filtered = result.loc[values == portfolio_id].copy()
    return filtered


def _horizons(manifest: Mapping[str, Any], performance: Mapping[str, Any], nav: pd.DataFrame) -> list[int]:
    values: list[int] = []
    for key in _mapping(manifest.get("spec")).get("horizons", []):
        number = _integer(key)
        if number is not None:
            values.append(number)
    for key in performance:
        number = _integer(key)
        if number is not None:
            values.append(number)
    if isinstance(nav, pd.DataFrame) and "horizon" in nav.columns:
        for key in nav["horizon"].dropna().tolist():
            number = _integer(key)
            if number is not None:
                values.append(number)
    return sorted(set(values))


def _lookup(mapping: Mapping[str, Any], key: int) -> Any:
    if str(key) in mapping:
        return mapping[str(key)]
    return mapping.get(key)


def _first_performance(performance: Mapping[str, Any]) -> dict[str, Any]:
    for key in sorted(performance, key=lambda value: (_integer(value) is None, _integer(value) or 0)):
        value = _mapping(performance.get(key))
        if value:
            return value
    return {}


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, float) and math.isnan(value):
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _first_text(frame: pd.DataFrame, column: str) -> str | None:
    if not isinstance(frame, pd.DataFrame) or column not in frame.columns:
        return None
    for value in frame[column].tolist():
        text = _safe_text(value)
        if text:
            return text
    return None


def _name_index(*frames: pd.DataFrame | None) -> dict[str, str]:
    names: dict[str, str] = {}
    for frame in frames:
        if not isinstance(frame, pd.DataFrame) or "symbol" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            symbol = _safe_text(row.get("symbol"))
            name = _safe_text(row.get("name"))
            if symbol and name and symbol not in names:
                names[symbol] = name
    return names


def _scope(
    manifest: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    spec: Mapping[str, Any],
    nav: pd.DataFrame,
) -> dict[str, Any]:
    wizard = _mapping(spec.get("wizard_metadata"))
    dates = _mapping(wizard.get("dates"))
    nav_dates = _frame_dates(nav)
    data_range = diagnostics.get("data_range")
    if not isinstance(data_range, Sequence) or isinstance(data_range, (str, bytes)):
        data_range = None
    return {
        "market": _safe_text(spec.get("market")),
        "requested_date": _safe_text(manifest.get("requested_date")),
        "signal_date": _safe_text(manifest.get("signal_date")),
        "requested_start_date": _safe_text(_first_value(dates.get("requested_start_date"), wizard.get("requested_start_date"))),
        "requested_end_date": _safe_text(_first_value(dates.get("requested_end_date"), wizard.get("requested_end_date"), manifest.get("requested_date"))),
        "entry_date": _date_text(_first_value(diagnostics.get("entry_date"), dates.get("entry_date"))),
        "exit_date": _date_text(_first_value(dates.get("exit_date"), diagnostics.get("evaluated_date"))),
        "actual_date_range": [nav_dates[0], nav_dates[-1]] if nav_dates else None,
        "valid_date_range": [_date_text(value) for value in data_range] if data_range else nav_dates,
        "calendar_source": _safe_text(_first_value(dates.get("calendar_source"), wizard.get("calendar_source"))),
        "session_count": len(_research_sessions(spec)),
    }


def _research_sessions(spec: Mapping[str, Any]) -> list[str]:
    wizard = _mapping(spec.get("wizard_metadata"))
    values = wizard.get("research_sessions")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        values = _mapping(wizard.get("dates")).get("research_sessions")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    result = []
    for value in values:
        item = _date_text(value)
        if item:
            result.append(item)
    return list(dict.fromkeys(result))


def _frame_dates(frame: pd.DataFrame) -> list[str]:
    if not isinstance(frame, pd.DataFrame) or "date" not in frame.columns:
        return []
    values = [_date_text(value) for value in frame["date"].tolist()]
    return sorted({value for value in values if value})


def _nav_rows(frame: pd.DataFrame, horizon: int, initial_cash: float | None) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    data = frame.copy()
    if "horizon" in data.columns:
        values = pd.to_numeric(data["horizon"], errors="coerce")
        data = data.loc[values == horizon].copy()
    rows: list[dict[str, Any]] = []
    for _, source in data.iterrows():
        row = {_safe_text(key) or str(key): _plain(value) for key, value in source.to_dict().items()}
        equity = _number(source.get("equity"))
        unit_nav = equity / initial_cash if equity is not None and initial_cash not in (None, 0) else None
        row["date"] = _date_text(source.get("date"))
        row["horizon"] = horizon
        row["equity"] = equity
        row["unit_nav"] = unit_nav
        row["cumulative_return"] = unit_nav - 1.0 if unit_nav is not None else None
        row["daily_return"] = _number(source.get("daily_return"))
        row["drawdown"] = _number(source.get("drawdown"))
        rows.append(row)
    return rows


def _execution_events(
    manifest: Mapping[str, Any],
    portfolio_id: str,
    horizon: int,
    summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    value = summary.get("execution_events")
    if value is None:
        by_horizon = _mapping(manifest.get("execution_events"))
        value = _lookup(_mapping(by_horizon.get(portfolio_id)), horizon)
        if value is None:
            value = _lookup(by_horizon, horizon)
    if isinstance(value, pd.DataFrame):
        return [_mapping(row) for row in value.to_dict("records")]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [_mapping(item) for item in value]


def _normalise_events(
    events: Iterable[Mapping[str, Any]],
    *,
    manifest: Mapping[str, Any],
    portfolio_id: str,
    horizon: int,
    names: Mapping[str, str],
    default_source_kind: str = "frozen_execution_event",
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    run_id = _safe_text(manifest.get("run_id")) or "run"
    for index, source in enumerate(events):
        raw = _plain(_mapping(source))
        action = _action_code(raw.get("action"))
        symbol = _safe_text(_first_value(raw.get("symbol"), raw.get("code")))
        event_id = _safe_text(_first_value(raw.get("event_id"), raw.get("trigger_event_id")))
        signature = _stable_json({key: value for key, value in raw.items() if key not in {"id", "event_index"}})
        occurrence = counts.get(signature, 0)
        counts[signature] = occurrence + 1
        stable_id = "event-" + _digest({"run_id": run_id, "portfolio_id": portfolio_id, "identity": signature, "occurrence": occurrence})[:20]
        chain_id = _chain_id(run_id, portfolio_id, event_id) if event_id else None
        reason_code, reason_text = _reason(
            raw.get("reason_code"),
            _first_value(raw.get("reason"), raw.get("reason_text")),
            action,
        )
        explicit_filled = raw.get("filled")
        filled = action in _FILL_ACTIONS and (
            explicit_filled is None or _as_bool(explicit_filled)
        )
        share_value = _first_value(raw.get("shares"), raw.get("quantity"))
        if action != "INITIAL_NOT_FILLED":
            share_value = _first_value(share_value, raw.get("requested_shares"))
        price_basis = _safe_text(raw.get("price_basis"))
        unavailable: list[str] = []
        if action in _FILL_ACTIONS and _number(raw.get("price")) is None:
            unavailable.append("冻结事件未保存研究成交价格")
        if action in _FILL_ACTIONS and _number(_net_cash(raw, action)) is None:
            unavailable.append("冻结事件未保存净现金变化")
        normalized = dict(raw)
        normalized.update(
            {
                "id": stable_id,
                "chain_id": chain_id,
                "date": _date_text(_first_value(raw.get("date"), raw.get("trade_date"))),
                "action": action,
                "action_label": _ACTION_LABELS.get(action, f"未知事件（{action}）"),
                "symbol": symbol,
                "name": _safe_text(_first_value(raw.get("name"), names.get(symbol) if symbol else None, symbol)),
                "filled": filled,
                "shares": _number(share_value),
                "price": _number(raw.get("price")),
                "commission": _number(raw.get("commission")),
                "slippage": _number(raw.get("slippage")),
                "net_cash": _number(_net_cash(raw, action)),
                "budget": _number(raw.get("budget")),
                "reason_code": reason_code,
                "reason_text": reason_text,
                "rank_cutoff": _date_text(raw.get("rank_cutoff")) or _safe_text(raw.get("rank_cutoff")),
                "source_url": _safe_text(raw.get("source_url")),
                "evidence_kind": _safe_text(raw.get("evidence_kind")) or default_source_kind,
                "source_kind": _safe_text(raw.get("source_kind")) or default_source_kind,
                "source": _safe_text(raw.get("source")) or _safe_text(raw.get("source_kind")) or default_source_kind,
                "price_basis": price_basis,
                "price_basis_text": _PRICE_BASIS_LABELS.get(price_basis, "研究成交价格口径未保存" if price_basis is None else f"研究价格口径：{price_basis}"),
                "price_basis_label": _PRICE_BASIS_LABELS.get(price_basis, "研究成交价格口径未保存" if price_basis is None else f"研究价格口径：{price_basis}"),
                "action_text": _ACTION_LABELS.get(action, f"未知事件（{action}）"),
                "unavailable_reasons": unavailable,
            }
        )
        normalized["unavailable_reason"] = "；".join(unavailable) if unavailable else None
        result.append(_plain(normalized))
    return result


def _add_initial_events(
    events: list[dict[str, Any]],
    initial_holdings: list[dict[str, Any]],
    *,
    manifest: Mapping[str, Any],
    portfolio_id: str,
    horizon: int,
    entry_date: str | None,
    names: Mapping[str, str],
    diagnostics: Mapping[str, Any],
    candidates_frame: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    result = list(events)
    explicit_initial = {
        (_safe_text(event.get("symbol")), _date_text(event.get("date")))
        for event in events
        if event.get("action") == "BUY"
        and (
            _date_text(event.get("date")) == entry_date
            or event.get("reason_code") == "initial_entry"
            or event.get("source_kind") == "derived_frozen_entry"
        )
    }
    run_id = _safe_text(manifest.get("run_id")) or "run"
    for holding in initial_holdings:
        symbol = _safe_text(holding.get("symbol"))
        holding_entry_date = _date_text(holding.get("entry_date")) or entry_date
        if not symbol or (symbol, holding_entry_date) in explicit_initial:
            continue
        shares = _number(holding.get("shares"))
        if shares is None or shares <= 0:
            continue
        raw = {
            "action": "BUY",
            "symbol": symbol,
            "name": holding.get("name") or names.get(symbol),
            "date": holding_entry_date,
            "shares": shares,
            "price": holding.get("entry_price"),
            "reason": "initial_entry",
            "evidence_kind": "derived_frozen_entry",
            "source_kind": "derived_frozen_entry",
            "source": "derived_frozen_entry",
            "price_basis": "frozen_entry_price_includes_buy_slippage",
        }
        derived = _normalise_events(
            [raw],
            manifest=manifest,
            portfolio_id=portfolio_id,
            horizon=horizon,
            names=names,
            default_source_kind="derived_frozen_entry",
        )[0]
        derived["action_label"] = "初始建仓"
        derived["action_text"] = "初始建仓"
        derived["unavailable_reasons"] = ["冻结建仓成本契约未保存，费用和净现金变化未推导"]
        derived["unavailable_reason"] = derived["unavailable_reasons"][0]
        result.append(derived)

    persisted_nonfills = {
        (_safe_text(event.get("symbol")), _date_text(event.get("date")))
        for event in events
        if event.get("action") == "INITIAL_NOT_FILLED"
    }
    reasons = _initial_reasons(diagnostics, portfolio_id)
    for symbol, reason in reasons.items():
        if not symbol or (symbol, entry_date) in persisted_nonfills:
            continue
        name = names.get(symbol) or _candidate_name(candidates_frame, symbol) or symbol
        code = _initial_reason_code(reason)
        raw = {
            "action": "INITIAL_NOT_FILLED",
            "symbol": symbol,
            "name": name,
            "date": entry_date,
            "reason": code,
            "reason_text": reason,
            "filled": False,
            "evidence_kind": "frozen_diagnostics",
            "source_kind": "frozen_diagnostics",
            "source": "frozen_diagnostics",
        }
        derived = _normalise_events(
            [raw],
            manifest=manifest,
            portfolio_id=portfolio_id,
            horizon=horizon,
            names=names,
            default_source_kind="frozen_diagnostics",
        )[0]
        derived["reason_text"] = _safe_text(reason) or _REASON_LABELS.get(code, _REASON_LABELS["unknown"])
        derived["shares"] = None
        derived["price"] = None
        derived["commission"] = None
        derived["slippage"] = None
        derived["net_cash"] = None
        derived["budget"] = None
        derived["unavailable_reasons"] = ["冻结诊断未保存初始请求数量和成本契约"]
        derived["unavailable_reason"] = derived["unavailable_reasons"][0]
        result.append(derived)
    return result


def _initial_holdings(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, source in frame.iterrows():
        shares = _number(source.get("shares"))
        if shares is None or shares <= 0:
            continue
        symbol = _safe_text(source.get("symbol"))
        if not symbol:
            continue
        row = {
            "symbol": symbol,
            "name": _safe_text(_first_value(source.get("name"), symbol)),
            "shares": shares,
            "entry_date": _date_text(source.get("entry_date")),
            "entry_price": _number(source.get("entry_price")),
            "target_weight": _number(source.get("target_weight")),
            "rank": _number(source.get("rank")),
            "industry": _safe_text(source.get("industry")),
            "source": "frozen_portfolio",
            "evidence_kind": "frozen_portfolio",
        }
        rows.append(_plain(row))
    return rows


def _ending_holdings(
    summary: Mapping[str, Any],
    names: Mapping[str, str],
    initial_frame: pd.DataFrame,
) -> tuple[list[dict[str, Any]], bool]:
    explicit = summary.get("ending_holdings")
    if explicit is None:
        explicit = summary.get("ending_positions")
    if isinstance(explicit, Mapping):
        explicit = [
            {**_mapping(value), "symbol": _first_value(_mapping(value).get("symbol"), symbol)}
            for symbol, value in explicit.items()
        ]
    if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes)):
        rows: list[dict[str, Any]] = []
        for item in explicit:
            source = _mapping(item)
            symbol = _safe_text(source.get("symbol"))
            if not symbol:
                continue
            rows.append(
                _plain(
                    {
                        "symbol": symbol,
                        "name": _safe_text(_first_value(source.get("name"), names.get(symbol), symbol)),
                        "shares": _number(_first_value(source.get("shares"), source.get("quantity"))),
                        "market_value": _number(_first_value(source.get("market_value"), source.get("value"))),
                        "unsettled": bool(source.get("unsettled", False)),
                        "source": "frozen_ending_holdings",
                        "evidence_kind": "frozen_ending_holdings",
                    }
                )
            )
        return rows, True
    positions = summary.get("open_positions")
    if isinstance(positions, Mapping):
        rows = []
        values = _mapping(summary.get("open_position_values"))
        unsettled = set(_string_list(summary.get("unsettled_symbols")))
        for symbol, shares in positions.items():
            code = _safe_text(symbol)
            if not code:
                continue
            rows.append(
                _plain(
                    {
                        "symbol": code,
                        "name": names.get(code, code),
                        "shares": _number(shares),
                        "market_value": _number(values.get(code)),
                        "unsettled": code in unsettled,
                        "source": "frozen_open_positions",
                        "evidence_kind": "frozen_open_positions",
                    }
                )
            )
        return rows, True
    if str(summary.get("liquidation_status", "")).upper() == "LIQUIDATED":
        return [], True
    return [], False


def _status(
    summary: Mapping[str, Any],
    manifest: Mapping[str, Any],
    initial_holdings: Sequence[Mapping[str, Any]],
    ending_holdings: Sequence[Mapping[str, Any]],
    nav: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    raw_status = str(summary.get("status") or manifest.get("status") or "").upper()
    liquidation = str(summary.get("liquidation_status") or "").upper()
    if raw_status in {"PLANNED", "QUEUED", "SCHEDULED"}:
        code, label, explanation = "PLANNED", "待运行", "研究计划已保存，尚未开始运行。"
    elif raw_status in {"RUNNING", "PENDING", "PREPARING", "CHECKING"}:
        code, label, explanation = "RUNNING", "运行中", "研究仍在运行，冻结结果尚未完成。"
    elif raw_status in {"FAILED", "ERROR", "CANCELLED", "CANCELED"}:
        code, label, explanation = "FAILED", "运行失败", "研究运行失败，当前没有可完整复盘的冻结结果。"
    elif liquidation == "UNSETTLED_DELISTING" or raw_status == "UNSETTLED":
        code, label, explanation = "UNSETTLED", "完整收益不可确定", "存在未结算股份；已知资产和现金不代表完整期末权益。"
    elif liquidation == "OPEN_POSITION":
        code, label, explanation = "COMPLETE_OPEN_POSITION", "完成但仍有可估值未平仓", "研究已完成，结果包含期末未平仓持仓的估值收益。"
    elif raw_status in {"COMPLETE", "SUCCEEDED", "SUCCESS"} and not initial_holdings:
        code, label, explanation = "EMPTY_PORTFOLIO", "空组合", "研究完成但没有冻结的实际建仓持仓。"
    elif liquidation == "LIQUIDATED" and raw_status in {"COMPLETE", "SUCCEEDED", "SUCCESS", ""}:
        code, label, explanation = "COMPLETE_LIQUIDATED", "完成且清算", "研究已完成，冻结证据显示期末持仓已全部清算。"
    elif raw_status in {"INSUFFICIENT_FORWARD_DATA", "INSUFFICIENT_EVIDENCE", "NO_ENTRY_DATA", "NO_EXECUTABLE_HOLDINGS"}:
        code, label, explanation = "INSUFFICIENT_EVIDENCE", "证据不足", "冻结数据不足以支持该观察周期的完整结果。"
    elif not summary:
        code, label, explanation = "INSUFFICIENT_EVIDENCE", "证据不足", "没有冻结绩效汇总证据。"
    else:
        code, label, explanation = "COMPLETE", "已完成", "研究已完成，但冻结产物没有提供更具体的清算状态。"
    return {
        "code": code,
        "label": label,
        "explanation": explanation,
        "raw_status": raw_status or None,
        "liquidation_status": liquidation or None,
        "has_nav": bool(nav),
        "has_ending_holdings": bool(ending_holdings) or liquidation == "LIQUIDATED",
    }


def _capabilities(
    manifest: Mapping[str, Any],
    spec: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    summary: Mapping[str, Any],
    horizon: int,
    nav: Sequence[Mapping[str, Any]],
    raw_events: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    ending_evidence: bool,
    all_nav: pd.DataFrame,
    portfolio_id: str,
) -> dict[str, Any]:
    sessions = _research_sessions(spec)
    nav_dates = sorted({row.get("date") for row in nav if row.get("date")})
    expected = _expected_sessions(sessions, nav_dates)
    missing = sorted(set(expected) - set(nav_dates)) if sessions else []
    completeness = "unknown" if not sessions else ("complete" if not missing else "incomplete")
    stale = [row for row in nav if row.get("stale_symbols") or (_number(row.get("max_valuation_stale_days")) or 0) > 0]
    benchmark = _benchmark_for_horizon(manifest, portfolio_id, horizon)
    disabled_reason = _safe_text(_mapping(spec.get("wizard_metadata")).get("benchmark_disabled_reason"))
    benchmark_available = bool(benchmark) and disabled_reason is None and str(benchmark.get("status", "")).upper() == "COMPLETE" and benchmark.get("total_return") is not None
    available_fields = set(_mapping(summary))
    available_fields.update({key for row in nav for key, value in row.items() if value is not None})
    available_fields.update({key for event in events for key, value in event.items() if value is not None})
    available_fields.update({"initial_holdings", "ending_holdings"})
    aggregate_reason = None
    if not nav:
        aggregate_reason = "冻结日终净值不可用"
    elif not sessions:
        aggregate_reason = "冻结元数据未提供可信交易日历，周/月完整性未知"
    elif missing:
        aggregate_reason = "可信交易日历与日终净值存在缺口"
    transaction_kind = "frozen_execution_events" if raw_events else ("derived_frozen_entry" if events else "none")
    fill_count = int(sum(bool(event.get("filled")) for event in events))
    return {
        "daily_nav": bool(nav),
        "daily_nav_available": bool(nav),
        "daily_nav_points": len(nav),
        "weekly_monthly_aggregation": bool(nav),
        "weekly_monthly_aggregation_source": "daily_nav_observed_closes" if nav else None,
        "weekly_monthly_completeness": completeness,
        "weekly_monthly_unavailable_reason": aggregate_reason,
        "aggregation": {
            "weekly": {
                "available": bool(nav),
                "source": "daily_nav_observed_closes" if nav else None,
                "completeness": completeness,
                "missing_dates": missing,
                "last_period_may_be_partial": _last_period_may_be_partial(nav_dates, "weekly"),
            },
            "monthly": {
                "available": bool(nav),
                "source": "daily_nav_observed_closes" if nav else None,
                "completeness": completeness,
                "missing_dates": missing,
                "last_period_may_be_partial": _last_period_may_be_partial(nav_dates, "monthly"),
            },
        },
        "session_list": sessions,
        "session_source": _safe_text(_mapping(_mapping(spec.get("wizard_metadata")).get("dates")).get("calendar_source")),
        "valid_date_range": [nav_dates[0], nav_dates[-1]] if nav_dates else None,
        "missing_session_dates": missing,
        "transaction_evidence": bool(raw_events),
        "transaction_evidence_available": bool(raw_events),
        "transaction_evidence_kind": transaction_kind,
        "stored_event_count": len(raw_events),
        "event_count": len(events),
        "fill_count": fill_count,
        "comparable_frozen_benchmark": benchmark_available,
        "comparable_frozen_benchmark_reason": None if benchmark_available else (disabled_reason or "本次运行未保存同组合、同区间可比较基准"),
        "available_fields": sorted(available_fields),
        "stale_valuation": bool(stale),
        "stale_nav_points": len(stale),
        "ending_holdings_evidence": ending_evidence,
        "initial_cash_evidence": summary.get("initial_cash") is not None,
        "horizon": horizon,
    }


def _expected_sessions(sessions: Sequence[str], nav_dates: Sequence[str]) -> list[str]:
    if not sessions or not nav_dates:
        return []
    start, end = nav_dates[0], nav_dates[-1]
    return [value for value in sessions if start <= value <= end]


def _last_period_may_be_partial(nav_dates: Sequence[str], period: str) -> bool:
    """Flag a trailing calendar period that ends before its visible boundary.

    The frozen session list is bounded by the research interval, so comparing
    its last value with the last NAV date cannot tell whether the interval
    itself stopped mid-period.  Use only the observable calendar boundary:
    Friday closes a trading week and a civil month-end closes a month.  A
    holiday-shortened period remains conservatively marked partial because no
    future session is fabricated to prove completeness.
    """
    if not nav_dates:
        return False
    try:
        last = pd.Timestamp(nav_dates[-1])
    except (TypeError, ValueError, OverflowError):
        return True
    if period == "weekly":
        return last.weekday() != 4
    if period == "monthly":
        return not bool(last.is_month_end)
    return True


def _benchmark_for_horizon(manifest: Mapping[str, Any], portfolio_id: str, horizon: int) -> dict[str, Any]:
    by_portfolio = manifest.get("benchmark_by_portfolio")
    if isinstance(by_portfolio, Mapping):
        selected = _mapping(by_portfolio.get(portfolio_id))
        if selected:
            return _mapping(_lookup(selected, horizon))
        values = list(by_portfolio.values())
        if len(values) == 1:
            by_horizon = _mapping(values[0])
            return _mapping(_lookup(by_horizon, horizon))
    return _mapping(_lookup(_mapping(manifest.get("benchmark")), horizon))


def _chains(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        chain_id = _safe_text(event.get("chain_id"))
        if chain_id:
            grouped.setdefault(chain_id, []).append(event)
    result: list[dict[str, Any]] = []
    for chain_id, chain_events in grouped.items():
        actions = [str(event.get("action", "")) for event in chain_events]
        reasons = [str(event.get("reason_code", "")) for event in chain_events]
        symbols = sorted({str(event.get("symbol")) for event in chain_events if event.get("symbol")})
        dates = sorted({str(event.get("date")) for event in chain_events if event.get("date")})
        summary_parts: list[str] = []
        if "DEFER_SELL" in actions:
            summary_parts.append("正式退市决定公开")
            summary_parts.append("公告明确复牌前等待")
        if "SELL" in actions:
            summary_parts.append("首个可成交日退出" if "DEFER_SELL" in actions else "完成卖出")
        if "SELECT" in actions:
            summary_parts.append("选择替补")
        if "BUY" in actions:
            summary_parts.append("替补买入成交")
        if "CASH" in actions:
            if "lot_budget" in reasons:
                summary_parts.append("预算不足一手，保留现金")
            elif "no_candidate" in reasons:
                summary_parts.append("没有合格替补，保留现金")
            else:
                summary_parts.append("保留现金")
        if "CANCEL_BUY" in actions:
            summary_parts.append("替补买入取消")
        if "UNSETTLED" in actions:
            summary_parts.append("股份未结算")
        if not summary_parts:
            summary_parts.append("；".join(dict.fromkeys(str(event.get("reason_text")) for event in chain_events if event.get("reason_text"))) or "已记录调仓事件")
        unique_parts = list(dict.fromkeys(summary_parts))
        result.append(
            _plain(
                {
                    "chain_id": chain_id,
                    "trigger_event_id": _first_text_from_events(chain_events, "event_id"),
                    "event_ids": [str(event.get("id")) for event in chain_events],
                    "event_count": len(chain_events),
                    "fill_count": int(sum(bool(event.get("filled")) for event in chain_events)),
                    "symbols": symbols,
                    "start_date": dates[0] if dates else None,
                    "end_date": dates[-1] if dates else None,
                    "summary": " → ".join(unique_parts),
                    "reason_codes": list(dict.fromkeys(reasons)),
                }
            )
        )
    return result


def _link_related_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Link explicit selection outcomes without using date proximity.

    Replacement ``CASH``/``DEFER_BUY``/``CANCEL_BUY`` records in older
    artifacts did not repeat the triggering event id.  A matching selected
    symbol and frozen rank cutoff is a persisted relationship; a shared date
    alone is intentionally insufficient.
    """
    selections: dict[tuple[str, str], str] = {}
    for event in events:
        if event.get("action") != "SELECT" or not event.get("chain_id"):
            continue
        symbol = _safe_text(event.get("symbol"))
        cutoff = _safe_text(event.get("rank_cutoff"))
        if symbol and cutoff:
            selections.setdefault((symbol, cutoff), str(event["chain_id"]))
    for event in events:
        if event.get("chain_id") or event.get("action") not in {"CASH", "DEFER_BUY", "CANCEL_BUY"}:
            continue
        symbol = _safe_text(event.get("symbol"))
        cutoff = _safe_text(event.get("rank_cutoff"))
        chain_id = selections.get((symbol or "", cutoff or ""))
        if chain_id:
            event["chain_id"] = chain_id
            event["chain_link_reason"] = "frozen_symbol_and_rank_cutoff"
    return events


def _first_text_from_events(events: Sequence[Mapping[str, Any]], key: str) -> str | None:
    for event in events:
        value = _safe_text(event.get(key))
        if value:
            return value
    return None


def _unavailable_reasons(
    summary: Mapping[str, Any],
    nav: Sequence[Mapping[str, Any]],
    status: Mapping[str, Any],
    ending_evidence: bool,
    initial_holdings: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    initial_cash: float | None,
) -> list[str]:
    reasons: list[str] = []
    if not summary:
        reasons.append("冻结绩效汇总不可用")
    for field in ["total_return", "ending_equity", "max_drawdown"]:
        if field not in summary or summary.get(field) is None:
            reasons.append(f"冻结绩效未保存 {field}")
    if initial_cash is None:
        reasons.append("冻结初始本金不可用，无法提供单位净值")
    if not nav:
        reasons.append("冻结日终净值不可用")
    if not ending_evidence and status.get("code") not in {"COMPLETE_LIQUIDATED", "EMPTY_PORTFOLIO"}:
        reasons.append("冻结期末持仓证据不可用，未从初始持仓推断期末状态")
    if not initial_holdings:
        reasons.append("冻结组合没有实际正股数建仓记录")
    if not events:
        reasons.append("冻结运行没有执行或建仓事件记录")
    elif any(event.get("unavailable_reasons") for event in events):
        reasons.append("部分事件缺少冻结成本或成交证据")
    if status.get("code") == "UNSETTLED":
        reasons.append("未结算股份使完整收益不可确定")
    return list(dict.fromkeys(reasons))


def _ending_cash(summary: Mapping[str, Any]) -> float | None:
    return _number(_first_value(summary.get("realized_cash"), summary.get("cash_residual")))


def _ending_cash_value(summary: Mapping[str, Any]) -> float | None:
    return _ending_cash(summary)


def _net_cash(raw: Mapping[str, Any], action: str) -> Any:
    if "net_cash" in raw:
        return raw.get("net_cash")
    if action == "SELL" and "net_proceeds" in raw:
        return raw.get("net_proceeds")
    if action == "BUY" and "total_cost" in raw:
        try:
            return -float(raw.get("total_cost"))
        except (TypeError, ValueError):
            return None
    return None


def _reason(code_value: Any, reason_value: Any, action: str) -> tuple[str, str]:
    raw_code = _safe_text(code_value) or _safe_text(reason_value)
    code = raw_code or _default_reason_code(action)
    if code in _REASON_LABELS:
        return code, _REASON_LABELS[code]
    text = _safe_text(reason_value)
    if text and any("\u4e00" <= char <= "\u9fff" for char in text):
        return code, text
    return code, f"暂无中文解释（{code}）"


def _default_reason_code(action: str) -> str:
    return {
        "BUY": "initial_entry" if action == "BUY" else "unknown",
        "SELL": "unknown",
        "SELECT": "replacement_selection",
        "OPEN_POSITION": "open_position",
        "UNSETTLED": "termination_decision_delisting",
        "INITIAL_NOT_FILLED": "initial_not_filled",
    }.get(action, "unknown")


def _initial_reasons(diagnostics: Mapping[str, Any], portfolio_id: str) -> dict[str, str]:
    portfolio_diagnostics = _mapping(diagnostics.get("portfolios")).get(portfolio_id)
    reasons = _mapping(_mapping(portfolio_diagnostics).get("reasons"))
    if not reasons:
        reasons = _mapping(_mapping(diagnostics.get("portfolio_reasons")).get(portfolio_id))
    return {str(symbol): _safe_text(reason) or "初始建仓未成交" for symbol, reason in reasons.items()}


def _initial_reason_code(reason: Any) -> str:
    text = _safe_text(reason) or ""
    if "一字" in text:
        return "one_price_bar"
    if "停牌" in text:
        return "confirmed_suspension"
    if "资金不足" in text or "整手" in text:
        return "lot_budget"
    if "开盘价" in text:
        return "missing_entry_price"
    return "initial_not_filled"


def _candidate_name(frame: pd.DataFrame | None, symbol: str) -> str | None:
    if not isinstance(frame, pd.DataFrame) or "symbol" not in frame.columns:
        return None
    rows = frame.loc[frame["symbol"].astype(str) == symbol]
    return _safe_text(rows.iloc[0].get("name")) if not rows.empty else None


def _action_code(value: Any) -> str:
    text = _safe_text(value) or "UNKNOWN"
    normalized = text.strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "INITIAL_NOTFILL": "INITIAL_NOT_FILLED",
        "NOT_FILLED": "INITIAL_NOT_FILLED",
        "DEFERSELL": "DEFER_SELL",
        "DEFERBUY": "DEFER_BUY",
        "OPEN": "OPEN_POSITION",
    }
    return aliases.get(normalized, normalized)


def _chain_id(run_id: str, portfolio_id: str, event_id: str) -> str:
    return "chain-" + _digest({"run_id": run_id, "portfolio_id": portfolio_id, "event_id": event_id})[:20]


def _event_sort_key(event: Mapping[str, Any]) -> tuple[Any, int, str]:
    action = str(event.get("action", ""))
    rank = {"BUY": 0, "INITIAL_NOT_FILLED": 1, "DEFER_SELL": 2, "SELL": 3, "SELECT": 4, "DEFER_BUY": 5, "CASH": 6}.get(action, 9)
    return (event.get("date") or "9999-99-99", rank, str(event.get("id", "")))


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return pd.Timestamp(value).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return _safe_text(value)


def _safe_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float, np.integer, np.floating)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是"}


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [text for item in value if (text := _safe_text(item)) is not None]


def _stable_json(value: Any) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    if isinstance(value, (pd.Timestamp, date)):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value
