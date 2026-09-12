const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const modulePath = path.resolve('alphalab/research/static/portfolio-review-charts.js');

function charts() {
  delete require.cache[require.resolve(modulePath)];
  return require(modulePath);
}

function navData() {
  return {
    run_id: 'run-chart-fixture',
    portfolio_id: 'strategy',
    initial_cash: 1000,
    scope: {
      valid_date_range: ['2025-01-02', '2025-01-10'],
    },
    nav: [
      { date: '2025-01-02', equity: 1050, daily_return: 0.05, drawdown: 0 },
      { date: '2025-01-03', equity: 1000, daily_return: -0.047619, drawdown: -0.047619 },
      { date: '2025-01-06', equity: null, daily_return: null, drawdown: null, stale_symbols: '000001', max_valuation_stale_days: 2 },
      { date: '2025-01-07', equity: 0, daily_return: -1, drawdown: -1 },
      { date: '2025-01-08', equity: 1100, daily_return: null, drawdown: 0 },
    ],
    events: [
      { id: 'buy-1', date: '2025-01-02', action: 'BUY', action_label: '买入', filled: true, symbol: '000001', shares: 10 },
      { id: 'select-1', date: '2025-01-02', action: 'SELECT', action_text: '入选', filled: false, symbol: '000002' },
      { id: 'sell-1', date: '2025-01-09', action: 'SELL', action_label: '卖出', filled: true, symbol: '000001', shares: 10 },
    ],
    capabilities: {
      session_list: ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08', '2025-01-09', '2025-01-10'],
      valid_date_range: ['2025-01-02', '2025-01-10'],
      missing_session_dates: ['2025-01-09', '2025-01-10'],
    },
  };
}

function longNavData() {
  const data = navData();
  data.scope.valid_date_range = ['2023-01-03', '2025-12-03'];
  data.nav = [
    {date: '2023-01-03', equity: 1000, daily_return: 0, drawdown: 0},
    {date: '2024-12-03', equity: 1100, daily_return: 0.1, drawdown: 0},
    {date: '2025-12-03', equity: 1200, daily_return: 0.09, drawdown: 0},
  ];
  data.events = [
    {id: 'buy-1', date: '2023-01-03', action: 'BUY', action_label: '买入', filled: true, symbol: '000001'},
    {id: 'sell-1', date: '2025-12-03', action: 'SELL', action_label: '卖出', filled: true, symbol: '000001'},
  ];
  data.capabilities = {
    session_list: ['2023-01-03', '2024-12-03', '2025-12-03'],
    valid_date_range: ['2023-01-03', '2025-12-03'],
    missing_session_dates: [],
  };
  return data;
}

function earlyPeriodData() {
  const data = longNavData();
  const sessionDates = [];
  for (let timestamp = Date.UTC(2023, 0, 3); timestamp <= Date.UTC(2023, 0, 31); timestamp += 24 * 60 * 60 * 1000) {
    const date = new Date(timestamp);
    if (date.getUTCDay() === 0 || date.getUTCDay() === 6) continue;
    sessionDates.push(date.toISOString().slice(0, 10));
  }
  const dates = [...sessionDates, '2024-12-03', '2025-12-03'];
  data.nav = dates.map((date, index) => ({
    date,
    equity: 1000 + index,
    daily_return: index ? 0.001 : 0,
    drawdown: 0,
  }));
  data.capabilities.session_list = dates;
  return data;
}

function fakeElement(width = 640, height = 360) {
  const element = {
    children: [],
    ownerDocument: null,
    parentNode: null,
    style: {},
    dataset: {},
    hidden: false,
    clientWidth: width,
    clientHeight: height,
    offsetWidth: width,
    offsetHeight: height,
    innerHTML: '',
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    removeChild(child) { this.children = this.children.filter((item) => item !== child); child.parentNode = null; },
    setAttribute(name, value) { this[name] = String(value); },
    addEventListener() {},
    removeEventListener() {},
    querySelectorAll() { return []; },
  };
  return element;
}

function fakeDocument() {
  const document = {
    createElement() { const element = fakeElement(); element.ownerDocument = document; return element; },
  };
  return document;
}

