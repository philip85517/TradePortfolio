const state = {
  summary: null,
  leaderboard: [],
  selectedSymbol: null,
  market: "ALL",
  isUpdating: false,
  byClass: [],
  expandedGroup: null,
  activePage: "home",
  activeProduct: "etf",
  activeView: "overview",
  rollingPlans: [],
  expandedPlan: null,
  selectedPlanHolding: null,
  selectedContribution: null,
  selectedContributionTrade: null,
  rollingPlanRequestId: 0,
  chartPeriod: "day",
  chart: null,
  candleSeries: null,
  volumeSeries: null,
  chartResizeObserver: null,
  chartTradeContext: null,
  chartTradeLookup: new Map(),
  chartTradeTooltip: null,
  chartMarkerLayer: null,
  chartCandleData: [],
  currentTimeseries: null,
  stockMarket: "a_share",
  stockOverview: [],
  expandedStockGroup: null,
  stockOverviewRequestId: 0,
  selectedEtfDetail: null,
};
window.alphaLabState = state;

const $ = (id) => document.getElementById(id);
const API_BASE = window.location.protocol === "file:" ? "http://127.0.0.1:8765" : "";

async function fetchJson(url, options = undefined) {
  const target = url.startsWith("http") ? url : `${API_BASE}${url}`;
  const response = await fetch(target, options);
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch (error) {
    const contentType = response.headers.get("content-type") || "unknown";
    const preview = text.replace(/\s+/g, " ").slice(0, 120);
    throw new Error(`接口返回非 JSON：${response.status} ${response.statusText} · ${contentType} · ${preview}`);
  }
  if (!response.ok || data.error) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function formatMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const n = Number(value);
  if (Math.abs(n) >= 1e8) return `${formatNumber(n / 1e8, 2)}亿`;
  if (Math.abs(n) >= 1e4) return `${formatNumber(n / 1e4, 1)}万`;
  return formatNumber(n, 0);
}

function formatPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const cls = value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
  return `<span class="${cls}">${(Number(value) * 100).toFixed(2)}%</span>`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

function className(name) {
  const labels = {
    A_SHARE_BROAD: "A股宽基",
    A_SHARE_INDUSTRY: "A股行业",
    HK_BROAD: "港股宽基",
    HK_TECH: "港股科技",
    US_BROAD: "美股宽基",
    US_TECH: "美股科技",
    COMMODITY_GOLD: "黄金",
    COMMODITY_OIL: "原油",
    COMMODITY_METAL: "金属",
    BOND: "债券",
    DIVIDEND: "红利",
    LOW_VOL: "低波",
    OTHER: "其他",
  };
  return labels[name] || name;
}

function assetClassMarket(name) {
  if (name?.startsWith("A_SHARE")) return "A_SHARE";
  if (name?.startsWith("HK")) return "HK";
  if (name?.startsWith("US")) return "US";
  if (name?.startsWith("COMMODITY")) return "COMMODITY";
  if (["BOND", "DIVIDEND", "LOW_VOL"].includes(name)) return "INCOME";
  return "OTHER";
}

async function init() {
  $("updateBtn").addEventListener("click", updateData);
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.addEventListener("click", () => switchPage(button.dataset.page));
  });
  document.querySelectorAll("[data-goto-page]").forEach((button) => {
    button.addEventListener("click", () => switchPage(button.dataset.gotoPage));
  });
  $("assetClass").addEventListener("change", () => {
    loadLeaderboard();
    loadThemeHeatmap();
  });
  $("sortBy").addEventListener("change", loadLeaderboard);
  $("searchBox").addEventListener("input", debounce(loadLeaderboard, 260));
  $("symbolSelect").addEventListener("change", () => loadTimeseries($("symbolSelect").value));
  $("planDays").addEventListener("change", loadRollingPlans);
  $("runPlanBtn").addEventListener("click", loadRollingPlans);
  $("stockIndustryLevel").addEventListener("change", loadStockOverview);
  $("stockSortBy").addEventListener("change", loadStockOverview);
  $("stockSearchBox").addEventListener("input", debounce(loadStockOverview, 260));
  ["weightConfigBtn", "saveExperimentBtn", "compareExperimentBtn"].forEach((id) => {
    $(id)?.addEventListener("click", () => setStatus("第一版已预留实验配置入口，后续接入保存和对比 API"));
  });
  bindChartPeriodTabs();
  await refreshAll();
}

async function refreshAll() {
  setStatus("读取 DuckDB 数据");
  await loadSummary();
  if (state.activePage === "stock-scoring") {
    await loadStockOverview();
  } else {
    await loadLeaderboard();
    await loadWatchlist();
    await loadThemeHeatmap();
    if (state.activePage === "etf-portfolio") await loadRollingPlans();
    const firstSymbol = state.selectedSymbol || state.leaderboard[0]?.members?.[0]?.symbol;
    if (firstSymbol) await loadTimeseries(firstSymbol);
  }
  setStatus("已读取");
}

function switchPage(page) {
  if (!["home", "etf-scoring", "etf-portfolio", "stock-scoring", "data-health"].includes(page)) return;
  window.scrollTo(0, 0);
  state.activePage = page;
  state.activeProduct = page === "stock-scoring" ? "stock" : "etf";
  state.activeView = page === "etf-portfolio" ? "plans" : "overview";
  const pageIds = {
    home: "homePage",
    "etf-scoring": "etfScoringPage",
    "etf-portfolio": "etfPortfolioPage",
    "stock-scoring": "stockScoringPage",
    "data-health": "dataHealthPage",
  };
  Object.entries(pageIds).forEach(([key, id]) => {
    $(id).hidden = key !== page;
  });
  document.querySelectorAll(".workbench-nav button").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === page);
  });
  updateWorkspaceChrome();
  if (page === "stock-scoring") {
    loadStockOverview();
  } else if (page === "etf-portfolio") {
    loadRollingPlans();
  } else if (page === "etf-scoring" && state.selectedSymbol) {
    placeChartForCurrentView();
    setTimeout(() => loadTimeseries(state.selectedSymbol), 0);
  }
}

function updateWorkspaceChrome() {
  const copy = {
    home: ["Research Home", "快速查看市场状态、最新评分、观察池和数据健康。"],
    "etf-scoring": ["ETF Scoring Lab", "研究 ETF 评分逻辑，点击标的查看因子贡献和入场条件。"],
    "etf-portfolio": ["ETF Portfolio Lab", "研究滚动持仓模拟、收益贡献和调仓路径解释。"],
    "stock-scoring": ["Stock Scoring Lab", "按市场与行业浏览股票评分概览，第一版保留行业视图。"],
    "data-health": ["Data Health", "检查 ETF、股票和更新日志状态。"],
  }[state.activePage];
  $("workspaceTitle").textContent = copy[0];
  $("workspaceSubtitle").textContent = copy[1];
  $("globalMarketLabel").textContent = state.activeProduct === "stock" ? marketName(state.stockMarket).replace("--", "股票") : marketName(state.market) || "全市场";
}

function switchView(view) {
  switchPage(view === "plans" ? "etf-portfolio" : "etf-scoring");
}

async function updateData() {
  if (state.isUpdating) return;
  state.isUpdating = true;
  updateUpdateButton();
  setStatus("正在更新行情");
  try {
    await fetchJson("/api/update", { method: "POST" });
    await refreshAll();
  } finally {
    state.isUpdating = false;
    updateUpdateButton();
  }
}

async function loadSummary() {
  const data = await fetchJson("/api/summary");
  state.summary = data;
  const summary = data.summary;
  state.byClass = data.by_class || [];
  $("dataRange").textContent = `${summary.start_date} 至 ${summary.end_date}`;
  $("coverageTotal").textContent = `${summary.symbols} 只 ETF`;
  updateUpdateButton();

  $("metricStrip").innerHTML = [
    ["ETF 数量", formatNumber(summary.symbols, 0)],
    ["日线行数", formatNumber(summary.rows, 0)],
    ["起始日期", summary.start_date],
    ["最新日期", summary.end_date],
  ]
    .map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  $("homeEtfDate").textContent = summary.end_date || "--";
  $("homeStockDate").textContent = renderStockMarketSummary(data.stock_market_groups || []);
  $("homeMarketScope").textContent = marketName(state.market) || "全市场";
  $("homeHealthState").textContent = data.freshness?.is_current ? "最新" : `可更新至 ${data.freshness?.expected_date || "--"}`;
  $("globalConfigLabel").textContent = "配置 etf_rotation_ema20_v1";

  renderAssetClassOptions();

  renderMarketTabs(data.market_groups || []);
  renderStockMarketTabs(data.stock_market_groups || []);
  renderCoverage(data.by_class);
  renderDataHealth(data);
}

