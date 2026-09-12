const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const ui = require(path.resolve('alphalab/research/static/portfolio-review.js'));

function sampleReview(overrides = {}) {
  const horizon = {
    summary: {
      total_return: -0.3784161991796283,
      profit_loss: -37841.61991796283,
      ending_equity: 62158.38008203717,
      initial_cash: 100000,
      max_drawdown: -0.5242825712289458,
      annualized_return: null,
      commission_paid: 38.21314266741137,
      slippage_paid: 63.67913618044251,
      cash_residual: 62158.38008203717,
      liquidation_status: 'LIQUIDATED',
      status: 'COMPLETE',
    },
    status: { code: 'COMPLETE_LIQUIDATED', label: '完成且清算', explanation: '冻结证据显示期末持仓已全部清算。' },
    nav: [
      { date: '2025-06-12', equity: 100000, unit_nav: 1, cumulative_return: 0, drawdown: 0 },
      { date: '2025-06-13', equity: 90000, unit_nav: 0.9, cumulative_return: -0.1, drawdown: -0.1 },
      { date: '2025-12-03', equity: 62158.38008203717, unit_nav: 0.6215838008203717, cumulative_return: -0.3784161991796283, drawdown: -0.5242825712289458 },
    ],
    events: [
      { id: 'initial-1', date: '2023-01-03', action: 'BUY', filled: true, symbol: '000001', name: '平安银行', shares: 100, price: 10, reason_code: 'initial_entry', source_kind: 'derived_frozen_entry' },
      { id: 'defer-1', date: '2025-06-09', action: 'DEFER_SELL', filled: false, symbol: '002336', reason_code: 'before_trading_resumes' },
      { id: 'defer-2', date: '2025-06-10', action: 'DEFER_SELL', filled: false, symbol: '002336', reason_code: 'before_trading_resumes' },
      { id: 'sell-1', event_id: 'cninfo:002336:2025-039', date: '2025-06-13', action: 'SELL', filled: true, symbol: '002336', name: '人乐退', shares: 500, price: 0.609695, commission: 0.09145425, slippage: 0.1525, net_cash: 304.75604575, chain_id: 'chain-1', reason_code: 'termination_decision', source_url: 'https://example.com/event.pdf', price_basis_label: '研究总回报价格' },
      { id: 'select-1', date: '2025-06-13', action: 'SELECT', filled: false, symbol: '300204', chain_id: 'chain-1', reason_code: 'replacement_selection', rank_cutoff: '2025-06-13', budget: 304.75604575 },
      { id: 'cash-1', date: '2025-06-16', action: 'CASH', filled: false, symbol: '300204', chain_id: 'chain-1', reason_code: 'lot_budget', budget: 304.75604575 },
      { id: 'terminal-1', date: '2025-12-03', action: 'SELL', filled: true, symbol: '000001', shares: 100, price: 8.2, reason_code: 'terminal' },
      { id: 'unsafe', date: '2025-12-03', action: 'SELECT', filled: false, symbol: '=FORMULA', name: '<script>alert(1)</script>', reason_code: 'x<y' },
    ],
    chains: [{ chain_id: 'chain-1', trigger_event_id: 'cninfo:002336:2025-039', event_ids: ['defer-1', 'defer-2', 'sell-1', 'select-1', 'cash-1'], start_date: '2025-06-09', end_date: '2025-06-16', summary: '正式退市决定公开 → 复牌前等待 → 退出 → 预算不足一手，保留现金' }],
    initial_holdings: [{ symbol: '000001', name: '平安银行', shares: 100, entry_date: '2023-01-03', entry_price: 10, source: 'frozen_portfolio' }],
    ending_holdings: [],
    capabilities: { daily_nav: true, weekly_monthly_aggregation: true, comparable_frozen_benchmark: false },
    unavailable_reasons: ['部分事件缺少冻结成本或成交证据'],
    initial_cash: 100000,
    ending_cash: 62158.38008203717,
    known_assets_value: 62158.38008203717,
    unsettled_symbols: [],
    transaction_count: 3,
  };
  return {
    schema_version: 1,
    run_id: 'research-test-run',
    portfolio_id: 'strategy',
    name: '测试组合',
    initial_cash: 100000,
    scope: { requested_start_date: '2023-01-01', requested_end_date: '2025-12-03', actual_date_range: ['2023-01-03', '2025-12-03'], valid_date_range: ['2023-01-03', '2025-12-03'] },
    quality_mode: 'strict',
    horizons: [706],
    by_horizon: { '706': { ...horizon, ...overrides } },
  };
}