function fakeChartApi(log, options = {}) {
  const strict = Boolean(options.strict);
  const dateKey = (value) => {
    if (typeof value === 'string') return value;
    if (value && typeof value === 'object' && value.year) return `${String(value.year).padStart(4, '0')}-${String(value.month).padStart(2, '0')}-${String(value.day).padStart(2, '0')}`;
    return String(value);
  };
  function series(kind, options, owner) {
    return {
      kind,
      options,
      data: [],
      markers: [],
      setData(value) { this.data = value; this.times = new Set(value.map((item) => dateKey(item.time))); log.push(['setData', kind, value.length]); owner.emitTimeRange({from: value[0]?.time || null, to: value.at(-1)?.time || null}); },
      setMarkers(value) {
        const sorted = value.every((marker, index) => index === 0 || dateKey(value[index - 1].time) <= dateKey(marker.time));
        if (!sorted) throw new Error('Lightweight Charts markers must be sorted by time');
        if (value.some((marker) => !this.times.has(dateKey(marker.time)))) throw new Error('marker time is not a series time');
        this.markers = value; log.push(['setMarkers', kind, value.length]);
      },
      applyOptions(value) { log.push(['applyOptions', kind, value]); },
    };
  }
  function chart(name) {
    const visibleHandlers = new Set();
    const timeRangeHandlers = new Set();
    const crosshairHandlers = new Set();
    const clickHandlers = new Set();
    const object = {
      name,
      series: [],
      ranges: [],
      dateRanges: [],
      visibleRanges: [],
      removed: false,
      crosshairPositions: [],
      clearedCrosshair: 0,
      addLineSeries(options) { const value = series('line', options, this); this.series.push(value); return value; },
      addAreaSeries(options) { const value = series('area', options, this); this.series.push(value); return value; },
      addCandlestickSeries(options) { const value = series('candlestick', options, this); this.series.push(value); return value; },
      removeSeries(value) { this.series = this.series.filter((item) => item !== value); log.push(['removeSeries', name]); },
      resize(width, height) { log.push(['resize', name, width, height]); },
      remove() { this.removed = true; log.push(['remove', name]); },
      timeScale() {
        return {
          subscribeVisibleLogicalRangeChange(fn) { visibleHandlers.add(fn); },
          unsubscribeVisibleLogicalRangeChange(fn) { visibleHandlers.delete(fn); },
          setVisibleLogicalRange(value) { object.ranges.push(value); log.push(['range', name, value]); },
          subscribeVisibleTimeRangeChange(fn) { timeRangeHandlers.add(fn); },
          unsubscribeVisibleTimeRangeChange(fn) { timeRangeHandlers.delete(fn); },
          setVisibleRange(value) {
            if (!object.series.some((item) => item.times?.size)) throw new Error(`Value is null: ${name} has no data`);
            const requested = {from: dateKey(value.from), to: dateKey(value.to)};
            const available = [...new Set(object.series.flatMap((item) => [...(item.times || [])]))].sort();
            const actual = strict && available.length
              ? {
                from: available.find((date) => date >= requested.from) || available[0],
                to: [...available].reverse().find((date) => date <= requested.to) || available.at(-1),
              }
              : value;
            object.dateRanges.push(value);
            object.visibleRanges.push(actual);
            log.push(['dateRange', name, value]);
          },
          fitContent() { log.push(['fit', name]); },
        };
      },
      subscribeCrosshairMove(fn) { crosshairHandlers.add(fn); },
      unsubscribeCrosshairMove(fn) { crosshairHandlers.delete(fn); },
      setCrosshairPosition(price, time, targetSeries) { object.crosshairPositions.push({price, time, targetSeries}); },
      clearCrosshairPosition() { object.clearedCrosshair += 1; },
      subscribeClick(fn) { clickHandlers.add(fn); },
      unsubscribeClick(fn) { clickHandlers.delete(fn); },
      emitCrosshair(value) { crosshairHandlers.forEach((fn) => fn(value)); },
      emitClick(value) { clickHandlers.forEach((fn) => fn(value)); },
      emitTimeRange(value) { timeRangeHandlers.forEach((fn) => fn(value)); },
      emitLogicalRange(value) { visibleHandlers.forEach(fn => fn(value)); },
    };
    return object;
  }
  return {
    charts: [],
    createChart(container, options) {
      const value = chart(this.charts.length ? 'drawdown' : 'main');
      value.container = container;
      value.options = options;
      this.charts.push(value);
      log.push(['create', value.name]);
      return value;
    },
  };
}

test('first close stays normalized to initial cash and preserves a real zero separately from null', () => {
  const h = charts();
  const rows = h.normalizeNavRows(navData().nav, 1000);
  assert.equal(rows[0].unit_nav, 1.05);
  assert.ok(Math.abs(rows[0].cumulative_return - 0.05) < 1e-12);
  assert.equal(rows[2].unit_nav, null);
  assert.equal(rows[3].unit_nav, 0);
  assert.equal(rows[3].cumulative_return, -1);
  const data = h.toMetricData(rows, 'unit_nav');
  assert.deepEqual(data.map((point) => [point.time, point.value]), [
    ['2025-01-02', 1.05], ['2025-01-03', 1], ['2025-01-06', null], ['2025-01-07', 0], ['2025-01-08', 1.1],
  ]);
  assert.equal(data.segments.length, 2);
  assert.equal(data.segments[0].length, 2);
  assert.equal(data.segments[1][0].value, 0);
});