function renderStockMarketSummary(rows) {
  if (!rows.length) return "暂无股票数据";
  return rows.map((row) => `${marketName(row.market)} ${formatNumber(row.stock_count, 0)}`).join(" / ");
}

function renderAssetClassOptions() {
  const select = $("assetClass");
  const current = select.value || "ALL";
  const rows = state.market === "ALL" ? state.byClass : state.byClass.filter((row) => assetClassMarket(row.asset_class) === state.market);
  select.innerHTML = `<option value="ALL">全部类别</option>${rows
    .map((row) => `<option value="${row.asset_class}">${className(row.asset_class)} · ${row.etf_count}</option>`)
    .join("")}`;
  select.value = [...select.options].some((opt) => opt.value === current) ? current : "ALL";
}

function renderMarketTabs(groups) {
  const counts = new Map(groups.map((row) => [row.market, row.etf_count]));
  const tabs = [
    ["ALL", "全部", state.summary?.summary?.symbols],
    ["A_SHARE", "A股", counts.get("A_SHARE")],
    ["HK", "港股", counts.get("HK")],
    ["US", "美股", counts.get("US")],
    ["COMMODITY", "商品", counts.get("COMMODITY")],
    ["INCOME", "固收红利", counts.get("INCOME")],
    ["OTHER", "其他", counts.get("OTHER")],
  ].filter(([, , count]) => count === undefined || Number(count) > 0);

  ["watchMarketTabs", "leaderboardMarketTabs", "planMarketTabs"].forEach((id) => {
    const container = $(id);
    if (!container) return;
    container.innerHTML = tabs
      .map(
        ([value, label, count]) => `<button type="button" class="${value === state.market ? "active" : ""}" data-market="${value}">
          ${label}${count ? `<span>${count}</span>` : ""}
        </button>`
      )
      .join("");
    container.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        state.market = button.dataset.market;
        $("homeMarketScope").textContent = marketName(state.market) || "全市场";
        updateWorkspaceChrome();
        $("assetClass").value = "ALL";
        renderAssetClassOptions();
        renderMarketTabs(groups);
        loadLeaderboard();
        loadWatchlist();
        loadThemeHeatmap();
        if (state.activeView === "plans") loadRollingPlans();
      });
    });
  });
}

function renderStockMarketTabs(groups) {
  const counts = new Map(groups.map((row) => [row.market, row.stock_count]));
  const tabs = [
    ["a_share", "A股", counts.get("a_share")],
    ["hk", "港股", counts.get("hk")],
    ["us", "美股", counts.get("us")],
  ].filter(([, , count]) => Number(count) > 0);
  const container = $("stockMarketTabs");
  if (!container) return;
  if (!tabs.length) {
    container.innerHTML = `<span class="tab-empty">暂无股票数据</span>`;
    return;
  }
  if (!tabs.some(([value]) => value === state.stockMarket)) {
    state.stockMarket = tabs[0][0];
  }
  container.innerHTML = tabs
    .map(
      ([value, label, count]) => `<button type="button" class="${value === state.stockMarket ? "active" : ""}" data-stock-market="${value}">
        ${label}<span>${count}</span>
      </button>`
    )
    .join("");
  container.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      state.stockMarket = button.dataset.stockMarket;
      updateWorkspaceChrome();
      state.expandedStockGroup = null;
      renderStockMarketTabs(groups);
      loadStockOverview();
    });
  });
}

async function loadStockOverview() {
  const requestId = ++state.stockOverviewRequestId;
  const params = new URLSearchParams({
    market: state.stockMarket,
    level: $("stockIndustryLevel").value || "level1",
    sort: $("stockSortBy").value || "group_rank",
    search: $("stockSearchBox").value || "",
    limit: "120",
  });
  $("stockOverviewCount").textContent = "读取中";
  $("stockOverviewBody").innerHTML = `<tr><td colspan="9" class="empty">正在读取股票概览...</td></tr>`;
  try {
    const data = await fetchJson(`/api/stock_overview?${params}`);
    if (requestId !== state.stockOverviewRequestId) return;
    state.stockOverview = data.rows || [];
    state.expandedStockGroup = null;
    $("stockOverviewCount").textContent = data.date ? `${data.date} · ${state.stockOverview.length} 类` : `${state.stockOverview.length} 类`;
    renderStockOverview(state.stockOverview);
  } catch (error) {
    if (requestId !== state.stockOverviewRequestId) return;
    state.stockOverview = [];
    $("stockOverviewCount").textContent = "读取失败";
    $("stockOverviewBody").innerHTML = `<tr><td colspan="9" class="empty">股票概览读取失败：${escapeHtml(error.message)}</td></tr>`;
  }
}

function renderStockOverview(rows) {
  const tbody = $("stockOverviewBody");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty">暂无股票行业概览</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map((row, index) => renderStockIndustryRow(row, index)).join("");
  tbody.querySelectorAll("tr[data-stock-index]").forEach((tr) => {
    tr.addEventListener("click", () => {
      const row = state.stockOverview[Number(tr.dataset.stockIndex)];
      if (!row) return;
      state.expandedStockGroup = state.expandedStockGroup === row.group_key ? null : row.group_key;
      renderStockOverview(state.stockOverview);
    });
  });
}

function renderStockIndustryRow(row, index) {
  const expanded = state.expandedStockGroup === row.group_key;
  const path = [row.level1, row.level2, row.level3].filter(Boolean).join(" / ");
  const detail = expanded ? renderStockIndustryDetail(row) : "";
  return `<tr data-stock-index="${index}" class="stock-group-row ${expanded ? "expanded" : ""}">
      <td class="num">${formatNumber(row.group_rank, 0)}</td>
      <td><strong>${escapeHtml(row.group_name)}</strong><span class="subtext">${escapeHtml(path)}</span></td>
      <td>${escapeHtml(row.leader_symbol || "")} ${escapeHtml(row.leader_name || "")}</td>
      <td class="num score-cell">${formatNumber(row.category_score, 1)}</td>
      <td class="num">${formatNumber(row.stock_count, 0)}</td>
      <td class="num">${formatPct(row.return_20d)}</td>
      <td class="num">${formatPct(row.return_60d)}</td>
      <td class="num">${formatPct(row.return_120d)}</td>
      <td class="num">${formatMoney(row.amount)}</td>
    </tr>${detail}`;
}

function renderStockIndustryDetail(row) {
  const members = row.members || [];
  return `<tr class="detail-row stock-detail-row"><td colspan="9">
    <div class="stock-member-grid">
      ${members.length ? members.map(renderStockMemberCard).join("") : `<div class="empty">暂无明细股票</div>`}
    </div>
  </td></tr>`;
}

function renderStockMemberCard(item) {
  const industryPath = [item.industry_level1, item.industry_level2, item.industry_level3].filter(Boolean).join(" / ");
  return `<div class="stock-member-card">
    <div class="stock-member-title">
      <span class="member-rank">#${formatNumber(item.daily_rank, 0)}</span>
      <strong>${escapeHtml(item.symbol)} ${escapeHtml(item.name || "")}</strong>
    </div>
    <span>${escapeHtml(industryPath)}</span>
    <div class="stock-member-stats">
      <span>分数 <strong>${formatNumber(item.total_score, 1)}</strong></span>
      <span>20日 <strong>${stripTags(formatPct(item.return_20d))}</strong></span>
      <span>60日 <strong>${stripTags(formatPct(item.return_60d))}</strong></span>
      <span>成交额 <strong>${formatMoney(item.amount)}</strong></span>
    </div>
  </div>`;
}

