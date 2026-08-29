from __future__ import annotations

from io import StringIO

import pandas as pd
import requests


def discover_a_share_universe() -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_info_a_code_name()
    out = pd.DataFrame(
        {
            "market": "a_share",
            "symbol": df["code"].astype(str).str.zfill(6),
            "name": df["name"].astype(str),
            "source_symbol": df["code"].astype(str).str.zfill(6),
            "provider": "baostock",
            "quote_ccy": "CNY",
            "status": "active_or_recent",
        }
    )
    return out.drop_duplicates(["market", "symbol"]).reset_index(drop=True)


def discover_hk_universe() -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_hk_spot()
    out = pd.DataFrame(
        {
            "market": "hk",
            "symbol": df["代码"].astype(str).str.zfill(5),
            "name": df["中文名称"].astype(str),
            "source_symbol": df["代码"].astype(str).str.zfill(5),
            "provider": "akshare",
            "quote_ccy": "HKD",
            "status": "active_or_recent",
        }
    )
    special_unit = out["symbol"].str.startswith("029") & out["name"].str.contains("（", regex=False, na=False)
    out = out[~special_unit]
    return out.drop_duplicates(["market", "symbol"]).reset_index(drop=True)


def discover_us_universe() -> pd.DataFrame:
    nasdaq = requests.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", timeout=30)
    nasdaq.raise_for_status()
    other = requests.get("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", timeout=30)
    other.raise_for_status()
    return parse_nasdaq_trader_universe(nasdaq.text, other.text)


def parse_nasdaq_trader_universe(nasdaq_text: str, other_text: str) -> pd.DataFrame:
    nasdaq = _read_pipe_table(nasdaq_text)
    other = _read_pipe_table(other_text)
    frames = []
    if not nasdaq.empty:
        nasdaq = nasdaq[nasdaq["Test Issue"].eq("N") & nasdaq["ETF"].eq("N")].copy()
        nasdaq["symbol"] = nasdaq["Symbol"].astype(str).str.upper()
        nasdaq["name"] = nasdaq["Security Name"].astype(str)
        nasdaq["source_symbol"] = "105." + nasdaq["symbol"]
        frames.append(nasdaq[["symbol", "name", "source_symbol"]])
    if not other.empty:
        other = other[other["Test Issue"].eq("N") & other["ETF"].eq("N")].copy()
        other["symbol"] = other["ACT Symbol"].astype(str).str.upper()
        other["name"] = other["Security Name"].astype(str)
        other["source_symbol"] = other.apply(_eastmoney_us_symbol, axis=1)
        frames.append(other[["symbol", "name", "source_symbol"]])
    if not frames:
        return pd.DataFrame(columns=["market", "symbol", "name", "source_symbol", "provider", "quote_ccy", "status"])
    out = pd.concat(frames, ignore_index=True)
    out = out[~out["symbol"].str.contains(r"[\^\$]", regex=True, na=False)]
    out = out[_is_us_common_stock(out)]
    out = out.drop_duplicates("symbol", keep="first")
    out["market"] = "us"
    out["provider"] = "akshare"
    out["quote_ccy"] = "USD"
    out["status"] = "active_or_recent"
    return out[["market", "symbol", "name", "source_symbol", "provider", "quote_ccy", "status"]].reset_index(drop=True)


def discover_okx_spot_universe(quote_ccy: str | None = "USDT") -> pd.DataFrame:
    response = requests.get("https://www.okx.com/api/v5/public/instruments", params={"instType": "SPOT"}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX instrument error: {payload}")
    rows = payload.get("data") or []
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["market", "symbol", "name", "source_symbol", "provider", "quote_ccy", "status"])
    df = df[df["state"].eq("live")]
    if quote_ccy:
        df = df[df["quoteCcy"].eq(quote_ccy)]
    symbol = df["instId"].str.replace("-", "/", regex=False)
    out = pd.DataFrame(
        {
            "market": "crypto",
            "symbol": symbol,
            "name": df["instId"],
            "source_symbol": symbol,
            "provider": "ccxt",
            "quote_ccy": df["quoteCcy"],
            "status": df["state"],
        }
    )
    return out.drop_duplicates(["market", "symbol"]).reset_index(drop=True)


def discover_universe(markets: list[str], include_us: bool = False, crypto_quote: str | None = "USDT") -> pd.DataFrame:
    frames = []
    for market in markets:
        if market == "a_share":
            frames.append(discover_a_share_universe())
        elif market == "hk":
            frames.append(discover_hk_universe())
        elif market == "us":
            if include_us:
                frames.append(discover_us_universe())
            else:
                frames.append(pd.DataFrame(columns=["market", "symbol", "name", "source_symbol", "provider", "quote_ccy", "status"]))
        elif market == "crypto":
            frames.append(discover_okx_spot_universe(quote_ccy=crypto_quote))
        else:
            raise ValueError(f"Unsupported market: {market}")
    if not frames:
        return pd.DataFrame(columns=["market", "symbol", "name", "source_symbol", "provider", "quote_ccy", "status"])
    return pd.concat(frames, ignore_index=True)


def _read_pipe_table(text: str) -> pd.DataFrame:
    lines = [line for line in text.splitlines() if "|" in line and not line.startswith("File Creation Time")]
    if not lines:
        return pd.DataFrame()
    return pd.read_csv(StringIO("\n".join(lines)), sep="|", dtype=str, keep_default_na=False).fillna("")


def _eastmoney_us_symbol(row: pd.Series) -> str:
    exchange = str(row.get("Exchange", "")).upper()
    symbol = str(row["symbol"]).upper()
    prefix = {
        "N": "106",
        "A": "107",
        "P": "107",
        "Z": "107",
        "V": "107",
    }.get(exchange, "107")
    return f"{prefix}.{symbol}"


def _is_us_common_stock(df: pd.DataFrame) -> pd.Series:
    symbol = df["symbol"].astype(str).str.upper()
    name = df["name"].astype(str).str.lower()
    non_common_suffix = symbol.str.contains(r"\.(?:W|U|R)$", regex=True, na=False)
    non_common_name = (
        name.str.contains("warrant", regex=False, na=False)
        | name.str.contains(" rights", regex=False, na=False)
        | name.str.contains(" units", regex=False, na=False)
        | name.str.contains("preferred stock", regex=False, na=False)
        | name.str.contains("preferred share", regex=False, na=False)
        | name.str.contains("preference share", regex=False, na=False)
    )
    return ~(non_common_suffix | non_common_name)