test('weekly and monthly candles use observed close values and mark only a genuinely incomplete last period', () => {
  const h = charts();
  const data = navData();
  const weekly = h.aggregateObservedClose(data.nav, '1W', {
    initialCash: data.initial_cash,
    sessionList: data.capabilities.session_list,
    missingSessionDates: data.capabilities.missing_session_dates,
  });
  assert.equal(weekly.length, 2);
  assert.deepEqual(weekly[0], {
    time: '2025-01-03', date: '2025-01-03', period: '1W', periodKey: '2025-W01',
    periodStart: '2025-01-02', periodEnd: '2025-01-03', open: 1.05, high: 1.05, low: 1,
    close: 1, observedDates: ['2025-01-02', '2025-01-03'], missingDates: [], unknownDates: [],
    completeness: 'complete', isPartial: false, warning: null,
  });
  assert.equal(weekly[1].open, 0);
  assert.equal(weekly[1].close, 1.1);
  assert.equal(weekly[1].missingDates.includes('2025-01-09'), true);
  assert.equal(weekly[1].isPartial, true);
  const monthly = h.aggregateObservedClose(data.nav, '1M', {
    initialCash: data.initial_cash,
    sessionList: ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08'],
  });
  assert.equal(monthly.length, 1);
  assert.equal(monthly[0].open, 1.05);
  assert.equal(monthly[0].high, 1.1);
  assert.equal(monthly[0].low, 0);
  assert.equal(monthly[0].close, 1.1);
  assert.equal(monthly[0].isPartial, false);
});

test('producer trailing partial capability survives an exact session end and is visible in fallback warnings', () => {
  const h = charts();
  const data = navData();
  data.capabilities.valid_date_range = ['2025-01-02', '2025-01-08'];
  data.capabilities.session_list = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08'];
  data.capabilities.missing_session_dates = [];
  data.capabilities.aggregation = {
    weekly: { last_period_may_be_partial: true },
    monthly: { last_period_may_be_partial: true },
  };
  const weekly = h.aggregateObservedClose(data, '1W');
  assert.equal(weekly.at(-1).isPartial, true);
  assert.match(weekly.at(-1).warning, /末段未完整/);
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const controller = h.create(container, { data, initialCash: 1000, library: null });
  controller.update({ period: '1W' });
  const output = container.children.map((child) => child.innerHTML).join('\n');
  assert.match(output, /末段未完整/);
  assert.match(output, /可信交易日历|警告|缺口/);
});

test('date range and event grouping retain factual dates, fills, decisions, and stable IDs', () => {
  const h = charts();
  const data = navData();
  const filtered = h.filterDateRange(data.nav, { start: '2025-01-03', end: '2025-01-07' });
  assert.deepEqual(filtered.map((row) => row.date), ['2025-01-03', '2025-01-06', '2025-01-07']);
  const groups = h.groupEvents(data.events, '1D');
  assert.equal(groups.length, 2);
  assert.equal(groups[0].date, '2025-01-02');
  assert.deepEqual(groups[0].eventIds, ['buy-1', 'select-1']);
  assert.equal(groups[0].fills, 1);
  assert.equal(groups[0].decisions, 1);
  assert.equal(groups[1].date, '2025-01-09');
  assert.equal(groups[1].events[0].id, 'sell-1');
  const periodGroups = h.groupEvents(data.events, '1W');
  assert.equal(periodGroups.length, 2);
  assert.deepEqual(periodGroups[0].eventDates, ['2025-01-02']);
  assert.deepEqual(periodGroups[1].eventDates, ['2025-01-09']);
});

test('cumulative return remains line data and separate runs do not share transformed or controller state', () => {
  const h = charts();
  const first = h.toMetricData(navData().nav, 'cumulative_return', 1000);
  assert.equal(first.mode, 'line');
  assert.ok(Math.abs(first[0].value - 0.05) < 1e-12);
  const secondRows = [{ date: '2026-02-02', equity: 2000 }];
  const second = h.toMetricData(secondRows, 'unit_nav', 2000);
  assert.equal(second[0].value, 1);
  assert.ok(Math.abs(first[0].value - 0.05) < 1e-12);
});