async function loadRollingPlans() {
  const requestId = ++state.rollingPlanRequestId;
  const params = new URLSearchParams({
    market: state.market,
    days: $("planDays").value || "30",
  });
  parkChartSection();
  setStatus("模拟滚动持仓");
  $("plansCount").textContent = "计算中";
  $("plansBody").innerHTML = `<tr><td colspan="10" class="empty">正在计算滚动持仓计划...</td></tr>`;
  try {
    const data = await fetchJson(`/api/rolling_plans?${params}`);
    if (requestId !== state.rollingPlanRequestId) return;
    state.rollingPlans = data.rows || [];
    state.expandedPlan = null;
    state.selectedPlanHolding = null;
    state.selectedContribution = null;
    state.selectedContributionTrade = null;
    $("plansCount").textContent = `${state.rollingPlans.length} 条`;
    renderRollingPlans(state.rollingPlans);
    setStatus("已读取");
  } catch (error) {
    if (requestId !== state.rollingPlanRequestId) return;
    state.rollingPlans = [];
    $("plansCount").textContent = "计算失败";
    parkChartSection();
    $("plansBody").innerHTML = `<tr><td colspan="10" class="empty">滚动持仓计划计算失败：${escapeHtml(error.message)}</td></tr>`;
    setStatus("读取失败");
  }
}

function renderRollingPlans(rows) {
  const tbody = $("plansBody");
  parkChartSection();
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty">暂无模拟结果</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(renderRollingPlanRow).join("");
  tbody.querySelectorAll("tr[data-plan]").forEach((tr) => {
    tr.addEventListener("click", () => {
      const nextPlan = state.expandedPlan === tr.dataset.plan ? null : tr.dataset.plan;
      state.expandedPlan = nextPlan;
      state.selectedPlanHolding = null;
      const plan = rows.find((item) => item.id === nextPlan);
      const firstContribution = plan?.contributions?.[0];
      state.selectedContribution = firstContribution ? { planId: nextPlan, symbol: firstContribution.symbol } : null;
      state.selectedContributionTrade = null;
      renderRollingPlans(state.rollingPlans);
      if (firstContribution) showContributionOnChart(nextPlan, firstContribution.symbol);
    });
  });
  tbody.querySelectorAll("button[data-holding-symbol]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      state.selectedPlanHolding = { planId: button.dataset.planId, symbol: button.dataset.holdingSymbol };
      renderRollingPlans(state.rollingPlans);
    });
  });
  tbody.querySelectorAll("button[data-contribution-symbol]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      state.selectedContribution = { planId: button.dataset.planId, symbol: button.dataset.contributionSymbol };
      state.selectedContributionTrade = null;
      renderRollingPlans(state.rollingPlans);
      showContributionOnChart(button.dataset.planId, button.dataset.contributionSymbol);
    });
  });
  tbody.querySelectorAll("button[data-trade-id]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      state.selectedContributionTrade = {
        planId: button.dataset.planId,
        symbol: button.dataset.tradeSymbol,
        tradeId: button.dataset.tradeId,
      };
      renderRollingPlans(state.rollingPlans);
      showContributionOnChart(button.dataset.planId, button.dataset.tradeSymbol, button.dataset.tradeId);
    });
  });
  tbody.querySelectorAll("button[data-symbol]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      clearTradeChartContext();
      loadTimeseries(button.dataset.symbol);
      switchView("overview");
    });
  });
  placeChartForCurrentView();
  resetPlanHorizontalScroll();
}

function renderRollingPlanRow(row) {
  const holdings = (row.current_holdings || []).slice(0, 4).map((item) => `${item.symbol}`).join(" / ");
  const expanded = state.expandedPlan === row.id;
  return `<tr data-plan="${row.id}" class="plan-row ${expanded ? "expanded" : ""}">
      <td class="num">${formatNumber(row.rank, 0)}</td>
      <td><strong>${row.title}</strong><span class="subtext">${row.difference_summary || `${row.rebalance_days}日周期 · ${row.holdings_count}只等权`}</span></td>
      <td class="num">${formatNumber(row.days, 0)}</td>
      <td class="num">${formatMoney(row.final_equity)}<span class="subtext">${stripTags(formatPct(row.total_return))}</span></td>
      <td class="num">${formatPct(row.max_drawdown)}</td>
      <td class="num">${formatPct(row.daily_volatility)}</td>
      <td class="num">${formatPct(row.annual_volatility)}</td>
      <td class="num">${formatPct(row.win_rate)}</td>
      <td class="num">${formatNumber(row.rebalance_count, 0)}</td>
      <td>${holdings || "--"}</td>
    </tr>${expanded ? renderRollingPlanDetail(row) : ""}`;
}

function renderRollingPlanDetail(row) {
  const contributions = row.contributions || [];
  const events = row.rebalance_history || [];
  const selectedContributionSymbol =
    state.selectedContribution?.planId === row.id ? state.selectedContribution.symbol : contributions[0]?.symbol;
  const selectedContribution = contributions.find((item) => item.symbol === selectedContributionSymbol) || contributions[0];
  return `<tr class="detail-row plan-detail-row"><td colspan="10">
    <div class="plan-trade-layout">
      <div class="detail-block plan-contribution-block">
        <h4>历史调仓标的收益</h4>
        <div class="contribution-list">${contributions.length ? renderContributions(contributions, row.id, selectedContribution?.symbol) : `<div class="empty">暂无贡献数据</div>`}</div>
        <div class="rebalance-timeline">
          <h4>调仓路径</h4>
          ${events.length ? events.map(renderRebalanceTimelineItem).join("") : `<div class="empty">暂无调仓记录</div>`}
        </div>
      </div>
      <div id="planChartSlot" class="plan-chart-slot">
        ${selectedContribution ? "" : `<div class="empty">暂无交易图表</div>`}
      </div>
    </div>
  </td></tr>`;
}

function renderRebalanceTimelineItem(event) {
  const added = (event.added || []).map((item) => `${item.symbol} ${item.name || ""}`.trim()).join(" / ") || "--";
  const removed = (event.removed || []).map((item) => `${item.symbol} ${item.name || ""}`.trim()).join(" / ") || "--";
  const holdings = (event.holdings || []).slice(0, 6).map((item) => item.symbol).join(" / ") || "--";
  return `<div class="timeline-item">
    <strong>${event.date}</strong>
    <span>买入 ${escapeHtml(added)}</span>
    <span>卖出 ${escapeHtml(removed)}</span>
    <small>持仓 ${escapeHtml(holdings)} · 换手 ${stripTags(formatPct(event.turnover))} · 成本 ${formatMoney(event.cost)} · 权益 ${formatMoney(event.equity)}</small>
  </div>`;
}

function renderHoldingItem(item, planId, selectedSymbol) {
  const active = item.symbol === selectedSymbol;
  return `<button type="button" class="holding-item ${active ? "active" : ""}" data-plan-id="${planId}" data-holding-symbol="${item.symbol}">
    <span><strong>${item.symbol}</strong> ${item.name || ""}</span>
    <span>${className(item.asset_class)} · ${formatPct(item.weight)} · ${formatMoney(item.value)}</span>
    <span>#${formatNumber(item.daily_rank, 0)} · 分数 ${formatNumber(item.total_score, 1)} · 持有 ${formatNumber(item.holding_days, 0)} 天</span>
  </button>`;
}

function renderHoldingProcess(item) {
  if (!item) return "";
  const periods = item.holding_periods || [];
  return `<div class="holding-process">
    <div class="holding-process-title">
      <h4>${item.symbol} ${item.name || ""} 持仓过程</h4>
      <span>累计 ${periods.length} 段持仓记录</span>
    </div>
    ${
      periods.length
        ? `<div class="process-table">
          ${periods.map(renderHoldingPeriod).join("")}
        </div>`
        : `<div class="empty">暂无买卖记录</div>`
    }
  </div>`;
}

function renderContributionProcess(item) {
  if (!item) return "";
  const periods = item.holding_periods || [];
  const trades = item.daily_trades || [];
  const selectedTradeId =
    state.selectedContributionTrade?.planId === state.expandedPlan && state.selectedContributionTrade?.symbol === item.symbol
      ? state.selectedContributionTrade.tradeId
      : trades[0]?.id;
  const selectedTrade = trades.find((trade) => trade.id === selectedTradeId) || trades[0];
  return `<div class="holding-process contribution-process">
    <div class="holding-process-title">
      <h4>${item.symbol} ${item.name || ""} 日线交易记录</h4>
      <span>累计贡献 ${formatMoney(item.pnl)} · ${formatNumber(trades.length, 0)} 笔 Buy/Sell</span>
    </div>
    ${
      trades.length
        ? `<div class="trade-record-layout">
          <div class="trade-record-list">
            ${trades.map((trade) => renderTradeRecord(trade, item.plan_id || "", item.symbol, selectedTrade?.id)).join("")}
          </div>
          ${renderTradeDetail(selectedTrade)}
        </div>`
        : `<div class="empty">暂无买卖记录</div>`
    }
    ${
      periods.length
        ? `<details class="period-details">
          <summary>查看持仓段收益</summary>
          <div class="process-table">${periods.map(renderHoldingPeriod).join("")}</div>
        </details>`
        : ""
    }
  </div>`;
}

