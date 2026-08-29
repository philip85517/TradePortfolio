"""配置加载、校验与哈希。"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from .utils import json_hash

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = PACKAGE_ROOT / "config"
DEFAULT_STRATEGY_CONFIG = DEFAULT_CONFIG_DIR / "etf_rotation_v0.yaml"
DEFAULT_ACCOUNT_CONFIG = DEFAULT_CONFIG_DIR / "paper_account.yaml"
DEFAULT_UNIVERSE_CONFIG = DEFAULT_CONFIG_DIR / "paper_universe.yaml"


def _read_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件必须为 YAML 映射: {path}")
    return data


def load_config(path: str | Path | None = None) -> dict:
    """加载策略配置；未指定时使用默认配置。"""
    cfg_path = Path(path) if path else DEFAULT_STRATEGY_CONFIG
    cfg_path = cfg_path.expanduser()
    if not cfg_path.exists():
        raise FileNotFoundError(f"策略配置文件不存在: {cfg_path}")
    return _read_yaml(cfg_path)


def load_account_config(path: str | Path | None = None) -> dict:
    cfg_path = Path(path) if path else DEFAULT_ACCOUNT_CONFIG
    cfg_path = cfg_path.expanduser()
    if not cfg_path.exists():
        raise FileNotFoundError(f"账户配置文件不存在: {cfg_path}")
    return _read_yaml(cfg_path)


def load_universe_config(path: str | Path | None = None) -> dict:
    cfg_path = Path(path) if path else DEFAULT_UNIVERSE_CONFIG
    cfg_path = cfg_path.expanduser()
    if not cfg_path.exists():
        return {"symbols": []}
    return _read_yaml(cfg_path)


def resolve_storage_path(config: dict | None = None, env_value: str | None = None) -> Path:
    """解析 SQLite 数据库路径：优先 env ALPHALAB_DB，其次配置，最后默认。"""
    if env_value:
        return Path(env_value).expanduser()
    cfg = config or {}
    storage = cfg.get("storage", {})
    if storage.get("db_path"):
        p = Path(str(storage["db_path"])).expanduser()
        if not p.is_absolute():
            return REPO_ROOT / p
        return p
    return PACKAGE_ROOT / "storage" / "paper_trading.db"


def resolve_data_dir(config: dict | None = None) -> Path:
    cfg = config or {}
    d = cfg.get("data", {}).get("market_dir")
    if d:
        p = Path(str(d)).expanduser()
        return p if p.is_absolute() else REPO_ROOT / p
    return PACKAGE_ROOT / "data" / "market"


def merge_strategy_account(strategy_cfg: dict, account_cfg: dict) -> dict:
    """合并策略与账户配置，账户字段优先。"""
    merged = copy.deepcopy(strategy_cfg)
    for key in ("account", "storage", "data", "execution"):
        if key in account_cfg:
            merged.setdefault(key, {}).update(copy.deepcopy(account_cfg[key]))
    return merged


def config_hash(cfg: dict) -> str:
    """配置哈希：所有配置变更都会改变哈希，用于订单唯一键与版本追溯。"""
    return json_hash(cfg)


def ensure_required_keys(cfg: dict) -> None:
    required = ("strategy", "universe", "alpha", "portfolio", "execution", "account", "validation")
    missing = [k for k in required if k not in cfg]
    if missing:
        raise ValueError(f"配置缺少必需节点: {', '.join(missing)}")