test('controller uses a guarded shared time range, callbacks, bounded event groups, and cleanup', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const log = [];
  const library = fakeChartApi(log);
  const selected = [];
  const hovered = [];
  const errors = [];
  const controller = h.create(container, {
    data: navData(),
    initialCash: 1000,
    library,
    onSelectEvent: (event) => selected.push(event.id),
    onHover: (value) => hovered.push(value),
    onError: (error) => errors.push(error),
  });
  controller.update({ metric: 'cumulative_return', period: '1D', range: 'all' });
  assert.equal(library.charts.length, 2);
  assert.equal(library.charts[0].series[0].kind, 'line');
  assert.ok(Math.abs(library.charts[0].series[0].data[0].value - 0.05) < 1e-12);
  assert.equal(library.charts[0].series[0].options.priceFormat.formatter(0.05), '5.00%');
  assert.equal(controller.getEventGroups().length, 2);
  library.charts[0].emitCrosshair({ time: '2025-01-02' });
  assert.equal(hovered.length, 1);
  assert.equal(hovered[0].date, '2025-01-02');
  assert.equal(hovered[0].eventCount, 2);
  for (const field of ['unit_nav', 'equity', 'cumulative_return', 'daily_return', 'drawdown', 'events']) assert.ok(Object.prototype.hasOwnProperty.call(hovered[0], field), field);
  library.charts[1].emitCrosshair({ time: '2025-01-03' });
  assert.equal(library.charts[0].crosshairPositions.at(-1).time, '2025-01-03');
  library.charts[0].emitCrosshair({ time: '2025-01-02' });
  assert.equal(library.charts[1].crosshairPositions.at(-1).time, '2025-01-02');
  library.charts[0].emitClick({ eventId: 'buy-1' });
  assert.deepEqual(selected, ['buy-1']);
  controller.locateEvent('sell-1');
  assert.equal(controller.getState().selectedEventId, 'sell-1');
  assert.ok(log.some((item) => item[0] === 'range' || item[0] === 'dateRange'));
  assert.equal(errors.length, 0);
  controller.resize();
  controller.destroy();
  controller.destroy();
  assert.equal(library.charts.every((chart) => chart.removed), true);
});

test('grouped marker click returns the full period context for an accessible chooser', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const library = fakeChartApi([]);
  const selected = [];
  const controller = h.create(container, {
    data: navData(),
    initialCash: 1000,
    library,
    onSelectEvent: (event) => selected.push(event),
  });
  const group = controller.getEventGroups()[0];
  assert.equal(group.events.length, 2);
  library.charts[0].emitClick({marker: {id: group.id}, time: group.time});
  assert.equal(selected.length, 1);
  assert.equal(selected[0].type, 'group');
  assert.equal(selected[0].groupId, group.id);
  assert.deepEqual(selected[0].events.map((event) => event.id), group.eventIds);
  assert.deepEqual(selected[0].events.map((event) => event.date), ['2025-01-02', '2025-01-02']);
  controller.destroy();
});

test('dense adjacent event markers stay glyph-sized while retaining event groups', () => {
  const h = charts();
  const events = [
    {id: 'defer-sell', date: '2025-06-03', action: 'DEFER_SELL', action_label: '延后卖出', filled: false, symbol: '000001'},
    {id: 'sell-2', date: '2025-06-04', action: 'SELL', action_label: '卖出', filled: true, symbol: '000001'},
    {id: 'select-2', date: '2025-06-05', action: 'SELECT', action_label: '入选', filled: false, symbol: '000002'},
    {id: 'cash-2', date: '2025-06-06', action: 'CASH', action_label: '保留现金', filled: false, symbol: null},
  ];
  const groups = h.groupEvents(events, '1D');
  const markers = h.markersForEvents(groups, events.map((event) => event.date));

  assert.equal(markers.length, events.length);
  assert.deepEqual(markers.map((marker) => marker.text), ['', '卖▼1', '', '']);
  assert.deepEqual(markers.map((marker) => marker.title), ['延后卖出', '实际卖出 1 笔', '入选', '保留现金']);
  assert.deepEqual(markers.map((marker) => [marker.id, marker.shape, marker.position, marker.color]), [
    [groups[0].id, 'square', 'aboveBar', '#6b7280'],
    [groups[1].id, 'arrowDown', 'aboveBar', '#b42318'],
    [groups[2].id, 'square', 'aboveBar', '#6b7280'],
    [groups[3].id, 'square', 'aboveBar', '#b45309'],
  ]);
  assert.deepEqual(markers.map((marker) => marker.eventIds), events.map((event) => [event.id]));
});