function renderTradeRecord(trade, planId, symbol, selectedId) {
  const active = trade.id === selectedId;
  const side = trade.side === "BUY" ? "Buy" : "Sell";
  const sideClass = trade.side === "BUY" ? "buy" : "sell";
  return `<button type="button" class="trade-record ${active ? "active" : ""}" data-plan-id="${state.expandedPlan || planId}" data-trade-symbol="${symbol}" data-trade-id="${trade.id}">
    <span class="trade-side ${sideClass}">${side}</span>
    <span>${trade.date}</span>
    <strong>${formatNumber(trade.price, 3)}</strong>
    <small>${formatMoney(trade.value)} · ${formatNumber(trade.quantity, 0)} 份</small>
  </button>`;
}

function renderTradeDetail(trade) {
  if (!trade) return "";
  const side = trade.side === "BUY" ? "买入" : "卖出";
  const pairedLabel = trade.side === "BUY" ? "对应卖出" : "对应买入";
  return `<div class="trade-detail">
    <div class="trade-detail-head">
      <span class="trade-side ${trade.side === "BUY" ? "buy" : "sell"}">${trade.side === "BUY" ? "Buy" : "Sell"}</span>
      <strong>${trade.date} ${side}</strong>
    </div>
    <div class="trade-detail-grid">
      <div><span>价格</span><strong>${formatNumber(trade.price, 3)}</strong></div>
      <div><span>${side}金额</span><strong>${formatMoney(trade.value)}</strong></div>
      <div><span>${side}数量</span><strong>${formatNumber(trade.quantity, 0)} 份</strong></div>
      <div><span>目标权重</span><strong>${stripTags(formatPct(trade.weight))}</strong></div>
      <div><span>当日排名</span><strong>#${formatNumber(trade.rank, 0)}</strong></div>
      <div><span>综合分</span><strong>${formatNumber(trade.score, 1)}</strong></div>
      <div><span>${pairedLabel}</span><strong>${trade.paired_date || "--"}</strong></div>
      <div><span>本段收益</span><strong>${stripTags(formatPct(trade.period_return))}</strong></div>
    </div>
    <div class="trade-reason">
      <span>${side}理由</span>
      <p>${trade.reason || "--"}</p>
    </div>
  </div>`;
}

function renderHoldingPeriod(period) {
  const sellText = period.sell_date ? `${period.sell_date} · #${formatNumber(period.sell_rank, 0)} · ${formatNumber(period.sell_score, 1)}` : "持有中";
  const sellReturn = period.sell_return === null || period.sell_return === undefined ? "--" : stripTags(formatPct(period.sell_return));
  return `<div class="process-row">
    <div>
      <strong>买入点</strong>
      <span>${period.buy_date} · 价格 ${formatNumber(period.buy_price, 3)}</span>
      <span>#${formatNumber(period.buy_rank, 0)} · 分数 ${formatNumber(period.buy_score, 1)}</span>
      <small>${period.buy_reason || ""}</small>
    </div>
    <div>
      <strong>${period.status === "OPEN" ? "当前持有" : "卖出点"}</strong>
      <span>${sellText}</span>
      <small>${period.sell_reason || ""}</small>
    </div>
    <div class="num">
      <strong>${period.status === "OPEN" ? "持有中" : `卖出收益 ${sellReturn}`}</strong>
      <span>${formatNumber(period.holding_days, 0)} 天 · 卖出价 ${formatNumber(period.sell_price, 3)}</span>
    </div>
  </div>`;
}

function renderRebalanceEvent(event) {
  const added = (event.added || []).map((item) => item.symbol).join(" / ") || "--";
  const removed = (event.removed || []).map((item) => item.symbol).join(" / ") || "--";
  return `<div class="event-item">
    <strong>${event.date}</strong>
    <span>买入 ${added}</span>
    <span>卖出 ${removed}</span>
    <small>换手 ${stripTags(formatPct(event.turnover))} · 成本 ${formatMoney(event.cost)}</small>
  </div>`;
}

function renderContributions(rows, planId, selectedSymbol) {
  const maxAbs = Math.max(...rows.map((row) => Math.abs(Number(row.pnl) || 0)), 1);
  return rows
    .map((row) => {
      const width = Math.max(4, (Math.abs(Number(row.pnl) || 0) / maxAbs) * 100);
      const cls = Number(row.pnl) >= 0 ? "positive-fill" : "negative-fill";
      const active = row.symbol === selectedSymbol;
      return `<button type="button" class="contribution-row ${active ? "active" : ""}" data-plan-id="${planId}" data-contribution-symbol="${row.symbol}">
        <div class="bar-label">${row.symbol} ${row.name || ""}</div>
        <div class="bar-track"><div class="bar-fill ${cls}" style="width:${width}%"></div></div>
        <div class="num">${formatMoney(row.pnl)}</div>
      </button>`;
    })
    .join("");
}

function renderDailyVolatility(rows) {
  if (!rows.length) return "";
  const maxAbs = Math.max(...rows.map((row) => Math.abs(Number(row.daily_return) || 0)), 0.001);
  return `<div class="daily-return-strip">${rows
    .map((row) => {
      const height = Math.max(3, (Math.abs(Number(row.daily_return) || 0) / maxAbs) * 42);
      const cls = Number(row.daily_return) >= 0 ? "positive-bar" : "negative-bar";
      return `<span class="${cls}" title="${row.date} ${stripTags(formatPct(row.daily_return))}" style="height:${height}px"></span>`;
    })
    .join("")}</div>`;
}

function renderCoverage(rows) {
  const max = Math.max(...rows.map((row) => row.etf_count), 1);
  $("coverageChart").innerHTML = rows
    .map((row, index) => {
      const colors = ["#2563eb", "#0f766e", "#b45309", "#6d28d9", "#15803d", "#b42318"];
      const width = Math.max(4, (row.etf_count / max) * 100);
      return `<div class="bar-row">
        <div class="bar-label">${className(row.asset_class)}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${width}%;background:${colors[index % colors.length]}"></div></div>
        <div class="num">${row.etf_count}</div>
      </div>`;
    })
    .join("");
}

async function loadLeaderboard() {
  const params = new URLSearchParams({
    market: state.market,
    asset_class: $("assetClass").value || "ALL",
    sort: $("sortBy").value || "daily_rank",
    search: $("searchBox").value || "",
    limit: "120",
  });
  const data = await fetchJson(`/api/leaderboard?${params}`);
  state.leaderboard = data.rows;
  state.expandedGroup = null;
  $("leaderboardCount").textContent = `${data.rows.length} 条`;
  renderLeaderboard(data.rows);
  renderSymbolOptions(data.rows);
}

function renderLeaderboard(rows) {
  const tbody = $("leaderboardBody");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty">没有匹配结果</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(renderLeaderboardGroup).join("");
  tbody.querySelectorAll("tr[data-group]").forEach((tr) => {
    tr.addEventListener("click", () => {
      state.expandedGroup = state.expandedGroup === tr.dataset.group ? null : tr.dataset.group;
      renderLeaderboard(state.leaderboard);
    });
  });
  tbody.querySelectorAll("button[data-symbol]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      loadTimeseries(button.dataset.symbol);
      loadEtfDetail(button.dataset.symbol);
    });
  });
  tbody.querySelectorAll("tr[data-primary-symbol]").forEach((tr) => {
    tr.addEventListener("dblclick", () => {
      const symbol = tr.dataset.primarySymbol;
      if (!symbol) return;
      loadTimeseries(symbol);
      loadEtfDetail(symbol);
    });
  });
}