function observableContainer() {
  const regions = new Map();
  const listeners = new Map();
  const container = {
    ownerDocument: null,
    _html: '',
    get innerHTML() { return this._html; },
    set innerHTML(value) { this._html = String(value ?? ''); },
    addEventListener(type, handler) { listeners.set(type, handler); },
    dispatch(type, target) { listeners.get(type)?.({type, target, preventDefault() {}}); },
    querySelector(selector) {
      const region = selector.match(/^\[data-review-region="([^"]+)"\]$/)?.[1];
      if (region) {
        if (!regions.has(region)) regions.set(region, {innerHTML: '', textContent: ''});
        return regions.get(region);
      }
      if (selector === '[data-review-chart-note]') {
        if (!regions.has('chart-note')) regions.set('chart-note', {innerHTML: '', textContent: ''});
        return regions.get('chart-note');
      }
      if (selector === '[data-review-group-event]') return {focus() {}};
      return null;
    },
    querySelectorAll() { return []; },
    region(name) { return regions.get(name)?.innerHTML || ''; },
  };
  return container;
}

function targetFor(attributes = {}) {
  return {
    closest(selector) {
      if (selector === '[data-review-group-event]' && attributes.groupEvent) return this;
      if (selector === '[data-review-action]' && attributes.action) return this;
      if (selector === '[data-review-event]' && attributes.event) return this;
      return null;
    },
    getAttribute(name) {
      if (name === 'data-review-group-event') return attributes.groupEvent || null;
      if (name === 'data-review-action') return attributes.action || null;
      if (name === 'data-review-event') return attributes.event || null;
      return null;
    },
  };
}

test('normalizes detail and fallback identity while keeping null metrics distinct from zero', () => {
  const model = ui.normalizeReviewPayload({ review: sampleReview() }, { horizon: 706 });
  assert.equal(model.runId, 'research-test-run');
  assert.equal(model.portfolioId, 'strategy');
  assert.equal(model.horizon, '706');
  assert.equal(model.data.nav[0].cumulative_return, 0);
  assert.equal(ui.formatMissing(null, '冻结摘要未保存年化收益'), '冻结摘要未保存年化收益');
  assert.equal(ui.formatMissing(0, '不会使用'), '0');
});

test('fallback selects the frozen manifest portfolio and horizon instead of primary summary', () => {
  const payload = {
    run_id: 'run-scoped-fallback',
    portfolio_id: 'primary',
    summary: {horizon: 21, total_return: 0.1, status: 'COMPLETE', liquidation_status: 'LIQUIDATED'},
    manifest: {
      run_id: 'run-scoped-fallback',
      portfolios: [{portfolio_id: 'primary', name: '主组合'}, {portfolio_id: 'alternate', name: '替代组合'}],
      portfolio_performance: {
        primary: {21: {horizon: 21, total_return: 0.1, status: 'COMPLETE', liquidation_status: 'LIQUIDATED', open_positions: {}}},
        alternate: {
          21: {horizon: 21, total_return: 0.2, status: 'COMPLETE', liquidation_status: 'LIQUIDATED', open_positions: {}},
          42: {horizon: 42, total_return: 0.9, status: 'COMPLETE', liquidation_status: 'OPEN_POSITION', open_positions: {"000002": 10}},
        },
      },
    },
  };
  const model = ui.normalizeReviewPayload(payload, {fallback: true, portfolioId: 'alternate', horizon: 42});
  assert.equal(model.portfolioId, 'alternate');
  assert.equal(model.horizon, '42');
  assert.equal(model.data.summary.total_return, 0.9);
  assert.equal(model.data.status.code, 'COMPLETE_OPEN');
  assert.equal(model.data.ending_holdings[0].symbol, '000002');
});