function periodMarkerData() {
  return {
    run_id: 'run-period-markers',
    portfolio_id: 'strategy',
    initial_cash: 1000,
    scope: {valid_date_range: ['2025-01-06', '2025-01-31']},
    nav: [
      {date: '2025-01-06', equity: 1000, daily_return: 0, drawdown: 0},
      {date: '2025-01-07', equity: 1010, daily_return: 0.01, drawdown: 0},
      {date: '2025-01-10', equity: 1020, daily_return: 0.01, drawdown: 0},
      {date: '2025-01-13', equity: 1030, daily_return: 0.01, drawdown: 0},
      {date: '2025-01-31', equity: 1040, daily_return: 0.01, drawdown: 0},
    ],
    events: [
      {id: 'buy-period', date: '2025-01-06', action: 'BUY', action_label: '买入', filled: true, symbol: '000001'},
      {id: 'decision-period', date: '2025-01-07', action: 'SELECT', action_text: '入选', filled: false, symbol: '000002'},
      {id: 'sell-period', date: '2025-01-10', action: 'SELL', action_label: '卖出', filled: true, symbol: '000001'},
      {id: 'buy-month', date: '2025-01-13', action: 'BUY', action_label: '买入', filled: true, symbol: '000003'},
    ],
    capabilities: {
      session_list: ['2025-01-06', '2025-01-07', '2025-01-10', '2025-01-13', '2025-01-31'],
      valid_date_range: ['2025-01-06', '2025-01-31'],
      missing_session_dates: [],
    },
  };
}

test('period markers split buys and sells, preserve actual dates, and hide decisions unless requested', () => {
  const h = charts();
  const data = periodMarkerData();
  const groups = h.groupEvents(data.events.slice(0, 3), '1W');
  const hidden = h.markersForEvents(groups, ['2025-01-10'], {showDecisions: false});
  assert.equal(hidden.length, 2);
  assert.deepEqual(hidden.map((marker) => marker.text), ['买▲1', '卖▼1']);
  assert.deepEqual(hidden.map((marker) => marker.eventIds), [['buy-period'], ['sell-period']]);
  assert.deepEqual(hidden.map((marker) => marker.sourceDates), [['2025-01-06'], ['2025-01-10']]);
  assert.deepEqual(hidden.map((marker) => marker.groupId), [groups[0].id, groups[0].id]);
  const shown = h.markersForEvents(groups, ['2025-01-10'], {showDecisions: true});
  assert.equal(shown.length, 3);
  assert.equal(shown.filter((marker) => marker.decision).length, 1);
  assert.equal(shown.find((marker) => marker.decision).color, '#6b7280');
});

test('weekly hover covers every event in the period and mapped marker clicks target the clicked fill', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const log = [];
  const library = fakeChartApi(log);
  const hovered = [];
  const selected = [];
  const controller = h.create(container, {
    data: periodMarkerData(),
    initialCash: 1000,
    library,
    onHover: (value) => hovered.push(value),
    onSelectEvent: (value) => selected.push(value),
  });
  controller.update({period: '1W'});
  const candle = library.charts[0].series.find((series) => series.kind === 'candlestick');
  assert.ok(candle);
  const buyMarker = candle.markers.find((marker) => marker.eventIds.includes('buy-period'));
  const sellMarker = candle.markers.find((marker) => marker.eventIds.includes('sell-period'));
  assert.ok(buyMarker);
  assert.ok(sellMarker);
  assert.equal(buyMarker.time, '2025-01-10');
  assert.equal(sellMarker.time, '2025-01-10');
  assert.deepEqual(buyMarker.sourceDates, ['2025-01-06']);
  assert.deepEqual(sellMarker.sourceDates, ['2025-01-10']);
  library.charts[0].emitCrosshair({time: '2025-01-07'});
  assert.deepEqual(hovered.at(-1).events.map((event) => event.id), ['buy-period', 'decision-period', 'sell-period']);
  assert.deepEqual(hovered.at(-1).events.map((event) => event.date), ['2025-01-06', '2025-01-07', '2025-01-10']);
  assert.equal(hovered.at(-1).eventCount, 3);
  assert.equal(hovered.at(-1).bar.time, '2025-01-10');
  const beforeClickRanges = library.charts[0].dateRanges.length;
  library.charts[0].emitClick({marker: {id: buyMarker.id, eventId: buyMarker.eventId, eventIds: buyMarker.eventIds, groupId: buyMarker.groupId}, time: buyMarker.time});
  assert.equal(selected.length, 1);
  assert.equal(selected[0].id, 'buy-period');
  assert.equal(library.charts[0].dateRanges.length, beforeClickRanges, 'clicking chart marker must pin details without changing zoom');
  controller.destroy();
});

