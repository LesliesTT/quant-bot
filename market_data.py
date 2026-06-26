from __future__ import annotations

"""
市场数据模块
- K线 / 价格：使用 Binance 公共 API（data-api.binance.vision，GitHub Actions 可访问）
- 合约规格：使用 Bybit 主网（lot size / step size）
"""

import logging
import math
import requests
import pandas as pd

logger = logging.getLogger(__name__)

# Binance 公共行情（无地区限制，GitHub Actions 可访问）
_BINANCE_BASE = "https://data-api.binance.vision"

# Bybit 主网（用于获取合约规格）
_BYBIT_BASE = "https://api.bybit.com"

_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]

# Binance 合约行情用 USDT 永续对（与 Bybit 品种对应）
_BINANCE_FUTURES_BASE = "https://fapi.binance.com"


def fetch_klines(symbol: str, interval: str, limit: int = 300) -> pd.DataFrame | None:
    """获取K线数据（优先用 Binance Futures，回退到 Binance Spot）"""
    # 先尝试 Binance Futures（与 Bybit 合约价格更接近）
    for base, path in [
        (_BINANCE_FUTURES_BASE, "/fapi/v1/klines"),
        (_BINANCE_BASE,         "/api/v3/klines"),
    ]:
        try:
            resp = requests.get(
                f"{base}{path}",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code == 200:
                df = pd.DataFrame(resp.json(), columns=_KLINE_COLS)
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)
                df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
                return df
        except Exception as e:
            logger.debug("K线获取尝试失败(%s): %s", base, e)
            continue

    logger.error("获取 %s %s K线失败：所有来源均不可用", symbol, interval)
    return None


def fetch_ticker_price(symbol: str) -> float | None:
    """获取当前价格（Binance Futures → Binance Spot 回退）"""
    for base, path in [
        (_BINANCE_FUTURES_BASE, "/fapi/v1/ticker/price"),
        (_BINANCE_BASE,         "/api/v3/ticker/price"),
    ]:
        try:
            resp = requests.get(
                f"{base}{path}",
                params={"symbol": symbol},
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code == 200:
                return float(resp.json()["price"])
        except Exception:
            continue

    logger.error("获取 %s 价格失败", symbol)
    return None


def fetch_lot_size(symbol: str) -> tuple[float, float, int]:
    """获取 Bybit 合约最小数量、步长和精度"""
    try:
        resp = requests.get(
            f"{_BYBIT_BASE}/v5/market/instruments-info",
            params={"category": "linear", "symbol": symbol},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            return _binance_lot_size(symbol)

        items = data["result"]["list"]
        if not items:
            return _binance_lot_size(symbol)

        lot       = items[0].get("lotSizeFilter", {})
        step_size = float(lot.get("qtyStep",    "0.001"))
        min_qty   = float(lot.get("minOrderQty","0.001"))
        qty_prec  = max(0, int(round(-math.log10(step_size)))) if step_size > 0 else 3
        return min_qty, step_size, qty_prec
    except Exception as e:
        logger.warning("Bybit lot size 获取失败，回退到 Binance: %s", e)
        return _binance_lot_size(symbol)


def _binance_lot_size(symbol: str) -> tuple[float, float, int]:
    """从 Binance Futures 获取合约规格（备用）"""
    try:
        resp = requests.get(
            f"{_BINANCE_FUTURES_BASE}/fapi/v1/exchangeInfo",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        for sym in resp.json().get("symbols", []):
            if sym["symbol"] != symbol:
                continue
            for f in sym.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                    minq = float(f["minQty"])
                    prec = max(0, int(round(-math.log10(step)))) if step > 0 else 3
                    return minq, step, prec
    except Exception as e:
        logger.error("Binance lot size 获取失败: %s", e)
    return 0.001, 0.001, 3