function renderLeaderboardGroup(row) {
  const groupKey = `${row.market}:${row.theme}`;
  const expanded = state.expandedGroup === groupKey;
  const member = row.members?.[0] || row;
  const detail = expanded
    ? `<tr class="detail-row"><td colspan="10">
        <div class="member-grid">${(row.members || [])
          .map(
            (item) => `<button type="button" data-symbol="${item.symbol}" class="member-card">
              <span class="member-rank">#${formatNumber(item.daily_rank, 0)}</span>
              <strong>${item.symbol} ${item.name}</strong>
              <span>分数 ${formatNumber(item.total_score, 1)} · 60日 ${stripTags(formatPct(item.return_60d))}</span>
              <span>过热扣分 ${formatNumber(item.overheat_penalty || 0, 0)} · 动量/ATR ${formatNumber(item.return_atr_20d, 2)}</span>
            </button>`
          )
          .join("")}</div>
      </td></tr>`
    : "";
  return `<tr data-group="${groupKey}" data-primary-symbol="${member.symbol || ""}" class="group-row ${expanded ? "expanded" : ""}">
        <td class="num">${formatNumber(row.group_rank, 0)}</td>
        <td><strong>${row.theme}</strong></td>
        <td>${member.symbol} ${member.name}</td>
        <td>${marketName(row.market)}</td>
        <td class="num score-cell">${formatNumber(row.total_score, 1)}</td>
        <td class="num">${formatNumber(row.member_count, 0)}</td>
        <td class="num">${formatPct(row.return_20d)}</td>
        <td class="num">${formatPct(row.return_60d)}</td>
        <td class="num">${formatPct(row.return_120d)}</td>
        <td class="num">${formatMoney(row.amount)}</td>
      </tr>${detail}`;
}

function renderSymbolOptions(rows) {
  const select = $("symbolSelect");
  const current = state.selectedSymbol || select.value;
  select.innerHTML = rows
    .slice(0, 80)
    .flatMap((row) => row.members || [])
    .map((row) => `<option value="${row.symbol}">${row.symbol} ${row.name}</option>`)
    .join("");
  if (current && [...select.options].some((opt) => opt.value === current)) {
    select.value = current;
  }
}

async function loadWatchlist() {
  const params = new URLSearchParams({ market: state.market });
  const data = await fetchJson(`/api/watchlist?${params}`);
  $("watchlistDate").textContent = data.date || "";
  const rows = data.rows || [];
  $("watchlist").innerHTML = rows.length
    ? rows
        .map(
          (row) => `<div class="rank-item">
            <div class="rank-no">${row.watch_rank}</div>
            <div class="rank-name">
              <strong>${row.theme}</strong>
              <span>${row.members?.[0]?.symbol || row.symbol} ${row.members?.[0]?.name || row.name} · ${row.member_count} 只 · 60日 ${stripTags(formatPct(row.return_60d))}</span>
            </div>
            <div class="score-pill">${formatNumber(row.total_score, 1)}</div>
          </div>`
        )
        .join("")
    : `<div class="empty">暂无观察池结果</div>`;
}

async function loadThemeHeatmap() {
  const params = new URLSearchParams({
    market: state.market,
    asset_class: $("assetClass").value || "ALL",
  });
  const data = await fetchJson(`/api/theme_heatmap?${params}`);
  $("heatmapDate").textContent = data.date || "";
  renderThemeHeatmap(data.rows || []);
}

function renderThemeHeatmap(rows) {
  const container = $("themeHeatmap");
  if (!rows.length) {
    container.innerHTML = `<div class="empty">暂无方向数据</div>`;
    return;
  }
  const scores = rows.map((row) => Number(row.total_score)).filter(Number.isFinite);
  const min = Math.min(...scores);
  const max = Math.max(...scores);
  const span = max - min || 1;
  container.innerHTML = rows
    .map((row) => {
      const strength = (Number(row.total_score) - min) / span;
      const level = Math.max(0, Math.min(1, strength));
      const hue = 10 + level * 135;
      const bg = `hsl(${hue}, 58%, ${92 - level * 34}%)`;
      const fg = level > 0.62 ? "#fff" : "#172126";
      const member = row.members?.[0] || {};
      return `<button type="button" class="heat-tile" data-symbol="${member.symbol || ""}" style="background:${bg};color:${fg}">
        <strong>${row.theme}</strong>
        <span>#${formatNumber(row.group_rank, 0)} · ${formatNumber(row.total_score, 1)}</span>
        <small>${row.member_count} 只 · ${member.symbol || ""}</small>
      </button>`;
    })
    .join("");
  container.querySelectorAll("button[data-symbol]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.symbol) loadTimeseries(button.dataset.symbol);
    });
  });
}

async function loadEtfDetail(symbol) {
  if (!symbol || !$("etfDetailPanel")) return;
  state.selectedEtfDetail = symbol;
  $("etfDetailPanel").innerHTML = `<div class="detail-empty">正在读取 ${escapeHtml(symbol)} 的评分拆解...</div>`;
  try {
    const data = await fetchJson(`/api/etf_detail?symbol=${encodeURIComponent(symbol)}`);
    if (state.selectedEtfDetail !== symbol) return;
    renderEtfDetail(data.detail);
  } catch (error) {
    if (state.selectedEtfDetail !== symbol) return;
    $("etfDetailPanel").innerHTML = `<div class="detail-empty">评分拆解读取失败：${escapeHtml(error.message)}</div>`;
  }
}

function renderEtfDetail(detail) {
  const panel = $("etfDetailPanel");
  if (!detail) {
    panel.innerHTML = `<div class="detail-empty">没有找到该 ETF 的评分明细。</div>`;
    return;
  }
  const factorRows = (detail.factors || []).map(renderFactorContribution).join("");
  const checks = (detail.checks || []).map(renderEntryCheck).join("");
  const history = (detail.score_history || []).slice(-18);
  panel.innerHTML = `<div class="detail-panel-inner">
    <div class="detail-title">
      <span>${escapeHtml(detail.date || "")}</span>
      <h3>${escapeHtml(detail.symbol)} ${escapeHtml(detail.name || "")}</h3>
      <p>${className(detail.asset_class)} · ${escapeHtml(detail.theme || "")}</p>
    </div>
    <div class="detail-score-grid">
      <div><span>综合分</span><strong>${formatNumber(detail.total_score, 1)}</strong></div>
      <div><span>排名</span><strong>#${formatNumber(detail.rank, 0)}</strong></div>
      <div><span>风险扣分</span><strong>${formatNumber(detail.overheat_penalty || 0, 0)}</strong></div>
    </div>
    <section class="detail-section">
      <h4>因子贡献</h4>
      <div class="factor-list">${factorRows || `<div class="empty">暂无因子明细</div>`}</div>
    </section>
    <section class="detail-section">
      <h4>入场条件</h4>
      <div class="check-list">${checks || `<div class="empty">暂无条件明细</div>`}</div>
    </section>
    <section class="detail-section">
      <h4>近期评分趋势</h4>
      ${renderScoreTrend(history)}
    </section>
  </div>`;
}

function renderFactorContribution(item) {
  const contribution = Number(item.contribution);
  const width = Number.isFinite(contribution) ? Math.max(3, Math.min(100, contribution)) : 0;
  return `<div class="factor-row">
    <div class="factor-head">
      <strong>${factorLabel(item.factor)}</strong>
      <span>${formatNumber(item.score, 1)} x ${formatNumber(Number(item.weight) * 100, 0)}%</span>
    </div>
    <div class="factor-meta">
      <span>原始值 ${formatFactorValue(item.factor, item.raw_value)}</span>
      <span>贡献 ${formatNumber(item.contribution, 1)}</span>
    </div>
    <div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div>
  </div>`;
}

function renderEntryCheck(item) {
  return `<div class="check-row ${item.passed ? "passed" : "failed"}">
    <span>${item.passed ? "通过" : "未过"}</span>
    <strong>${escapeHtml(item.label)}</strong>
    <small>${formatFactorValue(item.label, item.value)}${item.note ? ` · ${escapeHtml(item.note)}` : ""}</small>
  </div>`;
}

function renderScoreTrend(rows) {
  if (!rows.length) return `<div class="empty">暂无历史评分</div>`;
  const scores = rows.map((row) => Number(row.total_score)).filter(Number.isFinite);
  const max = Math.max(...scores, 1);
  return `<div class="score-trend">
    ${rows
      .map((row) => {
        const height = Math.max(5, ((Number(row.total_score) || 0) / max) * 68);
        return `<span title="${row.date} · 分数 ${formatNumber(row.total_score, 1)} · 排名 #${formatNumber(row.daily_rank, 0)}" style="height:${height}px"></span>`;
      })
      .join("")}
  </div>`;
}

function factorLabel(factor) {
  const labels = {
    return_20d: "20日动量",
    return_60d: "60日动量",
    return_120d: "120日动量",
    return_atr_20d: "ATR调整收益",
    effective_move_20d: "有效移动",
    ma20_gap_stability: "MA20稳定性",
    close_position_quality: "收盘位置质量",
    liquidity: "流动性",
  };
  return labels[factor] || factor;
}