test('fallback leaves legacy completion liquidation status unknown without affirmative evidence', () => {
  const model = ui.normalizeReviewPayload({
    run_id: 'legacy-run',
    portfolio_id: 'strategy',
    horizons: [706],
    summary: {horizon: 706, total_return: 0.1, status: 'COMPLETE'},
  }, {fallback: true, portfolioId: 'strategy', horizon: 706});
  assert.notEqual(model.data.status.code, 'COMPLETE_LIQUIDATED');
});

test('fallback keeps nonempty ending holdings from being labelled liquidated by evidence alone', () => {
  const model = ui.normalizeReviewPayload({
    run_id: 'legacy-open-ending-run',
    portfolio_id: 'strategy',
    horizons: [706],
    summary: {horizon: 706, status: 'COMPLETE', capabilities: {ending_holdings_evidence: true}},
    nav: [{horizon: 706, date: '2025-12-03', equity: 100}],
    ending_holdings: [{symbol: '000001', shares: 10}],
  }, {fallback: true, portfolioId: 'strategy', horizon: 706});
  assert.equal(model.data.status.code, 'COMPLETE');
});

test('event filters and factual summary separate fills, decisions, deferred attempts, and terminal exits', () => {
  const data = sampleReview().by_horizon['706'];
  assert.equal(ui.eventIsFill(data.events[0]), true);
  assert.equal(ui.eventIsFill(data.events[1]), false);
  assert.equal(ui.filterEvents(data.events, { symbol: '002336' }).length, 3);
  assert.equal(ui.filterEvents(data.events, { action: 'SELL', dateStart: '2025-06-13', dateEnd: '2025-06-13' }).length, 1);
  const facts = ui.factualSummary(data);
  assert.match(facts.headline, /初始实际持仓/);
  assert.match(facts.lines.join(' '), /退市|保留现金/);
  assert.equal(facts.initialFills, 1);
  assert.equal(facts.terminalExits, 1);
  assert.equal(facts.strategyFills, 1);
});