test('multiple benchmark lines rebase to the first portfolio NAV and sample weekly bars by last observed date', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const log = [];
  const library = fakeChartApi(log);
  const data = periodMarkerData();
  const controller = h.create(container, {
    data,
    initialCash: 1000,
    library,
    benchmarks: [
      {id: 'index-a', name: '指数 A', color: '#7c3aed', rows: [
        {date: '2025-01-06', unit_nav: 1},
        {date: '2025-01-07', unit_nav: 1.01},
        {date: '2025-01-10', unit_nav: 1.02},
        {date: '2025-01-13', unit_nav: 1.03},
        {date: '2025-01-31', unit_nav: 1.04},
      ]},
      {id: 'index-b', name: '指数 B', color: '#d97706', rows: [
        {date: '2025-01-06', unit_nav: 1},
        {date: '2025-01-10', unit_nav: 0.98},
        {date: '2025-01-31', unit_nav: 1.05},
      ]},
    ],
  });
  controller.update({period: '1W'});
  const lines = library.charts[0].series.filter((series) => series.kind === 'line' && series.options.visible !== false);
  assert.equal(lines.length, 2);
  assert.deepEqual(lines.map((series) => series.options.color), ['#7c3aed', '#d97706']);
  assert.deepEqual(lines[0].data.map((point) => point.time), ['2025-01-10', '2025-01-13', '2025-01-31']);
  assert.deepEqual(lines[0].data.map((point) => point.value), [1.02, 1.03, 1.04]);
  assert.deepEqual(lines[1].data.map((point) => point.time), ['2025-01-10', '2025-01-13', '2025-01-31']);
  assert.deepEqual(lines[1].data.map((point) => point.value), [0.98, 0.98, 1.05]);
  controller.destroy();
});

test('weekly main candles and daily drawdown synchronize by factual dates and apply all/bounded ranges', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const log = [];
  const errors = [];
  const library = fakeChartApi(log);
  const controller = h.create(container, { data: navData(), initialCash: 1000, library, onError: (error) => errors.push(error) });
  assert.deepEqual(library.charts[0].ranges[0], library.charts[1].ranges[0]);
  assert.ok(library.charts[0].ranges[0].from < 0);
  controller.update({ period: '1W' });
  const grids = library.charts.map(chart => chart.series.find(series => series.options.visible === false));
  assert.deepEqual(grids[0].data, grids[1].data);
  library.charts[1].emitCrosshair({ time: '2025-01-06' });
  assert.equal(library.charts[0].crosshairPositions.at(-1).time, '2025-01-06');
  library.charts[0].emitLogicalRange({from:-1,to:8});
  assert.deepEqual(library.charts[1].ranges.at(-1), {from:-1,to:8});
  controller.update({ range: { start: '2025-01-03', end: '2025-01-07' } });
  assert.deepEqual(library.charts[0].ranges.at(-1), library.charts[1].ranges.at(-1));
  assert.deepEqual(controller.getState().range, {start:'2025-01-03',end:'2025-01-07'});
  assert.equal(errors.length, 0);
  controller.destroy();
});

test('successive custom ranges use distinct canonical event groups on one controller', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const controller = h.create(container, { data: navData(), initialCash: 1000, library: null });
  controller.update({ range: { start: '2025-01-01', end: '2025-01-03' } });
  assert.deepEqual(controller.getEventGroups()[0].eventIds, ['buy-1', 'select-1']);
  controller.update({ range: { start: '2025-01-08', end: '2025-01-10' } });
  assert.deepEqual(controller.getEventGroups().map((group) => group.eventIds), [['sell-1']]);
  controller.destroy();
});

test('locateEvent expands a bounded range before positioning an out-of-range factual event', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const log = [];
  const library = fakeChartApi(log, {strict: true});
  const errors = [];
  const controller = h.create(container, {data: longNavData(), initialCash: 1000, library, onError: (error) => errors.push(error)});
  controller.update({range: '1Y'});
  assert.equal(controller.getState().range, '1Y');
  controller.locateEvent('buy-1');
  assert.equal(controller.getState().range, 'all');
  assert.equal(controller.getState().selectedEventId, 'buy-1');
  for (const chart of library.charts) {
    const visible = chart.visibleRanges.at(-1);
    assert.ok(visible.from <= '2023-01-03' && visible.to >= '2023-01-03');
    assert.ok(visible.to > visible.from);
  }
  assert.equal(errors.length, 0);
  controller.destroy();
});

