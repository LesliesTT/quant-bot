from __future__ import annotations

import logging
import requests
import pandas as pd

from config import BYBIT_MARKET_BASE

logger = logging.getLogger(__name__)

# Bybit interval 映射
_INTERVAL_MAP = {
    "1d":  "D",
    "4h":  "240",
    "1h":  "60",
    "15m": "15",
    "5m":  "5",
}


def fetch_klines(symbol: str, interval: str, limit: int = 300) -> pd.DataFrame | None:
    """从Bybit获取K线数据（v5接口）"""
    bybit_interval = _INTERVAL_MAP.get(interval, interval)
    try:
        resp = requests.get(
            f"{BYBIT_MARKET_BASE}/v5/market/kline",
            params={
                "category": "linear",
                "symbol":   symbol,
                "interval": bybit_interval,
                "limit":    limit,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            logger.error("Bybit K线错误: %s", data.get("retMsg"))
            return None

        # Bybit返回格式: [startTime, open, high, low, close, volume, turnover]
        # 结果是倒序的（最新在前），需要反转
        rows = data["result"]["list"]
        if not rows:
            return None

        df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "turnover"])
        df = df.iloc[::-1].reset_index(drop=True)  # 反转为正序

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"].astype(int), unit="ms")
        return df
    except Exception as e:
        logger.error("获取 %s %s K线失败: %s", symbol, interval, e)
        return None


def fetch_ticker_price(symbol: str) -> float | None:
    """获取当前最新价格"""
    try:
        resp = requests.get(
            f"{BYBIT_MARKET_BASE}/v5/market/tickers",
            params={"category": "linear", "symbol": symbol},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            return None
        items = data["result"]["list"]
        if not items:
            return None
        return float(items[0]["lastPrice"])
    except Exception as e:
        logger.error("获取 %s 价格失败: %s", symbol, e)
        return None


def fetch_lot_size(symbol: str) -> tuple[float, float, int]:
    """获取合约最小数量、步长和精度"""
    try:
        resp = requests.get(
            f"{BYBIT_MARKET_BASE}/v5/market/instruments-info",
            params={"category": "linear", "symbol": symbol},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            return 0.001, 0.001, 3

        items = data["result"]["list"]
        if not items:
            return 0.001, 0.001, 3

        lot = items[0].get("lotSizeFilter", {})
        step_size = float(lot.get("qtyStep", "0.001"))
        min_qty   = float(lot.get("minOrderQty", "0.001"))

        import math
        qty_prec = max(0, int(round(-math.log10(step_size)))) if step_size > 0 else 3
        return min_qty, step_size, qty_prec
    except Exception as e:
        logger.error("获取 %s 合约信息失败: %s", symbol, e)
        return 0.001, 0.001, 3