function formatFactorValue(factor, value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const key = String(factor);
  if (key.includes("return") || key.includes("gap") || key.includes("position") || Math.abs(Number(value)) < 1) {
    return stripTags(formatPct(value));
  }
  return formatNumber(value, 2);
}

function renderDataHealth(data) {
  const summary = data.summary || {};
  const freshness = data.freshness || {};
  $("healthFreshness").textContent = freshness.is_current ? "已到最新交易日" : `最新 ${freshness.latest_date || "--"} / 预期 ${freshness.expected_date || "--"}`;
  $("healthSummary").innerHTML = [
    ["ETF 标的数", formatNumber(summary.symbols, 0)],
    ["ETF 行情行数", formatNumber(summary.rows, 0)],
    ["起始日期", summary.start_date || "--"],
    ["最新日期", summary.end_date || "--"],
  ]
    .map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  const stockRows = data.stock_market_groups || [];
  $("stockHealthSummary").innerHTML = stockRows.length
    ? stockRows.map((row) => `<div><span>${marketName(row.market)}</span><strong>${formatNumber(row.stock_count, 0)} 只</strong></div>`).join("")
    : `<div><span>股票数据</span><strong>暂无</strong></div>`;
  const logs = data.update_log || [];
  $("updateLogBody").innerHTML = logs.length
    ? logs
        .map(
          (row) => `<tr>
            <td>${escapeHtml(row.run_at || "")}</td>
            <td>${escapeHtml(row.source || "")}</td>
            <td>${escapeHtml(row.start_date || "")} 至 ${escapeHtml(row.end_date || "")}</td>
            <td class="num">${formatNumber(row.rows_written, 0)}</td>
            <td class="num">${formatNumber(row.symbols_written, 0)}</td>
            <td class="num">${formatNumber(row.failures, 0)}</td>
            <td>${escapeHtml(row.note || "")}</td>
          </tr>`
        )
        .join("")
    : `<tr><td colspan="7" class="empty">暂无更新日志</td></tr>`;
}

function marketName(market) {
  const labels = {
    A_SHARE: "A股",
    HK: "港股",
    US: "美股",
    COMMODITY: "商品",
    INCOME: "固收红利",
    OTHER: "其他",
    ALL: "全市场",
    a_share: "A股",
    hk: "港股",
    us: "美股",
  };
  return labels[market] || market || "--";
}

function updateUpdateButton() {
  const button = $("updateBtn");
  const freshness = state.summary?.freshness;
  const isCurrent = Boolean(freshness?.is_current);
  button.disabled = state.isUpdating || isCurrent;
  button.textContent = state.isUpdating ? "更新中" : "更新数据";
  if (isCurrent) {
    button.title = `已到最新交易日 ${freshness.expected_date}`;
  } else if (freshness?.expected_date) {
    button.title = `可更新至 ${freshness.expected_date}`;
  } else {
    button.title = "更新本地行情数据";
  }
}

async function loadTimeseries(symbol) {
  if (!symbol) return;
  if (state.chartTradeContext?.symbol !== symbol) {
    clearTradeChartContext();
  }
  state.selectedSymbol = symbol;
  if ([...$("symbolSelect").options].some((opt) => opt.value === symbol)) {
    $("symbolSelect").value = symbol;
  }
  const params = new URLSearchParams({
    symbol,
    period: state.chartPeriod,
    days: String(chartLookbackDays(state.chartPeriod, symbol)),
  });
  const data = await fetchJson(`/api/timeseries?${params}`);
  renderTimeseries(data);
  if (state.activePage === "etf-scoring") loadEtfDetail(symbol);
}

function bindChartPeriodTabs() {
  $("chartPeriodTabs").querySelectorAll("button[data-period]").forEach((button) => {
    button.addEventListener("click", () => {
      const period = button.dataset.period || "day";
      if (state.chartPeriod === period) return;
      state.chartPeriod = period;
      updateChartPeriodTabs();
      loadTimeseries(state.selectedSymbol || $("symbolSelect").value);
    });
  });
}

function updateChartPeriodTabs() {
  $("chartPeriodTabs").querySelectorAll("button[data-period]").forEach((button) => {
    button.classList.toggle("active", button.dataset.period === state.chartPeriod);
  });
}

function placeChartForCurrentView() {
  const chartSection = $("chartSection");
  const overviewSlot = $("etfScoringChartSlot");
  if (!chartSection || !overviewSlot) return;
  const planSlot = state.activeView === "plans" ? $("planChartSlot") : null;
  const target = planSlot || overviewSlot;
  if (chartSection.parentElement !== target) {
    target.innerHTML = "";
    target.appendChild(chartSection);
  }
  requestAnimationFrame(resizePriceChart);
}

function parkChartSection() {
  const chartSection = $("chartSection");
  const overviewSlot = $("etfScoringChartSlot");
  if (!chartSection || !overviewSlot || chartSection.parentElement === overviewSlot) return;
  overviewSlot.appendChild(chartSection);
}

function resizePriceChart() {
  const container = $("priceChart");
  if (!container || !state.chart) return;
  state.chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
  renderTradeMarkerButtons();
}

function chartLookbackDays(period, symbol = state.selectedSymbol) {
  const base = period === "month" ? 1825 : period === "week" ? 1040 : 260;
  const context = state.chartTradeContext;
  if (!context || context.symbol !== symbol || !context.earliestDate) return base;
  const latestDate = state.summary?.summary?.end_date ? new Date(state.summary.summary.end_date) : new Date();
  const earliestDate = new Date(context.earliestDate);
  if (Number.isNaN(earliestDate.getTime()) || Number.isNaN(latestDate.getTime())) return base;
  const days = Math.ceil((latestDate - earliestDate) / 86400000) + 45;
  return Math.max(base, days);
}

function renderTimeseries(data) {
  const rows = data.rows || [];
  const stats = data.stats || {};
  state.currentTimeseries = data;
  $("priceTitle").textContent = `${data.symbol || ""} ${stats.name || ""}`;
  const periodLabel = { day: "日线", week: "周线", month: "月线" }[data.period || state.chartPeriod] || "日线";
  const tradeLabel = state.chartTradeContext?.symbol === data.symbol ? ` · ${state.chartTradeContext.label}` : "";
  $("priceSubtitle").textContent = rows.length ? `${periodLabel} · ${rows[0].date} 至 ${rows[rows.length - 1].date}${tradeLabel}` : `${periodLabel}${tradeLabel}`;
  $("symbolClass").textContent = className(stats.asset_class || "");
  renderSymbolStats(stats, data.symbol);
  drawPriceChart(rows);
}

function renderSymbolStats(stats, symbol) {
  const selectedTrade = selectedChartTrade(symbol);
  const tradePanel = selectedTrade ? renderChartTradeDetailPanel(selectedTrade) : renderChartTradeSummary(symbol);
  $("symbolStats").innerHTML = [
    ["最新价", formatNumber(stats.latest_close, 3)],
    ["成交额", formatMoney(stats.latest_amount)],
    ["20日收益", stripTags(formatPct(stats.return_20d))],
    ["60日收益", stripTags(formatPct(stats.return_60d))],
    ["60日年化波动", stripTags(formatPct(stats.volatility_60d))],
  ]
    .map(([label, value]) => `<div class="stat-row"><span>${label}</span><strong>${value}</strong></div>`)
    .join("") + tradePanel;
}

function drawPriceChart(rows) {
  const container = $("priceChart");
  if (!rows.length) {
    destroyPriceChart();
    container.innerHTML = `<div class="empty">暂无数据</div>`;
    return;
  }
  if (!window.LightweightCharts) {
    container.innerHTML = `<div class="empty">图表组件加载失败</div>`;
    return;
  }
  ensurePriceChart(container);
  const candleData = rows
    .filter((row) => [row.open, row.high, row.low, row.close].every((value) => Number.isFinite(Number(value))))
    .map((row) => ({
      time: row.date,
      open: Number(row.open),
      high: Number(row.high),
      low: Number(row.low),
      close: Number(row.close),
    }));
  const volumeData = rows
    .filter((row) => Number.isFinite(Number(row.amount)))
    .map((row) => ({
      time: row.date,
      value: Number(row.amount),
      color: Number(row.close) >= Number(row.open) ? "rgba(21, 128, 61, 0.28)" : "rgba(180, 35, 24, 0.28)",
    }));
  state.candleSeries.setData(candleData);
  state.volumeSeries.setData(volumeData);
  state.chartCandleData = candleData;
  state.chart.timeScale().fitContent();
  renderTradeMarkers(candleData);
}