test('period locate keeps an early event and its mapped close candle in the visible window', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const log = [];
  const library = fakeChartApi(log, {strict: true});
  const controller = h.create(container, {data: earlyPeriodData(), initialCash: 1000, library});
  const expectedClose = { '1W': '2023-01-06', '1M': '2023-01-31' };
  for (const period of ['1W', '1M']) {
    controller.update({period, metric: 'unit_nav', range: '1Y'});
    controller.locateEvent('buy-1');
    const candle = library.charts[0].series.find((series) => series.kind === 'candlestick');
    const marker = candle.markers.find((item) => item.eventIds.includes('buy-1'));
    const visible = library.charts[0].visibleRanges.at(-1);
    assert.ok(marker, period);
    assert.equal(marker.time, expectedClose[period]);
    assert.equal(marker.sourceDate, '2023-01-03');
    assert.ok(candle.data.some((point) => point.time === expectedClose[period] && point.open !== undefined), period);
    assert.ok(visible.from <= expectedClose[period] && expectedClose[period] <= visible.to, period);
    assert.ok(visible.from <= '2023-01-03' && '2023-01-03' <= visible.to, period);
  }
  controller.destroy();
});

test('cumulative return line keeps grouped markers in weekly and monthly modes', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const log = [];
  const library = fakeChartApi(log);
  const controller = h.create(container, {data: navData(), initialCash: 1000, library});
  for (const period of ['1W', '1M']) {
    controller.update({period, metric: 'cumulative_return'});
    const mainLines = library.charts[0].series.filter((series) => series.kind === 'line');
    assert.ok(mainLines.some((series) => series.markers.some((marker) => marker.eventIds.includes('buy-1'))), period);
    assert.ok(mainLines.every((series) => series.markers.every((marker) => series.data.some((point) => point.time === marker.time))), period);
  }
  controller.destroy();
});

test('native marker sets are sorted, series-time compatible, and period markers retain factual event dates', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const log = [];
  const errors = [];
  const data = navData();
  data.nav[1].stale_symbols = '000001';
  data.nav[1].max_valuation_stale_days = 2;
  const library = fakeChartApi(log);
  const selected = [];
  const controller = h.create(container, { data, initialCash: 1000, library, onError: (error) => errors.push(error), onSelectEvent: (event) => selected.push(event) });
  controller.update({ period: '1W' });
  const candle = library.charts[0].series.find((series) => series.kind === 'candlestick');
  assert.ok(candle);
  assert.deepEqual(candle.data.filter((point) => point.open !== undefined).map((point) => point.time), ['2025-01-03']);
  assert.equal(candle.data.some((point) => point.time === '2025-01-08' && point.open === undefined), false);
  assert.equal(candle.markers.every((marker, index) => index === 0 || marker.time >= candle.markers[index - 1].time), true);
  assert.equal(candle.markers.every((marker) => candle.data.some((point) => point.time === marker.time)), true);
  const warning = candle.markers.find((marker) => marker.id.startsWith('warning-') && marker.sourceDate === '2025-01-08');
  assert.ok(warning);
  assert.equal(warning.text, '');
  assert.equal(warning.title, '末段未完整');
  const stale = candle.markers.find((marker) => marker.id === 'stale-2025-01-03');
  assert.ok(stale);
  assert.equal(stale.text, '');
  assert.equal(stale.title, '估值陈旧');
  library.charts[0].emitClick({hoveredObjectId: stale.id, time: stale.time});
  assert.equal(selected.length, 0);
  const marker = candle.markers.find((item) => item.eventIds.includes('buy-1'));
  assert.equal(marker.sourceDate, '2025-01-02');
  assert.equal(candle.markers.some((item) => item.eventIds.includes('sell-1')), false);
  assert.equal(errors.length, 0);
  controller.destroy();
});

test('offline fallback renders gaps, values, and event choices when the chart library is unavailable', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const controller = h.create(container, { data: navData(), initialCash: 1000, library: null });
  controller.update({ metric: 'unit_nav', period: '1D', range: 'all' });
  const output = container.children.map((child) => child.innerHTML).join('\n');
  assert.match(output, /2025-01-02/);
  assert.match(output, /1\.05/);
  assert.match(output, /缺失|未知|空值/);
  assert.match(output, /buy-1|sell-1/);
  assert.match(output, /买入|卖出|入选/);
  assert.doesNotMatch(output, /DEFER_SELL|INITIAL_NOT_FILLED/);
  assert.doesNotMatch(output, /NaN|undefined/);
});

