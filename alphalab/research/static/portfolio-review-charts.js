/*
 * Shared portfolio review chart transforms and controller.
 *
 * The module deliberately keeps the research facts in the caller's frozen
 * review payload.  It derives display rows in a controller-local cache and
 * never writes a run or asks a market-data provider for missing values.
 */
(function attachPortfolioReviewCharts(root, factory) {
  const api = factory(root);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.PortfolioReviewCharts = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function portfolioReviewCharts(root) {
  "use strict";

  const VERSION = "portfolio-review-charts/1";
  const METRICS = new Set(["unit_nav", "equity", "cumulative_return"]);
  const PERIODS = new Set(["1D", "1W", "1M"]);
  const RANGES = new Set(["all", "1M", "3M", "6M", "1Y"]);
  const FILL_ACTIONS = new Set(["BUY", "SELL"]);
  const DAY_MS = 24 * 60 * 60 * 1000;

  function metricName(value) {
    const text = String(value || "unit_nav").trim().toLowerCase();
    if (["equity", "cash_value"].includes(text)) return "equity";
    if (["return", "cumulative_return", "cumulative-return", "cumulativereturn"].includes(text)) return "cumulative_return";
    return "unit_nav";
  }

  function asFiniteNumber(value) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
    const number = typeof value === "number" ? value : Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function asBoolean(value) {
    if (typeof value === "boolean") return value;
    if (typeof value === "number") return value !== 0;
    const text = String(value ?? "").trim().toLowerCase();
    if (["false", "0", "no", "n", "off", "null"].includes(text)) return false;
    if (["true", "1", "yes", "y", "on"].includes(text)) return true;
    return Boolean(value);
  }

  function asDate(value) {
    if (value instanceof Date && Number.isFinite(value.getTime())) return value.toISOString().slice(0, 10);
    if (value && typeof value === "object" && Number.isFinite(Number(value.year)) && Number.isFinite(Number(value.month)) && Number.isFinite(Number(value.day))) {
      const year = String(Number(value.year)).padStart(4, "0");
      const month = String(Number(value.month)).padStart(2, "0");
      const day = String(Number(value.day)).padStart(2, "0");
      const candidate = `${year}-${month}-${day}`;
      const date = new Date(`${candidate}T00:00:00Z`);
      return Number.isFinite(date.getTime()) ? candidate : null;
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      const date = new Date(value);
      return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 10) : null;
    }
    const text = String(value ?? "").trim();
    if (!text) return null;
    const match = text.match(/^(\d{4}-\d{2}-\d{2})(?:$|[T\s])/);
    if (match) {
      const date = new Date(`${match[1]}T00:00:00Z`);
      return Number.isFinite(date.getTime()) ? match[1] : null;
    }
    const date = new Date(text);
    return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 10) : null;
  }

  function dateValue(value) {
    const text = asDate(value);
    if (!text) return null;
    const valueDate = new Date(`${text}T00:00:00Z`);
    return Number.isFinite(valueDate.getTime()) ? valueDate : null;
  }

  function shiftDate(value, days) {
    const date = dateValue(value);
    if (!date || !Number.isFinite(Number(days))) return asDate(value);
    date.setUTCDate(date.getUTCDate() + Number(days));
    return date.toISOString().slice(0, 10);
  }

  function compareDates(left, right) {
    return String(left).localeCompare(String(right));
  }

  function uniqueDates(values) {
    return [...new Set((Array.isArray(values) ? values : []).map(asDate).filter(Boolean))].sort(compareDates);
  }

  function defineMeta(target, name, value) {
    Object.defineProperty(target, name, {value, enumerable: false, configurable: true});
    return target;
  }

  function dataObject(value) {
    if (Array.isArray(value)) return {nav: value};
    if (!value || typeof value !== "object") return {};
    if (value.review && typeof value.review === "object") return dataObject(value.review);
    if (value.nav || value.events || value.capabilities) return value;
    if (value.by_horizon && typeof value.by_horizon === "object") {
      const keys = Object.keys(value.by_horizon).sort((left, right) => Number(left) - Number(right));
      const selected = value.horizon !== undefined
        ? value.by_horizon[String(value.horizon)]
        : keys.length ? value.by_horizon[keys[0]] : null;
      return selected && typeof selected === "object"
        ? {
          ...selected,
          initial_cash: selected.initial_cash ?? value.initial_cash,
          scope: selected.scope || value.scope,
          capabilities: selected.capabilities || value.capabilities,
          run_id: value.run_id,
          portfolio_id: value.portfolio_id,
        }
        : {};
    }
    return value;
  }

  function initialCashFrom(value, fallback) {
    const object = dataObject(value);
    const direct = asFiniteNumber(fallback);
    if (direct !== null) return direct;
    return asFiniteNumber(object.initial_cash ?? object.initialCash);
  }

  function normalizeNavRows(input, initialCash) {
    const object = dataObject(input);
    const sourceRows = Array.isArray(input) ? input : (Array.isArray(object.nav) ? object.nav : []);
    const cash = initialCashFrom(object, initialCash);
    const rows = sourceRows.map((source, index) => {
      const original = source && typeof source === "object" ? source : {};
      const row = {...original};
      row.date = asDate(original.date ?? original.time);
      row._sourceIndex = index;
      row.equity = asFiniteNumber(original.equity);
      const sourceUnit = asFiniteNumber(original.unit_nav);
      row.unit_nav = sourceUnit !== null
        ? sourceUnit
        : row.equity !== null && cash !== null && cash !== 0
          ? row.equity / cash
          : null;
      const sourceReturn = asFiniteNumber(original.cumulative_return);
      row.cumulative_return = sourceReturn !== null
        ? sourceReturn
        : row.unit_nav === null ? null : row.unit_nav - 1;
      row.daily_return = asFiniteNumber(original.daily_return);
      row.drawdown = asFiniteNumber(original.drawdown);
      return row;
    }).filter((row) => row.date !== null).sort((left, right) => {
      const order = compareDates(left.date, right.date);
      return order || left._sourceIndex - right._sourceIndex;
    }).map((row) => {
      const result = {...row};
      delete result._sourceIndex;
      return result;
    });
    defineMeta(rows, "initialCash", cash);
    defineMeta(rows, "validDateRange", uniqueDates(object.scope?.valid_date_range || object.capabilities?.valid_date_range));
    defineMeta(rows, "sessionList", uniqueDates(object.capabilities?.session_list || object.session_list));
    defineMeta(rows, "missingSessionDates", uniqueDates(object.capabilities?.missing_session_dates || object.missing_session_dates));
    defineMeta(rows, "aggregation", object.capabilities?.aggregation || object.aggregation || {});
    defineMeta(rows, "runId", object.run_id || null);
    defineMeta(rows, "portfolioId", object.portfolio_id || null);
    return rows;
  }

  function firstValidUnitNav(rows) {
    return (Array.isArray(rows) ? rows : [])
      .map((row) => asFiniteNumber(row?.unit_nav))
      .find((value) => value !== null && value !== 0) ?? null;
  }

  function rebaseNavRows(rows, anchor) {
    const base = asFiniteNumber(anchor);
    if (base === null || base === 0) return Array.isArray(rows) ? rows : [];
    const source = Array.isArray(rows) ? rows : [];
    const rebased = source.map((row) => {
      const result = {...row};
      const unit = asFiniteNumber(row?.unit_nav);
      result.unit_nav = unit === null ? null : unit / base;
      result.cumulative_return = result.unit_nav === null ? null : result.unit_nav - 1;
      return result;
    });
    for (const name of ["initialCash", "validDateRange", "sessionList", "missingSessionDates", "aggregation", "runId", "portfolioId"]) {
      if (Object.prototype.hasOwnProperty.call(rows || [], name)) defineMeta(rebased, name, rows[name]);
    }
    return rebased;
  }

  function benchmarkConfigs(config) {
    const source = config && typeof config === "object" ? config : {};
    const plural = Array.isArray(source.benchmarks) ? source.benchmarks : [];
    if (plural.length) return plural.filter((item) => item && typeof item === "object");
    if (source.benchmark && (Array.isArray(source.benchmark) || typeof source.benchmark === "object")) {
      return [{
        id: source.benchmark?.id || "benchmark",
        name: source.benchmark?.name || "基准",
        color: source.benchmark?.color || "#2563eb",
        rows: Array.isArray(source.benchmark) ? source.benchmark : source.benchmark.rows || source.benchmark.nav || source.benchmark,
        legacy: true,
      }];
    }
    return [];
  }

  function dateRangeBounds(rows, range, options) {
    const source = Array.isArray(rows) ? rows : [];
    const option = options && typeof options === "object" ? options : {};
    let requestedStart = null;
    let requestedEnd = null;
    if (Array.isArray(range)) {
      requestedStart = asDate(range[0]);
      requestedEnd = asDate(range[1]);
    } else if (range && typeof range === "object") {
      requestedStart = asDate(range.start ?? range.from);
      requestedEnd = asDate(range.end ?? range.to);
    } else {
      const text = String(range || "all").trim();
      const upper = text.toUpperCase();
      if (upper === "ALL") {
        requestedStart = null;
      } else {
        const match = upper.match(/^(1|3|6)M$/) || upper.match(/^LAST[-_ ]?(1|3|6)[-_ ]?MONTHS?$/);
        const year = upper === "1Y" || upper === "12M" || /^LAST[-_ ]?1[-_ ]?YEAR$/.test(upper);
        const amount = match ? Number(match[1]) : year ? 12 : null;
        if (amount !== null) {
          const values = source.map((row) => asDate(row?.date)).filter(Boolean).sort(compareDates);
          requestedEnd = asDate(option.endDate) || values.at(-1) || null;
          const endValue = dateValue(requestedEnd);
          if (endValue) {
            endValue.setUTCMonth(endValue.getUTCMonth() - (year ? 12 : amount));
            requestedStart = endValue.toISOString().slice(0, 10);
          }
        }
      }
    }
    const values = source.map((row) => asDate(row?.date)).filter(Boolean).sort(compareDates);
    const dataStart = values[0] || null;
    const dataEnd = values.at(-1) || null;
    const trusted = uniqueDates(option.validDateRange || option.valid_date_range);
    const trustedStart = trusted[0] || dataStart;
    const trustedEnd = trusted.at(-1) || dataEnd;
    const end = requestedEnd || trustedEnd || dataEnd;
    const start = requestedStart || trustedStart || dataStart;
    const clippedStart = start && trustedStart && start < trustedStart ? trustedStart : start;
    const clippedEnd = end && trustedEnd && end > trustedEnd ? trustedEnd : end;
    return {
      start: clippedStart,
      end: clippedEnd,
      requestedStart,
      requestedEnd,
      clipped: clippedStart !== requestedStart || clippedEnd !== requestedEnd,
    };
  }

  function filterDateRange(input, range = "all", options = {}) {
    const rows = Array.isArray(input) ? input : (dataObject(input).nav || []);
    const bounds = dateRangeBounds(rows, range, options);
    const filtered = rows.filter((row) => {
      const date = asDate(row?.date ?? row?.time);
      return date && (!bounds.start || date >= bounds.start) && (!bounds.end || date <= bounds.end);
    });
    defineMeta(filtered, "range", bounds);
    defineMeta(filtered, "requestedRange", range);
    return filtered;
  }

  function metricValue(row, metric, initialCash) {
    const normalizedMetric = metricName(metric);
    if (!row || typeof row !== "object") return null;
    if (normalizedMetric === "unit_nav") {
      const direct = asFiniteNumber(row.unit_nav);
      if (direct !== null) return direct;
      const equity = asFiniteNumber(row.equity);
      const cash = initialCashFrom(row, initialCash);
      return equity !== null && cash !== null && cash !== 0 ? equity / cash : null;
    }
    if (normalizedMetric === "equity") return asFiniteNumber(row.equity);
    const direct = asFiniteNumber(row.cumulative_return);
    if (direct !== null) return direct;
    const unit = metricValue(row, "unit_nav", initialCash);
    return unit === null ? null : unit - 1;
  }

  function contiguousSegments(points) {
    const segments = [];
    let current = [];
    for (const point of points) {
      if (point.value === null || point.value === undefined || !Number.isFinite(point.value)) {
        if (current.length) segments.push(current);
        current = [];
      } else {
        current.push(point);
      }
    }
    if (current.length) segments.push(current);
    return segments;
  }

  function toMetricData(input, metric = "unit_nav", initialCash) {
    const object = dataObject(input);
    const rows = normalizeNavRows(input, initialCashFrom(object, initialCash));
    const normalizedMetric = metricName(metric);
    const points = rows.map((row) => ({
      time: row.date,
      date: row.date,
      value: metricValue(row, normalizedMetric, rows.initialCash),
      unit_nav: row.unit_nav,
      equity: row.equity,
      cumulative_return: row.cumulative_return,
      daily_return: row.daily_return,
      drawdown: row.drawdown,
      stale_symbols: row.stale_symbols ?? null,
      max_valuation_stale_days: asFiniteNumber(row.max_valuation_stale_days),
    }));
    defineMeta(points, "segments", contiguousSegments(points));
    defineMeta(points, "gaps", points.filter((point) => point.value === null).map((point) => point.date));
    defineMeta(points, "metric", normalizedMetric);
    defineMeta(points, "mode", "line");
    defineMeta(points, "initialCash", rows.initialCash);
    defineMeta(points, "validDateRange", rows.validDateRange);
    defineMeta(points, "sessionList", rows.sessionList);
    defineMeta(points, "missingSessionDates", rows.missingSessionDates);
    return points;
  }

  function toMetricSeries(input, metric = "unit_nav", initialCash) {
    const points = toMetricData(input, metric, initialCash);
    return {
      metric: points.metric,
      mode: "line",
      points,
      segments: points.segments,
      gaps: points.gaps,
      initialCash: points.initialCash,
    };
  }

  function periodName(period) {
    const value = String(period || "1D").trim().toUpperCase();
    if (value === "1D" || value === "D" || value === "DAILY") return "1D";
    if (value === "1W" || value === "W" || value === "WEEKLY") return "1W";
    if (value === "1M" || value === "M" || value === "MONTHLY" || value === "1MO") return "1M";
    return "1D";
  }

  function mondayOf(value) {
    const date = dateValue(value);
    if (!date) return null;
    const day = date.getUTCDay();
    const offset = day === 0 ? -6 : 1 - day;
    date.setUTCDate(date.getUTCDate() + offset);
    return date.toISOString().slice(0, 10);
  }

  function isoWeekKey(value) {
    const date = dateValue(value);
    if (!date) return null;
    const weekday = date.getUTCDay() || 7;
    date.setUTCDate(date.getUTCDate() + 4 - weekday);
    const year = date.getUTCFullYear();
    const yearStart = new Date(Date.UTC(year, 0, 1));
    const number = Math.ceil((((date - yearStart) / DAY_MS) + 1) / 7);
    return `${year}-W${String(number).padStart(2, "0")}`;
  }

  function periodKeyForDate(value, period) {
    const date = asDate(value);
    if (!date) return null;
    if (periodName(period) === "1W") return isoWeekKey(date);
    if (periodName(period) === "1M") return date.slice(0, 7);
    return date;
  }

  function groupPeriodDates(values, period) {
    const keys = new Map();
    for (const value of uniqueDates(values)) {
      const key = periodKeyForDate(value, period);
      if (!key) continue;
      if (!keys.has(key)) keys.set(key, []);
      keys.get(key).push(value);
    }
    return keys;
  }

  function aggregateObservedClose(input, period = "1W", options = {}) {
    const object = dataObject(input);
    const normalizedPeriod = periodName(period);
    const metric = metricName(options.metric);
    const initialCash = initialCashFrom(object, options.initialCash ?? options.initial_cash);
    const rows = normalizeNavRows(input, initialCash);
    const sessionList = uniqueDates(options.sessionList || options.session_list || rows.sessionList || object.capabilities?.session_list);
    const validDateRange = uniqueDates(options.validDateRange || options.valid_date_range || rows.validDateRange || object.capabilities?.valid_date_range);
    const declaredMissing = uniqueDates(options.missingSessionDates || options.missing_session_dates || rows.missingSessionDates || object.capabilities?.missing_session_dates);
    const aggregation = options.aggregation || object.capabilities?.aggregation || rows.aggregation || {};
    const aggregationKey = normalizedPeriod === "1W" ? "weekly" : normalizedPeriod === "1M" ? "monthly" : null;
    const capabilityPartial = aggregationKey && aggregation?.[aggregationKey]
      ? aggregation?.[aggregationKey]?.last_period_may_be_partial
      : null;
    const authoritativePartial = typeof capabilityPartial === "boolean"
      ? capabilityPartial
      : ["true", "1", "yes"].includes(String(capabilityPartial ?? "").trim().toLowerCase())
        ? true
        : ["false", "0", "no"].includes(String(capabilityPartial ?? "").trim().toLowerCase())
          ? false
          : null;
    const grouped = new Map();
    for (const row of rows) {
      const key = periodKeyForDate(row.date, normalizedPeriod);
      if (!key) continue;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(row);
    }
    const expectedGroups = groupPeriodDates(sessionList, normalizedPeriod);
    const actualDates = rows.map((row) => row.date);
    const expectedEnd = sessionList.at(-1) || validDateRange.at(-1) || actualDates.at(-1) || null;
    const actualEnd = actualDates.at(-1) || null;
    const sortedKeys = [...grouped.keys()].sort(compareDates);
    const bars = [];
    const warnings = [];
    for (const key of sortedKeys) {
      const groupRows = grouped.get(key) || [];
      const known = groupRows.map((row) => ({row, value: metricValue(row, metric, initialCash)})).filter((item) => item.value !== null);
      const rowDates = uniqueDates(groupRows.map((row) => row.date));
      const knownDates = uniqueDates(known.map((item) => item.row.date));
      const unknownDates = rowDates.filter((date) => !knownDates.includes(date));
      const expectedDates = expectedGroups.get(key) || [];
      const missingDates = uniqueDates(expectedDates.concat(
        declaredMissing.filter((date) => periodKeyForDate(date, normalizedPeriod) === key),
      )).filter((date) => !rowDates.includes(date));
      if (!known.length) {
        warnings.push({periodKey: key, dates: rowDates, missingDates, unknownDates, reason: "该周期没有可用日终净值"});
        continue;
      }
      const values = known.map((item) => item.value);
      const firstRow = groupRows[0];
      const lastKnown = known.at(-1).row;
      const isLast = key === sortedKeys.at(-1);
      const derivedPartial = Boolean(expectedEnd && actualEnd && actualEnd < expectedEnd);
      const isPartial = Boolean(isLast && (authoritativePartial !== null ? authoritativePartial : derivedPartial));
      let completeness = "unknown";
      if (sessionList.length) completeness = missingDates.length || unknownDates.length ? "incomplete" : "complete";
      let warning = null;
      if (missingDates.length) warning = "可信交易日历与日终净值存在缺口";
      else if (unknownDates.length) warning = "部分交易日终净值未知";
      else if (!sessionList.length) warning = "冻结元数据未提供可信交易日历，完整性未知";
      if (isPartial) warning = warning ? `${warning}；末段未完整` : "末段未完整";
      const bar = {
        time: lastKnown.date,
        date: lastKnown.date,
        period: normalizedPeriod,
        periodKey: key,
        periodStart: firstRow.date,
        periodEnd: groupRows.at(-1).date,
        open: values[0],
        high: Math.max(...values),
        low: Math.min(...values),
        close: values.at(-1),
        observedDates: knownDates,
        missingDates,
        unknownDates,
        completeness,
        isPartial,
        warning,
      };
      if (isPartial) bar.partialLabel = "末段未完整";
      bars.push(bar);
      if (warning) warnings.push({periodKey: key, dates: rowDates, missingDates, unknownDates, reason: warning});
    }
    for (const [key, expectedDates] of expectedGroups) {
      if (grouped.has(key)) continue;
      const missingDates = expectedDates.filter((date) => !actualDates.includes(date));
      warnings.push({periodKey: key, dates: [], missingDates, unknownDates: [], reason: "该周期没有可用日终净值"});
    }
    defineMeta(bars, "bars", bars);
    defineMeta(bars, "period", normalizedPeriod);
    defineMeta(bars, "metric", metric);
    defineMeta(bars, "source", "daily_nav_observed_closes");
    defineMeta(bars, "warnings", warnings);
    defineMeta(bars, "completeness", sessionList.length ? (warnings.length ? "incomplete" : "complete") : "unknown");
    defineMeta(bars, "missingDates", uniqueDates(warnings.flatMap((warning) => warning.missingDates || [])));
    defineMeta(bars, "lastPeriodMayBePartial", Boolean(bars.at(-1)?.isPartial));
    defineMeta(bars, "sessionList", sessionList);
    defineMeta(bars, "validDateRange", validDateRange);
    return bars;
  }

  function hashText(value) {
    let hash = 2166136261;
    const text = String(value);
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
  }

  function eventId(event, index, runId) {
    const direct = event?.id ?? event?.event_id;
    if (direct !== null && direct !== undefined && String(direct).trim()) return String(direct);
    const identity = JSON.stringify({
      runId: runId || null,
      index,
      date: asDate(event?.date ?? event?.trade_date),
      action: event?.action || null,
      symbol: event?.symbol || null,
      reason: event?.reason_code || event?.reason || null,
    });
    return `event-${hashText(identity)}`;
  }

  function eventIsFill(event) {
    if (event?.filled !== undefined && event?.filled !== null) return asBoolean(event.filled) && FILL_ACTIONS.has(String(event.action || "").toUpperCase());
    return FILL_ACTIONS.has(String(event?.action || "").toUpperCase());
  }

  function normalizeEvent(event, index, runId) {
    const source = event && typeof event === "object" ? event : {};
    const action = String(source.action || "").trim().toUpperCase();
    const result = {...source};
    result.id = eventId(source, index, runId);
    result.date = asDate(source.date ?? source.trade_date);
    result.action = action;
    result.filled = eventIsFill({...source, action});
    result.symbol = source.symbol === null || source.symbol === undefined ? null : String(source.symbol);
    result.event_index = index;
    return result;
  }

  function groupEvents(input, period = "1D", options = {}) {
    const object = dataObject(input);
    const source = Array.isArray(input) ? input : (Array.isArray(object.events) ? object.events : []);
    const normalizedPeriod = periodName(period);
    const groups = new Map();
    source.map((event, index) => normalizeEvent(event, index, options.runId || object.run_id)).filter((event) => event.date).forEach((event) => {
      const periodKey = periodKeyForDate(event.date, normalizedPeriod);
      const key = normalizedPeriod === "1D" ? event.date : periodKey;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(event);
    });
    const result = [...groups.keys()].sort(compareDates).map((key) => {
      const events = groups.get(key).slice().sort((left, right) => compareDates(left.date, right.date) || left.event_index - right.event_index);
      const eventDates = uniqueDates(events.map((event) => event.date));
      const eventIds = events.map((event) => event.id);
      const fills = events.filter(eventIsFill).length;
      const decisions = events.length - fills;
      return {
        id: `event-group-${hashText(`${normalizedPeriod}:${key}:${eventIds.join(",")}`)}`,
        key,
        period: normalizedPeriod,
        periodKey: key,
        date: eventDates[0],
        time: eventDates[0],
        eventDates,
        eventIds,
        events,
        count: events.length,
        fills,
        decisions,
        hasFill: fills > 0,
        hasDecision: decisions > 0,
      };
    });
    defineMeta(result, "period", normalizedPeriod);
    defineMeta(result, "fillCount", result.reduce((sum, group) => sum + group.fills, 0));
    defineMeta(result, "eventCount", result.reduce((sum, group) => sum + group.count, 0));
    return result;
  }

  function markerColor(event) {
    if (eventIsFill(event)) return String(event.action).toUpperCase() === "BUY" ? "#2563eb" : "#b42318";
    const action = String(event?.action || "").toUpperCase();
    if (action === "CASH" || action === "INITIAL_NOT_FILLED") return "#b45309";
    return "#6b7280";
  }

  function eventDisplayLabel(event) {
    const label = event?.action_label ?? event?.action_text;
    return String(label || event?.action || "事件");
  }

  function eventMarkerStyle(group) {
    const events = Array.isArray(group?.events) ? group.events : [];
    const fillActions = [...new Set(events.filter(eventIsFill).map((event) => String(event.action || "").toUpperCase()))];
    if (fillActions.length === 1 && fillActions[0] === "BUY") return {position: "belowBar", shape: "arrowUp"};
    if (fillActions.length === 1 && fillActions[0] === "SELL") return {position: "aboveBar", shape: "arrowDown"};
    if (fillActions.length) return {position: "aboveBar", shape: "circle"};
    return {position: "aboveBar", shape: "square"};
  }

  function markerEventsForGroup(group, showDecisions) {
    const events = Array.isArray(group?.events) ? group.events : [];
    const buys = events.filter((event) => eventIsFill(event) && String(event.action || "").toUpperCase() === "BUY");
    const sells = events.filter((event) => eventIsFill(event) && String(event.action || "").toUpperCase() === "SELL");
    const decisions = events.filter((event) => !eventIsFill(event));
    const result = [];
    if (buys.length) result.push({kind: "BUY", events: buys});
    if (sells.length) result.push({kind: "SELL", events: sells});
    if (showDecisions && decisions.length) result.push({kind: "DECISION", events: decisions});
    return result;
  }

  function markerId(group, kind, subsets) {
    if (subsets.length === 1 && subsets[0].kind === kind && subsets[0].events.length === group.events.length) return group.id;
    return `${group.id}:${String(kind).toLowerCase()}`;
  }

  function markersForEvents(groups, availableDates, options = {}) {
    const dates = availableDates ? new Set(uniqueDates(availableDates)) : null;
    const showDecisions = options.showDecisions === undefined ? true : asBoolean(options.showDecisions);
    const result = [];
    for (const group of (Array.isArray(groups) ? groups : [])) {
      const subsets = markerEventsForGroup(group, showDecisions);
      for (const subset of subsets) {
        const subsetEvents = subset.events;
        const first = subsetEvents[0] || {};
        const sourceDates = uniqueDates(subsetEvents.map((event) => event.date));
        const style = subset.kind === "BUY"
          ? {position: "belowBar", shape: "arrowUp"}
          : subset.kind === "SELL"
            ? {position: "aboveBar", shape: "arrowDown"}
            : {position: "aboveBar", shape: "square"};
        const count = subsetEvents.length;
        const eventIds = subsetEvents.map((event) => event.id);
        const isCashDecision = subset.kind === "DECISION" && subsetEvents.some((event) => ["CASH", "INITIAL_NOT_FILLED"].includes(String(event.action || "").toUpperCase()));
        const markerDate = sourceDates[0] || group.date || group.time;
        result.push({
          time: markerDate,
          sourceDate: markerDate,
          sourceDates,
          periodDate: group.date,
          id: markerId(group, subset.kind, subsets),
          groupId: group.id,
          eventId: count === 1 ? eventIds[0] : null,
          eventIds,
          position: style.position,
          shape: style.shape,
          color: subset.kind === "BUY" ? "#2563eb" : subset.kind === "SELL" ? "#b42318" : isCashDecision ? "#b45309" : "#6b7280",
          markerType: subset.kind,
          decision: subset.kind === "DECISION",
          title: subset.kind === "BUY"
            ? `实际买入 ${count} 笔`
            : subset.kind === "SELL"
              ? `实际卖出 ${count} 笔`
              : count > 1 ? `${count} 个决策` : eventDisplayLabel(first),
          text: subset.kind === "BUY" ? `买▲${count}` : subset.kind === "SELL" ? `卖▼${count}` : "",
          exactDateAvailable: !dates || dates.has(markerDate),
        });
      }
    }
    return sortMarkers(result);
  }

  function sortMarkers(markers) {
    return (Array.isArray(markers) ? markers : []).filter((marker) => asDate(marker?.time)).slice().sort((left, right) => (
      compareDates(asDate(left.time), asDate(right.time))
      || compareDates(left.id || "", right.id || "")
    ));
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[character]));
  }

  function formatNumber(value, digits = 4) {
    const number = asFiniteNumber(value);
    return number === null ? "未知" : number.toLocaleString("zh-CN", {minimumFractionDigits: digits, maximumFractionDigits: digits});
  }

  function axisPriceFormat(metric, drawdown = false) {
    const normalizedMetric = metricName(metric);
    if (drawdown || normalizedMetric === "cumulative_return") {
      return {
        type: "custom",
        formatter: (value) => {
          const number = asFiniteNumber(value);
          return number === null ? "未知" : `${(number * 100).toFixed(2)}%`;
        },
      };
    }
    if (normalizedMetric === "unit_nav") return {type: "price", precision: 4, minMove: 0.0001};
    return {type: "price", precision: 2, minMove: 0.01};
  }

  function seriesOptions(color, area, metric, drawdown = false) {
    const priceFormat = axisPriceFormat(metric, drawdown);
    return area ? {
      lineColor: color,
      topColor: `${color}33`,
      bottomColor: `${color}03`,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      priceFormat,
    } : {
      color,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      priceFormat,
    };
  }

  function chartWidth(container) {
    return Number(container?.clientWidth || container?.offsetWidth || 0);
  }

  function createElement(document, tag, className) {
    if (!document || typeof document.createElement !== "function") return null;
    const element = document.createElement(tag);
    if (className) {
      element.className = className;
      if (element.setAttribute) element.setAttribute("class", className);
    }
    return element;
  }

  function barHasUsableOHLC(bar) {
    return Boolean(
      bar
      && !(bar.missingDates?.length)
      && !(bar.unknownDates?.length)
      && [bar.open, bar.high, bar.low, bar.close].every((value) => asFiniteNumber(value) !== null),
    );
  }

  function fallbackSvg(points, bars, metric, drawdown) {
    const width = 760;
    const height = drawdown ? 120 : 220;
    const padding = {left: 42, right: 12, top: 12, bottom: 26};
    const candleBars = (Array.isArray(bars) ? bars : []).filter(barHasUsableOHLC);
    const values = candleBars.length
      ? candleBars.flatMap((bar) => [bar.low, bar.high]).filter((value) => asFiniteNumber(value) !== null)
      : points.flatMap((point) => [point.value]).filter((value) => asFiniteNumber(value) !== null);
    let min = values.length ? Math.min(...values) : 0;
    let max = values.length ? Math.max(...values) : 1;
    if (drawdown) min = Math.min(min, 0);
    if (max === min) max = min + 1;
    const source = bars?.length ? bars.map((bar) => ({time: bar.time, value: barHasUsableOHLC(bar) ? bar.close : null})) : points;
    const x = (index) => padding.left + (width - padding.left - padding.right) * (source.length <= 1 ? 0 : index / (source.length - 1));
    const y = (value) => padding.top + (max - value) / (max - min) * (height - padding.top - padding.bottom);
    const paths = [];
    if (bars?.length) {
      bars.forEach((bar, index) => {
        if (!barHasUsableOHLC(bar)) return;
        const middle = x(index);
        const open = y(bar.open); const close = y(bar.close); const high = y(bar.high); const low = y(bar.low);
        paths.push(`<line x1="${middle}" y1="${high}" x2="${middle}" y2="${low}" stroke="#2563eb"/><rect x="${middle - 4}" y="${Math.min(open, close)}" width="8" height="${Math.max(1, Math.abs(close - open))}" fill="${close >= open ? "#15803d" : "#b42318"}"/>`);
      });
    } else {
      const sourceIndexes = new Map(source.map((point, index) => [point, index]));
      for (const segment of contiguousSegments(source)) {
        if (!segment.length) continue;
        paths.push(`<path d="${segment.map((point, index) => `${index ? "L" : "M"}${x(sourceIndexes.get(point)).toFixed(2)},${y(point.value).toFixed(2)}`).join(" ")}" fill="none" stroke="${drawdown ? "#b45309" : "#0f766e"}" stroke-width="2"/>`);
      }
    }
    const labels = source.length ? `<text x="${padding.left}" y="${height - 7}" fill="#657174" font-size="10">${escapeHtml(source[0].time)}</text><text x="${width - padding.right}" y="${height - 7}" fill="#657174" font-size="10" text-anchor="end">${escapeHtml(source.at(-1).time)}</text>` : "";
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(drawdown ? "组合回撤图" : "组合净值图")}"><line x1="${padding.left}" y1="${y(0)}" x2="${width - padding.right}" y2="${y(0)}" stroke="#d9e1e3" stroke-dasharray="3 3"/>${paths.join("")}${labels}</svg>`;
  }

  function fallbackTable(points, metric) {
    if (!points.length) return `<p class="portfolio-review-empty">暂无可用日终净值；冻结结果仍可查看事件与状态。</p>`;
    const maxRows = 240;
    const visible = points.length <= maxRows ? points : [...points.slice(0, Math.floor(maxRows / 2)), ...points.slice(-Math.ceil(maxRows / 2))];
    const omitted = points.length - visible.length;
    const rows = visible.map((point) => {
      const value = point.value === null ? "未知（净值不可用）" : formatNumber(point.value, metric === "cumulative_return" ? 4 : 4);
      const stale = point.stale_symbols ? ` · 估值陈旧 ${escapeHtml(point.stale_symbols)}` : "";
      return `<tr><th scope="row">${escapeHtml(point.date)}</th><td>${escapeHtml(value)}${stale}</td></tr>`;
    }).join("");
    const note = omitted ? `<p>中间 ${omitted} 个点仍保留在图表数据中，表格仅展示首尾观测。</p>` : "";
    return `<details class="portfolio-review-data-table"><summary>可访问数据表</summary>${note}<table><thead><tr><th scope="col">日期</th><th scope="col">${escapeHtml(metric)}</th></tr></thead><tbody>${rows}</tbody></table></details>`;
  }

  function fallbackEvents(groups) {
    if (!groups.length) return "";
    const maxGroups = 240;
    const visible = groups.length <= maxGroups ? groups : [...groups.slice(0, Math.floor(maxGroups / 2)), ...groups.slice(-Math.ceil(maxGroups / 2))];
    const omitted = groups.length - visible.length;
    const rows = visible.map((group) => {
      const choices = group.events.map((event) => `<li><button type="button" data-portfolio-review-event="${escapeHtml(event.id)}">${escapeHtml(event.date)} · ${escapeHtml(eventDisplayLabel(event))} · ${escapeHtml(event.symbol || "")}</button></li>`).join("");
      return `<li><strong>${escapeHtml(group.date)}</strong> · ${group.count} 个事件<ol>${choices}</ol></li>`;
    }).join("");
    const note = omitted ? `<p>中间 ${omitted} 个事件组保留在控制器中，回退列表展示首尾。</p>` : "";
    return `<details class="portfolio-review-event-list"><summary>交易与事件</summary>${note}<ol>${rows}</ol></details>`;
  }

  function fallbackBarWarnings(bars) {
    const warnings = (Array.isArray(bars) ? bars : []).filter((bar) => bar?.warning || bar?.isPartial).map((bar) => {
      const label = bar.partialLabel || bar.warning || "周期完整性未知";
      return `<li><strong>${escapeHtml(bar.periodKey || bar.date)}</strong> · ${escapeHtml(label)}</li>`;
    }).join("");
    return warnings ? `<details class="portfolio-review-period-warnings" open><summary>周期完整性与缺口</summary><ul>${warnings}</ul></details>` : "";
  }

  function renderFallback(element, points, bars, metric, period, drawdown, groups, reason) {
    if (!element) return;
    if (drawdown) {
      element.innerHTML = `<div class="portfolio-review-fallback" data-fallback="drawdown"><p>离线回退 · 回撤保留冻结缺口</p>${fallbackSvg(points, [], metric, true)}${fallbackTable(points, "drawdown")}</div>`;
      return;
    }
    const note = reason || (period === "1D" ? "日终观测值；未知区间不会连线。" : "K 线由周期内实际日终净值的首/末/最高/最低观测值聚合，非盘中 OHLC。");
    element.innerHTML = `<div class="portfolio-review-fallback" data-fallback="main"><p>${escapeHtml(note)}</p>${fallbackBarWarnings(bars)}${fallbackSvg(points, bars, metric, false)}${fallbackTable(points, metric)}${fallbackEvents(groups)}</div>`;
    if (typeof element.querySelectorAll === "function") {
      element.querySelectorAll("[data-portfolio-review-event]").forEach((button) => {
        if (typeof button.addEventListener === "function") button.addEventListener("click", () => {
          if (typeof element._portfolioReviewSelectEvent === "function") element._portfolioReviewSelectEvent(button.getAttribute("data-portfolio-review-event"));
        });
      });
    }
  }

  function safeCallback(callback, value) {
    if (typeof callback !== "function") return;
    try { callback(value); } catch (_) { /* consumer callback errors do not break chart cleanup */ }
  }

  function resolveLibrary(options) {
    if (Object.prototype.hasOwnProperty.call(options, "library")) return options.library;
    return root?.LightweightCharts || root?.window?.LightweightCharts || null;
  }

  function chartTimeScale(chart) {
    try { return chart && typeof chart.timeScale === "function" ? chart.timeScale() : null; } catch (_) { return null; }
  }

  function create(container, options = {}) {
    const config = options && typeof options === "object" ? options : {};
    const object = dataObject(config.data || {});
    const initialCash = initialCashFrom(object, config.initialCash ?? config.initial_cash);
    const sourceRows = normalizeNavRows(object, initialCash);
    const configuredBenchmarkItems = benchmarkConfigs(config).map((item, index) => ({
      id: String(item.id || `benchmark-${index + 1}`),
      name: String(item.name || item.id || `基准 ${index + 1}`),
      color: String(item.color || ["#2563eb", "#7c3aed", "#d97706", "#059669"][index % 4]),
      rows: normalizeNavRows(item.rows ?? item.nav ?? item, initialCash),
      legacy: Boolean(item.legacy),
    }));
    const pluralBenchmarkMode = Array.isArray(config.benchmarks) && config.benchmarks.length > 0;
    const comparisonAnchor = pluralBenchmarkMode ? firstValidUnitNav(sourceRows) : null;
    const comparisonRows = comparisonAnchor === null ? sourceRows : rebaseNavRows(sourceRows, comparisonAnchor);
    const rows = sourceRows;
    const events = (Array.isArray(object.events) ? object.events : []).map((event, index) => normalizeEvent(event, index, object.run_id));
    const eventsByDate = new Map();
    for (const event of events) {
      if (!event.date) continue;
      if (!eventsByDate.has(event.date)) eventsByDate.set(event.date, []);
      eventsByDate.get(event.date).push(event);
    }
    const document = container?.ownerDocument || root?.document || null;
    const mainContainer = createElement(document, "div", "portfolio-review-chart-main");
    const drawdownContainer = createElement(document, "div", "portfolio-review-chart-drawdown");
    if (mainContainer) mainContainer.setAttribute?.("data-portfolio-review-chart", "main");
    if (drawdownContainer) drawdownContainer.setAttribute?.("data-portfolio-review-chart", "drawdown");
    if (container && typeof container.appendChild === "function") {
      if (mainContainer) container.appendChild(mainContainer);
      if (drawdownContainer) container.appendChild(drawdownContainer);
    }
    const initialRange = typeof config.range === "string" && RANGES.has(config.range.toUpperCase())
      ? (config.range.toLowerCase() === "all" ? "all" : config.range.toUpperCase())
      : config.range === undefined ? "all" : config.range;
    const state = {
      metric: metricName(config.metric),
      period: periodName(config.period),
      range: initialRange,
      selectedEventId: null,
      showDecisions: asBoolean(config.showDecisions),
    };
    const charts = {main: null, drawdown: null};
    const timeGrids = {main: null, drawdown: null};
    let timelineDates = [];
    const lineSeries = {main: [], drawdown: [], benchmark: []};
    const eventGroupsCache = new Map();
    const eventGroupIndexCache = new Map();
    const metricPointsCache = new Map();
    const metricRowIndexCache = new Map();
    const periodBarsCache = new Map();
    const benchmarkDataCache = new Map();
    const library = resolveLibrary(config);
    let destroyed = false;
    let syncing = false;
    let syncingCrosshair = false;
    let syncReady = false;
    let syncUsesTimeRange = false;
    let observer = null;
    let ownerWindow = root?.window || root;
    let mainRangeHandler = null;
    let drawdownRangeHandler = null;
    let crosshairHandler = null;
    let drawdownCrosshairHandler = null;
    let clickHandler = null;
    let renderCount = 0;
    let periodAggregationCount = 0;
    let usingFallback = true;
    let fallbackReason = null;
    let lastMarkerError = null;
    let markerLookup = new Map();

    function canonicalRangeKey(range) {
      const bounds = dateRangeBounds(rows, range, {
        endDate: rows.at(-1)?.date,
        validDateRange: rows.validDateRange,
      });
      return `${bounds.start || ""}:${bounds.end || ""}`;
    }

    function currentGroups() {
      const key = `${state.period}:${canonicalRangeKey(state.range)}`;
      if (!eventGroupsCache.has(key)) {
        const selected = filterDateRange(events, state.range, {
          endDate: rows.at(-1)?.date,
          validDateRange: rows.validDateRange,
        });
        const groups = groupEvents(selected, state.period, {runId: object.run_id});
        eventGroupsCache.set(key, groups);
        eventGroupIndexCache.set(key, new Map(groups.map((group) => [state.period === "1D" ? group.date : group.periodKey, group])));
      }
      return eventGroupsCache.get(key);
    }

    function currentGroupForDate(date) {
      const normalizedDate = asDate(date);
      if (!normalizedDate) return null;
      const key = `${state.period}:${canonicalRangeKey(state.range)}`;
      currentGroups();
      return eventGroupIndexCache.get(key)?.get(state.period === "1D" ? normalizedDate : periodKeyForDate(normalizedDate, state.period)) || null;
    }

    function rowsForMetric(metric = state.metric) {
      return metricName(metric) === "equity" ? rows : comparisonAnchor === null ? rows : comparisonRows;
    }

    function rowForMetricDate(metric, date) {
      const normalizedMetric = metricName(metric);
      if (!metricRowIndexCache.has(normalizedMetric)) {
        metricRowIndexCache.set(normalizedMetric, new Map(rowsForMetric(normalizedMetric).map((row) => [row.date, row])));
      }
      return metricRowIndexCache.get(normalizedMetric).get(asDate(date)) || null;
    }

    function currentPoints() {
      if (!metricPointsCache.has(state.metric)) metricPointsCache.set(state.metric, toMetricData(rowsForMetric(state.metric), state.metric, initialCash));
      const dailyPoints = metricPointsCache.get(state.metric);
      const periodDates = state.period !== "1D" && state.metric === "cumulative_return" ? new Set(periodTimelineBars().map(bar => bar.time)) : null;
      const allPoints = periodDates ? dailyPoints.filter(point => periodDates.has(point.time)) : dailyPoints;
      return filterDateRange(allPoints, state.range, {endDate: rows.at(-1)?.date, validDateRange: rows.validDateRange});
    }

    function periodBarsEntry(metricOverride = state.metric) {
      const metric = metricName(metricOverride);
      const key = `${metric}:${state.period}`;
      let entry = periodBarsCache.get(key);
      if (entry) return entry;
      periodAggregationCount += 1;
      const allBars = aggregateObservedClose(rowsForMetric(metric), state.period, {
        metric,
        initialCash,
        sessionList: rows.sessionList,
        validDateRange: rows.validDateRange,
        missingSessionDates: rows.missingSessionDates,
        aggregation: rows.aggregation,
      });
      entry = {
        allBars,
        byTime: new Map(allBars.map((bar) => [bar.time, bar])),
        byPeriodKey: new Map(allBars.map((bar) => [bar.periodKey, bar])),
      };
      periodBarsCache.set(key, entry);
      return entry;
    }

    function currentBars() {
      if (state.period === "1D" || state.metric === "cumulative_return") return [];
      const allBars = periodBarsEntry(state.metric).allBars;
      return filterDateRange(allBars, state.range, {endDate: rows.at(-1)?.date, validDateRange: rows.validDateRange});
    }

    function periodBarForDate(date) {
      if (state.period === "1D") return null;
      const entry = periodBarsEntry(state.metric === "cumulative_return" ? "unit_nav" : state.metric);
      return entry.byTime.get(date) || entry.byPeriodKey.get(periodKeyForDate(date, state.period)) || null;
    }

    function periodTimelineBars() {
      if (state.period === "1D") return [];
      return periodBarsEntry(state.metric === "cumulative_return" ? "unit_nav" : state.metric).allBars;
    }

    function selectedEventDate() {
      if (!state.selectedEventId) return null;
      return events.find((event) => event.id === state.selectedEventId)?.date || null;
    }

    function mainData() {
      const points = currentPoints();
      const bars = currentBars();
      return {points, bars, groups: currentGroups()};
    }

    function chartOptions(height) {
      return {
        autoSize: false,
        width: Math.max(1, chartWidth(container) || 640),
        height,
        layout: {background: {color: "#ffffff"}, textColor: "#182022"},
        grid: {vertLines: {color: "#eef3f4"}, horzLines: {color: "#eef3f4"}},
        rightPriceScale: {borderColor: "#d9e1e3"},
        timeScale: {
          borderColor: "#d9e1e3",
          rightOffset: 4,
          barSpacing: 8,
          minBarSpacing: 0.1,
          fixLeftEdge: false,
          fixRightEdge: false,
          shiftVisibleRangeOnNewBar: false,
        },
        crosshair: {mode: 1},
      };
    }

    function initCharts() {
      if (!mainContainer || !drawdownContainer || !library || typeof library.createChart !== "function" || !rows.length) {
        fallbackReason = !rows.length ? "暂无冻结日终净值" : "图表库不可用，已启用离线回退";
        return false;
      }
      try {
        charts.main = library.createChart(mainContainer, chartOptions(360));
        charts.drawdown = library.createChart(drawdownContainer, chartOptions(120));
        const mainScale = chartTimeScale(charts.main);
        const drawdownScale = chartTimeScale(charts.drawdown);
        syncUsesTimeRange = Boolean(
          !(typeof mainScale?.subscribeVisibleLogicalRangeChange === "function" && typeof drawdownScale?.subscribeVisibleLogicalRangeChange === "function")
          &&           typeof mainScale?.subscribeVisibleTimeRangeChange === "function"
          && typeof drawdownScale?.subscribeVisibleTimeRangeChange === "function"
          && typeof mainScale?.setVisibleRange === "function"
          && typeof drawdownScale?.setVisibleRange === "function",
        );
        mainRangeHandler = (range) => {
          if (syncing || !syncReady || !range || !drawdownScale || !lineSeries.drawdown.length) return;
          syncing = true;
          try {
            if (syncUsesTimeRange) drawdownScale.setVisibleRange?.(range);
            else drawdownScale.setVisibleLogicalRange?.(range);
          } finally { syncing = false; }
        };
        drawdownRangeHandler = (range) => {
          if (syncing || !syncReady || !range || !mainScale || !lineSeries.main.length) return;
          syncing = true;
          try {
            if (syncUsesTimeRange) mainScale.setVisibleRange?.(range);
            else mainScale.setVisibleLogicalRange?.(range);
          } finally { syncing = false; }
        };
        if (syncUsesTimeRange) {
          mainScale.subscribeVisibleTimeRangeChange(mainRangeHandler);
          drawdownScale.subscribeVisibleTimeRangeChange(drawdownRangeHandler);
        } else {
          mainScale?.subscribeVisibleLogicalRangeChange?.(mainRangeHandler);
          drawdownScale?.subscribeVisibleLogicalRangeChange?.(drawdownRangeHandler);
        }
        crosshairHandler = (parameter) => {
          handleHover(parameter);
          syncCrosshair(charts.main, charts.drawdown, parameter);
        };
        drawdownCrosshairHandler = (parameter) => {
          handleHover(parameter);
          syncCrosshair(charts.drawdown, charts.main, parameter);
        };
        clickHandler = (parameter) => handleClick(parameter);
        charts.main.subscribeCrosshairMove?.(crosshairHandler);
        charts.drawdown.subscribeCrosshairMove?.(drawdownCrosshairHandler);
        charts.main.subscribeClick?.(clickHandler);
        usingFallback = false;
        return true;
      } catch (_) {
        fallbackReason = "图表库初始化失败，已启用离线回退";
        removeCharts();
        return false;
      }
    }

    function visibleDateRange() {
      if (!rows.length) return null;
      const bounds = dateRangeBounds(rows, state.range, {
        endDate: rows.at(-1)?.date,
        validDateRange: rows.validDateRange,
      });
      if (!bounds.start || !bounds.end || bounds.start > bounds.end) return null;
      const paddingDays = state.period === "1M" ? 7 : state.period === "1W" ? 2 : 2;
      return {from: shiftDate(bounds.start, -paddingDays), to: shiftDate(bounds.end, paddingDays)};
    }

    function eventContextRange(date) {
      const source = filterDateRange(rows, "all", {
        endDate: rows.at(-1)?.date,
        validDateRange: rows.validDateRange,
      });
      if (!source.length) return {from: date, to: date};
      const dates = source.map((row) => row.date);
      let index = dates.findIndex((value) => value >= date);
      if (index < 0) index = dates.length - 1;
      let from = dates[Math.max(0, index - 10)] || date;
      let to = dates[Math.min(dates.length - 1, index + 10)] || date;
      if (state.period !== "1D" && state.metric !== "cumulative_return") {
        const periodKey = periodKeyForDate(date, state.period);
        const bar = currentBars().find((candidate) => candidate.periodKey === periodKey);
        if (bar?.time) {
          if (bar.time < from) from = bar.time;
          if (bar.time > to) to = bar.time;
        }
      }
      return {from: date < from ? date : from, to: date > to ? date : to};
    }

    function expandRangeForEvent(date) {
      if (!date) return false;
      const bounds = dateRangeBounds(rows, state.range, {
        endDate: rows.at(-1)?.date,
        validDateRange: rows.validDateRange,
      });
      if ((!bounds.start || date >= bounds.start) && (!bounds.end || date <= bounds.end)) return false;
      state.range = "all";
      render();
      return true;
    }

    function applyVisibleRange() {
      if (usingFallback) return;
      const range = visibleDateRange();
      if (!range) return;
      const bounds = dateRangeBounds(rows, state.range, {endDate: rows.at(-1)?.date, validDateRange: rows.validDateRange});
      const fromIndex = Math.max(0, timelineDates.findIndex(date => date >= bounds.start));
      let toIndex = timelineDates.findLastIndex(date => date <= bounds.end);
      if (toIndex < fromIndex) toIndex = fromIndex;
      const padding = Math.max(2, (toIndex - fromIndex) * 0.05);
      const logicalRange = {from: fromIndex - padding, to: toIndex + padding};
      const scales = [
        [chartTimeScale(charts.main), lineSeries.main],
        [chartTimeScale(charts.drawdown), lineSeries.drawdown],
      ].filter((entry) => entry[0] && entry[1].length).map((entry) => entry[0]);
      syncing = true;
      try {
        for (const scale of scales) {
          if (timelineDates.length && typeof scale.setVisibleLogicalRange === "function") scale.setVisibleLogicalRange(logicalRange);
          else if (typeof scale.setVisibleRange === "function") scale.setVisibleRange(range);

        }
      } finally { syncing = false; }
    }

    function removeSeries(chart, series) {
      if (!chart || !series) return;
      if (typeof chart.removeSeries === "function") {
        try { chart.removeSeries(series); return; } catch (_) { /* fall through to empty data */ }
      }
      try { series.setData?.([]); } catch (_) { /* stale chart API */ }
    }

    function clearSeries(kind, chart) {
      for (const series of lineSeries[kind] || []) removeSeries(chart, series);
      lineSeries[kind] = [];
    }

    function setLineSegments(chart, key, segments, color, area, options = {}) {
      if (!options.append) clearSeries(key, chart);
      if (!chart) return [];
      const result = [];
      const sourceSegments = Array.isArray(segments) ? segments : [];
      const metric = options.metric || (key === "drawdown" ? "drawdown" : state.metric);
      for (const [segmentIndex, segment] of sourceSegments.entries()) {
        if (!segment.length) continue;
        const series = area && typeof chart.addAreaSeries === "function"
          ? chart.addAreaSeries(seriesOptions(color, true, metric, key === "drawdown"))
          : typeof chart.addLineSeries === "function"
            ? chart.addLineSeries(seriesOptions(color, false, metric, key === "drawdown"))
            : null;
        if (!series) continue;
        let data = segment.map((point) => ({time: point.time, value: point.value}));
        if (options.dashed && data.length) {
          try { series.applyOptions?.({lineStyle: 1, lineWidth: 1, title: options.title || ""}); } catch (_) {}
        }
        if (options.pad && state.period !== "1D") {
          const paddingDays = state.period === "1M" ? 7 : 2;
          if (segmentIndex === 0) data = [{time: shiftDate(data[0].time, -paddingDays)}, ...data];
          if (segmentIndex === sourceSegments.length - 1) data = [...data, {time: shiftDate(data.at(-1).time, paddingDays)}];
        }
        if (options.selectedEventDate && !data.some((point) => asDate(point.time) === options.selectedEventDate)) {
          data = [...data, {time: options.selectedEventDate}].sort((left, right) => compareDates(asDate(left.time), asDate(right.time)));
        }
        series.setData(data);
        series.__portfolioReviewTimes = new Set(data.map((point) => asDate(point.time)).filter(Boolean));
        result.push(series);
      }
      lineSeries[key] = options.append ? [...(lineSeries[key] || []), ...result] : result;
      return result;
    }

    function addReferenceLine(series, metric, drawdown = false) {
      if (!series || typeof series.createPriceLine !== "function") return;
      const price = drawdown ? 0 : metric === "unit_nav" ? 1 : metric === "equity" ? initialCash : 0;
      if (!Number.isFinite(price)) return;
      try {
        series.createPriceLine({
          price,
          color: "#9aa6a9",
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: false,
          title: drawdown ? "0" : metric === "unit_nav" ? "初始单位净值 1.0" : metric === "equity" ? "初始本金" : "0%",
        });
      } catch (_) { /* compatible chart builds may omit price lines */ }
    }

    function applyMarkers(markers) {
      const sorted = sortMarkers(markers);
      markerLookup = new Map(sorted.map((marker) => [String(marker.id), marker]));
      const targets = [...lineSeries.main];
      lastMarkerError = null;
      for (const series of targets) {
        const times = series.__portfolioReviewTimes || null;
        const compatible = sorted.filter((marker) => marker.exactDateAvailable !== false && (!times || times.has(asDate(marker.time))));
        try {
          series.setMarkers(compatible);
        } catch (error) {
          lastMarkerError = {
            type: "marker",
            message: String(error?.message || error),
            markerIds: compatible.map((marker) => marker.id),
          };
          safeCallback(config.onError, {...lastMarkerError});
        }
      }
    }

    function crosshairPrice(chart, date) {
      const row = rowForMetricDate(state.metric, date);
      if (chart === charts.drawdown) return asFiniteNumber(row?.drawdown);
      if (state.period !== "1D" && state.metric !== "cumulative_return") {
        const bar = periodBarForDate(date);
        return asFiniteNumber(bar?.close);
      }
      return row ? metricValue(row, state.metric, initialCash) : null;
    }

    function syncCrosshair(source, target, parameter) {
      if (syncingCrosshair || destroyed || !target) return;
      const date = asDate(parameter?.time || parameter?.date);
      syncingCrosshair = true;
      try {
        if (!date) {
          target.clearCrosshairPosition?.();
          return;
        }
        const series = target === charts.drawdown ? lineSeries.drawdown[0] : lineSeries.main[0];
        const price = crosshairPrice(target, date);
        if (series && price !== null && typeof target.setCrosshairPosition === "function") {
          target.setCrosshairPosition(price, date, series);
        } else {
          target.clearCrosshairPosition?.();
        }
      } finally { syncingCrosshair = false; }
    }

    function staleMarkers(points, bars, period) {
      const barByPeriod = new Map((Array.isArray(bars) ? bars : []).map((bar) => [bar.periodKey, bar]));
      return points.filter((point) => (
        point.value !== null
        && (point.stale_symbols || (asFiniteNumber(point.max_valuation_stale_days) || 0) > 0)
      )).map((point) => {
        const bar = period === "1D" ? null : barByPeriod.get(periodKeyForDate(point.time, period));
        if (period !== "1D" && !bar) return null;
        return {
        time: bar?.time || point.time,
        sourceDate: point.time,
        id: `stale-${point.time}`,
        eventId: null,
        eventIds: [],
        position: "aboveBar",
        shape: "square",
        color: "#b45309",
        title: "估值陈旧",
        text: "",
        exactDateAvailable: true,
        };
      }).filter(Boolean);
    }

    function warningMarkers(bars) {
      const source = Array.isArray(bars) ? bars : [];
      const usable = source.filter(barHasUsableOHLC);
      return source.filter((bar) => bar.warning || bar.isPartial).map((bar) => {
        const target = barHasUsableOHLC(bar)
          ? bar
          : usable.find((candidate) => candidate.time >= bar.time) || usable.at(-1);
        return target ? {
          time: target.time,
          sourceDate: bar.periodEnd || bar.time,
          id: `warning-${bar.periodKey || bar.time}`,
          eventId: null,
          eventIds: [],
          position: "aboveBar",
          shape: "square",
          color: "#b45309",
          title: bar.partialLabel || "周期完整性警告",
          text: "",
          exactDateAvailable: true,
        } : null;
      }).filter(Boolean);
    }

    function chartMarkers(data, metricData) {
      const availableDates = metricData.filter((point) => point.value !== null).map((point) => point.time);
      const candleMode = state.period !== "1D" && state.metric !== "cumulative_return" && data.bars.length > 0;
      const periodBars = new Map(periodTimelineBars().map((bar) => [bar.periodKey, bar]));
      const eventMarkers = markersForEvents(data.groups, availableDates, {showDecisions: state.showDecisions}).map((marker) => {
        if (state.period === "1D") return marker;
        const group = data.groups.find((candidate) => candidate.id === marker.groupId);
        const bar = group ? periodBars.get(group.periodKey) : null;
        return bar
          ? {...marker, time: bar.time, mappedDate: bar.time, sourceDate: marker.sourceDate || marker.time, sourceDates: [...(marker.sourceDates || [])], exactDateAvailable: true}
          : {...marker, exactDateAvailable: false};
      });
      return sortMarkers(eventMarkers.concat(
        staleMarkers(metricData, candleMode ? data.bars : [], candleMode ? state.period : "1D"),
        candleMode ? warningMarkers(data.bars) : [],
      ));
    }

    function benchmarkMetricValue(row, metric) {
      const normalizedMetric = metricName(metric);
      if (normalizedMetric === "equity") return null;
      if (normalizedMetric === "unit_nav") return asFiniteNumber(row?.unit_nav);
      const unit = asFiniteNumber(row?.unit_nav);
      return unit === null ? null : unit - 1;
    }

    function benchmarkEntry(item) {
      const key = `${item.id}:${state.metric}:${state.period}`;
      const cached = benchmarkDataCache.get(key);
      if (cached) return cached;
      const metric = metricName(state.metric);
      if (metric === "equity") {
        const empty = {points: [], byTime: new Map()};
        benchmarkDataCache.set(key, empty);
        return empty;
      }
      const source = rebaseNavRows(item.rows, firstValidUnitNav(item.rows));
      const candidates = source.filter((row) => row?.date);
      const timeline = state.period === "1D"
        ? rows.map((row) => ({time: row.date}))
        : periodTimelineBars().map((bar) => ({time: bar.time}));
      const points = [];
      const byTime = new Map();
      let cursor = 0;
      let latest = null;
      for (const target of timeline) {
        while (cursor < candidates.length && candidates[cursor].date <= target.time) latest = candidates[cursor++];
        const value = latest ? benchmarkMetricValue(latest, metric) : null;
        const point = {
          time: target.time,
          date: target.time,
          value,
          observed_date: latest?.observed_date || latest?.date || null,
          benchmark_id: item.id,
          benchmark_name: item.name,
        };
        points.push(point);
        byTime.set(target.time, point);
      }
      const entry = {points, byTime};
      benchmarkDataCache.set(key, entry);
      return entry;
    }

    function benchmarkPointsForHover(date) {
      if (state.metric === "equity" || !configuredBenchmarkItems.length) return [];
      const target = state.period === "1D" ? asDate(date) : periodBarForDate(asDate(date))?.time;
      if (!target) return [];
      return configuredBenchmarkItems.map((item) => {
        const point = benchmarkEntry(item).byTime.get(target);
        return point && point.value !== null ? {
          id: item.id,
          name: item.name,
          color: item.color,
          value: point.value,
          observed_date: point.observed_date,
          date: target,
        } : null;
      }).filter(Boolean);
    }

    function renderBenchmarkLines() {
      clearSeries("benchmark", charts.main);
      if (!charts.main || state.metric === "equity" || !configuredBenchmarkItems.length) return;
      for (const [index, item] of configuredBenchmarkItems.entries()) {
        const entry = benchmarkEntry(item);
        const filtered = filterDateRange(entry.points, state.range, {
          endDate: rows.at(-1)?.date,
          validDateRange: rows.validDateRange,
        });
        const segments = contiguousSegments(filtered);
        for (const segment of segments) {
          setLineSegments(charts.main, "benchmark", [segment], item.color, false, {
            append: index > 0 || lineSeries.benchmark.length > 0,
            dashed: true,
            title: item.name,
          });
        }
      }
    }

    function candleSeriesData(bars) {
      const actual = new Map((Array.isArray(bars) ? bars : []).filter(barHasUsableOHLC).map((bar) => [bar.time, {
        time: bar.time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      }]));
      const dates = uniqueDates([...actual.keys()]);
      if (!dates.length) return [];
      const paddingDays = state.period === "1M" ? 7 : 2;
      const selectedDate = selectedEventDate();
      const displayDates = uniqueDates([...dates, ...(selectedDate ? [selectedDate] : [])]);
      return [
        {time: shiftDate(displayDates[0], -paddingDays)},
        ...displayDates.map((date) => actual.get(date) || {time: date}),
        {time: shiftDate(displayDates.at(-1), paddingDays)},
      ];
    }

    function syncTimeGrids() {
      for (const key of ["main", "drawdown"]) {
        removeSeries(charts[key], timeGrids[key]); timeGrids[key] = null;
      }
      timelineDates = uniqueDates([...lineSeries.main, ...lineSeries.drawdown, ...lineSeries.benchmark].flatMap(series => [...(series.__portfolioReviewTimes || [])]));
      for (const key of ["main", "drawdown"]) {
        const series = charts[key]?.addLineSeries?.({visible:false, priceLineVisible:false, lastValueVisible:false});
        if (series) { series.setData(timelineDates.map(time => ({time}))); timeGrids[key] = series; }
      }
    }

    function renderCharts() {
      const data = mainData();
      const metricData = data.points;
      const segments = contiguousSegments(metricData);
      const drawdownPoints = state.period === "1D"
        ? toMetricData(rows, "unit_nav", initialCash).map((point) => ({...point, value: point.drawdown}))
        : (() => {
          const byPeriod = new Map();
          for (const row of rows) {
            if (asFiniteNumber(row.drawdown) === null) continue;
            const key = periodKeyForDate(row.date, state.period);
            if (!byPeriod.has(key)) byPeriod.set(key, []);
            byPeriod.get(key).push(row);
          }
          return periodTimelineBars().map((bar) => {
          const row = (byPeriod.get(bar.periodKey) || []).filter((candidate) => candidate.date <= bar.time).at(-1);
          return {time: bar.time, date: bar.time, value: asFiniteNumber(row?.drawdown)};
          });
        })();
      const drawdownFiltered = filterDateRange(drawdownPoints, state.range, {endDate: rows.at(-1)?.date, validDateRange: rows.validDateRange});
      syncReady = false;
      clearSeries("main", charts.main);
      clearSeries("drawdown", charts.drawdown);
      clearSeries("benchmark", charts.main);
      if (state.period !== "1D" && state.metric !== "cumulative_return" && data.bars.length && typeof charts.main?.addCandlestickSeries === "function") {
        const candle = charts.main.addCandlestickSeries({
          upColor: "#15803d", downColor: "#b42318", borderVisible: false, wickUpColor: "#15803d", wickDownColor: "#b42318",
          priceLineVisible: false,
          priceFormat: axisPriceFormat(state.metric),
        });
        const candleData = candleSeriesData(data.bars);
        candle.setData(candleData);
        candle.__portfolioReviewTimes = new Set(data.bars.filter(barHasUsableOHLC).map((bar) => asDate(bar.time)).filter(Boolean));
        addReferenceLine(candle, state.metric);
        lineSeries.main = [candle];
      } else {
        const mainSeries = setLineSegments(charts.main, "main", segments, "#0f766e", state.period === "1D" && state.metric !== "cumulative_return", {pad: state.period !== "1D"});
        addReferenceLine(mainSeries[0], state.metric);
      }
      const drawdownSeries = setLineSegments(charts.drawdown, "drawdown", contiguousSegments(drawdownFiltered), "#b45309", false, {
        pad: state.period !== "1D",
        selectedEventDate: state.period !== "1D" ? selectedEventDate() : null,
      });
      addReferenceLine(drawdownSeries[0], "drawdown", true);
      renderBenchmarkLines();
      applyMarkers(chartMarkers(data, metricData));
      syncTimeGrids();
      syncReady = true;
      applyVisibleRange();
      renderCount += 1;
    }

    function renderFallbackState() {
      const data = mainData();
      renderFallback(mainContainer, data.points, data.bars, state.metric, state.period, false, data.groups, fallbackReason);
      const drawdownPoints = toMetricData(rows, "unit_nav", initialCash).map((point) => ({...point, value: point.drawdown}));
      renderFallback(drawdownContainer, filterDateRange(drawdownPoints, state.range, {endDate: rows.at(-1)?.date, validDateRange: rows.validDateRange}), [], "drawdown", "1D", true, [], fallbackReason);
      if (mainContainer) mainContainer._portfolioReviewSelectEvent = (id) => selectEvent(id, true);
      renderCount += 1;
    }

    function render() {
      if (destroyed) return;
      if (usingFallback) renderFallbackState();
      else renderCharts();
    }

    function eventById(id) {
      const value = String(id ?? "");
      return events.find((event) => event.id === value) || null;
    }

    function selectEvent(id, notify) {
      const event = eventById(id);
      if (!event || destroyed) return null;
      state.selectedEventId = event.id;
      if (notify) { safeCallback(config.onSelectEvent, {...event}); return {...event}; }
      const markerDate = event.date;
      expandRangeForEvent(markerDate);
      if (!usingFallback) {
        const contextRange = eventContextRange(markerDate);
        syncing = true;
        try {
          for (const chart of [charts.main, charts.drawdown]) {
            const scale = chartTimeScale(chart);
            if (!scale || !markerDate) continue;
            try {
              if (typeof scale.setVisibleRange === "function") scale.setVisibleRange(contextRange);
              else if (typeof scale.setVisibleLogicalRange === "function") scale.setVisibleLogicalRange(contextRange);
            } catch (_) { /* a compatible chart may reject an unavailable event date */ }
          }
        } finally { syncing = false; }
        const data = mainData();
        applyMarkers(chartMarkers(data, data.points));
      }
      return {...event};
    }

    function eventIdFromParameter(parameter) {
      if (!parameter || typeof parameter !== "object") return null;
      if (parameter.eventId) return parameter.eventId;
      if (parameter.marker?.eventId) return parameter.marker.eventId;
      if (parameter.marker?.eventIds?.length === 1) return parameter.marker.eventIds[0];
      if (parameter.hoveredObjectId) return parameter.hoveredObjectId;
      if (Array.isArray(parameter.eventIds) && parameter.eventIds.length) return parameter.eventIds[0];
      return null;
    }

    function handleClick(parameter) {
      const groups = currentGroups();
      const markerId = parameter?.marker?.id || parameter?.hoveredObjectId || null;
      const knownMarker = markerId ? markerLookup.get(String(markerId)) : null;
      const marker = parameter?.marker || knownMarker;
      const markerEventIds = [
        ...(Array.isArray(parameter?.eventIds) ? parameter.eventIds : []),
        ...(Array.isArray(marker?.eventIds) ? marker.eventIds : []),
      ].map(String).filter((id, index, values) => values.indexOf(id) === index && eventById(id));
      if (markerEventIds.length === 1) {
        selectEvent(markerEventIds[0], true);
        return;
      }
      if (markerEventIds.length > 1) {
        const markerGroupId = String(marker?.groupId || parameter?.groupId || "");
        const markerGroup = groups.find((item) => item.id === markerGroupId) || null;
        safeCallback(config.onSelectEvent, {
          type: "group",
          groupId: markerGroupId || marker?.id || null,
          date: markerGroup?.date || marker?.sourceDate || asDate(parameter?.time),
          period: markerGroup?.period || state.period,
          events: markerEventIds.map((id) => eventById(id)).filter(Boolean).map((event) => ({...event})),
        });
        return;
      }
      if (knownMarker && !knownMarker.eventIds?.length) return;
      const groupCandidates = [
        parameter?.groupId,
        parameter?.marker?.groupId,
        parameter?.hoveredObjectId,
      ].filter(Boolean).map(String);
      let group = groups.find((item) => groupCandidates.includes(String(item.id))) || null;
      if (!group) {
        const date = asDate(parameter?.time || parameter?.date || parameter?.marker?.time);
        if (date) {
          group = groups.find((item) => item.time === date || item.date === date || (state.period !== "1D" && item.periodKey === periodKeyForDate(date, state.period))) || null;
        }
      }
      if (group) {
        if (group.events.length > 1) {
          safeCallback(config.onSelectEvent, {
            type: "group",
            groupId: group.id,
            date: group.date,
            period: group.period,
            events: group.events.map((event) => ({...event})),
          });
        } else if (group.events[0]) selectEvent(group.events[0].id, true);
        return;
      }
      const direct = eventIdFromParameter(parameter);
      if (direct && eventById(direct)) selectEvent(direct, true);
    }

    function handleHover(parameter) {
      if (destroyed || !parameter) return;
      const date = asDate(parameter.time || parameter.date);
      if (!date) return;
      const row = rowForMetricDate(state.metric, date);
      const periodGroup = state.period === "1D" ? null : currentGroupForDate(date);
      const dayEvents = periodGroup ? periodGroup.events : eventsByDate.get(date) || [];
      const bar = periodBarForDate(date);
      const value = state.period !== "1D" && state.metric !== "cumulative_return"
        ? asFiniteNumber(bar?.close)
        : row ? metricValue(row, state.metric, initialCash) : null;
      safeCallback(config.onHover, {
        date,
        row: row ? {...row} : null,
        metric: state.metric,
        value,
        unit_nav: row?.unit_nav ?? null,
        equity: row?.equity ?? null,
        cumulative_return: row?.cumulative_return ?? null,
        daily_return: row?.daily_return ?? null,
        drawdown: row?.drawdown ?? null,
        events: dayEvents.map((event) => ({...event})),
        eventCount: dayEvents.length,
        period: state.period,
        bar,
        benchmarks: benchmarkPointsForHover(date),
      });
    }

    function removeCharts() {
      syncReady = false;
      const mainScale = chartTimeScale(charts.main);
      const drawdownScale = chartTimeScale(charts.drawdown);
      if (syncUsesTimeRange) {
        try { mainScale?.unsubscribeVisibleTimeRangeChange?.(mainRangeHandler); } catch (_) {}
        try { drawdownScale?.unsubscribeVisibleTimeRangeChange?.(drawdownRangeHandler); } catch (_) {}
      } else {
        try { mainScale?.unsubscribeVisibleLogicalRangeChange?.(mainRangeHandler); } catch (_) {}
        try { drawdownScale?.unsubscribeVisibleLogicalRangeChange?.(drawdownRangeHandler); } catch (_) {}
      }
      try { charts.main?.unsubscribeCrosshairMove?.(crosshairHandler); } catch (_) {}
      try { charts.drawdown?.unsubscribeCrosshairMove?.(drawdownCrosshairHandler); } catch (_) {}
      try { charts.main?.unsubscribeClick?.(clickHandler); } catch (_) {}
      try { charts.main?.remove?.(); } catch (_) {}
      try { charts.drawdown?.remove?.(); } catch (_) {}
      charts.main = null;
      charts.drawdown = null;
      timeGrids.main = null; timeGrids.drawdown = null; timelineDates = [];
      syncUsesTimeRange = false;
      lineSeries.main = [];
      lineSeries.drawdown = [];
      lineSeries.benchmark = [];
    }

    function resize() {
      if (destroyed) return controller;
      const width = chartWidth(container);
      if (!width) return controller;
      try { charts.main?.resize?.(width, Math.max(1, Number(mainContainer?.clientHeight || 360))); } catch (_) {}
      try { charts.drawdown?.resize?.(width, Math.max(1, Number(drawdownContainer?.clientHeight || 120))); } catch (_) {}
      return controller;
    }

    function update(next = {}) {
      if (destroyed || !next || typeof next !== "object") return controller;
      const previous = {...state};
      if (next.metric !== undefined) state.metric = metricName(next.metric);
      if (next.period !== undefined) state.period = periodName(next.period);
      if (next.range !== undefined) state.range = typeof next.range === "string" && RANGES.has(next.range.toUpperCase()) ? (next.range.toLowerCase() === "all" ? "all" : next.range.toUpperCase()) : next.range;
      if (next.showDecisions !== undefined) state.showDecisions = asBoolean(next.showDecisions);
      if (next.selectedEventId !== undefined) state.selectedEventId = next.selectedEventId === null ? null : String(next.selectedEventId);
      const changed = previous.metric !== state.metric
        || previous.period !== state.period
        || previous.showDecisions !== state.showDecisions
        || JSON.stringify(previous.range) !== JSON.stringify(state.range);
      if (changed) render();
      else if (previous.selectedEventId !== state.selectedEventId && state.selectedEventId) selectEvent(state.selectedEventId, false);
      return controller;
    }

    function locateEvent(id) {
      return selectEvent(id, false);
    }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      try { observer?.disconnect?.(); } catch (_) {}
      observer = null;
      try { ownerWindow?.removeEventListener?.("resize", resize); } catch (_) {}
      removeCharts();
      if (container && typeof container.removeChild === "function") {
        for (const child of [mainContainer, drawdownContainer]) {
          if (child && child.parentNode === container) {
            try { container.removeChild(child); } catch (_) {}
          }
        }
      }
    }

    const controller = {
      update,
      locateEvent,
      resize,
      destroy,
      getEventGroups: () => currentGroups().map((group) => ({...group, events: group.events.map((event) => ({...event}))})),
      getState: () => ({...state, renderCount, periodAggregationCount, usingFallback, markerError: lastMarkerError ? {...lastMarkerError} : null}),
      getData: () => ({rows: rows.map((row) => ({...row})), events: events.map((event) => ({...event})), runId: object.run_id || null, portfolioId: object.portfolio_id || null}),
    };

    if (typeof ownerWindow?.addEventListener === "function") ownerWindow.addEventListener("resize", resize);
    const ResizeObserverImpl = config.ResizeObserver || root?.ResizeObserver || ownerWindow?.ResizeObserver;
    if (typeof ResizeObserverImpl === "function" && container) {
      try {
        observer = new ResizeObserverImpl(() => resize());
        observer.observe?.(container);
      } catch (_) { observer = null; }
    }
    usingFallback = !initCharts();
    render();
    return controller;
  }

  const api = {
    VERSION,
    METRICS: [...METRICS],
    PERIODS: [...PERIODS],
    normalizeNavRows,
    normalizeNAV: normalizeNavRows,
    filterDateRange,
    filterRange: filterDateRange,
    filterRowsByDateRange: filterDateRange,
    toMetricData,
    metricData: toMetricData,
    toMetricSeries,
    metricSeries: toMetricSeries,
    aggregateObservedClose,
    aggregateObservedOHLC: aggregateObservedClose,
    aggregateObservedCandles: aggregateObservedClose,
    aggregateWeeklyMonthly: aggregateObservedClose,
    periodKeyForDate,
    groupEvents,
    groupEventMarkers: groupEvents,
    markersForEvents,
    eventMarkers: markersForEvents,
    create,
  };
  return api;
}));