test('CSV exports carry frozen identity and filter scope, guard free text, and retain numeric negatives', () => {
  const review = sampleReview();
  const data = review.by_horizon['706'];
  const nav = ui.buildNavCsv(review, data, { scope: 'all' });
  assert.match(nav, /research-test-run/);
  assert.match(nav, /strategy/);
  assert.match(nav, /horizon=706/);
  assert.match(nav, /-0\.3784161991796283/);
  const events = ui.buildEventCsv(review, data, data.events.slice(-1), { scope: 'filtered' });
  assert.match(events, /scope=filtered/);
  assert.match(events, /'=FORMULA/);
  assert.match(events, /<script>alert\(1\)<\/script>/);
  assert.doesNotMatch(events, /'-0\.1525/);
});

test('review controller keeps full-run summary stable while chart range changes and supports selection clearing', () => {
  const container = {
    innerHTML: '',
    ownerDocument: null,
    appendChild() {},
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  const controller = ui.create(container, { data: sampleReview(), charts: null });
  const before = controller.getModel().data.summary.total_return;
  controller.update({ range: '1Y' });
  assert.equal(controller.getModel().data.summary.total_return, before);
  controller.selectEvent('sell-1');
  assert.equal(controller.getState().selectedEventId, 'sell-1');
  controller.clearSelection();
  assert.equal(controller.getState().selectedEventId, null);
  controller.destroy();
});

test('detail payload replaces summary fallback chart when identity is unchanged', () => {
  const regions = new Map();
  const container = {
    ownerDocument: null,
    addEventListener() {},
    querySelector(selector) {
      const region = selector.match(/^\[data-review-region="([^"]+)"\]$/)?.[1];
      if (region) {
        if (!regions.has(region)) regions.set(region, { innerHTML: '', textContent: '' });
        return regions.get(region);
      }
      if (selector === '[data-review-chart-note]') {
        if (!regions.has('chart-note')) regions.set('chart-note', { innerHTML: '', textContent: '' });
        return regions.get('chart-note');
      }
      return null;
    },
    querySelectorAll() { return []; },
  };
  let creates = 0;
  let destroys = 0;
  const charts = {
    create() {
      creates += 1;
      return {
        update() {},
        destroy() { destroys += 1; },
        getState() { return { range: 'all' }; },
      };
    },
  };
  const summaryResult = { run_id: 'research-test-run', portfolio_id: 'strategy', summary: { horizon: 706, status: 'COMPLETE' } };
  const controller = ui.create(container, { data: summaryResult, charts });
  assert.equal(controller.getModel().data.nav.length, 0);
  assert.equal(creates, 1);
  controller.setData(sampleReview(), { portfolioId: 'strategy', horizon: 706 });
  assert.equal(controller.getModel().data.nav.length, 3);
  assert.equal(creates, 2);
  assert.equal(destroys, 1);
  controller.destroy();
});

test('DOM review lifecycle exposes evidence chronology, selected side detail, grouped chooser, and money units', () => {
  const container = observableContainer();
  let chartOptions;
  const charts = {
    create(_host, options) {
      chartOptions = options;
      return {update() {}, destroy() {}, getState() { return {range: 'all'}; }};
    },
  };
  const controller = ui.create(container, {data: sampleReview(), charts});
  assert.match(container.region('metrics'), /盈亏金额（元）/);
  chartOptions.onHover({
    date: '2025-06-13',
    value: 0.9,
    eventCount: 2,
    events: [
      {action: 'SELL', action_label: '卖出'},
      {action: 'SELECT', action_label: '入选'},
    ],
    row: {stale_symbols: '000001'},
    bar: {warning: '末段未完整'},
  });
  const chartNote = container.querySelector('[data-review-chart-note]').textContent;
  assert.match(chartNote, /蓝色上箭头=买入/);
  assert.match(chartNote, /点击图标查看事件/);
  assert.match(chartNote, /事件：卖出、入选/);
  assert.match(chartNote, /末段未完整/);
  assert.match(chartNote, /估值陈旧：000001/);
  controller.setEvidence({spec: {wizard_metadata: {delisting_events: [{
    event_id: 'cninfo:002336:2025-039', published_at: '2025-06-06', trading_resumes_on: '2025-06-13',
    delisted_date: '2025-07-04', source_title: '正式退市决定公告', evidence_note: '首个可成交日来自冻结公告。',
  }]}}});
  controller.selectEvent('sell-1');
  assert.match(container.region('details'), /data-review-selected-detail/);
  assert.match(container.region('details'), /公告日期/);
  assert.match(container.region('details'), /2025-06-06/);
  assert.match(container.region('details'), /公告复牌日/);
  assert.doesNotMatch(container.region('details'), /portfolio-review-event-body/);

  const firstGroup = chartOptions.onSelectEvent;
  firstGroup({type: 'group', groupId: 'event-group-test', period: '1D', date: '2025-06-13', events: [
    sampleReview().by_horizon['706'].events.find((event) => event.id === 'sell-1'),
    sampleReview().by_horizon['706'].events.find((event) => event.id === 'select-1'),
  ]});
  assert.equal(controller.getState().eventGroup.events.length, 2);
  assert.match(container.region('details'), /选择要查看的单个事件/);
  container.dispatch('click', targetFor({groupEvent: 'select-1'}));
  assert.equal(controller.getState().selectedEventId, 'select-1');
  assert.equal(controller.getState().eventGroup, null);
  controller.destroy();
});

test('no-model failure is observable and retry invokes the owner callback', () => {
  const container = observableContainer();
  const retries = [];
  const controller = ui.create(container, {charts: null, onRetry: (identity) => retries.push(identity)});
  assert.match(container.innerHTML, /正在读取冻结组合结果/);
  controller.setError(new Error('冻结结果接口不可用'));
  assert.match(container.innerHTML, /role="alert"/);
  assert.match(container.innerHTML, /冻结结果接口不可用/);
  container.dispatch('click', targetFor({action: 'retry-results'}));
  assert.equal(retries.length, 1);
  assert.match(container.innerHTML, /正在读取冻结组合结果/);
  controller.destroy();
});

test('period and filter changes clear a stale grouped event chooser', () => {
  const container = observableContainer();
  let chartOptions;
  const charts = {create(_host, options) { chartOptions = options; return {update() {}, destroy() {}, getState() { return {range: 'all'}; }}; }};
  const controller = ui.create(container, {data: sampleReview(), charts});
  const events = sampleReview().by_horizon['706'].events.slice(3, 5);
  const openGroup = () => chartOptions.onSelectEvent({type: 'group', groupId: 'period-group', period: '1M', date: '2025-06-01', events});

  openGroup();
  assert.equal(controller.getState().eventGroup.events.length, 2);
  controller.update({period: '1D'});
  assert.equal(controller.getState().eventGroup, null);
  assert.doesNotMatch(container.region('details'), /选择要查看的单个事件/);

  openGroup();
  controller.update({period: '1W'});
  assert.equal(controller.getState().eventGroup, null);

  openGroup();
  container.dispatch('input', {getAttribute(name) { return name === 'data-review-filter' ? 'symbol' : null; }, value: '300204', selectionStart: 0, selectionEnd: 0});
  assert.equal(controller.getState().eventGroup, null);
  assert.doesNotMatch(container.region('details'), /选择要查看的单个事件/);
  controller.destroy();
});

test('holdings render ending market value and settlement evidence separately from initial entry fields', () => {
  const open = sampleReview({
    summary: {status: 'COMPLETE', liquidation_status: 'OPEN_POSITION', ending_equity: 1234.5},
    status: {code: 'COMPLETE_OPEN', label: '完成但有未平仓', explanation: '仍有持仓。'},
    ending_holdings: [{symbol: '000002', name: '乙公司', shares: 50, market_value: 1234.5, unsettled: false, source: 'frozen_ending_holdings'}],
    capabilities: {daily_nav: true, ending_holdings_evidence: true},
  });
  const openContainer = observableContainer();
  const openController = ui.create(openContainer, {data: open, charts: null});
  openController.update({tab: 'holdings'});
  const openHtml = openContainer.region('details');
  assert.match(openHtml, /已保存市值/);
  assert.match(openHtml, /1,234\.50/);
  assert.match(openHtml, /已结算/);
  assert.doesNotMatch(openHtml, /乙公司.*10\.0000/);
  openController.destroy();

  const unsettled = sampleReview({
    summary: {status: 'COMPLETE', liquidation_status: 'UNSETTLED_DELISTING'},
    status: {code: 'UNSETTLED', label: '含未结算股份', explanation: '仍有未结算股份。'},
    ending_holdings: [{symbol: '000003', shares: 20, market_value: 800, unsettled: true, source: 'frozen_ending_holdings'}],
    capabilities: {daily_nav: true, ending_holdings_evidence: true},
  });
  const unsettledContainer = observableContainer();
  const unsettledController = ui.create(unsettledContainer, {data: unsettled, charts: null});
  unsettledController.update({tab: 'holdings'});
  assert.match(unsettledContainer.region('details'), /未结算/);
  unsettledController.destroy();

  const unknown = sampleReview({
    summary: {status: 'COMPLETE'},
    status: {code: 'COMPLETE', label: '结果已保存', explanation: '清算状态未知。'},
    ending_holdings: [],
    capabilities: {daily_nav: true, ending_holdings_evidence: false},
  });
  const unknownContainer = observableContainer();
  const unknownController = ui.create(unknownContainer, {data: unknown, charts: null});
  unknownController.update({tab: 'holdings'});
  const unknownHtml = unknownContainer.region('details');
  assert.match(unknownHtml, /期末持仓\/清算证据未提供/);
  assert.doesNotMatch(unknownHtml, /全部清算后现金/);
  assert.doesNotMatch(unknownHtml, /冻结证据显示期末没有持仓/);
  unknownController.destroy();
});
