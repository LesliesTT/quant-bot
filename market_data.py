from __future__ import annotations

import logging
import requests
import pandas as pd

from config import BINANCE_MARKET_BASE

logger = logging.getLogger(__name__)

_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


def fetch_klines(symbol: str, interval: str, limit: int = 300) -> pd.DataFrame | None:
    """从Binance获取K线数据（使用真实行情端点，数据更丰富）"""
    try:
        resp = requests.get(
            f"{BINANCE_MARKET_BASE}/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        df = pd.DataFrame(resp.json(), columns=_KLINE_COLS)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        return df
    except Exception as e:
        logger.error("获取 %s %s K线失败: %s", symbol, interval, e)
        return None


def fetch_ticker_price(symbol: str) -> float | None:
    """获取当前最新价格"""
    try:
        resp = requests.get(
            f"{BINANCE_MARKET_BASE}/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=10,
        )
        resp.raise_for_status()
        return float(resp.json()["price"])
    except Exception as e:
        logger.error("获取 %s 价格失败: %s", symbol, e)
        return None


def fetch_lot_size(symbol: str) -> tuple[float, float, int]:
    """获取交易对最小数量、步长和精度"""
    try:
        resp = requests.get(
            f"{BINANCE_MARKET_BASE}/api/v3/exchangeInfo",
            params={"symbol": symbol},
            timeout=15,
        )
        resp.raise_for_status()
        for sym_info in resp.json()["symbols"]:
            if sym_info["symbol"] != symbol:
                continue
            min_qty = step_size = 0.0
            import math
            for f in sym_info["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    min_qty   = float(f["minQty"])
                    step_size = float(f["stepSize"])
            qty_prec = max(0, int(round(-math.log10(step_size)))) if step_size > 0 else 6
            return min_qty, step_size, qty_prec
        return 0.001, 0.001, 3
    except Exception as e:
        logger.error("获取 %s 交易对信息失败: %s", symbol, e)
        return 0.001, 0.001, 3
