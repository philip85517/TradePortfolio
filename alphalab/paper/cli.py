"""命令行入口：交易闭环与历史截面研究原型。"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

from .. import __version__
from ..backtest.engine import run_backtest
from ..config import (
    REPO_ROOT,
    load_account_config,
    load_config,
    merge_strategy_account,
    resolve_storage_path,
)
from ..data import Universe
from ..data.loader import load_etf_metadata, load_market_data
from ..research import DuckDBMarketDataAdapter, HistoricalResearchLab, ResearchRunStore, ResearchSpec
from ..research.review import serve_review
from ..storage import PaperDatabase
from ..utils import money_round, normalize_symbol, parse_date
from ..validation.parity_checks import compare_nav_series, compare_positions, format_parity_report
from .execute import execute
from .common import apply_universe_data_flags
from .prepare import prepare
from .reconcile import reconcile
from .replay import replay
from .report import build_report, write_report

DEFAULT_SEED_SYMBOLS = [
    ("510300.SH", "沪深300ETF华泰柏瑞", "A_SHARE_BROAD"),
    ("510500.SH", "中证500ETF南方", "A_SHARE_BROAD"),
    ("159915.SZ", "创业板ETF易方达", "A_SHARE_BROAD"),
    ("510050.SH", "上证50ETF华夏", "A_SHARE_BROAD"),
    ("588000.SH", "科创50ETF华夏", "A_SHARE_BROAD"),
    ("512100.SH", "中证1000ETF南方", "A_SHARE_BROAD"),
    ("513100.SH", "纳指ETF国泰", "US_TECH"),
    ("518880.SH", "黄金ETF华安", "COMMODITY_GOLD"),
    ("159920.SZ", "恒生ETF华夏", "HK_BROAD"),
    ("513050.SH", "中概互联网ETF易方达", "HK_TECH"),
]

DEFAULT_RESEARCH_DB = REPO_ROOT / "etf_strategy" / "data" / "processed" / "stock_market_data_2021.duckdb"
DEFAULT_RESEARCH_RUNS = REPO_ROOT / "alphalab" / "reports" / "research"


def _load_runtime_config(args) -> dict:
    strategy_cfg = load_config(getattr(args, "config", None))
    account_cfg = load_account_config()
    return merge_strategy_account(strategy_cfg, account_cfg)


def _db(args, config: dict | None = None) -> PaperDatabase:
    env_db = os.environ.get("ALPHALAB_DB")
    path = resolve_storage_path(config, env_db) if config else resolve_storage_path(None, env_db)
    return PaperDatabase(path)


def _ensure_initialized(db: PaperDatabase, config: dict) -> None:
    if not db.is_initialized():
        db.initialize()
        db.init_account(float(config.get("account", {}).get("initial_cash", 100000)))
        print(f"[init] 模拟账户已初始化，初始资金 {db.get_initial_cash():,.2f}")


def _real_meta_symbols() -> set[str]:
    from ..config import REPO_ROOT

    meta = REPO_ROOT / "etf_strategy" / "data" / "raw" / "etf_meta_latest.csv"
    if not meta.exists():
        return set()
    try:
        df = pd.read_csv(meta)
        return {normalize_symbol(str(s)) for s in df["symbol"]}
    except Exception:
        return set()


def _latest_real_date(config: dict) -> str | None:
    """探测本地真实行情的最新日期（DuckDB 优先，CSV 兜底）。"""
    from ..config import REPO_ROOT

    candidates: list[Path] = []
    duck = config.get("data", {}).get("duckdb", {})
    if duck.get("path"):
        candidates.append(Path(str(duck["path"])).expanduser())
    candidates.append(REPO_ROOT / "etf_strategy" / "data" / "processed" / "etf_strategy.duckdb")
    try:
        import duckdb

        for p in candidates:
            if not p.exists():
                continue
            con = duckdb.connect(str(p), read_only=True)
            try:
                row = con.execute("SELECT MAX(date) FROM etf_daily").fetchone()
            except Exception:
                row = None
            finally:
                con.close()
            if row and row[0]:
                return str(pd.Timestamp(row[0]).date())
    except Exception:
        pass
    legacy = REPO_ROOT / "etf_strategy" / "data" / "raw" / "etf_daily_5y.csv"
    if legacy.exists():
        try:
            with open(legacy, "rb") as f:
                f.seek(-512, 2)
                tail = f.read().decode("utf-8", errors="ignore").strip().splitlines()[-1]
            return tail.split(",")[0]
        except Exception:
            pass
    return None


def cmd_universe(args) -> int:
    u = Universe()
    if args.action == "list":
        items = u.items()
        if not items:
            print("标的池为空。使用 `python -m alphalab universe add <代码> ...` 或 `universe seed` 添加。")
            return 0
        print(f"{'代码':<12}{'名称':<24}{'资产类别':<18}{'数据':<8}{'状态':<6}")
        for it in items:
            print(
                f"{it['symbol']:<12}{it.get('name', ''):<24}{it.get('asset_class', 'OTHER'):<18}"
                f"{'合成' if it.get('synthetic') else '真实':<8}{'启用' if it.get('enabled', True) else '停用'}"
            )
        return 0
    if args.action == "seed":
        names = {sym: name for sym, name, _ in DEFAULT_SEED_SYMBOLS}
        classes = {sym: cls for sym, _, cls in DEFAULT_SEED_SYMBOLS}
        added = u.add([s for s, _, _ in DEFAULT_SEED_SYMBOLS], names=names, asset_classes=classes, synthetic=args.synthetic)
        print(f"已添加/确认 {len(added)} 只默认标的。")
        return 0
    if args.action == "add":
        if not args.symbols:
            print("请指定要添加的代码，例如: python -m alphalab universe add 510300.SH 159915.SZ")
            return 2
        real = _real_meta_symbols()
        names: dict[str, str] = {}
        classes: dict[str, str] = {}
        syms = [normalize_symbol(s) for s in args.symbols]
        meta = load_etf_metadata(syms)
        for _, row in meta.iterrows():
            names[row["symbol"]] = row["name"]
            classes[row["symbol"]] = row["asset_class"]
        synthetic = args.synthetic or any(s not in real for s in syms)
        if synthetic and not args.synthetic:
            print("[提示] 部分代码无本地真实行情，将标记为合成数据（--synthetic 可强制全部合成）")
        added = u.add(syms, names=names, asset_classes=classes, synthetic=synthetic)
        for it in added:
            print(
                f"已添加 {it['symbol']} {it.get('name', '')} "
                f"({'合成' if it.get('synthetic') else '真实'}行情)"
            )
        return 0
    if args.action == "remove":
        removed = u.remove(args.symbols)
        print(f"已移除 {len(removed)} 只: {', '.join(removed) if removed else '无'}")
        return 0
    if args.action == "enable":
        u.enable(args.symbols)
        print("已启用:", ", ".join(normalize_symbol(s) for s in args.symbols))
        return 0
    if args.action == "disable":
        u.disable(args.symbols)
        print("已停用:", ", ".join(normalize_symbol(s) for s in args.symbols))
        return 0
    return 2


def cmd_init_account(args) -> int:
    config = _load_runtime_config(args)
    db = _db(args, config)
    db.initialize(force=args.force)
    initial = float(args.initial_cash or config.get("account", {}).get("initial_cash", 100000))
    db.init_account(initial, args.date or parse_date(pd.Timestamp.today()))
    print(f"模拟账户初始化完成：{initial:,.2f} CNY")
    return 0


def cmd_prepare(args) -> int:
    config = _load_runtime_config(args)
    db = _db(args, config)
    _ensure_initialized(db, config)
    u = Universe()
    res = prepare(args.date, config, db, u, force=args.force)
    print(f"[prepare] {res.signal_date} run={res.run_id}")
    print(f"  总资产 {res.total_equity:,.2f} | 现金 {res.cash:,.2f} | 信号 {len(res.targets)} 条 | 订单 {len(res.orders)} 笔")
    for o in res.orders:
        print(f"    {o.side:<4} {o.symbol} {o.planned_quantity} 股 @ 参考价 {o.reference_price:.4f} | {o.reason}")
    if not res.orders:
        print("  无调仓订单（非调仓日或持仓已符合目标）。")
    return 0


def cmd_execute(args) -> int:
    config = _load_runtime_config(args)
    db = _db(args, config)
    _ensure_initialized(db, config)
    u = Universe()
    res = execute(args.date, config, db, u, force=args.force)
    print(f"[execute] {res.execution_date} run={res.run_id}")
    print(f"  成交 {len(res.fills)} 笔 | 拒绝 {len(res.rejected)} 笔 | 日终资产 {res.total_equity:,.2f} | 现金 {res.cash:,.2f}")
    for f in res.fills:
        print(f"    {f.side:<4} {f.symbol} {f.quantity} 股 @ {f.fill_price:.4f} 佣金 {f.commission:.2f}")
    for r in res.rejected:
        print(f"    REJECTED {r.symbol} {r.side}: {r.reason}")
    if res.anomalies:
        for a in res.anomalies:
            print(f"  [{a['severity']}] {a['anomaly_type']}: {a['message']}")
    return 0


def cmd_reconcile(args) -> int:
    config = _load_runtime_config(args)
    db = _db(args, config)
    u = Universe()
    res = reconcile(args.date, config, db, u)
    print(f"[reconcile] {res.trade_date} run={res.run_id}")
    if not res.anomalies:
        print("  [PASS] 全部恒等式检查通过。")
    for a in res.anomalies:
        print(f"  [{a['severity']}] {a['anomaly_type']}: {a['message']}")
    return 1 if any(a["severity"] == "FATAL" for a in res.anomalies) else 0


def cmd_report(args) -> int:
    config = _load_runtime_config(args)
    db = _db(args, config)
    u = Universe()
    text = build_report(args.date, db, u, config)
    print(text)
    path = write_report(args.date, text, args.out)
    print(f"[report] 已保存: {path}")
    return 0


def cmd_status(args) -> int:
    config = _load_runtime_config(args)
    db = _db(args, config)
    if not db.is_initialized():
        print("账本尚未初始化。")
        return 1
    d = args.date or (db.nav_series()[-1]["trade_date"] if db.nav_series() else None)
    if not d:
        print("账本为空。")
        return 0
    nav = db.get_daily_nav(d)
    print(f"[status] {d}")
    if nav:
        print(f"  总资产 {nav['total_equity']:,.2f} | 现金 {nav['cash']:,.2f} | 持仓市值 {nav['market_value']:,.2f}")
        print(f"  累计收益 {nav['cumulative_return']:.2%} | 当日收益 {nav['daily_pnl']:,.2f}")
    positions = db.get_positions(d)
    if positions:
        print("  持仓:")
        for p in positions:
            print(f"    {p['symbol']} {p['quantity']} 股 市值 {p['market_value']:,.2f}")
    orders = db.get_orders(signal_date=d)
    if orders:
        print("  下一交易日计划:")
        for o in orders:
            print(f"    {o['side']} {o['symbol']} {o['planned_quantity']} 股 @ {o['reference_price']:.4f}")
    return 0


def cmd_replay(args) -> int:
    config = _load_runtime_config(args)
    db = _db(args, config)
    u = Universe()
    res = replay(
        args.start,
        args.end,
        config,
        db,
        u,
        reset_account=args.reset_account,
        generate_reports=args.report,
    )
    print(f"[replay] {res.start} → {res.end} | {len(res.trading_days)} 个交易日")
    print(f"  prepare {res.prepare_runs} 次 | execute {res.execute_runs} 次 | 成交 {res.total_fills} 笔 | 拒绝 {res.total_rejected} 笔")
    print(f"  异常 {res.anomalies} 条 | 期末资产 {res.final_equity:,.2f} | 期末现金 {res.final_cash:,.2f}")
    return 1 if res.anomalies else 0


def cmd_backtest(args) -> int:
    u = Universe()
    config = apply_universe_data_flags(_load_runtime_config(args), u)
    symbols = args.symbols or u.symbols()
    if not symbols:
        print("标的池为空，请先添加标的。")
        return 2
    res = run_backtest(
        symbols,
        args.start,
        args.end,
        config,
        float(config.get("account", {}).get("initial_cash", 100000)),
    )
    print(f"[backtest] {res.start} → {res.end} | 期末资产 {res.final_equity:,.2f}")
    if not res.nav.empty:
        cum = res.nav.iloc[-1]["cumulative_return"]
        print(f"  累计收益 {cum:.2%} | 交易日 {len(res.nav)} | 成交 {len(res.fills)} 笔")
    return 0


def cmd_compare(args) -> int:
    u = Universe()
    config = apply_universe_data_flags(_load_runtime_config(args), u)
    symbols = args.symbols or u.symbols()
    if not symbols:
        print("标的池为空，请先添加标的。")
        return 2
    initial = float(config.get("account", {}).get("initial_cash", 100000))
    bt = run_backtest(symbols, args.start, args.end, config, initial)

    tmp = Path(tempfile.mkdtemp(prefix="alphalab_compare_")) / "paper.db"
    db = PaperDatabase(tmp)
    db.initialize()
    db.init_account(initial, parse_date(args.start))
    rp = replay(args.start, args.end, config, db, u, reset_account=False)

    replay_nav = pd.DataFrame([dict(r) for r in db.nav_series()])
    replay_positions = [
        {"trade_date": p["trade_date"], "symbol": p["symbol"], "quantity": p["quantity"]}
        for p in db.all_positions()
    ]
    bt_positions = bt.position_snapshots
    diffs = compare_nav_series(bt.nav, replay_nav) + compare_positions(bt_positions, replay_positions)
    print(f"[compare] 回测 vs 回放（{args.start} → {args.end}）")
    print(format_parity_report(diffs))
    return 1 if diffs else 0


def cmd_demo(args) -> int:
    config = _load_runtime_config(args)
    tmp_dir = Path(tempfile.mkdtemp(prefix="alphalab_demo_"))
    real_universe = Universe()
    u = Universe(tmp_dir / "universe.yaml")
    if args.symbols:
        real = _real_meta_symbols()
        names = {}
        classes = {}
        meta = load_etf_metadata([normalize_symbol(s) for s in args.symbols])
        for _, row in meta.iterrows():
            names[row["symbol"]] = row["name"]
            classes[row["symbol"]] = row["asset_class"]
        u.add([normalize_symbol(s) for s in args.symbols], names=names, asset_classes=classes, synthetic=args.synthetic)
    elif not real_universe.symbols():
        names = {sym: name for sym, name, _ in DEFAULT_SEED_SYMBOLS}
        classes = {sym: cls for sym, _, cls in DEFAULT_SEED_SYMBOLS}
        u.add([s for s, _, _ in DEFAULT_SEED_SYMBOLS], names=names, asset_classes=classes, synthetic=args.synthetic)
    else:
        u = real_universe
    symbols = u.symbols()
    if args.synthetic:
        config.setdefault("data", {})["force_synthetic_symbols"] = sorted(set(symbols))

    start = args.start
    end = args.end
    if not start or not end:
        latest = _latest_real_date(config) if not args.synthetic else None
        end = end or latest or pd.Timestamp.today().date().isoformat()
        start = start or (pd.Timestamp(end) - pd.Timedelta(days=45)).date().isoformat()
        print(f"[demo] 自动选择区间 {start} → {end}" + ("（真实行情）" if latest and not args.synthetic else "（合成行情）"))

    tmp = tmp_dir / "paper.db"
    db = PaperDatabase(tmp)
    db.initialize()
    db.init_account(float(config.get("account", {}).get("initial_cash", 100000)), parse_date(start))
    res = replay(start, end, config, db, u, reset_account=False, generate_reports=args.report)
    print(f"[demo] 完成：{res.start} → {res.end} | {len(res.trading_days)} 个交易日")
    print(f"  成交 {res.total_fills} 笔 | 拒绝 {res.total_rejected} 笔 | 异常 {res.anomalies} 条")
    print(f"  期末资产 {res.final_equity:,.2f} | 期末现金 {res.final_cash:,.2f}")
    print(f"  账本: {tmp}")
    return 1 if res.anomalies else 0


def cmd_research_run(args) -> int:
    """运行固定 V0 历史截面研究。"""
    adapter = DuckDBMarketDataAdapter(args.db)
    lab = HistoricalResearchLab(adapter, args.runs_dir)
    horizons = _parse_research_horizons(args.horizons)
    spec = ResearchSpec(
        requested_date=args.as_of,
        rule_version=args.rule_version,
        top_n=args.top_n,
        horizons=horizons,
        initial_cash=args.initial_cash,
        commission_rate=args.commission_rate,
        slippage_rate=args.slippage_rate,
        market=args.market,
        portfolio_weighting=args.portfolio_weighting,
        max_single_weight=args.max_single_weight,
        max_industry_weight=args.max_industry_weight,
        min_holdings=args.min_holdings,
        universe_mode=args.universe_mode,
    )
    result = lab.run(spec)
    print(f"[research] 请求日期 {result.requested_date} | 有效信号日 {result.signal_date}")
    funnel = result.diagnostics.get("funnel", {})
    print(
        f"  universe {funnel.get('universe', 0)} → 合格 {funnel.get('rule_eligible', 0)}"
        f" → Top {len(result.portfolio)}"
    )
    for horizon in sorted(result.performance):
        performance = result.performance[horizon]
        if performance.status == "COMPLETE":
            print(
                f"  {horizon}日: 收益 {performance.total_return:.2%}"
                f" | 最大回撤 {performance.max_drawdown:.2%}"
            )
        else:
            print(f"  {horizon}日: {performance.status}（{performance.message}）")
    print(f"  运行 ID: {result.run_id}")
    print(f"  产物: {result.artifact_dir}")
    return 0


def _parse_research_horizons(value: str) -> tuple[int, ...]:
    """解析研究 CLI 的逗号分隔观察周期。"""
    try:
        horizons = tuple(sorted({int(part.strip()) for part in str(value).split(",") if part.strip()}))
    except ValueError as exc:
        raise ValueError("horizons 必须是逗号分隔的正整数，例如 21,42") from exc
    if not horizons or any(horizon <= 0 for horizon in horizons):
        raise ValueError("horizons 必须是逗号分隔的正整数，例如 21,42")
    return horizons


def _parse_research_dates(value: str) -> tuple[str, ...]:
    dates = tuple(part.strip() for part in str(value).split(",") if part.strip())
    if not dates:
        raise ValueError("as-of 至少需要一个日期")
    try:
        return tuple(parse_date(item).isoformat() for item in dates)
    except ValueError as exc:
        raise ValueError("as-of 必须是逗号分隔的 YYYY-MM-DD 日期") from exc


def cmd_research_study(args) -> int:
    """在多个历史截面重复运行固定因子并输出描述性汇总。"""
    adapter = DuckDBMarketDataAdapter(args.db)
    lab = HistoricalResearchLab(adapter, args.runs_dir)
    horizons = _parse_research_horizons(args.horizons)
    specs = tuple(
        ResearchSpec(
            requested_date=requested_date,
            rule_version=args.rule_version,
            top_n=args.top_n,
            horizons=horizons,
            initial_cash=args.initial_cash,
            commission_rate=args.commission_rate,
            slippage_rate=args.slippage_rate,
            market=args.market,
            portfolio_weighting=args.portfolio_weighting,
            max_single_weight=args.max_single_weight,
            max_industry_weight=args.max_industry_weight,
            min_holdings=args.min_holdings,
            universe_mode=args.universe_mode,
        )
        for requested_date in _parse_research_dates(args.as_of)
    )
    result = lab.run_study(specs, bootstrap_seed=args.bootstrap_seed, bootstrap_samples=args.bootstrap_samples)
    print(f"[research] study {result.study_id} | 截面 {len(result.reports)} 个")
    for row in result.summary.to_dict("records"):
        if row["sample_count"]:
            excess_text = (
                f" | 超额均值 {row['mean_excess']:.2%}"
                f" | 超额95% CI [{row['excess_ci95_low']:.2%}, {row['excess_ci95_high']:.2%}]"
                if row.get("excess_sample_count")
                else " | 超额收益无可评估样本"
            )
            print(
                f"  {row['horizon']}日: 样本 {row['sample_count']}/{row['requested_count']}"
                f" | 均值 {row['mean_return']:.2%} | 胜率 {row['win_rate']:.2%}"
                f"{excess_text}"
                f" | {row['evidence_label']}"
            )
        else:
            print(f"  {row['horizon']}日: 无可评估样本 | {row['evidence_label']}")
    print(f"  产物: {result.artifact_dir}")
    return 0


def cmd_research_review(args) -> int:
    """启动固定研究运行的只读候选审阅页。"""
    serve_review(
        run_id=args.run_id,
        runs_dir=args.runs_dir,
        db_path=args.db,
        host=args.host,
        port=args.port,
    )
    return 0


def cmd_research_list(args) -> int:
    """列出冻结研究运行。"""
    runs = ResearchRunStore(args.runs_dir).list()
    if not runs:
        print(f"[research] 没有运行: {Path(args.runs_dir).expanduser()}")
        return 0
    for item in runs:
        performance = item.get("performance", {})
        metrics = []
        for horizon in sorted(performance, key=lambda value: int(value)):
            result = performance[horizon]
            if result.get("status") == "COMPLETE" and result.get("total_return") is not None:
                metrics.append(f"{horizon}日 {float(result['total_return']):.2%}")
            else:
                metrics.append(f"{horizon}日 {result.get('status', '--')}")
        print(
            f"{item['run_id']} | 请求 {item.get('requested_date', '--')}"
            f" | 信号 {item.get('signal_date', '--')} | 规则 {item.get('rule_version', '--')}"
            f" | 持仓 {item.get('selected_count', 0)} | {'; '.join(metrics) or '无指标'}"
        )
    return 0


def cmd_research_show(args) -> int:
    """显示单次冻结运行摘要。"""
    manifest = ResearchRunStore(args.runs_dir).manifest(args.run_id)
    spec = manifest.get("spec", {})
    diagnostics = manifest.get("diagnostics", {})
    print(f"[research] {manifest.get('run_id')}")
    print(f"  请求日期: {manifest.get('requested_date', '--')} | 有效信号日: {manifest.get('signal_date', '--')}")
    print(f"  规则: {manifest.get('rule_version', '--')} | Top N: {spec.get('top_n', '--')} | 市场: {spec.get('market', '--')}")
    print(f"  数据范围: {' → '.join(str(value) for value in diagnostics.get('data_range', [])) or '--'}")
    for horizon, result in sorted(manifest.get("performance", {}).items(), key=lambda item: int(item[0])):
        total = result.get("total_return")
        drawdown = result.get("max_drawdown")
        if total is None:
            print(f"  {horizon}日: {result.get('status', '--')}（{result.get('message') or ''}）")
        else:
            print(f"  {horizon}日: 收益 {float(total):.2%} | 最大回撤 {float(drawdown):.2%}")
    return 0


def cmd_research_compare(args) -> int:
    """比较两个冻结研究运行。"""
    comparison = ResearchRunStore(args.runs_dir).compare(args.left, args.right)
    left = comparison["left"]; right = comparison["right"]
    print(f"[research] 比较 {left['run_id']} → {right['run_id']}")
    print(f"  规则: {left.get('rule_version', '--')} → {right.get('rule_version', '--')}")
    portfolio = comparison["portfolio"]
    print(f"  新增持仓: {', '.join(portfolio['added']) or '无'}")
    print(f"  移除持仓: {', '.join(portfolio['removed']) or '无'}")
    for horizon, result in comparison["performance"].items():
        delta = result.get("total_return_delta")
        drawdown_delta = result.get("max_drawdown_delta")
        print(
            f"  {horizon}日: 收益差 {float(delta):+.2%}" if delta is not None else f"  {horizon}日: 收益差 --",
            f"| 回撤差 {float(drawdown_delta):+.2%}" if drawdown_delta is not None else "| 回撤差 --",
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alphalab", description="AlphaLab ETF 日线轮动模拟交易闭环")
    parser.add_argument("--version", action="version", version=f"alphalab {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_u = sub.add_parser("universe", help="目标标的池管理")
    p_u.add_argument("action", choices=["list", "add", "remove", "enable", "disable", "seed"])
    p_u.add_argument("symbols", nargs="*")
    p_u.add_argument("--synthetic", action="store_true", help="强制标记为合成行情")
    p_u.set_defaults(func=cmd_universe)

    p_init = sub.add_parser("init-account", help="初始化模拟账户")
    p_init.add_argument("--initial-cash", type=float, default=None)
    p_init.add_argument("--date", default=None)
    p_init.add_argument("--config", default=None)
    p_init.add_argument("--force", action="store_true")
    p_init.add_argument("--db", default=None)
    p_init.set_defaults(func=cmd_init_account)

    for name, help_text in [
        ("prepare", "T 日收盘后生成 T+1 交易计划"),
        ("execute", "T+1 日按开盘价模拟成交"),
        ("reconcile", "对账"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--date", required=True)
        p.add_argument("--config", default=None)
        p.add_argument("--db", default=None)
        p.add_argument("--force", action="store_true")
        p.set_defaults(func=globals()[f"cmd_{name}"])

    p_rpt = sub.add_parser("report", help="生成 Markdown 日报")
    p_rpt.add_argument("--date", required=True)
    p_rpt.add_argument("--config", default=None)
    p_rpt.add_argument("--db", default=None)
    p_rpt.add_argument("--out", default=None)
    p_rpt.set_defaults(func=cmd_report)

    p_status = sub.add_parser("status", help="查看账户状态")
    p_status.add_argument("--date", default=None)
    p_status.add_argument("--config", default=None)
    p_status.add_argument("--db", default=None)
    p_status.set_defaults(func=cmd_status)

    p_replay = sub.add_parser("replay", help="历史回放")
    p_replay.add_argument("--start", required=True)
    p_replay.add_argument("--end", required=True)
    p_replay.add_argument("--config", default=None)
    p_replay.add_argument("--db", default=None)
    p_replay.add_argument("--reset-account", action="store_true")
    p_replay.add_argument("--report", action="store_true")
    p_replay.set_defaults(func=cmd_replay)

    p_bt = sub.add_parser("backtest", help="内存回测（统一策略核心）")
    p_bt.add_argument("--start", required=True)
    p_bt.add_argument("--end", required=True)
    p_bt.add_argument("--config", default=None)
    p_bt.add_argument("--symbols", nargs="*", default=None)
    p_bt.set_defaults(func=cmd_backtest)

    p_cmp = sub.add_parser("compare", help="回测与回放一致性对比")
    p_cmp.add_argument("--start", required=True)
    p_cmp.add_argument("--end", required=True)
    p_cmp.add_argument("--config", default=None)
    p_cmp.add_argument("--symbols", nargs="*", default=None)
    p_cmp.set_defaults(func=cmd_compare)

    p_demo = sub.add_parser("demo", help="一键端到端验证（临时账本）")
    p_demo.add_argument("--start", default=None)
    p_demo.add_argument("--end", default=None)
    p_demo.add_argument("--config", default=None)
    p_demo.add_argument("--symbols", nargs="*", default=None)
    p_demo.add_argument("--synthetic", action="store_true")
    p_demo.add_argument("--report", action="store_true")
    p_demo.set_defaults(func=cmd_demo)

    p_research = sub.add_parser("research", help="历史截面因子研究原型")
    research_sub = p_research.add_subparsers(dest="research_command", required=True)
    p_research_run = research_sub.add_parser("run", help="运行固定 V0 选股与组合前瞻回测")
    p_research_run.add_argument("--as-of", required=True, help="请求的历史日期，例如 2025-07-01")
    p_research_run.add_argument("--rule-version", default="fixed_v0", help="因子插件版本，当前支持 fixed_v0")
    p_research_run.add_argument("--db", default=str(DEFAULT_RESEARCH_DB), help="只读股票行情 DuckDB")
    p_research_run.add_argument("--runs-dir", default=str(DEFAULT_RESEARCH_RUNS), help="研究产物目录")
    p_research_run.add_argument("--top-n", type=int, default=10)
    p_research_run.add_argument("--initial-cash", type=float, default=100000.0)
    p_research_run.add_argument("--horizons", default="21,42", help="观察周期，逗号分隔交易日，例如 21,42")
    p_research_run.add_argument("--commission-rate", type=float, default=0.0003, help="单边佣金比例")
    p_research_run.add_argument("--slippage-rate", type=float, default=0.0005, help="单边滑点比例")
    p_research_run.add_argument("--market", default="a_share", help="市场，V0 默认 a_share")
    p_research_run.add_argument("--portfolio-weighting", choices=["equal", "score"], default="equal")
    p_research_run.add_argument("--max-single-weight", type=float, default=None)
    p_research_run.add_argument("--max-industry-weight", type=float, default=None)
    p_research_run.add_argument("--min-holdings", type=int, default=0)
    p_research_run.add_argument("--universe-mode", choices=["observed-history", "point-in-time"], default="observed-history")
    p_research_run.set_defaults(func=cmd_research_run)
    p_research_review = research_sub.add_parser("review", help="启动固定运行的只读候选审阅页")
    p_research_review.add_argument("--run-id", required=True, help="research run ID")
    p_research_review.add_argument("--db", default=str(DEFAULT_RESEARCH_DB), help="只读股票行情 DuckDB")
    p_research_review.add_argument("--runs-dir", default=str(DEFAULT_RESEARCH_RUNS), help="研究产物目录")
    p_research_review.add_argument("--host", default="127.0.0.1")
    p_research_review.add_argument("--port", type=int, default=8787)
    p_research_review.set_defaults(func=cmd_research_review)
    p_research_list = research_sub.add_parser("list", help="列出冻结研究运行")
    p_research_list.add_argument("--runs-dir", default=str(DEFAULT_RESEARCH_RUNS), help="研究产物目录")
    p_research_list.set_defaults(func=cmd_research_list)
    p_research_show = research_sub.add_parser("show", help="显示单次研究运行摘要")
    p_research_show.add_argument("--run-id", required=True, help="research run ID")
    p_research_show.add_argument("--runs-dir", default=str(DEFAULT_RESEARCH_RUNS), help="研究产物目录")
    p_research_show.set_defaults(func=cmd_research_show)
    p_research_compare = research_sub.add_parser("compare", help="比较两次研究运行")
    p_research_compare.add_argument("--left", required=True, help="基线 run ID")
    p_research_compare.add_argument("--right", required=True, help="对比 run ID")
    p_research_compare.add_argument("--runs-dir", default=str(DEFAULT_RESEARCH_RUNS), help="研究产物目录")
    p_research_compare.set_defaults(func=cmd_research_compare)
    p_research_study = research_sub.add_parser("study", help="多个历史截面研究汇总")
    p_research_study.add_argument("--as-of", required=True, help="逗号分隔的历史日期，例如 2024-01-01,2024-04-01")
    p_research_study.add_argument("--db", default=str(DEFAULT_RESEARCH_DB), help="只读股票行情 DuckDB")
    p_research_study.add_argument("--runs-dir", default=str(DEFAULT_RESEARCH_RUNS), help="研究产物目录")
    p_research_study.add_argument("--rule-version", default="fixed_v0", help="因子插件版本，当前支持 fixed_v0")
    p_research_study.add_argument("--top-n", type=int, default=10)
    p_research_study.add_argument("--initial-cash", type=float, default=100000.0)
    p_research_study.add_argument("--horizons", default="21,42", help="观察周期，逗号分隔交易日")
    p_research_study.add_argument("--commission-rate", type=float, default=0.0003, help="单边佣金比例")
    p_research_study.add_argument("--slippage-rate", type=float, default=0.0005, help="单边滑点比例")
    p_research_study.add_argument("--market", default="a_share", help="市场，V0 默认 a_share")
    p_research_study.add_argument("--portfolio-weighting", choices=["equal", "score"], default="equal")
    p_research_study.add_argument("--max-single-weight", type=float, default=None)
    p_research_study.add_argument("--max-industry-weight", type=float, default=None)
    p_research_study.add_argument("--min-holdings", type=int, default=0)
    p_research_study.add_argument("--universe-mode", choices=["observed-history", "point-in-time"], default="observed-history")
    p_research_study.add_argument("--bootstrap-seed", type=int, default=0)
    p_research_study.add_argument("--bootstrap-samples", type=int, default=2000)
    p_research_study.set_defaults(func=cmd_research_study)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in ("prepare", "execute", "reconcile") and getattr(args, "db", None):
        os.environ["ALPHALAB_DB"] = args.db
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
