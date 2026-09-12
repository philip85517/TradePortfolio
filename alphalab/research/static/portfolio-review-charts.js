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
    defineMeta(rows, "runId", object.run_id || null);
    defineMeta(rows, "portfolioId", object.portfolio_id || null);
    return rows;
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
      const isPartial = Boolean(isLast && expectedEnd && actualEnd && actualEnd < expectedEnd);
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

  function markersForEvents(groups, availableDates) {
    const dates = availableDates ? new Set(uniqueDates(availableDates)) : null;
    return (Array.isArray(groups) ? groups : []).map((group) => {
      const first = group.events?.[0] || {};
      return {
        time: group.time || group.date,
        id: group.id,
        eventId: group.events?.length === 1 ? group.events[0].id : null,
        eventIds: [...(group.eventIds || [])],
        position: group.hasFill ? (group.fills && !group.decisions ? "belowBar" : "aboveBar") : "aboveBar",
        shape: group.hasFill ? (group.fills && !group.decisions ? "arrowUp" : "circle") : "square",
        color: markerColor(first),
        text: group.count > 1 ? `${group.count} 个事件` : String(first.action || "事件"),
        exactDateAvailable: !dates || dates.has(group.date),
      };
    });
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

  function seriesOptions(color, area) {
    return area ? {
      lineColor: color,
      topColor: `${color}33`,
      bottomColor: `${color}03`,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    } : {
      color,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
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

  function fallbackSvg(points, bars, metric, drawdown) {
    const width = 760;
    const height = drawdown ? 120 : 220;
    const padding = {left: 42, right: 12, top: 12, bottom: 26};
    const values = bars?.length
      ? bars.flatMap((bar) => [bar.low, bar.high]).filter((value) => asFiniteNumber(value) !== null)
      : points.flatMap((point) => [point.value]).filter((value) => asFiniteNumber(value) !== null);
    let min = values.length ? Math.min(...values) : 0;
    let max = values.length ? Math.max(...values) : 1;
    if (drawdown) min = Math.min(min, 0);
    if (max === min) max = min + 1;
    const source = bars?.length ? bars.map((bar) => ({time: bar.time, value: bar.close})) : points;
    const x = (index) => padding.left + (width - padding.left - padding.right) * (source.length <= 1 ? 0 : index / (source.length - 1));
    const y = (value) => padding.top + (max - value) / (max - min) * (height - padding.top - padding.bottom);
    const paths = [];
    if (bars?.length) {
      bars.forEach((bar, index) => {
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
      const choices = group.events.map((event) => `<li><button type="button" data-portfolio-review-event="${escapeHtml(event.id)}">${escapeHtml(event.date)} · ${escapeHtml(event.action || "事件")} · ${escapeHtml(event.symbol || "")}</button></li>`).join("");
      return `<li><strong>${escapeHtml(group.date)}</strong> · ${group.count} 个事件<ol>${choices}</ol></li>`;
    }).join("");
    const note = omitted ? `<p>中间 ${omitted} 个事件组保留在控制器中，回退列表展示首尾。</p>` : "";
    return `<details class="portfolio-review-event-list"><summary>交易与事件</summary>${note}<ol>${rows}</ol></details>`;
  }

  function renderFallback(element, points, bars, metric, period, drawdown, groups, reason) {
    if (!element) return;
    if (drawdown) {
      element.innerHTML = `<div class="portfolio-review-fallback" data-fallback="drawdown"><p>离线回退 · 回撤保留冻结缺口</p>${fallbackSvg(points, [], metric, true)}${fallbackTable(points, "drawdown")}</div>`;
      return;
    }
    const note = reason || (period === "1D" ? "日终观测值；未知区间不会连线。" : "K 线由周期内实际日终净值的首/末/最高/最低观测值聚合，非盘中 OHLC。");
    element.innerHTML = `<div class="portfolio-review-fallback" data-fallback="main"><p>${escapeHtml(note)}</p>${fallbackSvg(points, bars, metric, false)}${fallbackTable(points, metric)}${fallbackEvents(groups)}</div>`;
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
    const rows = normalizeNavRows(object, initialCash);
    const events = (Array.isArray(object.events) ? object.events : []).map((event, index) => normalizeEvent(event, index, object.run_id));
    const benchmarkObject = dataObject(config.benchmark || {});
    const benchmarkRows = Array.isArray(config.benchmark)
      ? normalizeNavRows(config.benchmark, initialCash)
      : normalizeNavRows(benchmarkObject, initialCashFrom(benchmarkObject, initialCash));
    const document = container?.ownerDocument || root?.document || null;
    const mainContainer = createElement(document, "div", "portfolio-review-chart-main");
    const drawdownContainer = createElement(document, "div", "portfolio-review-chart-drawdown");
    if (mainContainer) mainContainer.setAttribute?.("data-portfolio-review-chart", "main");
    if (drawdownContainer) drawdownContainer.setAttribute?.("data-portfolio-review-chart", "drawdown");
    if (container && typeof container.appendChild === "function") {
      if (mainContainer) container.appendChild(mainContainer);
      if (drawdownContainer) container.appendChild(drawdownContainer);
    }
    const state = {metric: "unit_nav", period: "1D", range: "all", selectedEventId: null};
    const charts = {main: null, drawdown: null};
    const lineSeries = {main: [], drawdown: [], benchmark: []};
    const eventGroupsCache = new Map();
    const library = resolveLibrary(config);
    let destroyed = false;
    let syncing = false;
    let observer = null;
    let ownerWindow = root?.window || root;
    let mainRangeHandler = null;
    let drawdownRangeHandler = null;
    let crosshairHandler = null;
    let clickHandler = null;
    let renderCount = 0;
    let usingFallback = true;
    let fallbackReason = null;

    function currentGroups() {
      const key = `${state.period}:${state.range}`;
      if (!eventGroupsCache.has(key)) {
        const selected = filterDateRange(events, state.range, {
          endDate: rows.at(-1)?.date,
          validDateRange: rows.validDateRange,
        });
        eventGroupsCache.set(key, groupEvents(selected, state.period, {runId: object.run_id}));
      }
      return eventGroupsCache.get(key);
    }

    function currentPoints() {
      const allPoints = toMetricData(rows, state.metric, initialCash);
      return filterDateRange(allPoints, state.range, {endDate: rows.at(-1)?.date, validDateRange: rows.validDateRange});
    }

    function currentBars() {
      if (state.period === "1D" || state.metric === "cumulative_return") return [];
      const allBars = aggregateObservedClose(rows, state.period, {
        metric: state.metric,
        initialCash,
        sessionList: rows.sessionList,
        validDateRange: rows.validDateRange,
        missingSessionDates: rows.missingSessionDates,
      });
      return filterDateRange(allBars, state.range, {endDate: rows.at(-1)?.date, validDateRange: rows.validDateRange});
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
        timeScale: {borderColor: "#d9e1e3", rightOffset: 4, fixLeftEdge: false, fixRightEdge: false},
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
        mainRangeHandler = (range) => {
          if (syncing || !range || !drawdownScale || typeof drawdownScale.setVisibleLogicalRange !== "function") return;
          syncing = true;
          try { drawdownScale.setVisibleLogicalRange(range); } finally { syncing = false; }
        };
        drawdownRangeHandler = (range) => {
          if (syncing || !range || !mainScale || typeof mainScale.setVisibleLogicalRange !== "function") return;
          syncing = true;
          try { mainScale.setVisibleLogicalRange(range); } finally { syncing = false; }
        };
        mainScale?.subscribeVisibleLogicalRangeChange?.(mainRangeHandler);
        drawdownScale?.subscribeVisibleLogicalRangeChange?.(drawdownRangeHandler);
        crosshairHandler = (parameter) => handleHover(parameter);
        clickHandler = (parameter) => handleClick(parameter);
        charts.main.subscribeCrosshairMove?.(crosshairHandler);
        charts.main.subscribeClick?.(clickHandler);
        usingFallback = false;
        return true;
      } catch (_) {
        fallbackReason = "图表库初始化失败，已启用离线回退";
        removeCharts();
        return false;
      }
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

    function setLineSegments(chart, key, segments, color, area) {
      clearSeries(key, chart);
      if (!chart) return [];
      const result = [];
      for (const segment of segments) {
        if (!segment.length) continue;
        const series = area && typeof chart.addAreaSeries === "function"
          ? chart.addAreaSeries(seriesOptions(color, true))
          : typeof chart.addLineSeries === "function"
            ? chart.addLineSeries(seriesOptions(color, false))
            : null;
        if (!series) continue;
        series.setData(segment.map((point) => ({time: point.time, value: point.value})));
        result.push(series);
      }
      lineSeries[key] = result;
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
      const targets = [...lineSeries.main, ...lineSeries.benchmark];
      const dateMarkers = markers.filter((marker) => marker.exactDateAvailable !== false);
      for (const series of targets) {
        try { series.setMarkers(dateMarkers); } catch (_) { /* markers are optional in old compatible builds */ }
      }
    }

    function staleMarkers(points) {
      return points.filter((point) => point.stale_symbols || (asFiniteNumber(point.max_valuation_stale_days) || 0) > 0).map((point) => ({
        time: point.time,
        id: `stale-${point.time}`,
        eventId: null,
        eventIds: [],
        position: "aboveBar",
        shape: "square",
        color: "#b45309",
        text: "估值陈旧",
        exactDateAvailable: true,
      }));
    }

    function renderCharts() {
      const data = mainData();
      const metricData = data.points;
      const segments = contiguousSegments(metricData);
      const drawdownPoints = toMetricData(rows, "unit_nav", initialCash).map((point) => ({...point, value: point.drawdown}));
      const drawdownFiltered = filterDateRange(drawdownPoints, state.range, {endDate: rows.at(-1)?.date, validDateRange: rows.validDateRange});
      clearSeries("main", charts.main);
      clearSeries("drawdown", charts.drawdown);
      clearSeries("benchmark", charts.main);
      if (state.period !== "1D" && state.metric !== "cumulative_return" && data.bars.length && typeof charts.main?.addCandlestickSeries === "function") {
        const candle = charts.main.addCandlestickSeries({
          upColor: "#15803d", downColor: "#b42318", borderVisible: false, wickUpColor: "#15803d", wickDownColor: "#b42318",
          priceLineVisible: false,
        });
        candle.setData(data.bars.map((bar) => ({time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close})));
        addReferenceLine(candle, state.metric);
        lineSeries.main = [candle];
      } else {
        const mainSeries = setLineSegments(charts.main, "main", segments, "#0f766e", state.period === "1D" && state.metric !== "cumulative_return");
        addReferenceLine(mainSeries[0], state.metric);
      }
      const drawdownSeries = setLineSegments(charts.drawdown, "drawdown", contiguousSegments(drawdownFiltered), "#b45309", false);
      addReferenceLine(drawdownSeries[0], "drawdown", true);
      if (benchmarkRows.length && state.period === "1D" && charts.main) {
        const benchmarkPoints = filterDateRange(toMetricData(benchmarkRows, state.metric, initialCash), state.range, {endDate: rows.at(-1)?.date, validDateRange: rows.validDateRange});
        setLineSegments(charts.main, "benchmark", contiguousSegments(benchmarkPoints), "#2563eb", false);
      }
      applyMarkers(markersForEvents(data.groups, rows.map((row) => row.date)).concat(staleMarkers(metricData)));
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
      if (notify) safeCallback(config.onSelectEvent, {...event});
      if (!usingFallback) {
        const markerDate = event.date;
        for (const chart of [charts.main, charts.drawdown]) {
          const scale = chartTimeScale(chart);
          if (!scale || !markerDate) continue;
          try {
            if (typeof scale.setVisibleRange === "function") scale.setVisibleRange({from: markerDate, to: markerDate});
            else if (typeof scale.setVisibleLogicalRange === "function") scale.setVisibleLogicalRange({from: markerDate, to: markerDate});
          } catch (_) { /* an event outside the available axis remains in the accessible list */ }
        }
        const groups = currentGroups();
        const metricData = toMetricData(rows, state.metric, initialCash);
        applyMarkers(markersForEvents(groups, rows.map((row) => row.date)).concat(staleMarkers(metricData)));
      }
      return {...event};
    }

    function eventIdFromParameter(parameter) {
      if (!parameter || typeof parameter !== "object") return null;
      if (parameter.eventId) return parameter.eventId;
      if (parameter.marker?.eventId) return parameter.marker.eventId;
      if (parameter.marker?.id) return parameter.marker.id;
      if (parameter.hoveredObjectId) return parameter.hoveredObjectId;
      if (Array.isArray(parameter.eventIds) && parameter.eventIds.length) return parameter.eventIds[0];
      return null;
    }

    function handleClick(parameter) {
      const direct = eventIdFromParameter(parameter);
      if (direct && eventById(direct)) {
        selectEvent(direct, true);
        return;
      }
      const groupId = direct || parameter?.groupId;
      const group = currentGroups().find((item) => item.id === groupId);
      if (group?.events?.[0]) selectEvent(group.events[0].id, true);
    }

    function handleHover(parameter) {
      if (destroyed || !parameter) return;
      const date = asDate(parameter.time || parameter.date);
      if (!date) return;
      const row = rows.find((item) => item.date === date) || null;
      const dayEvents = events.filter((event) => event.date === date);
      safeCallback(config.onHover, {
        date,
        row: row ? {...row} : null,
        metric: state.metric,
        value: row ? metricValue(row, state.metric, initialCash) : null,
        unit_nav: row?.unit_nav ?? null,
        equity: row?.equity ?? null,
        cumulative_return: row?.cumulative_return ?? null,
        daily_return: row?.daily_return ?? null,
        drawdown: row?.drawdown ?? null,
        events: dayEvents.map((event) => ({...event})),
        eventCount: dayEvents.length,
      });
    }

    function removeCharts() {
      const mainScale = chartTimeScale(charts.main);
      const drawdownScale = chartTimeScale(charts.drawdown);
      try { mainScale?.unsubscribeVisibleLogicalRangeChange?.(mainRangeHandler); } catch (_) {}
      try { drawdownScale?.unsubscribeVisibleLogicalRangeChange?.(drawdownRangeHandler); } catch (_) {}
      try { charts.main?.unsubscribeCrosshairMove?.(crosshairHandler); } catch (_) {}
      try { charts.main?.unsubscribeClick?.(clickHandler); } catch (_) {}
      try { charts.main?.remove?.(); } catch (_) {}
      try { charts.drawdown?.remove?.(); } catch (_) {}
      charts.main = null;
      charts.drawdown = null;
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
      if (next.selectedEventId !== undefined) state.selectedEventId = next.selectedEventId === null ? null : String(next.selectedEventId);
      const changed = previous.metric !== state.metric || previous.period !== state.period || JSON.stringify(previous.range) !== JSON.stringify(state.range);
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
      getState: () => ({...state, renderCount, usingFallback}),
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