function ensurePriceChart(container) {
  if (state.chart) return;
  container.innerHTML = "";
  state.chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: container.clientHeight,
    layout: {
      background: { color: "#ffffff" },
      textColor: "#657174",
      fontFamily: getComputedStyle(document.documentElement).fontFamily,
    },
    grid: {
      vertLines: { color: "#edf2f4" },
      horzLines: { color: "#edf2f4" },
    },
    rightPriceScale: {
      borderColor: "#d9e1e3",
      scaleMargins: { top: 0.08, bottom: 0.28 },
    },
    timeScale: {
      borderColor: "#d9e1e3",
      timeVisible: false,
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
    },
  });
  state.candleSeries = state.chart.addCandlestickSeries({
    upColor: "#15803d",
    downColor: "#b42318",
    borderUpColor: "#15803d",
    borderDownColor: "#b42318",
    wickUpColor: "#15803d",
    wickDownColor: "#b42318",
    priceFormat: { type: "price", precision: 3, minMove: 0.001 },
  });
  state.volumeSeries = state.chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "",
    lastValueVisible: false,
    priceLineVisible: false,
  });
  state.volumeSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.78, bottom: 0 },
  });
  state.chart.subscribeCrosshairMove(handleChartCrosshairMove);
  state.chart.subscribeClick(handleChartClick);
  state.chart.timeScale().subscribeVisibleTimeRangeChange(() => renderTradeMarkerButtons());
  state.chartTradeTooltip = document.createElement("div");
  state.chartTradeTooltip.className = "chart-trade-tooltip";
  state.chartTradeTooltip.hidden = true;
  container.appendChild(state.chartTradeTooltip);
  state.chartMarkerLayer = document.createElement("div");
  state.chartMarkerLayer.className = "chart-marker-layer";
  container.appendChild(state.chartMarkerLayer);
  state.chartResizeObserver = new ResizeObserver(() => {
    if (!state.chart) return;
    state.chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
    renderTradeMarkerButtons();
  });
  state.chartResizeObserver.observe(container);
}

function destroyPriceChart() {
  if (state.chartResizeObserver) {
    state.chartResizeObserver.disconnect();
    state.chartResizeObserver = null;
  }
  if (state.chart) {
    state.chart.remove();
    state.chart = null;
    state.candleSeries = null;
    state.volumeSeries = null;
    state.chartTradeTooltip = null;
    state.chartMarkerLayer = null;
    state.chartTradeLookup = new Map();
  }
}

function showContributionOnChart(planId, symbol, tradeId = null) {
  const plan = state.rollingPlans.find((row) => row.id === planId);
  const contribution = plan?.contributions?.find((row) => row.symbol === symbol);
  if (!plan || !contribution) return;
  state.chartTradeContext = buildTradeChartContext(plan, contribution, tradeId);
  state.chartPeriod = "day";
  updateChartPeriodTabs();
  placeChartForCurrentView();
  loadTimeseries(symbol);
  if (state.activeView === "overview") {
    setTimeout(() => $("priceChart")?.scrollIntoView({ behavior: "smooth", block: "center" }), 120);
  } else {
    resetPlanHorizontalScroll();
  }
}

function resetPlanHorizontalScroll() {
  const tableWrap = document.querySelector(".plan-table-wrap");
  if (tableWrap) tableWrap.scrollLeft = 0;
}

function buildTradeChartContext(plan, contribution, selectedTradeId = null) {
  const periods = (contribution.holding_periods || [])
    .slice()
    .sort((a, b) => String(a.buy_date || "").localeCompare(String(b.buy_date || "")));
  const trades = [];
  periods.forEach((period, index) => {
    const number = index + 1;
    const base = {
      number,
      symbol: contribution.symbol,
      name: contribution.name,
      planTitle: plan.title,
      contributionPnl: contribution.pnl,
      buyDate: dateKey(period.buy_date),
      buyPrice: period.buy_price,
      buyQuantity: period.buy_quantity,
      buyValue: period.buy_value,
      buyWeight: period.buy_weight,
      buyRank: period.buy_rank,
      buyScore: period.buy_score,
      buyReason: period.buy_reason,
      sellDate: dateKey(period.sell_date),
      sellPrice: period.sell_price,
      sellQuantity: period.sell_quantity,
      sellValue: period.sell_value,
      sellWeight: period.sell_weight,
      sellRank: period.sell_rank,
      sellScore: period.sell_score,
      sellReason: period.sell_reason,
      periodReturn: period.sell_return,
      holdingDays: period.holding_days,
      status: period.status,
    };
    if (base.buyDate) {
      trades.push({
        ...base,
        id: `${contribution.symbol}-${index}-BUY`,
        side: "BUY",
        date: base.buyDate,
        markerText: `B${number}`,
        price: base.buyPrice,
        quantity: base.buyQuantity,
        value: base.buyValue,
        weight: base.buyWeight,
        rank: base.buyRank,
        score: base.buyScore,
        reason: base.buyReason,
      });
    }
    if (base.sellDate) {
      trades.push({
        ...base,
        id: `${contribution.symbol}-${index}-SELL`,
        side: "SELL",
        date: base.sellDate,
        markerText: `S${number}`,
        price: base.sellPrice,
        quantity: base.sellQuantity,
        value: base.sellValue,
        weight: base.sellWeight,
        rank: base.sellRank,
        score: base.sellScore,
        reason: base.sellReason,
      });
    }
  });
  const tradeDates = trades.map((trade) => trade.date).filter(Boolean).sort();
  return {
    planId: plan.id,
    symbol: contribution.symbol,
    label: `${plan.title} · 贡献 ${formatMoney(contribution.pnl)}`,
    trades,
    selectedTradeId,
    earliestDate: tradeDates[0] || null,
  };
}

function renderTradeMarkers(candleData) {
  if (!state.candleSeries) return;
  const context = state.chartTradeContext;
  const chartDates = new Set(candleData.map((row) => dateKey(row.time)));
  if (!context || context.symbol !== state.selectedSymbol) {
    state.candleSeries.setMarkers([]);
    state.chartTradeLookup = new Map();
    $("priceChart").dataset.tradeMarkerCount = "0";
    renderTradeMarkerButtons();
    hideChartTradeTooltip();
    return;
  }
  const lookup = new Map();
  const markers = context.trades
    .filter((trade) => chartDates.has(trade.date))
    .map((trade) => {
      const isBuy = trade.side === "BUY";
      const selected = context.selectedTradeId === trade.id;
      const color = isBuy ? (selected ? "#047857" : "#15803d") : selected ? "#b91c1c" : "#b42318";
      if (!lookup.has(trade.date)) lookup.set(trade.date, []);
      lookup.get(trade.date).push(trade);
      return {
        time: trade.date,
        position: isBuy ? "belowBar" : "aboveBar",
        color,
        shape: isBuy ? "arrowUp" : "arrowDown",
        text: trade.markerText,
        size: selected ? 2 : 1,
      };
    });
  state.chartTradeLookup = lookup;
  state.candleSeries.setMarkers(markers);
  $("priceChart").dataset.tradeMarkerCount = String(markers.length);
  renderTradeMarkerButtons();
  hideChartTradeTooltip();
}

function renderTradeMarkerButtons() {
  const layer = state.chartMarkerLayer;
  if (!layer || !state.chart || !state.candleSeries) return;
  layer.innerHTML = "";
  const context = state.chartTradeContext;
  if (!context || context.symbol !== state.selectedSymbol) return;
  const chartDates = new Set((state.chartCandleData || []).map((row) => dateKey(row.time)));
  context.trades
    .filter((trade) => chartDates.has(trade.date))
    .forEach((trade) => {
      const x = state.chart.timeScale().timeToCoordinate(trade.date);
      const y = state.candleSeries.priceToCoordinate(Number(trade.price));
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      const isBuy = trade.side === "BUY";
      const button = document.createElement("button");
      button.type = "button";
      button.className = `chart-marker-hit ${isBuy ? "buy" : "sell"} ${context.selectedTradeId === trade.id ? "active" : ""}`;
      button.textContent = trade.markerText;
      button.title = `${trade.date} ${isBuy ? "买入" : "卖出"} · ${formatNumber(trade.price, 3)}`;
      button.style.left = `${x}px`;
      button.style.top = `${Math.max(12, Math.min(layer.clientHeight - 12, y + (isBuy ? 24 : -24)))}px`;
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        selectChartTrade(trade);
      });
      layer.appendChild(button);
    });
}

