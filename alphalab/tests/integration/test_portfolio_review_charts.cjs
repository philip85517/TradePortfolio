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
      { id: 'buy-1', date: '2025-01-02', action: 'BUY', filled: true, symbol: '000001', shares: 10 },
      { id: 'select-1', date: '2025-01-02', action: 'SELECT', filled: false, symbol: '000002' },
      { id: 'sell-1', date: '2025-01-09', action: 'SELL', filled: true, symbol: '000001', shares: 10 },
    ],
    capabilities: {
      session_list: ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08', '2025-01-09', '2025-01-10'],
      valid_date_range: ['2025-01-02', '2025-01-10'],
      missing_session_dates: ['2025-01-09', '2025-01-10'],
    },
  };
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

function fakeChartApi(log) {
  function series(kind) {
    return {
      kind,
      data: [],
      markers: [],
      setData(value) { this.data = value; log.push(['setData', kind, value.length]); },
      setMarkers(value) { this.markers = value; log.push(['setMarkers', kind, value.length]); },
      applyOptions(value) { log.push(['applyOptions', kind, value]); },
    };
  }
  function chart(name) {
    const visibleHandlers = new Set();
    const crosshairHandlers = new Set();
    const clickHandlers = new Set();
    const object = {
      name,
      series: [],
      ranges: [],
      removed: false,
      addLineSeries() { const value = series('line'); this.series.push(value); return value; },
      addAreaSeries() { const value = series('area'); this.series.push(value); return value; },
      addCandlestickSeries() { const value = series('candlestick'); this.series.push(value); return value; },
      removeSeries(value) { this.series = this.series.filter((item) => item !== value); log.push(['removeSeries', name]); },
      resize(width, height) { log.push(['resize', name, width, height]); },
      remove() { this.removed = true; log.push(['remove', name]); },
      timeScale() {
        return {
          subscribeVisibleLogicalRangeChange(fn) { visibleHandlers.add(fn); },
          unsubscribeVisibleLogicalRangeChange(fn) { visibleHandlers.delete(fn); },
          setVisibleLogicalRange(value) { object.ranges.push(value); log.push(['range', name, value]); },
          setVisibleRange(value) { object.ranges.push(value); log.push(['dateRange', name, value]); },
          fitContent() { log.push(['fit', name]); },
        };
      },
      subscribeCrosshairMove(fn) { crosshairHandlers.add(fn); },
      unsubscribeCrosshairMove(fn) { crosshairHandlers.delete(fn); },
      subscribeClick(fn) { clickHandlers.add(fn); },
      unsubscribeClick(fn) { clickHandlers.delete(fn); },
      emitCrosshair(value) { crosshairHandlers.forEach((fn) => fn(value)); },
      emitClick(value) { clickHandlers.forEach((fn) => fn(value)); },
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
  const controller = h.create(container, {
    data: navData(),
    initialCash: 1000,
    library,
    onSelectEvent: (event) => selected.push(event.id),
    onHover: (value) => hovered.push(value),
  });
  controller.update({ metric: 'cumulative_return', period: '1D', range: 'all' });
  assert.equal(library.charts.length, 2);
  assert.equal(library.charts[0].series[0].kind, 'line');
  assert.ok(Math.abs(library.charts[0].series[0].data[0].value - 0.05) < 1e-12);
  assert.equal(controller.getEventGroups().length, 2);
  library.charts[0].emitCrosshair({ time: '2025-01-02' });
  assert.equal(hovered.length, 1);
  assert.equal(hovered[0].date, '2025-01-02');
  assert.equal(hovered[0].eventCount, 2);
  library.charts[0].emitClick({ eventId: 'buy-1' });
  assert.deepEqual(selected, ['buy-1']);
  controller.locateEvent('sell-1');
  assert.equal(controller.getState().selectedEventId, 'sell-1');
  assert.ok(log.some((item) => item[0] === 'range' || item[0] === 'dateRange'));
  controller.resize();
  controller.destroy();
  controller.destroy();
  assert.equal(library.charts.every((chart) => chart.removed), true);
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

test('local named chart assets are present and vendor bundle is the locked 4.2.3 standalone build', () => {
  const source = fs.readFileSync(modulePath, 'utf8');
  const vendorPath = path.resolve('alphalab/research/static/vendor/lightweight-charts.standalone.production.js');
  const vendor = fs.readFileSync(vendorPath, 'utf8');
  assert.match(source, /PortfolioReviewCharts/);
  assert.match(vendor, /Lightweight Charts.*v4\.2\.3/);
  assert.ok(fs.statSync(path.resolve('alphalab/research/static/vendor/LICENSE')).size > 0);
  assert.ok(fs.statSync(path.resolve('alphalab/research/static/vendor/NOTICE')).size > 0);
});