test('large local fixture does not rebuild chart series for repeated hover events', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const log = [];
  const library = fakeChartApi(log);
  const nav = [];
  const events = [];
  for (let index = 0; index < 5000; index += 1) {
    const date = new Date(Date.UTC(2010, 0, 1 + index)).toISOString().slice(0, 10);
    nav.push({date, equity: 100000 + index});
    if (index < 1000) events.push({id: `event-${index}`, date, action: index % 2 ? 'SELECT' : 'BUY', filled: index % 2 === 0, symbol: '000001'});
  }
  const controller = h.create(container, {data: {nav, events, capabilities: {session_list: nav.map((row) => row.date)}}, initialCash: 100000, library});
  const renders = controller.getState().renderCount;
  for (let index = 0; index < 100; index += 1) library.charts[0].emitCrosshair({time: nav[index].date});
  assert.equal(controller.getState().renderCount, renders);
  assert.equal(controller.getEventGroups().length, 1000);
  controller.destroy();
});

test('large weekly and monthly hovers reuse cached period aggregation and date indexes', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const library = fakeChartApi([]);
  const nav = [];
  const events = [];
  for (let index = 0; index < 5000; index += 1) {
    const date = new Date(Date.UTC(2010, 0, 1 + index)).toISOString().slice(0, 10);
    nav.push({date, equity: 100000 + index});
    if (index < 1000) events.push({id: `period-event-${index}`, date, action: index % 2 ? 'SELECT' : 'BUY', filled: index % 2 === 0, symbol: '000001'});
  }
  const controller = h.create(container, {data: {nav, events, capabilities: {session_list: nav.map((row) => row.date)}}, initialCash: 100000, library});
  for (const period of ['1W', '1M']) {
    controller.update({period});
    const before = controller.getState().periodAggregationCount;
    const renders = controller.getState().renderCount;
    for (let index = 0; index < 100; index += 1) library.charts[0].emitCrosshair({time: nav[index].date});
    assert.equal(controller.getState().periodAggregationCount, before);
    assert.equal(controller.getState().renderCount, renders);
  }
  assert.equal(controller.getState().periodAggregationCount, 2);
  controller.destroy();
});

test('local named chart assets are present and vendor bundle is the locked 4.2.3 standalone build', () => {
  const source = fs.readFileSync(modulePath, 'utf8');
  const vendorPath = path.resolve('alphalab/research/static/vendor/lightweight-charts.standalone.production.js');
  const vendor = fs.readFileSync(vendorPath, 'utf8');
  assert.match(source, /PortfolioReviewCharts/);
  assert.match(vendor, /Lightweight Charts.*v4\.2\.3/);
  assert.ok(fs.statSync(path.resolve('alphalab/research/static/vendor/LICENSE')).size > 0);
  assert.ok(fs.statSync(path.resolve('alphalab/research/static/vendor/NOTICE')).size > 0);
});

test('full-history daily view permits all rows and adds visible logical margins', () => {
  const library = fakeChartApi([]);
  const create = library.createChart;
  library.createChart = function(container, options) {
    const chart = create.call(this, container, options);
    const scale = chart.timeScale();
    scale.getVisibleLogicalRange = () => ({from:0, to:706});
    chart.timeScale = () => scale;
    return chart;
  };
  const container = fakeElement(); container.ownerDocument = fakeDocument();
  const nav=Array.from({length:707},(_,i)=>({date:new Date(Date.UTC(2023,0,3+i)).toISOString().slice(0,10),equity:1000+i,drawdown:0}));
  const controller = charts().create(container,{data:{nav,initial_cash:1000,events:[]},library});
  assert.ok(library.charts[0].options.timeScale.minBarSpacing <= 0.5);
  for (const chart of library.charts) {
    const range=chart.ranges.at(-1);
    assert.ok(range.from < -10);
    assert.ok(range.to > 716);
  }
  controller.destroy();
});

test('weekly return view uses the same period endpoints as its benchmark and marker', () => {
  const library=fakeChartApi([]);
  const container=fakeElement();container.ownerDocument=fakeDocument();
  const controller=charts().create(container,{data:periodMarkerData(),library,period:'1W',metric:'cumulative_return'});
  const main=library.charts[0].series[0];
  const times=main.data.filter(p=>p.value!==undefined).map(p=>p.time);
  assert.equal(times.includes('2025-01-07'),false);
  assert.ok(main.markers.some(m=>m.eventIds.includes('buy-period') && m.time==='2025-01-10'));
  controller.destroy();
});
