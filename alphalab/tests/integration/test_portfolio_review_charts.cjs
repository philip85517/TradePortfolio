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

test('weekly main candles and daily drawdown synchronize by factual dates and apply all/bounded ranges', () => {
  const h = charts();
  const document = fakeDocument();
  const container = fakeElement();
  container.ownerDocument = document;
  const log = [];
  const errors = [];
  const library = fakeChartApi(log);
  const controller = h.create(container, { data: navData(), initialCash: 1000, library, onError: (error) => errors.push(error) });
  const initialFits = log.filter((item) => item[0] === 'fit');
  assert.equal(initialFits.length, 2);
  assert.deepEqual(library.charts[0].dateRanges[0], {from: '2025-01-02', to: '2025-01-10'});
  assert.deepEqual(library.charts[1].dateRanges[0], {from: '2025-01-02', to: '2025-01-10'});
  controller.update({ period: '1W' });
  library.charts[1].emitCrosshair({ time: '2025-01-06' });
  assert.equal(library.charts[0].crosshairPositions.at(-1).time, '2025-01-06');
  library.charts[0].emitTimeRange({ from: '2025-01-02', to: '2025-01-08' });
  assert.deepEqual(library.charts[1].dateRanges.at(-1), { from: '2025-01-02', to: '2025-01-08' });
  controller.update({ range: { start: '2025-01-03', end: '2025-01-07' } });
  assert.deepEqual(library.charts[0].dateRanges.at(-1), { from: '2025-01-03', to: '2025-01-07' });
  assert.deepEqual(library.charts[1].dateRanges.at(-1), { from: '2025-01-03', to: '2025-01-07' });
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
  const library = fakeChartApi(log);
  const controller = h.create(container, { data: navData(), initialCash: 1000, library, onError: (error) => errors.push(error) });
  controller.update({ period: '1W' });
  const candle = library.charts[0].series.find((series) => series.kind === 'candlestick');
  assert.ok(candle);
  assert.deepEqual(candle.data.filter((point) => point.open !== undefined).map((point) => point.time), ['2025-01-03']);
  assert.ok(candle.data.some((point) => point.time === '2025-01-08' && point.open === undefined));
  assert.equal(candle.markers.every((marker, index) => index === 0 || marker.time >= candle.markers[index - 1].time), true);
  assert.equal(candle.markers.every((marker) => candle.data.some((point) => point.time === marker.time)), true);
  assert.ok(candle.markers.some((marker) => marker.id.startsWith('warning-') && marker.sourceDate === '2025-01-08'));
  const marker = candle.markers.find((item) => item.eventIds.includes('sell-1'));
  assert.equal(marker.sourceDate, '2025-01-09');
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

test('local named chart assets are present and vendor bundle is the locked 4.2.3 standalone build', () => {
  const source = fs.readFileSync(modulePath, 'utf8');
  const vendorPath = path.resolve('alphalab/research/static/vendor/lightweight-charts.standalone.production.js');
  const vendor = fs.readFileSync(vendorPath, 'utf8');
  assert.match(source, /PortfolioReviewCharts/);
  assert.match(vendor, /Lightweight Charts.*v4\.2\.3/);
  assert.ok(fs.statSync(path.resolve('alphalab/research/static/vendor/LICENSE')).size > 0);
  assert.ok(fs.statSync(path.resolve('alphalab/research/static/vendor/NOTICE')).size > 0);
});
