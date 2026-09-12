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
      { id: 'sell-1', date: '2025-06-13', action: 'SELL', filled: true, symbol: '002336', name: '人乐退', shares: 500, price: 0.609695, commission: 0.09145425, slippage: 0.1525, net_cash: 304.75604575, chain_id: 'chain-1', reason_code: 'termination_decision', source_url: 'https://example.com/event.pdf', price_basis_label: '研究总回报价格' },
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

test('normalizes detail and fallback identity while keeping null metrics distinct from zero', () => {
  const model = ui.normalizeReviewPayload({ review: sampleReview() }, { horizon: 706 });
  assert.equal(model.runId, 'research-test-run');
  assert.equal(model.portfolioId, 'strategy');
  assert.equal(model.horizon, '706');
  assert.equal(model.data.nav[0].cumulative_return, 0);
  assert.equal(ui.formatMissing(null, '冻结摘要未保存年化收益'), '冻结摘要未保存年化收益');
  assert.equal(ui.formatMissing(0, '不会使用'), '0');
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
