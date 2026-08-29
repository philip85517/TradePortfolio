(() => {
  "use strict";

  const state = { mode: "selection", summary: null, candidates: [], visible: [], selectedSymbol: null, detail: null, portfolio: null, portfolioId: null, portfolioIds: [], chart: null };
  const $ = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  }

  function number(value, digits = 2) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
    return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }

  function percent(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
    return `${(Number(value) * 100).toFixed(2)}%`;
  }

  function api(path) {
    return fetch(path, { headers: { Accept: "application/json" }, cache: "no-store" }).then(async (response) => {
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
      return payload;
    });
  }

  function showError(error) {
    const banner = $("errorBanner");
    banner.textContent = error?.message || String(error);
    banner.hidden = false;
  }

  function hideError() { $("errorBanner").hidden = true; }

  function renderSummary(summary) {
    $("requestedDate").textContent = summary.requested_date || "--";
    $("signalDate").textContent = summary.signal_date || "--";
    $("topRuleVersion").textContent = summary.rule_version || "--";
    const marketLabel = { a_share: "A 股", hk: "港股", us: "美股" }[summary.market] || summary.market || "--";
    $("marketLabel").textContent = `市场 ${marketLabel}`;
    $("dataRange").textContent = Array.isArray(summary.data_range) && summary.data_range.length === 2
      ? `${summary.data_range[0]} → ${summary.data_range[1]}`
      : "--";
    $("sidebarRun").textContent = summary.run_id || "--";
    $("sidebarDate").textContent = `信号日 ${summary.signal_date || "--"}`;
    const funnel = summary.funnel || {};
    $("funnel").textContent = `${funnel.universe ?? summary.candidate_count ?? 0} / ${funnel.rule_eligible ?? summary.eligible_count ?? 0} / ${summary.selected_count ?? 0}`;
    const universeMode = summary.universe_mode || summary.spec?.universe_mode || "observed-history";
    $("universeMode").textContent = universeMode === "point-in-time" ? "Point-in-time" : "历史观测（探索）";
    const quality = summary.data_quality || {};
    const warnings = Number(quality.invalid_ohlc_rows || 0) + Number(quality.duplicate_groups || 0) + Number(quality.unknown_adjustment_rows || 0);
    $("qualityState").textContent = warnings ? `${warnings.toLocaleString()} 条警告` : "无明显警告";
    $("subtitle").textContent = `运行 ${summary.run_id} · 固定 ${summary.rule_version || "V0"} · 只读审阅`;
    const industry = $("industry");
    for (const value of summary.industries || []) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      industry.appendChild(option);
    }
    renderPortfolioOptions(summary.portfolios || []);
  }

  function renderPortfolioOptions(portfolios) {
    const select = $("portfolioSelect");
    const values = (portfolios || []).filter((item) => item?.portfolio_id).map((item) => ({
      id: String(item.portfolio_id),
      name: item.name || item.portfolio_id,
    }));
    state.portfolioIds = values.map((item) => item.id);
    if (!state.portfolioId || !state.portfolioIds.includes(state.portfolioId)) state.portfolioId = state.portfolioIds[0] || null;
    select.innerHTML = values.length
      ? values.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(item.id)}</option>`).join("")
      : "<option>暂无组合</option>";
    select.value = state.portfolioId || "";
    select.disabled = values.length < 2;
  }

  function renderCandidates(rows) {
    state.visible = rows;
    $("resultCount").textContent = `${rows.length} / ${state.candidates.length} 只`;
    const body = $("candidateBody");
    body.innerHTML = rows.map((row) => {
      const selected = Boolean(row.selected);
      const active = String(row.symbol) === String(state.selectedSymbol);
      return `<tr class="${active ? "active" : ""}" data-symbol="${escapeHtml(row.symbol)}" tabindex="0">
        <td class="rank-cell">${row.rank ?? "—"}</td>
        <td class="symbol-cell">${escapeHtml(row.symbol)}<br><small>${escapeHtml(row.name || "")}</small></td>
        <td>${escapeHtml(row.industry || "UNKNOWN")}</td><td>${percent(row.return_20d)}</td><td>${percent(row.return_60d)}</td><td>${number(Number(row.amount_20d || 0) / 10000)} 万</td><td>${number(row.total_score)}</td><td>${percent(row.target_weight)}</td>
        <td><span class="status-dot ${selected ? "selected" : ""}">${selected ? "入选" : (row.eligible ? "合格" : "剔除")}</span></td>
      </tr>`;
    }).join("");
    $("candidateEmpty").hidden = rows.length > 0;
    body.querySelectorAll("tr[data-symbol]").forEach((row) => {
      row.addEventListener("click", () => selectSymbol(row.dataset.symbol));
      row.addEventListener("keydown", (event) => { if (event.key === "Enter") selectSymbol(row.dataset.symbol); });
    });
  }

  function renderReasonOptions(reasons) {
    const select = $("reason");
    const current = select.value;
    select.innerHTML = '<option value="all">全部原因</option>';
    (reasons || []).forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
    select.value = (reasons || []).includes(current) ? current : "all";
  }

  function loadCandidates() {
    const query = new URLSearchParams({ search: $("search").value, status: $("status").value, industry: $("industry").value, reason: $("reason").value });
    return api(`/api/candidates?${query}`).then((payload) => {
      state.candidates = payload.rows || [];
      renderReasonOptions(payload.reasons || []);
      renderCandidates(state.candidates);
      if (!state.selectedSymbol && state.candidates.length) selectSymbol(state.candidates[0].symbol);
    });
  }

  function renderFactors(candidate) {
    const values = [
      ["20日收益率", percent(candidate.return_20d)], ["60日收益率", percent(candidate.return_60d)], ["20日平均成交额", number(candidate.amount_20d / 10000) + " 万"],
      ["20日百分位", number(candidate.return_20d_pct)], ["60日百分位", number(candidate.return_60d_pct)], ["成交额百分位", number(candidate.amount_20d_pct)],
      ["MA60", number(candidate.ma60)], ["总分", number(candidate.total_score)], ["目标权重", candidate.selected ? percent(candidate.target_weight) : "--"],
    ];
    $("factorGrid").innerHTML = values.map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
    $("detailReason").textContent = candidate.reason || "--";
    const badge = $("selectionBadge");
    badge.textContent = candidate.selected ? "Top 入选" : (candidate.eligible ? "合格未入选" : "已剔除");
    badge.className = `badge ${candidate.selected ? "selected" : (candidate.eligible ? "" : "excluded")}`;
  }

  function renderPerformance(detail) {
    const panel = $("evaluationSummary");
    if (state.mode !== "evaluation") { panel.hidden = true; return; }
    panel.hidden = false;
    const cards = Object.values(detail.performance || {});
    const portfolioCards = Object.values(detail.portfolio_performance || {});
    const stockHtml = cards.map((item) => `<div class="performance-card"><span>${item.horizon} 日个股收益</span><strong>${percent(item.stock_return)}</strong><small>组合贡献 ${percent(item.contribution)} · ${item.status}</small></div>`).join("");
    const portfolioHtml = portfolioCards.map((item) => `<div class="performance-card"><span>${item.horizon} 日组合收益 / 成本前</span><strong>${percent(item.total_return)}</strong><small>成本前 ${percent(item.gross_return)} · 最大回撤 ${percent(item.max_drawdown)} · 胜率 ${percent(item.holding_win_rate)}</small></div>`).join("");
    $("performanceGrid").innerHTML = stockHtml + portfolioHtml || `<div class="muted">该股票没有可用的前瞻结果。</div>`;
  }

  function renderPortfolio(payload) {
    const panel = $("portfolioSummary");
    if (state.mode !== "evaluation" || !payload || payload.status !== "OK") {
      panel.hidden = true;
      return;
    }
    state.portfolioId = payload.portfolio_id || state.portfolioId;
    if (state.portfolioIds.includes(state.portfolioId)) $("portfolioSelect").value = state.portfolioId;
    panel.hidden = false;
    const holdings = payload.holdings || [];
    const performance = payload.performance || {};
    const complete = Object.values(performance).filter((item) => item.status === "COMPLETE");
    const horizonText = (payload.horizons || []).join(" / ");
    const metricValues = [
      ["持仓数", `${holdings.length} 只`],
      ["建仓日", payload.entry_date || "--"],
      ["观察周期", horizonText ? `${horizonText} 日` : "--"],
      ["可评估周期", `${complete.length} / ${Object.keys(performance).length || 0}`],
    ];
    const primaryHorizon = String((payload.horizons || [])[0] || "");
    const primaryComparison = (payload.comparison || {})[primaryHorizon];
    if (primaryComparison && primaryComparison.total_return_delta !== null && primaryComparison.total_return_delta !== undefined) {
      metricValues.push([`${primaryHorizon}日相对基准`, percent(primaryComparison.total_return_delta)]);
    }
    const portfolioHtml = (payload.portfolios || []).map((portfolio) => {
      const result = (portfolio.performance || {})[primaryHorizon] || {};
      const profit = result.profit_loss === null || result.profit_loss === undefined ? "--" : number(result.profit_loss);
      return `<div class="performance-card"><span>${escapeHtml(portfolio.name || portfolio.portfolio_id)} · ${primaryHorizon}日</span><strong>${percent(result.total_return)}</strong><small>本金 ${number(portfolio.initial_cash, 0)} · 盈亏 ${profit}</small></div>`;
    }).join("");
    $("portfolioMetricsGrid").innerHTML = metricValues.map(([label, value]) => `<div class="performance-card"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("") + portfolioHtml;
    $("portfolioStatusBadge").textContent = complete.length ? "已完成" : "不可评估";
    $("portfolioStatusBadge").className = `badge ${complete.length ? "accent" : ""}`;
    $("portfolioGrid").innerHTML = holdings.length
      ? holdings.map((holding) => `<div class="performance-card"><span>#${holding.rank ?? "--"} · ${escapeHtml(holding.symbol)}</span><strong>${escapeHtml(holding.name || "--")}</strong><small>目标权重 ${percent(holding.target_weight)} · 建仓 ${holding.entry_date || "--"}</small></div>`).join("")
      : `<div class="muted">当前运行没有可建仓持仓。</div>`;
    renderPortfolioChart($("navChart"), payload.nav || [], "equity", (value) => number(value, 0), payload.benchmark_nav || []);
    renderPortfolioChart($("drawdownChart"), payload.nav || [], "drawdown", (value) => percent(value), payload.benchmark_nav || []);
  }

  function renderPortfolioChart(container, rows, field, formatValue, benchmarkRows = []) {
    if (!rows.length && !benchmarkRows.length) {
      container.innerHTML = `<div class="empty-state">暂无组合路径</div>`;
      return;
    }
    const groups = new Map();
    const addRows = (source, sourceRows) => sourceRows.forEach((row) => {
      const horizon = Number(row.horizon);
      const value = Number(row[field]);
      if (!Number.isFinite(horizon) || !Number.isFinite(value)) return;
      const key = `${source}:${horizon}`;
      if (!groups.has(key)) groups.set(key, { source, horizon, rows: [] });
      groups.get(key).rows.push({ date: row.date, value });
    });
    addRows("portfolio", rows);
    addRows("benchmark", benchmarkRows);
    if (!groups.size) { container.innerHTML = `<div class="empty-state">暂无组合路径</div>`; return; }
    const width = Math.max(container.clientWidth || 320, 280);
    const height = 170;
    const left = 44; const right = 12; const top = 14; const bottom = 27;
    const values = [...groups.values()].flatMap((group) => group.rows).map((row) => row.value);
    let min = Math.min(...values); let max = Math.max(...values);
    if (field === "drawdown") min = Math.min(min, 0); else min = Math.min(min, 0);
    if (max === min) max = min + 1;
    const x = (index, length) => left + (width - left - right) * (length <= 1 ? 0 : index / (length - 1));
    const y = (value) => top + (max - value) / (max - min) * (height - top - bottom);
    const colors = ["#0f766e", "#2563eb", "#b45309", "#7c3aed", "#15803d"];
    const line = (x1, y1, x2, y2, color, widthValue = 1, dash = "") => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="${widthValue}" ${dash ? `stroke-dasharray="${dash}"` : ""}/>`;
    let svg = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true"><rect width="100%" height="100%" fill="#fff"/>`;
    [top, top + (height - top - bottom) / 2, height - bottom].forEach((gridY) => { svg += line(left, gridY, width - right, gridY, "#eef1f5"); });
    if (field === "drawdown" && min < 0 && max > 0) svg += line(left, y(0), width - right, y(0), "#b7c0cc", 1, "3 3");
    let legend = "";
    [...groups.values()].forEach((group, index) => {
      const color = colors[group.horizon % colors.length];
      const points = group.rows.map((row, pointIndex) => `${x(pointIndex, group.rows.length)},${y(row.value)}`).join(" ");
      const dash = group.source === "benchmark" ? ' stroke-dasharray="4 3"' : "";
      svg += `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="${group.source === "benchmark" ? 1.5 : 2}"${dash} stroke-linejoin="round" stroke-linecap="round"/>`;
      const legendStyle = group.source === "benchmark" ? `background:transparent;border-top:2px dashed ${color}` : `background:${color}`;
      legend += `<span><i style="${legendStyle}"></i>${group.source === "benchmark" ? "基准" : "组合"}${group.horizon}日</span>`;
    });
    const firstSeries = [...groups.values()][0].rows;
    const firstDate = firstSeries[0]?.date || "--";
    const lastDate = firstSeries.at(-1)?.date || "--";
    svg += `<text x="${left}" y="${height - 7}" fill="#738092" font-size="9">${firstDate}</text><text x="${width - right}" y="${height - 7}" fill="#738092" font-size="9" text-anchor="end">${lastDate}</text><text x="7" y="${top + 4}" fill="#738092" font-size="9">${escapeHtml(formatValue(max))}</text><text x="7" y="${height - bottom}" fill="#738092" font-size="9">${escapeHtml(formatValue(min))}</text></svg>`;
    container.innerHTML = `${svg}<div class="mini-chart-legend">${legend}</div>`;
  }

  function loadPortfolio() {
    const query = state.portfolioId ? `?${new URLSearchParams({ portfolio_id: state.portfolioId })}` : "";
    return api(`/api/portfolio${query}`).then((payload) => { state.portfolio = payload; renderPortfolio(payload); return payload; });
  }

  function renderDetail(detail) {
    state.detail = detail;
    $("detailEmpty").hidden = true;
    $("detailContent").hidden = false;
    const candidate = detail.candidate || {};
    $("detailSymbol").textContent = candidate.symbol || detail.symbol;
    $("detailName").textContent = candidate.name || detail.symbol;
    $("detailIndustry").textContent = `${candidate.industry || "UNKNOWN"} · 信号日 ${detail.signal_date}`;
    const index = state.visible.findIndex((row) => String(row.symbol) === String(detail.symbol));
    $("detailPosition").textContent = index < 0 ? "--" : `${index + 1} / ${state.visible.length}`;
    $("previousButton").disabled = index <= 0;
    $("nextButton").disabled = index < 0 || index >= state.visible.length - 1;
    renderFactors(candidate);
    renderPerformance(detail);
    renderChart(detail);
  }

  function selectSymbol(symbol) {
    if (!symbol) return;
    state.selectedSymbol = symbol;
    renderCandidates(state.visible);
    const params = { symbol, mode: state.mode };
    if (state.portfolioId) params.portfolio_id = state.portfolioId;
    api(`/api/stock?${new URLSearchParams(params)}`).then(renderDetail).catch(showError);
  }

  function switchMode(mode) {
    if (state.mode === mode) return;
    state.mode = mode;
    $("selectionMode").classList.toggle("active", mode === "selection");
    $("evaluationMode").classList.toggle("active", mode === "evaluation");
    $("chartNotice").textContent = mode === "selection" ? "选股审阅模式：后端响应在有效信号日截止。" : "事后评估模式：显示建仓点、未来走势和观察周期终点。";
    $("chartLegend").innerHTML = mode === "selection"
      ? '<span><i class="dot signal"></i>信号日</span>'
      : '<span><i class="dot signal"></i>信号日</span><span><i class="dot entry"></i>建仓</span><span><i class="dot horizon"></i>观察终点</span>';
    if (mode === "evaluation") {
      if (state.portfolio) renderPortfolio(state.portfolio); else loadPortfolio().catch(showError);
    } else {
      $("portfolioSummary").hidden = true;
    }
    if (state.selectedSymbol) selectSymbol(state.selectedSymbol);
  }

  function renderChart(detail) {
    const container = $("chart");
    if (state.chart) { state.chart.remove(); state.chart = null; }
    container.innerHTML = "";
    if (!detail.rows?.length) { container.innerHTML = `<div class="empty-state">${detail.status === "NO_CHART_DATA" ? "该股票缺少图表行情。" : "信号日前没有可用行情。"}</div>`; return; }
    if (window.LightweightCharts) {
      try { renderLightweightChart(container, detail); return; } catch (error) { console.warn("Lightweight Charts fallback", error); }
    }
    renderSvgChart(container, detail);
  }

  function renderLightweightChart(container, detail) {
    const chart = LightweightCharts.createChart(container, { width: container.clientWidth || 800, height: 390, layout: { background: { color: "#ffffff" }, textColor: "#738092" }, grid: { vertLines: { color: "#eef1f5" }, horzLines: { color: "#eef1f5" } }, rightPriceScale: { borderColor: "#dfe5ec" }, timeScale: { borderColor: "#dfe5ec", timeVisible: true } });
    const candles = chart.addCandlestickSeries({ upColor: "#138a61", downColor: "#c94c53", borderVisible: false, wickUpColor: "#138a61", wickDownColor: "#c94c53" });
    const volumes = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", scaleMargins: { top: 0.78, bottom: 0 } });
    candles.setData(detail.rows.map((row) => ({ time: row.date, open: Number(row.open), high: Number(row.high), low: Number(row.low), close: Number(row.close) })));
    volumes.setData(detail.rows.map((row) => ({ time: row.date, value: Number(row.volume || 0), color: Number(row.close) >= Number(row.open) ? "#b6e4d2" : "#f3c3c6" })));
    const markers = [{ time: detail.markers.signal_date, position: "aboveBar", color: "#f0a449", shape: "arrowDown", text: "信号" }];
    if (state.mode === "evaluation") {
      if (detail.markers.entry_date) markers.push({ time: detail.markers.entry_date, position: "belowBar", color: "#1f6feb", shape: "arrowUp", text: "建仓" });
      Object.entries(detail.markers).filter(([key]) => key.startsWith("horizon_")).forEach(([key, value]) => markers.push({ time: value, position: "aboveBar", color: "#138a61", shape: "circle", text: key.replace("horizon_", "") }));
    }
    candles.setMarkers(markers.sort((left, right) => String(left.time).localeCompare(String(right.time))));
    chart.timeScale().fitContent();
    state.chart = chart;
  }

  function renderSvgChart(container, detail) {
    const rows = detail.rows;
    const width = Math.max(container.clientWidth || 760, 540); const height = 390; const left = 45; const right = 12; const top = 15; const priceBottom = 260; const volumeBottom = 360;
    const prices = rows.flatMap((row) => [Number(row.high), Number(row.low)]).filter(Number.isFinite); const min = Math.min(...prices); const max = Math.max(...prices); const range = max - min || 1; const plotWidth = width - left - right; const step = plotWidth / Math.max(rows.length - 1, 1); const x = (index) => left + index * step; const y = (value) => top + (max - value) / range * (priceBottom - top); const maxVolume = Math.max(...rows.map((row) => Number(row.volume || 0)), 1);
    const line = (x1, y1, x2, y2, color, widthValue = 1, dash = "") => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="${widthValue}" ${dash ? `stroke-dasharray="${dash}"` : ""}/>`;
    let svg = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><rect width="100%" height="100%" fill="#fff"/>`;
    [top, 95, 175, priceBottom, volumeBottom].forEach((gridY) => { svg += line(left, gridY, width - right, gridY, "#eef1f5"); });
    const markerIndex = (date) => rows.findIndex((row) => row.date === date);
    const markers = [[detail.markers.signal_date, "#f0a449", "信号"]]; if (state.mode === "evaluation") { if (detail.markers.entry_date) markers.push([detail.markers.entry_date, "#1f6feb", "建仓"]); Object.entries(detail.markers).filter(([key]) => key.startsWith("horizon_")).forEach(([key, value]) => markers.push([value, "#138a61", key.replace("horizon_", "")])); }
    markers.forEach(([date, color, label]) => { const index = markerIndex(date); if (index >= 0) { const markerX = x(index); svg += line(markerX, top, markerX, volumeBottom, color, 1.5, "4 4"); svg += `<text x="${markerX + 4}" y="${top + 12}" fill="${color}" font-size="10">${label}</text>`; } });
    rows.forEach((row, index) => { const open = Number(row.open); const close = Number(row.close); const high = Number(row.high); const low = Number(row.low); const color = close >= open ? "#138a61" : "#c94c53"; const barWidth = Math.max(2, Math.min(10, step * .55)); const candleX = x(index); svg += line(candleX, y(high), candleX, y(low), color, 1); svg += `<rect x="${candleX - barWidth / 2}" y="${Math.min(y(open), y(close))}" width="${barWidth}" height="${Math.max(1, Math.abs(y(open) - y(close)))}" fill="${color}" opacity=".88"/>`; const volumeHeight = Number(row.volume || 0) / maxVolume * 78; svg += `<rect x="${candleX - barWidth / 2}" y="${volumeBottom - volumeHeight}" width="${barWidth}" height="${volumeHeight}" fill="${color}" opacity=".32"/>`; });
    svg += `<text x="${left}" y="${volumeBottom + 20}" fill="#738092" font-size="10">${rows[0].date}</text><text x="${width - right}" y="${volumeBottom + 20}" fill="#738092" font-size="10" text-anchor="end">${rows[rows.length - 1].date}</text><text x="8" y="${top + 5}" fill="#738092" font-size="10">${number(max)}</text><text x="8" y="${priceBottom}" fill="#738092" font-size="10">${number(min)}</text><text x="${left}" y="${volumeBottom + 36}" fill="#738092" font-size="10">成交量</text></svg>`;
    container.innerHTML = svg;
  }

  function move(delta) { const index = state.visible.findIndex((row) => String(row.symbol) === String(state.selectedSymbol)); const next = state.visible[index + delta]; if (next) selectSymbol(next.symbol); }

  $("search").addEventListener("input", () => loadCandidates().catch(showError));
  $("status").addEventListener("change", () => loadCandidates().catch(showError));
  $("industry").addEventListener("change", () => loadCandidates().catch(showError));
  $("reason").addEventListener("change", () => loadCandidates().catch(showError));
  $("portfolioSelect").addEventListener("change", () => {
    state.portfolioId = $("portfolioSelect").value || null;
    state.portfolio = null;
    if (state.mode === "evaluation") {
      loadPortfolio().then(() => { if (state.selectedSymbol) selectSymbol(state.selectedSymbol); }).catch(showError);
    }
  });
  $("selectionMode").addEventListener("click", () => switchMode("selection"));
  $("evaluationMode").addEventListener("click", () => switchMode("evaluation"));
  $("previousButton").addEventListener("click", () => move(-1));
  $("nextButton").addEventListener("click", () => move(1));
  document.addEventListener("keydown", (event) => { if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return; if (event.key === "ArrowUp") { event.preventDefault(); move(-1); } if (event.key === "ArrowDown") { event.preventDefault(); move(1); } });

  api("/api/summary").then((summary) => { state.summary = summary; renderSummary(summary); return loadCandidates(); }).catch(showError);
})();