function handleChartCrosshairMove(param) {
  const tooltip = state.chartTradeTooltip;
  if (!tooltip || !state.chart || !param?.point || !param.time) {
    hideChartTradeTooltip();
    return;
  }
  const trades = state.chartTradeLookup.get(dateKey(param.time));
  if (!trades?.length) {
    hideChartTradeTooltip();
    return;
  }
  tooltip.innerHTML = trades.map(renderChartTradeTooltip).join("");
  tooltip.hidden = false;
  const container = $("priceChart");
  const width = Math.min(360, container.clientWidth - 16);
  tooltip.style.maxWidth = `${width}px`;
  const left = Math.min(Math.max(8, param.point.x + 14), Math.max(8, container.clientWidth - width - 8));
  const top = Math.min(Math.max(8, param.point.y + 14), Math.max(8, container.clientHeight - tooltip.offsetHeight - 8));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function handleChartClick(param) {
  if (!param?.time) return;
  const trades = state.chartTradeLookup.get(dateKey(param.time));
  if (!trades?.length) return;
  selectChartTrade(trades[0]);
}

function selectChartTrade(trade) {
  const context = state.chartTradeContext;
  if (!context || context.symbol !== trade.symbol) return;
  context.selectedTradeId = trade.id;
  state.selectedContribution = { planId: context.planId, symbol: trade.symbol };
  state.selectedContributionTrade = { planId: context.planId, symbol: trade.symbol, tradeId: trade.id };
  if (state.expandedPlan === context.planId) {
    renderRollingPlans(state.rollingPlans);
  }
  renderTradeMarkers(state.chartCandleData || []);
  if (state.currentTimeseries?.symbol === trade.symbol) {
    renderSymbolStats(state.currentTimeseries.stats || {}, trade.symbol);
  }
}

function selectedChartTrade(symbol) {
  const context = state.chartTradeContext;
  if (!context || context.symbol !== symbol || !context.selectedTradeId) return null;
  return context.trades.find((trade) => trade.id === context.selectedTradeId) || null;
}

function renderChartTradeSummary(symbol) {
  const context = state.chartTradeContext;
  if (!context || context.symbol !== symbol) return "";
  const buyCount = context.trades.filter((trade) => trade.side === "BUY").length;
  const sellCount = context.trades.filter((trade) => trade.side === "SELL").length;
  return `<div class="trade-focus-card">
    <div class="trade-focus-head">
      <strong>交易标记</strong>
      <span>${formatNumber(buyCount, 0)} 买 / ${formatNumber(sellCount, 0)} 卖</span>
    </div>
  </div>`;
}

function renderChartTradeDetailPanel(trade) {
  const side = trade.side === "BUY" ? "买入" : "卖出";
  const reason = trade.side === "BUY" ? trade.buyReason : trade.sellReason;
  const counterpartLabel = trade.side === "BUY" ? "对应卖出" : "对应买入";
  const counterpart = trade.side === "BUY" ? trade.sellDate || "持有中" : trade.buyDate || "--";
  const periodReturn = trade.periodReturn === null || trade.periodReturn === undefined ? "--" : stripTags(formatPct(trade.periodReturn));
  return `<div class="trade-focus-card ${trade.side === "BUY" ? "buy" : "sell"}">
    <div class="trade-focus-head">
      <span class="trade-side ${trade.side === "BUY" ? "buy" : "sell"}">${trade.side === "BUY" ? "Buy" : "Sell"}</span>
      <strong>${escapeHtml(trade.date || "--")} ${side}</strong>
    </div>
    <div class="trade-focus-grid">
      <div><span>成交价</span><strong>${formatNumber(trade.price, 3)}</strong></div>
      <div><span>成交数量</span><strong>${formatNumber(trade.quantity, 0)}</strong></div>
      <div><span>成交金额</span><strong>${formatMoney(trade.value)}</strong></div>
      <div><span>目标权重</span><strong>${stripTags(formatPct(trade.weight))}</strong></div>
      <div><span>当日排名</span><strong>#${formatNumber(trade.rank, 0)}</strong></div>
      <div><span>综合分</span><strong>${formatNumber(trade.score, 1)}</strong></div>
      <div><span>${counterpartLabel}</span><strong>${escapeHtml(counterpart)}</strong></div>
      <div><span>本段收益</span><strong>${periodReturn}</strong></div>
    </div>
    <div class="trade-focus-reason">
      <span>${side}理由</span>
      <p>${escapeHtml(reason || "--")}</p>
    </div>
  </div>`;
}

function renderChartTradeTooltip(trade) {
  const isBuy = trade.side === "BUY";
  const title = isBuy ? `${trade.markerText} 买入点` : `${trade.markerText} 卖出点`;
  const counterpart = isBuy ? trade.sellDate || "持有中" : trade.buyDate || "--";
  const periodReturn = trade.periodReturn === null || trade.periodReturn === undefined ? "--" : stripTags(formatPct(trade.periodReturn));
  const pnl = trade.sellValue !== null && trade.sellValue !== undefined && trade.buyValue !== null && trade.buyValue !== undefined ? Number(trade.sellValue) - Number(trade.buyValue) : null;
  return `<div class="chart-trade-card ${isBuy ? "buy" : "sell"}">
    <div class="chart-trade-head">
      <span>${escapeHtml(title)}</span>
      <strong>${escapeHtml(trade.date || "--")}</strong>
    </div>
    <div class="chart-trade-grid">
      <div><span>买入价</span><strong>${formatNumber(trade.buyPrice, 3)}</strong></div>
      <div><span>买入量</span><strong>${formatNumber(trade.buyQuantity, 0)}</strong></div>
      <div><span>买入金额</span><strong>${formatMoney(trade.buyValue)}</strong></div>
      <div><span>买入排名</span><strong>#${formatNumber(trade.buyRank, 0)}</strong></div>
      <div><span>卖出价</span><strong>${formatNumber(trade.sellPrice, 3)}</strong></div>
      <div><span>卖出量</span><strong>${formatNumber(trade.sellQuantity, 0)}</strong></div>
      <div><span>卖出金额</span><strong>${formatMoney(trade.sellValue)}</strong></div>
      <div><span>卖出/买入日</span><strong>${escapeHtml(counterpart)}</strong></div>
      <div><span>持有天数</span><strong>${formatNumber(trade.holdingDays, 0)}</strong></div>
      <div><span>本段总收益</span><strong>${periodReturn}</strong></div>
      <div><span>本段盈亏</span><strong>${formatMoney(pnl)}</strong></div>
      <div><span>累计贡献</span><strong>${formatMoney(trade.contributionPnl)}</strong></div>
    </div>
    <div class="chart-trade-reason">
      <span>买入条件</span>
      <p>${escapeHtml(trade.buyReason || "--")}</p>
    </div>
    <div class="chart-trade-reason">
      <span>卖出条件</span>
      <p>${escapeHtml(trade.sellReason || (trade.status === "OPEN" ? "当前仍在组合目标持仓内" : "--"))}</p>
    </div>
  </div>`;
}

function hideChartTradeTooltip() {
  if (state.chartTradeTooltip) state.chartTradeTooltip.hidden = true;
}

function clearTradeChartContext() {
  state.chartTradeContext = null;
  state.chartTradeLookup = new Map();
  state.chartCandleData = [];
  if ($("priceChart")) $("priceChart").dataset.tradeMarkerCount = "0";
  hideChartTradeTooltip();
  if (state.candleSeries) state.candleSeries.setMarkers([]);
  renderTradeMarkerButtons();
}

function dateKey(value) {
  if (!value) return null;
  if (typeof value === "string") return value.slice(0, 10);
  if (typeof value === "object" && "year" in value && "month" in value && "day" in value) {
    return `${value.year}-${String(value.month).padStart(2, "0")}-${String(value.day).padStart(2, "0")}`;
  }
  return String(value).slice(0, 10);
}

function setStatus(text) {
  $("statusLine").textContent = text;
}

function stripTags(html) {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div.textContent || div.innerText || "";
}

function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

init().catch((error) => {
  setStatus(`错误：${error.message}`);
});
