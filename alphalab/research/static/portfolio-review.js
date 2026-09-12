/*
 * Portfolio result review for the original wizard.
 *
 * The module is deliberately independent from the wizard state machine.  It
 * receives a frozen portfolio projection (or the saved summary fallback),
 * renders a compact review, and exposes pure helpers for the browser and
 * integration tests.  It never requests current prices or writes a run.
 */
(function attachPortfolioReview(root, factory) {
  const api = factory(root);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.PortfolioReviewUI = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function portfolioReviewUI(root) {
  "use strict";

  const VERSION = "portfolio-review-ui/1";
  const FILL_ACTIONS = new Set(["BUY", "SELL"]);
  const ACTION_LABELS = {
    BUY: "买入",
    SELL: "卖出",
    SELECT: "选择替补",
    DEFER_SELL: "卖出顺延",
    DEFER_BUY: "买入顺延",
    CANCEL_BUY: "取消替补",
    CASH: "保留现金",
    UNSETTLED: "未结算",
    OPEN_POSITION: "未平仓",
    INITIAL_NOT_FILLED: "初始未成交",
    TERMINAL_SELL: "期末卖出",
    INITIAL_BUY: "初始建仓",
    DEFER: "顺延",
    NO_CANDIDATE: "无替补，保留现金",
  };
  const REASON_LABELS = {
    initial_entry: "初始建仓",
    before_trading_resumes: "尚未到公告明确的复牌日",
    termination_decision: "正式退市决定触发退出",
    termination_decision_delisting: "退市前未能成交，保留未结算股份",
    terminal: "研究结束日退出",
    lot_budget: "卖出净回款不足一手，保留现金",
    no_candidate: "没有合格替补，保留现金",
    confirmed_suspension: "来源确认停牌",
    one_price_bar: "一字行情，保守不成交",
    terminal_or_ineligible: "研究结束或候选已不合格",
    replacement_selection: "按冻结排名选择替补",
    listing_metadata_delisting: "已到历史退市边界",
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[character]));
  }

  function finite(value) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
    const number = typeof value === "number" ? value : Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function dateText(value) {
    if (value instanceof Date && Number.isFinite(value.getTime())) return value.toISOString().slice(0, 10);
    const text = String(value ?? "").trim();
    const match = text.match(/^(\d{4}-\d{2}-\d{2})(?:$|[T\s])/);
    return match ? match[1] : text || null;
  }

  function formatNumber(value, digits = 2) {
    const number = finite(value);
    return number === null ? "未知" : number.toLocaleString("zh-CN", {minimumFractionDigits: digits, maximumFractionDigits: digits});
  }

  function formatPercent(value, digits = 2) {
    const number = finite(value);
    return number === null ? "未知" : `${(number * 100).toFixed(digits)}%`;
  }

  function formatMoney(value) { return formatNumber(value, 2); }
  function formatPrice(value) { return formatNumber(value, 4); }

  function formatMissing(value, reason) {
    if (value !== null && value !== undefined && !(typeof value === "number" && Number.isNaN(value))) {
      return typeof value === "number" ? String(value) : String(value);
    }
    return String(reason || "冻结证据未提供");
  }

  function metricMissing(summary, field, fallback) {
    const reasons = summary?.unavailable_reasons || summary?.unavailableReasons || [];
    if (Array.isArray(reasons) && reasons.length) return reasons[0];
    return fallback || `${field} 未保存`;
  }

  function eventIsFill(event) {
    const action = String(event?.action || "").toUpperCase();
    if (!FILL_ACTIONS.has(action)) return false;
    if (event?.filled === undefined || event?.filled === null) return true;
    if (typeof event.filled === "string") return !["false", "0", "no", "n"].includes(event.filled.trim().toLowerCase());
    return Boolean(event.filled);
  }

  function eventAction(event) { return String(event?.action || "").trim().toUpperCase(); }
  function eventReason(event) { return String(event?.reason_code || event?.reason || "").trim(); }
  function actionLabel(event) { return String(event?.action_label || event?.action_text || ACTION_LABELS[eventAction(event)] || eventAction(event) || "事件"); }
  function reasonLabel(event) { return String(event?.reason_text || REASON_LABELS[eventReason(event)] || eventReason(event) || "未说明原因"); }

  function dataObject(value) {
    if (!value || typeof value !== "object") return {};
    if (value.review && typeof value.review === "object") return value.review;
    return value;
  }

  function manifestPerformance(payload) {
    const manifest = payload?.manifest;
    const performance = manifest?.portfolio_performance;
    return performance && typeof performance === "object" && !Array.isArray(performance) ? performance : null;
  }

  function horizonKeys(payload, portfolioId) {
    const object = dataObject(payload);
    const byHorizon = object.by_horizon && typeof object.by_horizon === "object" ? object.by_horizon : {};
    const values = Object.keys(byHorizon);
    if (values.length) return values.sort((left, right) => Number(left) - Number(right));
    const manifest = manifestPerformance(payload);
    if (manifest) {
      const selected = manifest[String(portfolioId ?? payload?.portfolio_id ?? "strategy")];
      if (!selected || typeof selected !== "object" || Array.isArray(selected)) return [];
      return Object.keys(selected).sort((left, right) => Number(left) - Number(right));
    }
    const performance = payload?.performance && typeof payload.performance === "object" ? payload.performance : {};
    const pValues = Object.keys(performance);
    if (pValues.length) return pValues.sort((left, right) => Number(left) - Number(right));
    const declared = Array.isArray(object.horizons) ? object.horizons : Array.isArray(payload?.horizons) ? payload.horizons : [];
    return declared.map((value) => String(value)).filter(Boolean).sort((left, right) => Number(left) - Number(right));
  }

  function statusFor(summary, nav, ending, endingEvidence = false) {
    const raw = String(summary?.status || "").toUpperCase();
    const liquidation = String(summary?.liquidation_status || "").toUpperCase();
    if (liquidation === "UNSETTLED_DELISTING") return {code: "UNSETTLED", label: "含未结算股份", explanation: "研究已保存，但冻结证据未能结算全部退市股份。"};
    if (liquidation === "OPEN_POSITION") return {code: "COMPLETE_OPEN", label: "完成但有未平仓", explanation: "研究已完成，期末仍有持仓，期末权益包含冻结估值。"};
    if ((raw === "COMPLETE" || raw === "SUCCEEDED") && (liquidation === "LIQUIDATED" || (endingEvidence === true && ending.length === 0))) return {code: "COMPLETE_LIQUIDATED", label: "完成且清算", explanation: "研究已完成，冻结证据显示期末持仓已全部清算。"};
    if (nav.length) return {code: "COMPLETE", label: "结果已保存", explanation: "冻结净值可查看；清算状态以保存的执行证据为准。"};
    if (raw === "FAILED" || raw === "ERROR") return {code: "FAILED", label: "结果读取失败", explanation: "运行摘要可用，但详细净值尚未读取。"};
    return {code: "NO_EVIDENCE", label: "证据不足", explanation: "冻结运行未提供可展示的净值或期末证据。"};
  }

  function fallbackHorizon(payload, key, options = {}) {
    const source = dataObject(payload);
    const portfolioId = String(options.portfolioId ?? source.portfolio_id ?? payload?.portfolio_id ?? "strategy");
    const manifest = manifestPerformance(payload);
    const manifestPortfolio = manifest?.[portfolioId];
    const scopedManifest = Boolean(manifest);
    const manifestSummary = manifestPortfolio && typeof manifestPortfolio === "object" ? manifestPortfolio[key] : null;
    const directPerformance = payload?.performance?.[key];
    const legacySummary = payload?.summary;
    const legacyPortfolioId = String(source.portfolio_id ?? payload?.portfolio_id ?? "strategy");
    const legacyHorizon = legacySummary?.horizon == null ? null : String(legacySummary.horizon);
    const legacyIdentityMatches = !scopedManifest
      && portfolioId === legacyPortfolioId
      && (legacyHorizon === null || legacyHorizon === String(key));
    const performance = (scopedManifest ? manifestSummary : directPerformance) || (legacyIdentityMatches ? legacySummary : null);
    const navSource = Array.isArray(payload?.nav)
      ? payload.nav.filter((row) => {
        const rowPortfolio = row?.portfolio_id;
        const portfolioMatches = rowPortfolio == null
          ? (!scopedManifest || portfolioId === legacyPortfolioId)
          : String(rowPortfolio) === portfolioId;
        return portfolioMatches && String(row?.horizon ?? key) === String(key);
      })
      : [];
    const safePerformance = performance && typeof performance === "object" ? performance : {};
    const eventSource = Array.isArray(safePerformance.execution_events) ? safePerformance.execution_events : [];
    const ending = Array.isArray(safePerformance.ending_holdings)
      ? safePerformance.ending_holdings
      : Array.isArray(safePerformance.ending_positions)
        ? safePerformance.ending_positions
        : legacyIdentityMatches && Array.isArray(payload?.ending_holdings)
          ? payload.ending_holdings
          : Object.entries(safePerformance.open_positions || {}).map(([symbol, shares]) => ({symbol, shares}));
    const endingEvidence = safePerformance.ending_holdings_evidence === true
      || safePerformance.ending_evidence === true
      || safePerformance.capabilities?.ending_holdings_evidence === true;
    const unavailable = performance ? [] : ["所选组合或观察周期没有可用的冻结绩效摘要"];
    return {
      ...source,
      portfolio_id: portfolioId,
      summary: {...safePerformance},
      status: statusFor(safePerformance, navSource, ending, endingEvidence),
      nav: navSource,
      events: eventSource,
      chains: [],
      initial_holdings: legacyIdentityMatches && Array.isArray(payload?.holdings) ? payload.holdings : [],
      ending_holdings: ending,
      capabilities: {daily_nav: navSource.length > 0, weekly_monthly_aggregation: navSource.length > 0, comparable_frozen_benchmark: Boolean(payload?.benchmark_nav?.length), ending_holdings_evidence: endingEvidence},
      unavailable_reasons: unavailable.length ? unavailable : navSource.length ? [] : ["运行摘要未包含日终净值，详细结果读取失败"],
      initial_cash: safePerformance.initial_cash ?? (legacyIdentityMatches ? payload?.initial_cash : undefined),
      ending_cash: safePerformance.realized_cash ?? safePerformance.cash_residual,
      known_assets_value: safePerformance.known_assets_value ?? safePerformance.ending_equity,
      unsettled_symbols: safePerformance.unsettled_symbols || [],
      transaction_count: eventSource.filter(eventIsFill).length,
    };
  }

  function normalizeReviewPayload(payload, options = {}) {
    const input = payload && typeof payload === "object" ? payload : {};
    const source = dataObject(input);
    const portfolioId = String(options.portfolioId ?? source.portfolio_id ?? input.portfolio_id ?? "strategy");
    const keys = horizonKeys(input, portfolioId);
    const hasRequestedHorizon = options.horizon !== undefined && options.horizon !== null;
    const requested = hasRequestedHorizon ? String(options.horizon) : keys[0];
    const horizon = hasRequestedHorizon ? requested : (keys[0] || requested || "");
    const byHorizon = source.by_horizon && typeof source.by_horizon === "object" ? source.by_horizon : {};
    const detail = byHorizon[horizon] || fallbackHorizon(input, horizon, {portfolioId});
    const summary = detail.summary || input.summary || {};
    const nav = Array.isArray(detail.nav) ? detail.nav : [];
    const events = Array.isArray(detail.events) ? detail.events : [];
    const benchmarkNav = Array.isArray(detail.benchmark_nav)
      ? detail.benchmark_nav
      : Array.isArray(input.benchmark_nav)
        ? input.benchmark_nav.filter((row) => row?.horizon == null || String(row.horizon) === String(horizon))
        : [];
    const ending = Array.isArray(detail.ending_holdings) ? detail.ending_holdings : [];
    const review = source.by_horizon ? source : {
      ...source,
      run_id: source.run_id || input.run_id,
      portfolio_id: source.portfolio_id || input.portfolio_id,
      name: source.name || input.name,
      initial_cash: source.initial_cash ?? input.initial_cash,
      scope: source.scope || input.scope,
      quality_mode: source.quality_mode || input.quality_mode,
    };
    const runId = String(review.run_id || input.run_id || "");
    const manifest = input?.manifest && typeof input.manifest === "object" ? input.manifest : {};
    const portfolioOptions = Array.isArray(input.portfolios)
      ? input.portfolios
      : Array.isArray(review.portfolios)
        ? review.portfolios
        : Array.isArray(manifest.portfolios) ? manifest.portfolios : [];
    return {
      review,
      data: {
        ...detail,
        summary,
        nav,
        benchmark_nav: benchmarkNav,
        events,
        ending_holdings: ending,
        initial_holdings: Array.isArray(detail.initial_holdings) ? detail.initial_holdings : [],
        chains: Array.isArray(detail.chains) ? detail.chains : [],
        capabilities: detail.capabilities || {},
      },
      runId,
      portfolioId,
      horizon,
      horizons: keys,
      portfolioOptions,
      name: String(review.name || input.name || portfolioOptions.find((item) => String(item.portfolio_id) === portfolioId)?.name || portfolioId),
      isFallback: Boolean(options.fallback),
    };
  }

  function filterEvents(events, filters = {}) {
    const rows = Array.isArray(events) ? events : [];
    const symbol = String(filters.symbol || "").trim().toLowerCase();
    const action = String(filters.action || "").trim().toUpperCase();
    const reason = String(filters.reason || "").trim().toLowerCase();
    const from = dateText(filters.dateStart || filters.start || null);
    const to = dateText(filters.dateEnd || filters.end || null);
    return rows.filter((event) => {
      const eventSymbol = `${event?.symbol || ""} ${event?.name || ""}`.toLowerCase();
      const eventActionValue = eventAction(event);
      const eventReasonValue = `${eventReason(event)} ${reasonLabel(event)}`.toLowerCase();
      const date = dateText(event?.date);
      return (!symbol || eventSymbol.includes(symbol))
        && (!action || eventActionValue === action)
        && (!reason || eventReasonValue.includes(reason))
        && (!from || (date && date >= from))
        && (!to || (date && date <= to));
    });
  }

  function factualSummary(data) {
    const events = Array.isArray(data?.events) ? data.events : [];
    const fills = events.filter(eventIsFill);
    const initialFills = fills.filter((event) => eventAction(event) === "BUY" && (eventReason(event) === "initial_entry" || String(event.source_kind || "") === "derived_frozen_entry" || String(event.evidence_kind || "").includes("entry"))).length;
    const initialNonFills = events.filter((event) => eventAction(event) === "INITIAL_NOT_FILLED" || (event.filled === false && eventReason(event) === "one_price_bar" && event.source_kind === "frozen_diagnostics")).length;
    const terminalExits = fills.filter((event) => eventAction(event) === "SELL" && ["terminal", "terminal_sell"].includes(eventReason(event))).length;
    const strategyFills = fills.length - terminalExits - initialFills;
    const selected = events.filter((event) => ["SELECT", "CASH"].includes(eventAction(event)));
    const cash = events.filter((event) => eventAction(event) === "CASH");
    const lines = [];
    if (initialFills) lines.push(`${initialFills} 只初始实际持仓已从冻结组合证据建仓${initialNonFills ? `；另有 ${initialNonFills} 只初始未成交` : ""}。`);
    else if (initialNonFills) lines.push(`冻结证据记录 ${initialNonFills} 只初始未成交，未将其计为买入。`);
    for (const chain of data?.chains || []) if (chain?.summary) lines.push(String(chain.summary));
    if (!data?.chains?.length) {
      const deferred = events.filter((event) => eventAction(event) === "DEFER_SELL");
      if (deferred.length) lines.push(`${deferred[0].symbol || "该标的"} 在 ${deferred[0].date} 至 ${deferred.at(-1).date} 连续顺延 ${deferred.length} 次。`);
    }
    if (selected.some((event) => eventAction(event) === "SELECT") && cash.length) {
      const select = selected.find((event) => eventAction(event) === "SELECT");
      const cashEvent = cash[0];
      lines.push(`替补选择 ${select?.symbol || "未标明标的"} 未形成买入成交；${reasonLabel(cashEvent)}。`);
    }
    if (terminalExits) lines.push(`${terminalExits} 次研究结束日退出属于终端清算，不计为策略调仓。`);
    const ending = Array.isArray(data?.ending_holdings) ? data.ending_holdings : [];
    if (!ending.length && String(data?.status?.code || "").includes("LIQUIDATED")) lines.push("期末冻结证据显示持仓已全部清算，现金结果单独列示。");
    const headline = initialFills ? `${initialFills} 只初始实际持仓，${fills.length} 次冻结成交` : "冻结成交与决策记录";
    return {headline, lines, initialFills, initialNonFills, terminalExits, strategyFills, fills: fills.length, selected: selected.length, cash: cash.length};
  }

  function collapseDeferred(events) {
    const rows = [];
    let index = 0;
    while (index < events.length) {
      const current = events[index];
      if (!["DEFER_SELL", "DEFER_BUY"].includes(eventAction(current))) { rows.push({kind: "event", event: current}); index += 1; continue; }
      const group = [current];
      let cursor = index + 1;
      while (cursor < events.length && eventAction(events[cursor]) === eventAction(current) && String(events[cursor].symbol || "") === String(current.symbol || "") && eventReason(events[cursor]) === eventReason(current)) {
        group.push(events[cursor]); cursor += 1;
      }
      rows.push({kind: "defer", events: group}); index = cursor;
    }
    return rows;
  }

  function isSafeUrl(value) {
    try { const url = new URL(String(value)); return ["http:", "https:"].includes(url.protocol); } catch (_) { return false; }
  }

  function csvText(value) {
    let text = String(value ?? "");
    if (/^[=+\-@]/.test(text) || /^[\t\r\n]/.test(text)) text = `'${text}`;
    return text;
  }

  function csvCell(value, textual = false) {
    if (value === null || value === undefined) return "";
    const raw = textual ? csvText(value) : (typeof value === "number" && Number.isFinite(value) ? String(value) : String(value));
    return /[",\r\n]/.test(raw) ? `"${raw.replace(/"/g, '""')}"` : raw;
  }

  function csvLine(values, textualIndexes = []) { const textSet = new Set(textualIndexes); return values.map((value, index) => csvCell(value, textSet.has(index))).join(","); }

  function exportMeta(review, data, options = {}) {
    const scope = String(options.scope || "all");
    const basis = options.basis || data?.capabilities?.weekly_monthly_aggregation_source || "frozen_day_close_nav";
    return [
      `# run_id=${csvCell(review?.run_id || review?.runId, true)}`,
      `# portfolio_id=${csvCell(review?.portfolio_id || review?.portfolioId, true)}`,
      `# horizon=${csvCell(data?.summary?.horizon ?? options.horizon ?? review?.horizons?.[0] ?? "", true)}`,
      `# basis=${csvCell(basis, true)}`,
      `# filter_scope=${csvCell(scope, true)}`,
    ];
  }

  function buildNavCsv(review, data, options = {}) {
    const rows = Array.isArray(options.rows) ? options.rows : Array.isArray(data?.nav) ? data.nav : [];
    const lines = exportMeta(review, data, options);
    lines.push(csvLine(["date", "equity", "unit_nav", "cumulative_return", "daily_return", "drawdown"], [0]));
    for (const row of rows) lines.push(csvLine([dateText(row.date), finite(row.equity), finite(row.unit_nav), finite(row.cumulative_return), finite(row.daily_return), finite(row.drawdown)], [0]));
    return `${lines.join("\r\n")}\r\n`;
  }

  function buildEventCsv(review, data, events, options = {}) {
    const rows = Array.isArray(events) ? events : Array.isArray(data?.events) ? data.events : [];
    const lines = exportMeta(review, data, options);
    const headers = ["date", "source_date", "action", "symbol", "name", "filled", "shares", "price", "commission", "slippage", "net_cash", "budget", "reason_code", "reason_text", "rank_cutoff", "price_basis", "source_kind", "source_url"];
    lines.push(csvLine(headers, headers.map((_, index) => index)));
    for (const event of rows) {
      lines.push(csvLine([
        dateText(event.date), dateText(event.source_date || event.announcement_date), actionLabel(event), event.symbol, event.name,
        eventIsFill(event), finite(event.shares), finite(event.price), finite(event.commission), finite(event.slippage), finite(event.net_cash ?? event.net_proceeds), finite(event.budget), eventReason(event), event.reason_text || reasonLabel(event), event.rank_cutoff, event.price_basis_label || event.price_basis_text || event.price_basis, event.source_kind || event.evidence_kind, isSafeUrl(event.source_url) ? event.source_url : (event.source_url ? `来源链接不可用：${event.source_url}` : null),
      ], headers.map((_, index) => index).filter((index) => ![5, 6, 7, 8, 9, 10, 11].includes(index))));
    }
    return `${lines.join("\r\n")}\r\n`;
  }

  function triggerDownload(text, filename, documentObject, windowObject) {
    const doc = documentObject || root?.document;
    const win = windowObject || root?.window || root;
    if (!doc || typeof doc.createElement !== "function") return false;
    try {
      const blob = new Blob(["\ufeff", text], {type: "text/csv;charset=utf-8"});
      const url = (win?.URL || root?.URL || URL).createObjectURL(blob);
      const anchor = doc.createElement("a"); anchor.href = url; anchor.download = filename; anchor.click();
      (win?.URL || root?.URL || URL).revokeObjectURL(url); return true;
    } catch (_) { return false; }
  }

  function portfolioName(review, data, model) {
    return String(model?.name || review?.name || data?.summary?.portfolio_name || model?.portfolioId || "组合");
  }

  function scopeDates(review, data) {
    const scope = review?.scope || {};
    const requestedStart = scope.requested_start_date || scope.start_date || scope.requestedDate || "未知";
    const requestedEnd = scope.requested_end_date || scope.end_date || "未知";
    const actual = Array.isArray(scope.actual_date_range) ? scope.actual_date_range : [];
    const navDates = (data?.nav || []).map((row) => dateText(row.date)).filter(Boolean).sort();
    return {
      requestedStart: String(requestedStart), requestedEnd: String(requestedEnd),
      actualStart: String(actual[0] || navDates[0] || "未知"),
      actualEnd: String(actual[1] || navDates.at(-1) || data?.summary?.evaluated_date || "未知"),
    };
  }

  function reasonForMetric(data, field) {
    const summary = data?.summary || {};
    const list = Array.isArray(data?.unavailable_reasons) ? data.unavailable_reasons : Array.isArray(summary.unavailable_reasons) ? summary.unavailable_reasons : [];
    if (field === "annualized_return" && finite(summary.annualized_return) === null) return "冻结摘要未保存年化收益";
    if (field === "cash_residual" && finite(summary.cash_residual) === null) return "冻结证据未保存现金余额";
    if (field === "unrealized_holdings_value" && finite(summary.unrealized_holdings_value) === null) return "期末估值证据未保存";
    return list[0] || "冻结证据未提供";
  }

  function statusClass(data, state) {
    const code = String(data?.status?.code || "").toUpperCase();
    if (state === "error") return "is-error";
    if (state === "fallback") return "is-fallback";
    if (code.includes("UNSETTLED") || code.includes("OPEN")) return "is-warning";
    if (code.includes("LIQUIDATED")) return "is-success";
    return "is-neutral";
  }

  function statusExplanation(data) {
    return String(data?.status?.explanation || data?.summary?.message || "冻结结果状态由运行证据决定。");
  }

  function metricValueHtml(label, value, type, reason, extraClass = "") {
    const present = value !== null && value !== undefined && !(typeof value === "number" && Number.isNaN(value));
    let display = present ? (type === "percent" ? formatPercent(value) : type === "money" ? formatMoney(value) : formatNumber(value, type === "shares" ? 0 : 2)) : formatMissing(value, reason);
    if (type === "percent" && present && finite(value) !== null && finite(value) > 0) display = `+${display}`;
    const signClass = present && finite(value) !== null ? (finite(value) > 0 ? "positive" : finite(value) < 0 ? "negative" : "neutral") : "unknown";
    const visibleLabel = type === "money" && !String(label).includes("元") ? `${label}（元）` : label;
    return `<div class="portfolio-review-metric ${escapeHtml(extraClass)}"><span>${escapeHtml(visibleLabel)}</span><strong class="${signClass}">${escapeHtml(display)}</strong></div>`;
  }

  function compactMetricGrid(data) {
    const summary = data?.summary || {};
    return [
      metricValueHtml("总收益率", summary.total_return, "percent", reasonForMetric(data, "total_return"), "primary-metric"),
      metricValueHtml("盈亏金额", summary.profit_loss, "money", reasonForMetric(data, "profit_loss"), "primary-metric"),
      metricValueHtml("期末权益", summary.ending_equity, "money", reasonForMetric(data, "ending_equity"), "primary-metric"),
      metricValueHtml("最大回撤", summary.max_drawdown, "percent", reasonForMetric(data, "max_drawdown"), "primary-metric"),
    ].join("");
  }

  function secondaryMetricGrid(data) {
    const summary = data?.summary || {};
    return [
      metricValueHtml("初始本金", data.initial_cash ?? summary.initial_cash, "money", reasonForMetric(data, "initial_cash")),
      metricValueHtml("年化收益", summary.annualized_return, "percent", reasonForMetric(data, "annualized_return")),
      metricValueHtml("佣金", summary.commission_paid, "money", reasonForMetric(data, "commission_paid")),
      metricValueHtml("滑点", summary.slippage_paid, "money", reasonForMetric(data, "slippage_paid")),
      metricValueHtml("期末现金", data.ending_cash ?? summary.cash_residual ?? summary.realized_cash, "money", reasonForMetric(data, "cash_residual")),
      metricValueHtml("未平仓估值", summary.unrealized_holdings_value, "money", reasonForMetric(data, "unrealized_holdings_value")),
    ].join("");
  }

  function summaryFactsHtml(data) {
    const facts = factualSummary(data);
    const lines = facts.lines.length ? facts.lines : ["冻结事件未提供可生成事实摘要的记录。"];
    const preview = lines.slice(0, 2).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
    const extra = lines.length > 2 ? `<details class="portfolio-review-facts-more"><summary>展开其余 ${lines.length - 2} 条事实</summary><ul>${lines.slice(2).map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul></details>` : "";
    return `<section class="portfolio-review-facts" aria-labelledby="portfolioReviewFactsTitle"><div class="portfolio-review-section-title"><div><span class="portfolio-review-kicker">FACTS FROM FROZEN EVENTS</span><h3 id="portfolioReviewFactsTitle">结果摘要</h3></div><span class="portfolio-review-count">${escapeHtml(facts.headline)}</span></div><ul class="portfolio-review-facts-preview">${preview}</ul>${extra}</section>`;
  }

  function buildEvidenceIndex(payload) {
    const index = Object.create(null);
    const sources = [
      payload,
      payload?.spec,
      payload?.wizard_metadata,
      payload?.spec?.wizard_metadata,
      payload?.spec?.wizard_metadata?.delisting,
    ];
    for (const source of sources) {
      const records = Array.isArray(source?.delisting_events) ? source.delisting_events : [];
      for (const record of records) {
        if (!record || typeof record !== "object") continue;
        const keys = [
          record.event_id,
          record.trigger_event_id,
          record.chain_trigger_event_id,
          record.id,
          record.announcement_number,
          record.symbol && record.announcement_number ? `${record.symbol}:${record.announcement_number}` : null,
        ].filter((key) => key !== null && key !== undefined && String(key).trim());
        for (const key of keys) if (!index[String(key)]) index[String(key)] = {...record};
      }
    }
    return index;
  }

  function eventDetailParts(event, evidence) {
    const parts = [];
    const notFilled = !eventIsFill(event);
    const publishedDate = dateText(evidence?.published_at || evidence?.announcement_date || event.source_date || event.announcement_date);
    if (publishedDate) parts.push(["公告日期", publishedDate]);
    const executableDate = dateText(evidence?.trading_resumes_on || evidence?.first_executable_date || evidence?.resumption_date);
    if (executableDate) parts.push(["公告复牌日", executableDate]);
    const delistedDate = dateText(evidence?.delisted_date || evidence?.termination_date);
    if (delistedDate) parts.push(["终止上市日", delistedDate]);
    parts.push(["实际日期", dateText(event.date) || "未知（事件日期未保存）"]);
    if (event.shares !== null && event.shares !== undefined) parts.push(["股数", formatNumber(event.shares, 0)]);
    else parts.push(["股数", notFilled ? "不适用（未形成成交）" : "未知（冻结成交证据未提供）"]);
    parts.push(["价格", event.price === null || event.price === undefined ? (notFilled ? "不适用（未形成成交）" : "未知（冻结成交价格未提供）") : formatPrice(event.price)]);
    parts.push(["佣金", event.commission === null || event.commission === undefined ? (notFilled ? "不适用（未形成成交）" : "未知（冻结成本未提供）") : formatMoney(event.commission)]);
    parts.push(["滑点", event.slippage === null || event.slippage === undefined ? (notFilled ? "不适用（未形成成交）" : "未知（冻结成本未提供）") : formatMoney(event.slippage)]);
    const cash = event.net_cash ?? event.net_proceeds;
    parts.push(["净现金", cash === null || cash === undefined ? (notFilled ? "不适用（未形成成交）" : "未知（冻结回款未提供）") : formatMoney(cash)]);
    if (event.budget !== null && event.budget !== undefined) parts.push(["替补预算", formatMoney(event.budget)]);
    if (event.rank_cutoff) parts.push(["排名截止", String(event.rank_cutoff)]);
    parts.push(["原因", reasonLabel(event)]);
    parts.push(["价格口径", event.price_basis_label || event.price_basis_text || event.price_basis || "未知（冻结价格口径未提供）"]);
    parts.push(["证据", event.evidence_kind || event.source_kind || event.source || "未知（证据类型未保存）"]);
    return parts;
  }

  function sourceLinkHtml(event, evidence) {
    const url = event?.source_url || evidence?.source_url;
    if (!url) return `<span class="portfolio-review-unknown">来源链接：未知（冻结证据未提供）</span>`;
    if (!isSafeUrl(url)) return `<span class="portfolio-review-unknown">来源链接：${escapeHtml(url)}（链接协议不可用）</span>`;
    return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">查看冻结来源</a>`;
  }

  function eventEvidence(event, evidenceById) {
    if (!event || !evidenceById || typeof evidenceById !== "object") return null;
    const keys = [event.event_id, event.trigger_event_id, event.chain_trigger_event_id, event.chain_id, event.id].filter(Boolean);
    for (const key of keys) if (evidenceById[String(key)]) return evidenceById[String(key)];
    return null;
  }

  function eventRowHtml(event, selectedId, evidenceById) {
    const action = eventAction(event);
    const fill = eventIsFill(event);
    const terminal = fill && action === "SELL" && ["terminal", "terminal_sell"].includes(eventReason(event));
    const classNames = ["portfolio-review-event-row", fill ? "is-fill" : "is-decision", terminal ? "is-terminal" : ""];
    const status = fill ? "真实成交" : action === "INITIAL_NOT_FILLED" ? "未成交" : "决策 / 过程";
    return `<article class="${classNames.join(" ")} ${selectedId === String(event.id) ? "is-selected" : ""}" data-event-row="${escapeHtml(event.id)}"><button class="portfolio-review-event-select" type="button" data-review-event="${escapeHtml(event.id)}" aria-pressed="${selectedId === String(event.id) ? "true" : "false"}"><span class="event-symbol"><b>${escapeHtml(event.symbol || "未标明标的")}</b><small>${escapeHtml(event.name || "")}</small></span><span class="event-action"><i aria-hidden="true"></i>${escapeHtml(actionLabel(event))}<small>${escapeHtml(status)}</small></span><span class="event-reason">${escapeHtml(reasonLabel(event))}</span><time datetime="${escapeHtml(dateText(event.date) || "")}">${escapeHtml(dateText(event.date) || "未知日期")}</time></button></article>`;
  }

  function groupedEventHtml(events, selectedId, evidenceById) {
    const rows = [];
    const source = Array.isArray(events) ? events : [];
    let index = 0;
    while (index < source.length) {
      const event = source[index];
      const action = eventAction(event);
      if (["DEFER_SELL", "DEFER_BUY"].includes(action)) {
        const group = [event];
        let cursor = index + 1;
        while (cursor < source.length && eventAction(source[cursor]) === action
          && String(source[cursor].symbol || "") === String(event.symbol || "")
          && eventReason(source[cursor]) === eventReason(event)) {
          group.push(source[cursor]);
          cursor += 1;
        }
        const first = dateText(group[0].date) || "未知日期";
        const last = dateText(group.at(-1).date) || first;
        const selected = group.some((row) => String(row.id) === String(selectedId));
        rows.push(`<details class="portfolio-review-defer-group" ${selected ? "open" : ""}><summary><time>${escapeHtml(first)} → ${escapeHtml(last)}</time><span>${escapeHtml(group[0].symbol || "未标明标的")} · ${group.length} 次${escapeHtml(action === "DEFER_SELL" ? "卖出顺延" : "买入顺延")}，展开查看每日冻结记录</span></summary>${group.map((row) => eventRowHtml(row, selectedId, evidenceById)).join("")}</details>`);
        index = cursor;
        continue;
      }
      const date = dateText(event.date) || "未知日期";
      const group = [event];
      let cursor = index + 1;
      while (cursor < source.length && (dateText(source[cursor].date) || "未知日期") === date
        && !["DEFER_SELL", "DEFER_BUY"].includes(eventAction(source[cursor]))) {
        group.push(source[cursor]);
        cursor += 1;
      }
      if (group.length > 1) rows.push(`<details class="portfolio-review-same-date" ${group.some((row) => String(row.id) === String(selectedId)) ? "open" : ""}><summary><time>${escapeHtml(date)}</time><span>${group.length} 个事件，选择一个查看明细</span></summary>${group.map((row) => eventRowHtml(row, selectedId, evidenceById)).join("")}</details>`);
      else rows.push(eventRowHtml(group[0], selectedId, evidenceById));
      index = cursor;
    }
    return rows.join("");
  }

  function filteredEventsView(data, filters, eventLimit) {
    const filtered = filterEvents(data?.events, filters);
    const limit = Math.max(1, Number(eventLimit || 160));
    const visible = filtered.length <= limit ? filtered : [...filtered.slice(0, Math.floor(limit / 2)), ...filtered.slice(-Math.ceil(limit / 2))];
    return {filtered, visible, omitted: filtered.length - visible.length};
  }

  function eventFiltersHtml(data, filters) {
    const actions = [...new Set((data?.events || []).map(eventAction).filter(Boolean))].sort();
    const reasons = [...new Set((data?.events || []).map(eventReason).filter(Boolean))].sort();
    return `<div class="portfolio-review-event-filters" role="search" aria-label="交易事件筛选"><label>股票<input data-review-filter="symbol" type="search" value="${escapeHtml(filters.symbol || "")}" placeholder="代码或名称"></label><label>操作<select data-review-filter="action"><option value="">全部操作</option>${actions.map((value) => `<option value="${escapeHtml(value)}" ${String(filters.action || "").toUpperCase() === value ? "selected" : ""}>${escapeHtml(ACTION_LABELS[value] || value)}</option>`).join("")}</select></label><label>原因<select data-review-filter="reason"><option value="">全部原因</option>${reasons.map((value) => `<option value="${escapeHtml(value)}" ${String(filters.reason || "") === value ? "selected" : ""}>${escapeHtml(REASON_LABELS[value] || value)}</option>`).join("")}</select></label><label>起始日期<input data-review-filter="dateStart" type="date" value="${escapeHtml(filters.dateStart || "")}"></label><label>结束日期<input data-review-filter="dateEnd" type="date" value="${escapeHtml(filters.dateEnd || "")}"></label><button type="button" data-review-action="clear-filters">清除筛选</button></div>`;
  }

  function selectedEventDetailHtml(model, state) {
    const event = model?.data?.events?.find((candidate) => String(candidate.id) === String(state.selectedEventId)) || null;
    if (!event) return `<div class="portfolio-review-selected-detail-empty"><span class="portfolio-review-kicker">SELECTED EVENT</span><h4>选择一个事件查看冻结明细</h4><p>从左侧事件列表或图表标记选择事件；日期、成交与来源始终按冻结记录显示。</p></div>`;
    const evidence = eventEvidence(event, state.evidenceById);
    const detail = eventDetailParts(event, evidence).map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
    const note = evidence?.evidence_note ? `<p class="portfolio-review-evidence-note"><strong>证据说明</strong>${escapeHtml(evidence.evidence_note)}</p>` : "";
    const sourceTitle = evidence?.source_title ? `<p class="portfolio-review-source-title">${escapeHtml(evidence.source_title)}</p>` : "";
    return `<div class="portfolio-review-selected-detail-content"><span class="portfolio-review-kicker">SELECTED EVENT</span><h4 id="portfolioReviewSelectedEventTitle">${escapeHtml(event.symbol || "未标明标的")} · ${escapeHtml(actionLabel(event))}</h4><p class="portfolio-review-selected-event-meta">${escapeHtml(dateText(event.date) || "未知日期")} · ${escapeHtml(reasonLabel(event))}</p><dl>${detail}</dl><p class="portfolio-review-source-link">${sourceLinkHtml(event, evidence)}</p>${sourceTitle}${note}</div>`;
  }

  function eventGroupChooserHtml(state) {
    const group = state.eventGroup;
    const events = Array.isArray(group?.events) ? group.events : [];
    if (!events.length) return selectedEventDetailHtml(null, state);
    const buttons = events.map((event) => `<button type="button" class="portfolio-review-group-event" data-review-group-event="${escapeHtml(event.id)}"><span><b>${escapeHtml(event.symbol || "未标明标的")}</b><small>${escapeHtml(event.name || "")}</small></span><span>${escapeHtml(actionLabel(event))} · ${escapeHtml(reasonLabel(event))}</span><time datetime="${escapeHtml(dateText(event.date) || "")}">${escapeHtml(dateText(event.date) || "未知日期")}</time></button>`).join("");
    return `<div class="portfolio-review-event-group-chooser" role="region" aria-labelledby="portfolioReviewEventGroupTitle"><span class="portfolio-review-kicker">EVENT GROUP</span><h4 id="portfolioReviewEventGroupTitle">${escapeHtml(group.period === "1D" ? (group.date || "同一日期") : `${group.period || "周期"} · ${group.date || "同一周期"}`)} 有 ${events.length} 个冻结事件</h4><p>请选择要查看的单个事件；每个事件的实际日期会保留。</p><div class="portfolio-review-group-options" role="list">${buttons}</div><button type="button" class="portfolio-review-group-cancel" data-review-action="clear-event-group">返回事件列表</button></div>`;
  }

  function eventTabHtml(model, state) {
    const data = model.data;
    const view = filteredEventsView(data, state.filters, state.eventLimit);
    const selectedId = state.selectedEventId;
    const notice = state.filterNotice ? `<div class="portfolio-review-inline-notice" role="status">${escapeHtml(state.filterNotice)}</div>` : "";
    const scopeLabel = Object.values(state.filters).some(Boolean) ? `当前筛选 ${view.filtered.length} / ${data.events.length} 条` : `全部事件 ${data.events.length} 条`;
    const rows = view.visible.length ? groupedEventHtml(view.visible, selectedId, state.evidenceById) : `<p class="portfolio-review-empty">没有符合当前筛选条件的事件。请清除筛选后查看冻结记录。</p>`;
    const more = view.omitted > 0 ? `<button class="portfolio-review-more" type="button" data-review-action="more-events">显示更多事件（还剩 ${view.omitted} 条，控制器仍保留全部记录）</button>` : "";
    const chainRows = (data.chains || []).map((chain) => `<details class="portfolio-review-chain"><summary><span>${escapeHtml(chain.start_date || "未知")} → ${escapeHtml(chain.end_date || "未知")}</span><strong>${escapeHtml(chain.summary || "冻结事件链")}</strong></summary><p>触发 → 约束 → 执行 → 替补选择 → 结果</p><p>${escapeHtml(chain.summary || "冻结证据未提供链摘要")}</p><div class="chain-events">${(chain.event_ids || []).map((id) => data.events.find((event) => String(event.id) === String(id))).filter(Boolean).map((event) => eventRowHtml(event, selectedId, state.evidenceById)).join("")}</div></details>`).join("");
    const selectedDetail = state.eventGroup ? eventGroupChooserHtml(state) : selectedEventDetailHtml(model, state);
    return `<section class="portfolio-review-tab-panel" data-review-tab-panel="events"><div class="portfolio-review-section-title"><div><span class="portfolio-review-kicker">EVENT TRACE</span><h3>交易与调仓</h3></div><div class="portfolio-review-tab-actions"><span>${escapeHtml(scopeLabel)}</span><button type="button" data-review-export="events-all">下载全部事件 CSV</button><button type="button" data-review-export="events-filtered">下载当前筛选 CSV</button></div></div>${notice}${eventFiltersHtml(data, state.filters)}<div class="portfolio-review-legend" aria-label="事件类型图例"><span class="legend-fill">● 真实成交</span><span class="legend-decision">■ 决策 / 顺延</span><span class="legend-terminal">◆ 期末清算</span></div><div class="portfolio-review-event-layout"><div class="portfolio-review-event-list-column"><div class="portfolio-review-event-list">${rows}</div>${more}${chainRows ? `<div class="portfolio-review-chain-list"><h4>调仓链</h4>${chainRows}</div>` : ""}</div><aside class="portfolio-review-selected-detail" data-review-selected-detail aria-label="选中事件详情">${selectedDetail}</aside></div></section>`;
  }

  function holdingsTable(title, rows, emptyText, schema = "initial") {
    const list = Array.isArray(rows) ? rows : [];
    if (!list.length) return `<div class="portfolio-review-holdings-card"><h4>${escapeHtml(title)}</h4><p class="portfolio-review-empty">${escapeHtml(emptyText)}</p></div>`;
    if (schema === "ending") {
      const body = list.map((row) => {
        const settlement = row.unsettled === true ? "未结算" : row.unsettled === false ? "已结算" : "未知";
        return `<tr><th scope="row"><b>${escapeHtml(row.symbol || "未知代码")}</b><small>${escapeHtml(row.name || "")}</small></th><td>${escapeHtml(row.shares == null ? "未知" : formatNumber(row.shares, 0))}</td><td>${escapeHtml(row.market_value == null ? "未知" : formatMoney(row.market_value))}</td><td>${escapeHtml(settlement)}</td><td>${escapeHtml(row.source || row.evidence_kind || "冻结证据")}</td></tr>`;
      }).join("");
      return `<div class="portfolio-review-holdings-card"><h4>${escapeHtml(title)}</h4><div class="portfolio-review-table-wrap"><table><thead><tr><th>股票</th><th>股数</th><th>已保存市值</th><th>结算状态</th><th>证据</th></tr></thead><tbody>${body}</tbody></table></div></div>`;
    }
    const body = list.map((row) => `<tr><th scope="row"><b>${escapeHtml(row.symbol || "未知代码")}</b><small>${escapeHtml(row.name || "")}</small></th><td>${escapeHtml(row.shares == null ? "未知" : formatNumber(row.shares, 0))}</td><td>${escapeHtml(row.entry_price == null ? "未知" : formatPrice(row.entry_price))}</td><td>${escapeHtml(row.entry_date || row.date || "未知")}</td><td>${escapeHtml(row.source || row.evidence_kind || "冻结证据")}</td></tr>`).join("");
    return `<div class="portfolio-review-holdings-card"><h4>${escapeHtml(title)}</h4><div class="portfolio-review-table-wrap"><table><thead><tr><th>股票</th><th>股数</th><th>价格</th><th>日期</th><th>证据</th></tr></thead><tbody>${body}</tbody></table></div></div>`;
  }

  function holdingsTabHtml(model) {
    const data = model.data;
    const summary = data.summary || {};
    const unsettled = Array.isArray(data.unsettled_symbols) && data.unsettled_symbols.length ? data.unsettled_symbols.join("、") : "无";
    const ending = data.ending_holdings || [];
    const endingEvidence = data.capabilities?.ending_holdings_evidence === true
      || data.status?.liquidation_status === "LIQUIDATED"
      || String(summary.liquidation_status || "").toUpperCase() === "LIQUIDATED"
      || data.status?.code === "COMPLETE_LIQUIDATED";
    const endingText = ending.length
      ? "冻结期末持仓证据"
      : endingEvidence
        ? "冻结证据显示期末没有持仓"
        : "冻结运行未提供期末持仓/清算证据";
    const valuationText = ending.length ? "含期末持仓估值" : endingEvidence ? "全部清算后现金" : "期末持仓/清算证据未提供";
    return `<section class="portfolio-review-tab-panel" data-review-tab-panel="holdings"><div class="portfolio-review-section-title"><div><span class="portfolio-review-kicker">POSITION ACCOUNTING</span><h3>初始与期末持仓</h3></div><span>初始持仓 ≠ 当前行情持仓</span></div><div class="portfolio-review-holdings-grid">${holdingsTable("初始实际持仓（冻结建仓）", data.initial_holdings, "没有保存初始持仓证据；不从候选池推断买入。", "initial")}${holdingsTable("期末持仓（冻结证据）", ending, endingText, "ending")}</div><div class="portfolio-review-cash-summary"><div><span>期末现金</span><strong>${escapeHtml(data.ending_cash == null ? formatMissing(null, "冻结现金余额未提供") : formatMoney(data.ending_cash))}</strong></div><div><span>已知资产</span><strong>${escapeHtml(data.known_assets_value == null ? formatMissing(null, "冻结资产证据未提供") : formatMoney(data.known_assets_value))}</strong></div><div><span>未结算股份</span><strong>${escapeHtml(unsettled)}</strong></div><div><span>估值语义</span><strong>${escapeHtml(valuationText)}</strong></div></div><p class="portfolio-review-muted">${escapeHtml(data.status?.code === "UNSETTLED" ? "未结算股份不包含在完整收益中；已知资产与现金单独列示。" : "表中的初始价格和期末证据均来自冻结运行，不补查新价格。")}</p></section>`;
  }

  function evidenceText(model, state) {
    const review = model.review || {};
    const data = model.data || {};
    const scope = scopeDates(review, data);
    const metadata = state.evidence || {};
    const spec = metadata.spec || metadata.wizard_metadata || {};
    const wizard = spec.wizard_metadata || spec;
    const portfolio = wizard.portfolio || {};
    const delisting = Array.isArray(wizard.delisting_events) ? wizard.delisting_events : [];
    const raw = state.evidence ? JSON.stringify(state.evidence, null, 2) : "摘要接口尚未加载；首屏仅使用组合冻结投影。";
    const summaryRows = [
      ["质量模式", review.quality_mode === "strict" ? "正式研究" : review.quality_mode === "exploratory" ? "探索研究" : (review.quality_mode || "未知（运行未保存）")],
      ["请求区间", `${scope.requestedStart} → ${scope.requestedEnd}`],
      ["实际区间", `${scope.actualStart} → ${scope.actualEnd}`],
      ["持有交易日", (data.summary?.horizon ?? model.horizon) || "未知"],
      ["价格口径", data.events?.find((event) => event.price_basis_label || event.price_basis_text || event.price_basis)?.price_basis_label || "按事件保存的冻结价格口径"],
      ["佣金假设", portfolio.commission_rate == null ? "未知（摘要未提供）" : `${Number(portfolio.commission_rate) * 100}%`],
      ["滑点假设", portfolio.slippage_rate == null ? "未知（摘要未提供）" : `${Number(portfolio.slippage_rate) * 100}%`],
      ["交易日历", review.scope?.calendar_source || data.capabilities?.session_source || "未知（交易日历未保存）"],
      ["基准", data.capabilities?.comparable_frozen_benchmark ? "同口径冻结基准可用" : `本次运行未保存基准${data.capabilities?.comparable_frozen_benchmark_reason ? `：${data.capabilities.comparable_frozen_benchmark_reason}` : ""}`],
    ];
    if (state.evidenceError) summaryRows.push(["摘要证据", `暂不可加载：${state.evidenceError}`]);
    if (delisting.length) summaryRows.push(["退市来源", `${delisting.length} 条冻结公告证据`]);
    const rows = summaryRows.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(String(value))}</dd></div>`).join("");
    const limitations = [...new Set([...(data.unavailable_reasons || []), ...(data.capabilities?.weekly_monthly_unavailable_reason ? [data.capabilities.weekly_monthly_unavailable_reason] : [])])];
    return `<section class="portfolio-review-tab-panel" data-review-tab-panel="evidence"><div class="portfolio-review-section-title"><div><span class="portfolio-review-kicker">FROZEN SETTINGS / EVIDENCE</span><h3>研究设置与证据</h3></div><span>原始诊断按需展开</span></div><dl class="portfolio-review-evidence-grid">${rows}</dl>${limitations.length ? `<div class="portfolio-review-limitations"><strong>局限与缺失</strong><ul>${limitations.map((value) => `<li>${escapeHtml(value)}</li>`).join("")}</ul></div>` : ""}<details class="portfolio-review-raw-evidence"><summary>查看原始冻结摘要与诊断</summary><pre>${escapeHtml(raw)}</pre></details></section>`;
  }

  function resultHeaderHtml(model, state) {
    const dates = scopeDates(model.review, model.data);
    const summary = model.data.summary || {};
    const status = model.data.status || statusFor(
      summary,
      model.data.nav || [],
      model.data.ending_holdings || [],
      model.data.capabilities?.ending_holdings_evidence === true,
    );
    const selectedPortfolio = model.portfolioId;
    const portfolioOptions = model.portfolioOptions || [];
    const portfolioSelect = portfolioOptions.length > 1 ? `<label>组合<select data-review-select="portfolio" aria-label="选择组合">${portfolioOptions.map((item) => `<option value="${escapeHtml(item.portfolio_id)}" ${String(item.portfolio_id) === selectedPortfolio ? "selected" : ""}>${escapeHtml(item.name || item.portfolio_id)}</option>`).join("")}</select></label>` : "";
    const horizonSelect = (model.horizons || []).length > 1 ? `<label>观察周期<select data-review-select="horizon" aria-label="选择观察周期">${model.horizons.map((value) => `<option value="${escapeHtml(value)}" ${value === model.horizon ? "selected" : ""}>${escapeHtml(value)} 交易日</option>`).join("")}</select></label>` : `<span class="portfolio-review-horizon">${escapeHtml(model.horizon || summary.horizon || "未知")} 个持有交易日</span>`;
    const fallbackNotice = state.status === "fallback" ? `<div class="portfolio-review-fallback-notice" role="status"><strong>详细结果读取失败，当前显示已保存摘要。</strong><span>重试读取结果只重新读取冻结文件，不会重跑研究。</span><button type="button" data-review-action="retry-results">重试读取结果</button></div>` : "";
    const missingNotice = state.status === "error" ? `<div class="portfolio-review-error-notice" role="alert"><strong>结果暂不可显示</strong><span>${escapeHtml(state.errorMessage || "服务未返回有效结果")}</span><button type="button" data-review-action="retry-results">重试读取结果</button></div>` : "";
    return `<header class="portfolio-review-header"><div><span class="portfolio-review-kicker">PORTFOLIO REVIEW · READ ONLY</span><h2>组合复盘 · ${escapeHtml(portfolioName(model.review, model.data, model))}</h2><p class="portfolio-review-subtitle">运行 <code>${escapeHtml(model.runId || "未知")}</code> · 请求 ${escapeHtml(dates.requestedStart)} → ${escapeHtml(dates.requestedEnd)} · 实际 ${escapeHtml(dates.actualStart)} → ${escapeHtml(dates.actualEnd)}</p></div><div class="portfolio-review-header-controls">${portfolioSelect}${horizonSelect}<span class="portfolio-review-status ${statusClass(model.data, state.status)}"><b>${escapeHtml(status.label || "结果")}</b><small>${escapeHtml(statusExplanation(model.data))}</small></span></div></header>${fallbackNotice}${missingNotice}`;
  }

  function toolbarHtml(model, state) {
    const capability = model.data?.capabilities || {};
    const benchmark = capability.comparable_frozen_benchmark ? `<label class="portfolio-review-benchmark"><input type="checkbox" data-review-toggle="benchmark" ${state.benchmark ? "checked" : ""}>显示冻结基准</label>` : `<span class="portfolio-review-benchmark-disabled">本次运行未保存可比较冻结基准</span>`;
    const custom = state.range && typeof state.range === "object" ? state.range : {start: "", end: ""};
    const metricButtons = [["unit_nav", "单位净值"], ["equity", "权益（元）"], ["cumulative_return", "累计收益（%）"]].map(([value, label]) => `<button type="button" data-review-metric="${value}" class="${state.metric === value ? "active" : ""}" aria-pressed="${state.metric === value ? "true" : "false"}">${label}</button>`).join("");
    const periodButtons = [["1D", "日终"], ["1W", "周"], ["1M", "月"]].map(([value, label]) => `<button type="button" data-review-period="${value}" class="${state.period === value ? "active" : ""}" aria-pressed="${state.period === value ? "true" : "false"}">${label}</button>`).join("");
    const rangeButtons = [["all", "全部"], ["1M", "近 1 月"], ["3M", "近 3 月"], ["6M", "近 6 月"], ["1Y", "近 1 年"]].map(([value, label]) => `<button type="button" data-review-range="${value}" class="${state.range === value ? "active" : ""}" aria-pressed="${state.range === value ? "true" : "false"}">${label}</button>`).join("");
    return `<div class="portfolio-review-toolbar" role="toolbar" aria-label="组合图表工具栏"><div class="portfolio-review-control-group" role="group" aria-label="指标"><span>指标</span>${metricButtons}</div><div class="portfolio-review-control-group" role="group" aria-label="周期"><span>周期</span>${periodButtons}</div><div class="portfolio-review-control-group" role="group" aria-label="范围"><span>范围</span>${rangeButtons}<label class="portfolio-review-custom-range">自定义<input type="date" data-review-range-input="start" value="${escapeHtml(custom.start || "")}"><span>至</span><input type="date" data-review-range-input="end" value="${escapeHtml(custom.end || "")}"></label><button type="button" data-review-action="apply-range">应用</button><button type="button" data-review-action="reset-range">重置</button></div><div class="portfolio-review-chart-note"><span>1W / 1M 为实际日终净值聚合的 K 线（开高低收），不代表盘中 OHLC。</span>${benchmark}<button type="button" data-review-export-nav>下载 NAV CSV</button></div></div>`;
  }

  function tabsHtml(state) {
    return `<div class="portfolio-review-tabs" role="tablist" aria-label="组合复盘详情"><button type="button" role="tab" data-review-tab="events" aria-selected="${state.tab === "events" ? "true" : "false"}" tabindex="${state.tab === "events" ? "0" : "-1"}">交易与调仓</button><button type="button" role="tab" data-review-tab="holdings" aria-selected="${state.tab === "holdings" ? "true" : "false"}" tabindex="${state.tab === "holdings" ? "0" : "-1"}">初始与期末持仓</button><button type="button" role="tab" data-review-tab="evidence" aria-selected="${state.tab === "evidence" ? "true" : "false"}" tabindex="${state.tab === "evidence" ? "0" : "-1"}">研究设置与证据</button></div>`;
  }

  function simpleFallbackHtml(data) {
    const rows = (data?.nav || []).filter((row) => finite(row?.equity) !== null || finite(row?.unit_nav) !== null);
    if (!rows.length) return `<div class="portfolio-review-simple-fallback"><p>图表资源不可用，冻结结果没有可展示的日终净值。</p></div>`;
    const width = 900; const height = 220; const values = rows.map((row) => finite(row.unit_nav) ?? finite(row.equity)).filter((value) => value !== null); const low = Math.min(...values); const high = Math.max(...values); const span = high === low ? 1 : high - low;
    const points = rows.map((row, index) => { const value = finite(row.unit_nav) ?? finite(row.equity); return value === null ? null : `${(index / Math.max(1, rows.length - 1) * width).toFixed(2)},${(1 - (value - low) / span) * (height - 30) + 10}`; }).filter(Boolean).join(" ");
    return `<div class="portfolio-review-simple-fallback"><p>图表库未加载，使用可访问的冻结净值回退。未知值不会连线。</p><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="组合日终净值回退图"><polyline points="${points}" fill="none" stroke="#0f766e" stroke-width="3"/></svg><table><caption>冻结日终净值（可访问回退）</caption><thead><tr><th>日期</th><th>单位净值</th><th>权益</th></tr></thead><tbody>${rows.slice(0, 240).map((row) => `<tr><th scope="row">${escapeHtml(dateText(row.date))}</th><td>${escapeHtml(row.unit_nav == null ? "未知" : formatNumber(row.unit_nav, 4))}</td><td>${escapeHtml(row.equity == null ? "未知" : formatMoney(row.equity))}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function simpleDrawdownHtml(data) {
    const rows = (data?.nav || []).filter((row) => finite(row?.drawdown) !== null);
    if (!rows.length) return `<p class="portfolio-review-empty">暂无冻结回撤序列。</p>`;
    const width = 900; const height = 110; const low = Math.min(0, ...rows.map((row) => finite(row.drawdown))); const span = Math.max(0.01, -low); const points = rows.map((row, index) => `${(index / Math.max(1, rows.length - 1) * width).toFixed(2)},${10 + (finite(row.drawdown) - 0) / span * (height - 20)}`).join(" ");
    return `<div class="portfolio-review-simple-drawdown"><p>冻结日终回撤（保持原口径）</p><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="组合回撤回退图"><polyline points="${points}" fill="none" stroke="#b45309" stroke-width="2"/></svg></div>`;
  }

  function chartFallbackHtml(data) {
    return `<div class="portfolio-review-chart-fallback" data-review-fallback="chart">${simpleFallbackHtml(data)}${simpleDrawdownHtml(data)}</div>`;
  }

  function query(container, selector) {
    try { return container && typeof container.querySelector === "function" ? container.querySelector(selector) : null; } catch (_) { return null; }
  }

  function queryAll(container, selector) {
    try { return container && typeof container.querySelectorAll === "function" ? [...container.querySelectorAll(selector)] : []; } catch (_) { return []; }
  }

  function create(container, options = {}) {
    const config = options && typeof options === "object" ? options : {};
    const documentObject = container?.ownerDocument || config.document || root?.document || null;
    const windowObject = config.window || root?.window || root || null;
    const chartModule = Object.prototype.hasOwnProperty.call(config, "charts") ? config.charts : root?.PortfolioReviewCharts;
    const state = {
      status: config.data ? (config.fallback ? "fallback" : "ready") : "loading",
      metric: "unit_nav", period: "1D", range: "all", tab: "events", benchmark: false,
      selectedEventId: null, eventGroup: null, filters: {symbol: "", action: "", reason: "", dateStart: "", dateEnd: ""},
      eventLimit: 160, filterNotice: "", errorMessage: "", evidence: null, evidenceById: Object.create(null), evidenceError: "", hover: null,
      renderCount: 0,
    };
    let model = config.data ? normalizeReviewPayload(config.data, {portfolioId: config.portfolioId, horizon: config.horizon, fallback: config.fallback}) : null;
    let chartController = null;
    let shellReady = false;
    let destroyed = false;
    let listenersBound = false;

    function identity() { return {runId: model?.runId || null, portfolioId: model?.portfolioId || null, horizon: model?.horizon || null}; }

    function setRegion(name, html) {
      const region = query(container, `[data-review-region="${name}"]`);
      if (region) region.innerHTML = html;
    }

    function destroyChart() {
      if (chartController && typeof chartController.destroy === "function") {
        try { chartController.destroy(); } catch (_) { /* chart teardown must not block fallback rendering */ }
      }
      chartController = null;
    }

    function chartData() {
      if (!model) return null;
      return {
        ...model.data,
        run_id: model.runId,
        portfolio_id: model.portfolioId,
        initial_cash: model.data.initial_cash ?? model.data.summary?.initial_cash,
      };
    }

    function renderChart() {
      const host = query(container, '[data-review-region="chart"]');
      if (!host || !model) return;
      destroyChart();
      host.innerHTML = "";
      const data = chartData();
      if (chartModule && typeof chartModule.create === "function") {
        try {
          chartController = chartModule.create(host, {
            data,
            initialCash: model.data.initial_cash ?? model.data.summary?.initial_cash,
            benchmark: state.benchmark && model.data.benchmark_nav ? model.data.benchmark_nav : null,
            onSelectEvent: (event) => {
              if (event?.type === "group" || (event?.groupId && Array.isArray(event.events) && event.events.length > 1)) {
                openEventGroup(event);
                return;
              }
              selectEvent(event?.id || event?.event_id, {fromChart: true});
            },
            onHover: handleHover,
            onError: (error) => { state.chartError = error?.message || "图表标记暂不可用"; renderChartNote(); },
          });
        } catch (error) {
          state.chartError = error?.message || "图表资源初始化失败";
          host.innerHTML = chartFallbackHtml(model.data);
        }
      } else {
        state.chartError = "图表库未加载，已使用离线回退";
        host.innerHTML = chartFallbackHtml(model.data);
      }
      renderChartNote();
    }

    function renderChartNote() {
      const note = query(container, '[data-review-chart-note]');
      if (!note) return;
      const data = model?.data || {};
      const capability = data.capabilities || {};
      const periodText = state.period === "1D" ? "日终净值折线" : "周期内实际日终净值聚合 K 线";
      const gapText = capability.weekly_monthly_completeness === "complete" ? "冻结交易日历标记为完整" : capability.weekly_monthly_completeness ? `冻结周期完整性：${capability.weekly_monthly_completeness}` : "周期完整性以冻结交易日历为准";
      const markerGuide = "图例：蓝色上箭头=买入，红色下箭头=卖出，灰色方块=决策，橙色方块=保留现金/数据提示，圆点=混合事件；点击图标查看事件";
      const hoverEvents = Array.isArray(state.hover?.events) ? state.hover.events.map(actionLabel).filter(Boolean) : [];
      const hoverBar = state.hover?.bar;
      const hoverRow = state.hover?.row;
      const staleDays = finite(hoverRow?.max_valuation_stale_days);
      const hoverWarnings = [
        hoverBar?.warning || hoverBar?.partialLabel || "",
        hoverRow?.stale_symbols ? `估值陈旧：${hoverRow.stale_symbols}` : staleDays !== null && staleDays > 0 ? `估值陈旧 ${staleDays} 天` : "",
      ].filter(Boolean);
      const hoverDetails = [
        hoverEvents.length ? `事件：${hoverEvents.join("、")}` : "",
        ...hoverWarnings,
      ].filter(Boolean).join("；");
      const hoverText = state.hover
        ? `${state.hover.date} · ${state.hover.value == null ? "净值未知" : formatNumber(state.hover.value, 4)}${state.hover.eventCount ? ` · ${state.hover.eventCount} 个事件` : ""}${hoverDetails ? ` · ${hoverDetails}` : ""}`
        : "悬停图表查看冻结日终值";
      note.textContent = `${periodText}；${gapText}。${markerGuide}。${state.chartError ? ` ${state.chartError}。` : ""} ${hoverText}。图表库：TradingView Lightweight Charts 4.2.3`;
    }

    function renderLoading() {
      if (!model) {
        if (container) container.innerHTML = state.status === "error"
          ? `<div class="portfolio-review-state portfolio-review-error-state" role="alert"><h2>结果读取失败</h2><p>${escapeHtml(state.errorMessage || "冻结结果暂不可用")}</p><button type="button" data-review-action="retry-results">重试读取结果</button></div>`
          : `<div class="portfolio-review-state portfolio-review-loading" role="status" aria-live="polite"><span class="portfolio-review-spinner" aria-hidden="true"></span><h2>正在读取冻结组合结果</h2><p>只读取本次运行保存的净值与执行证据，不会重新运行策略。</p></div>`;
        shellReady = false;
        return;
      }
      if (!shellReady) renderShell();
      const region = query(container, '[data-review-region="state"]');
      if (region) region.innerHTML = `<div class="portfolio-review-loading-inline" role="status"><span class="portfolio-review-spinner" aria-hidden="true"></span>正在重新读取冻结结果；现有摘要保留。</div>`;
    }

    function renderShell() {
      if (!container || !model) { renderLoading(); return; }
      container.innerHTML = `<div class="portfolio-review-shell" data-review-root="true"><div data-review-region="state"></div><div data-review-region="header"></div><div data-review-region="metrics"></div><div data-review-region="facts"></div><div data-review-region="toolbar"></div><div class="portfolio-review-chart-wrap"><div data-review-region="chart"></div><p class="portfolio-review-chart-note" data-review-chart-note></p></div><div data-review-region="tabs"></div><div data-review-region="details"></div></div>`;
      shellReady = true;
      bindListeners();
      renderAllRegions();
      renderChart();
    }

    function renderAllRegions() {
      if (!model) { renderLoading(); return; }
      state.renderCount += 1;
      setRegion("header", resultHeaderHtml(model, state));
      setRegion("metrics", `<section class="portfolio-review-metrics"><div class="portfolio-review-primary-grid">${compactMetricGrid(model.data)}</div><div class="portfolio-review-secondary-grid">${secondaryMetricGrid(model.data)}</div></section>`);
      setRegion("facts", summaryFactsHtml(model.data));
      setRegion("toolbar", toolbarHtml(model, state));
      setRegion("tabs", tabsHtml(state));
      setRegion("details", state.tab === "events" ? eventTabHtml(model, state) : state.tab === "holdings" ? holdingsTabHtml(model) : evidenceText(model, state));
      renderChartNote();
      const stateRegion = query(container, '[data-review-region="state"]');
      if (stateRegion) stateRegion.innerHTML = state.status === "fallback" ? `<div class="portfolio-review-fallback-inline" role="status">详细投影暂不可用，当前显示已保存运行摘要；重新读取不会重跑研究。</div>` : state.status === "error" ? `<div class="portfolio-review-error-inline" role="alert">${escapeHtml(state.errorMessage || "结果读取失败")}</div>` : state.status === "loading" ? `<div class="portfolio-review-loading-inline" role="status"><span class="portfolio-review-spinner" aria-hidden="true"></span>正在读取冻结结果；已保存摘要仍可查看。</div>` : "";
    }

    function renderEventsRegion() {
      if (!model || state.tab !== "events") return;
      if (state.selectedEventId && !filterEvents(model.data.events, state.filters).some((event) => String(event.id) === String(state.selectedEventId))) {
        state.selectedEventId = null;
        chartController?.update?.({selectedEventId: null});
      }
      setRegion("details", eventTabHtml(model, state));
    }

    function handleHover(snapshot) {
      if (destroyed) return;
      state.hover = snapshot && typeof snapshot === "object" ? {...snapshot} : null;
      renderChartNote();
      const hover = query(container, "[data-review-hover]");
      if (hover) hover.textContent = state.hover ? `${state.hover.date} · 单位净值 ${state.hover.unit_nav == null ? "未知" : formatNumber(state.hover.unit_nav, 4)} · 权益 ${state.hover.equity == null ? "未知" : formatMoney(state.hover.equity)} · 回撤 ${state.hover.drawdown == null ? "未知" : formatPercent(state.hover.drawdown)}` : "悬停图表查看冻结日终值";
    }

    function findEvent(id) {
      return model?.data?.events?.find((event) => String(event.id) === String(id) || String(event.event_id) === String(id)) || null;
    }

    function selectEvent(id, options = {}) {
      if (destroyed || !id || !findEvent(id)) return null;
      const event = findEvent(id);
      const view = filteredEventsView(model.data, state.filters, state.eventLimit);
      if (options.fromChart && !view.filtered.some((item) => String(item.id) === String(event.id))) {
        state.filters = {symbol: "", action: "", reason: "", dateStart: "", dateEnd: ""};
        state.filterNotice = "已清除事件筛选，显示图表所选冻结事件。";
      }
      state.selectedEventId = String(event.id);
      state.eventGroup = null;
      if (chartController && !options.fromChart && typeof chartController.locateEvent === "function") {
        try { chartController.locateEvent(event.id); } catch (_) { /* fallback list remains usable */ }
      }
      if (chartController && typeof chartController.getState === "function") {
        try {
          const chartState = chartController.getState();
          if (chartState?.range !== undefined && JSON.stringify(chartState.range) !== JSON.stringify(state.range)) {
            state.range = chartState.range;
            setRegion("toolbar", toolbarHtml(model, state));
          }
        } catch (_) { /* fallback list remains usable */ }
      }
      renderEventsRegion();
      const selected = query(container, `[data-event-row="${String(event.id).replace(/"/g, "\\\"")}]`);
      selected?.scrollIntoView?.({block: "nearest"});
      return event;
    }

    function clearSelection() {
      state.selectedEventId = null;
      state.eventGroup = null;
      chartController?.update?.({selectedEventId: null});
      renderEventsRegion();
      return controller;
    }

    function openEventGroup(group) {
      if (destroyed || !group || !Array.isArray(group.events) || group.events.length < 2) return null;
      state.selectedEventId = null;
      state.eventGroup = {
        groupId: String(group.groupId || group.id || ""),
        date: dateText(group.date || group.time) || null,
        period: String(group.period || state.period || "1D"),
        events: group.events.map((event) => ({...event})),
      };
      renderEventsRegion();
      query(container, "[data-review-group-event]")?.focus?.();
      return state.eventGroup;
    }

    function setData(payload, options = {}) {
      if (destroyed) return controller;
      const next = normalizeReviewPayload(payload, {portfolioId: options.portfolioId ?? state.portfolioId, horizon: options.horizon ?? state.horizon, fallback: options.fallback});
      const nextIdentity = {runId: next.runId, portfolioId: next.portfolioId, horizon: next.horizon};
      const oldIdentity = identity();
      const changedIdentity = JSON.stringify(nextIdentity) !== JSON.stringify(oldIdentity);
      const dataExpanded = Boolean(model && !model.data.nav.length && next.data.nav.length);
      if (changedIdentity || dataExpanded) { destroyChart(); shellReady = false; state.selectedEventId = null; state.eventGroup = null; state.filters = {symbol: "", action: "", reason: "", dateStart: "", dateEnd: ""}; }
      model = next;
      state.portfolioId = next.portfolioId; state.horizon = next.horizon;
      state.status = options.fallback ? "fallback" : "ready";
      state.errorMessage = options.error ? String(options.error.message || options.error) : "";
      state.filterNotice = "";
      if (!shellReady || changedIdentity || dataExpanded) renderShell(); else { renderAllRegions(); if (chartController) chartController.update({metric: state.metric, period: state.period, range: state.range, selectedEventId: state.selectedEventId}); }
      return controller;
    }

    function setLoading(options = {}) {
      state.status = "loading"; state.errorMessage = "";
      if (options.data) model = normalizeReviewPayload(options.data, {portfolioId: options.portfolioId, horizon: options.horizon, fallback: true});
      if (!model) renderLoading(); else renderLoading();
      return controller;
    }

    function setError(error, fallbackPayload) {
      if (fallbackPayload) return setData(fallbackPayload, {fallback: true, error});
      state.status = "error"; state.errorMessage = String(error?.message || error || "结果读取失败");
      if (shellReady) renderAllRegions(); else renderLoading();
      return controller;
    }

    function setEvidence(payload) { state.evidence = payload && typeof payload === "object" ? payload : null; state.evidenceById = buildEvidenceIndex(state.evidence); state.evidenceError = ""; if (shellReady && state.tab === "evidence") setRegion("details", evidenceText(model, state)); else if (shellReady && state.tab === "events") setRegion("details", eventTabHtml(model, state)); return controller; }
    function setEvidenceError(error) { state.evidenceError = String(error?.message || error || "摘要证据读取失败"); if (shellReady && state.tab === "evidence") setRegion("details", evidenceText(model, state)); return controller; }

    function update(next = {}) {
      if (destroyed || !next || typeof next !== "object") return controller;
      const chartUpdate = {};
      let benchmarkChanged = false;
      if (next.metric !== undefined) { state.metric = String(next.metric); chartUpdate.metric = state.metric; }
      if (next.period !== undefined) { state.eventGroup = null; state.period = String(next.period); chartUpdate.period = state.period; }
      if (next.range !== undefined) { state.range = next.range; chartUpdate.range = state.range; }
      if (next.tab !== undefined && ["events", "holdings", "evidence"].includes(String(next.tab))) state.tab = String(next.tab);
      if (next.filters) { state.eventGroup = null; state.filters = {...state.filters, ...next.filters}; }
      if (next.filters && state.selectedEventId && !filterEvents(model?.data?.events, state.filters).some((event) => String(event.id) === String(state.selectedEventId))) {
        state.selectedEventId = null;
        chartUpdate.selectedEventId = null;
      }
      if (next.benchmark !== undefined) { benchmarkChanged = state.benchmark !== Boolean(next.benchmark); state.benchmark = Boolean(next.benchmark); chartUpdate.benchmark = state.benchmark; }
      if (benchmarkChanged && chartController) renderChart();
      else if (Object.keys(chartUpdate).length && chartController) chartController.update({...chartUpdate, selectedEventId: state.selectedEventId});
      if (next.metric !== undefined || next.period !== undefined || next.range !== undefined || next.benchmark !== undefined) setRegion("toolbar", toolbarHtml(model, state));
      if (next.tab !== undefined || next.period !== undefined || next.filters) { setRegion("tabs", tabsHtml(state)); setRegion("details", state.tab === "events" ? eventTabHtml(model, state) : state.tab === "holdings" ? holdingsTabHtml(model) : evidenceText(model, state)); }
      renderChartNote();
      return controller;
    }

    function bindListeners() {
      if (listenersBound || !container || typeof container.addEventListener !== "function") return;
      listenersBound = true;
      container.addEventListener("click", (event) => {
        const target = event.target;
        const groupEvent = target?.closest?.("[data-review-group-event]");
        if (groupEvent) { event.preventDefault(); state.eventGroup = null; selectEvent(groupEvent.getAttribute("data-review-group-event"), {fromChart: true}); return; }
        const eventButton = target?.closest?.("[data-review-event]");
        if (eventButton) { event.preventDefault(); selectEvent(eventButton.getAttribute("data-review-event"), {fromChart: false}); return; }
        const metric = target?.closest?.("[data-review-metric]"); if (metric) { update({metric: metric.getAttribute("data-review-metric")}); return; }
        const period = target?.closest?.("[data-review-period]"); if (period) { update({period: period.getAttribute("data-review-period")}); return; }
        const range = target?.closest?.("[data-review-range]"); if (range) { update({range: range.getAttribute("data-review-range")}); return; }
        const tab = target?.closest?.("[data-review-tab]"); if (tab) { update({tab: tab.getAttribute("data-review-tab")}); return; }
        const action = target?.closest?.("[data-review-action]")?.getAttribute("data-review-action");
        if (action === "clear-filters") { state.filters = {symbol: "", action: "", reason: "", dateStart: "", dateEnd: ""}; state.filterNotice = ""; renderEventsRegion(); return; }
        if (action === "clear-event-group") { state.eventGroup = null; renderEventsRegion(); return; }
        if (action === "more-events") { state.eventLimit += 240; renderEventsRegion(); return; }
        if (action === "reset-range") { update({range: "all"}); return; }
        if (action === "apply-range") { const start = query(container, '[data-review-range-input="start"]')?.value || ""; const end = query(container, '[data-review-range-input="end"]')?.value || ""; if (start && end) update({range: {start, end}}); return; }
        if (action === "retry-results") { state.status = "loading"; state.errorMessage = ""; renderLoading(); config.onRetry?.(identity()); return; }
        const exportType = target?.closest?.("[data-review-export]")?.getAttribute("data-review-export");
        if (exportType) {
          const events = exportType === "events-filtered" ? filteredEventsView(model.data, state.filters, state.eventLimit).filtered : model.data.events;
          const text = buildEventCsv(model.review, model.data, events, {scope: exportType === "events-filtered" ? "filtered" : "all", horizon: model.horizon});
          triggerDownload(text, `${model.runId || "portfolio"}-${model.portfolioId}-${model.horizon}-events-${exportType === "events-filtered" ? "filtered" : "all"}.csv`, documentObject, windowObject);
        }
        const navExport = target?.closest?.("[data-review-export-nav]");
        if (navExport) triggerDownload(buildNavCsv(model.review, model.data, {scope: "all", horizon: model.horizon}), `${model.runId || "portfolio"}-${model.portfolioId}-${model.horizon}-nav.csv`, documentObject, windowObject);
      });
      container.addEventListener("input", (event) => {
        const field = event.target?.getAttribute?.("data-review-filter");
        if (!field) return;
        const selectionStart = typeof event.target.selectionStart === "number" ? event.target.selectionStart : null;
        const selectionEnd = typeof event.target.selectionEnd === "number" ? event.target.selectionEnd : selectionStart;
        state.filters[field] = event.target.value;
        state.eventGroup = null;
        state.filterNotice = "";
        renderEventsRegion();
        const nextField = query(container, `[data-review-filter="${field}"]`);
        if (nextField) {
          nextField.focus?.();
          if (selectionStart !== null && typeof nextField.setSelectionRange === "function") {
            try { nextField.setSelectionRange(selectionStart, selectionEnd); } catch (_) { /* some native date controls reject selection */ }
          }
        }
      });
      container.addEventListener("change", (event) => {
        const select = event.target?.getAttribute?.("data-review-select");
        if (select) {
          const value = event.target.value;
          if (select === "portfolio") config.onSelectionChange?.({...identity(), portfolioId: value});
          if (select === "horizon") config.onSelectionChange?.({...identity(), horizon: value});
          return;
        }
        const toggle = event.target?.getAttribute?.("data-review-toggle"); if (toggle === "benchmark") update({benchmark: event.target.checked});
      });
      container.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && (state.selectedEventId || state.eventGroup)) { event.preventDefault(); clearSelection(); return; }
        const tab = event.target?.closest?.("[data-review-tab]");
        if (!tab) return;
        const tabs = queryAll(container, "[data-review-tab]"); const index = tabs.indexOf(tab); let next = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") next = tabs[(index + 1) % tabs.length];
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = tabs[(index - 1 + tabs.length) % tabs.length];
        if (event.key === "Home") next = tabs[0]; if (event.key === "End") next = tabs.at(-1);
        if (next) { event.preventDefault(); update({tab: next.getAttribute("data-review-tab")}); next.focus?.(); }
      });
    }

    function setIdentity(next = {}) {
      state.portfolioId = next.portfolioId ?? state.portfolioId; state.horizon = next.horizon ?? state.horizon;
      if (model) model = normalizeReviewPayload(model.review, {portfolioId: state.portfolioId, horizon: state.horizon});
      return controller;
    }

    function destroy() { if (destroyed) return; destroyed = true; destroyChart(); if (container) container.innerHTML = ""; }

    const controller = {
      update,
      setData,
      setLoading,
      setError,
      setEvidence,
      setEvidenceError,
      setIdentity,
      selectEvent,
      clearSelection,
      retry: () => config.onRetry?.(identity()),
      exportNav: () => buildNavCsv(model.review, model.data, {scope: "all", horizon: model.horizon}),
      exportEvents: (scope = "all") => buildEventCsv(model.review, model.data, scope === "filtered" ? filteredEventsView(model.data, state.filters, state.eventLimit).filtered : model.data.events, {scope, horizon: model.horizon}),
      resize: () => { chartController?.resize?.(); return controller; },
      destroy,
      getState: () => ({...state, filters: {...state.filters}, identity: identity(), hasChart: Boolean(chartController)}),
      getModel: () => model ? JSON.parse(JSON.stringify(model)) : null,
      getChart: () => chartController,
    };

    bindListeners();
    if (model) renderShell(); else renderLoading();
    return controller;
  }

  return {
    VERSION,
    ACTION_LABELS,
    REASON_LABELS,
    escapeHtml,
    formatNumber,
    formatPercent,
    formatMoney,
    formatPrice,
    formatMissing,
    buildEvidenceIndex,
    eventIsFill,
    actionLabel,
    reasonLabel,
    normalizeReviewPayload,
    normalize: normalizeReviewPayload,
    filterEvents,
    filter: filterEvents,
    factualSummary,
    summarizeEvents: factualSummary,
    collapseDeferred,
    csvCell,
    buildNavCsv,
    navCsv: buildNavCsv,
    buildEventCsv,
    eventCsv: buildEventCsv,
    triggerDownload,
    create,
    mount: create,
  };
}));
