from __future__ import annotations

"""
Bybit 测试网合约下单执行器
使用 HMAC-SHA256 签名，Bybit API v5
支持双向持仓（对冲模式）：positionIdx=1(做多) / positionIdx=2(做空)
"""

import hashlib
import hmac
import logging
import math
import time

import requests

from config import BYBIT_API_KEY, BYBIT_SECRET_KEY, BYBIT_TRADE_BASE, BYBIT_MARKET_BASE, LEVERAGE
from market_data import fetch_lot_size, fetch_ticker_price

logger = logging.getLogger(__name__)

RECV_WINDOW = "5000"


# ── 签名 ──────────────────────────────────────────────────────────────────────

def _sign(timestamp: str, payload: str) -> str:
    """Bybit签名: timestamp + api_key + recv_window + payload"""
    msg = timestamp + BYBIT_API_KEY + RECV_WINDOW + payload
    return hmac.new(
        BYBIT_SECRET_KEY.encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _headers(timestamp: str, signature: str) -> dict:
    return {
        "X-BAPI-API-KEY":      BYBIT_API_KEY,
        "X-BAPI-SIGN":         signature,
        "X-BAPI-SIGN-ALGO":    "HmacSHA256",
        "X-BAPI-TIMESTAMP":    timestamp,
        "X-BAPI-RECV-WINDOW":  RECV_WINDOW,
        "Content-Type":        "application/json",
    }


def _get_headers(params: dict) -> dict:
    """GET请求签名（payload为查询字符串）"""
    ts = str(int(time.time() * 1000))
    from urllib.parse import urlencode
    payload = urlencode(params)
    sig = _sign(ts, payload)
    return _headers(ts, sig)


def _post_headers(body: str) -> dict:
    """POST请求签名（payload为JSON字符串）"""
    ts = str(int(time.time() * 1000))
    sig = _sign(ts, body)
    return _headers(ts, sig)


def _floor(value: float, step: float) -> float:
    """向下取整到步长精度"""
    if step <= 0:
        return value
    precision = max(0, int(round(-math.log10(step))))
    factor = 10 ** precision
    return math.floor(value * factor) / factor


# ── 初始化设置 ────────────────────────────────────────────────────────────────

def enable_hedge_mode() -> bool:
    """启用对冲模式（双向持仓），允许同时持有多空"""
    import json
    body = json.dumps({"category": "linear", "mode": 3})
    try:
        resp = requests.post(
            f"{BYBIT_TRADE_BASE}/v5/position/switch-mode",
            headers=_post_headers(body),
            data=body,
            timeout=15,
        )
        data = resp.json()
        ret = data.get("retCode", -1)
        if ret == 0:
            logger.info("✅ 对冲模式已启用")
            return True
        elif ret == 110025:  # 已经是对冲模式
            logger.info("ℹ️ 对冲模式已开启（无需重复设置）")
            return True
        else:
            logger.warning("对冲模式设置: %s", data.get("retMsg"))
            return False
    except Exception as e:
        logger.error("启用对冲模式失败: %s", e)
        return False


def setup_symbol(symbol: str) -> bool:
    """设置杠杆（全仓模式）"""
    import json
    body = json.dumps({
        "category":     "linear",
        "symbol":       symbol,
        "buyLeverage":  str(LEVERAGE),
        "sellLeverage": str(LEVERAGE),
    })
    try:
        resp = requests.post(
            f"{BYBIT_TRADE_BASE}/v5/position/set-leverage",
            headers=_post_headers(body),
            data=body,
            timeout=15,
        )
        data = resp.json()
        ret = data.get("retCode", -1)
        if ret in (0, 110043):  # 0=成功, 110043=杠杆未改变
            logger.info("✅ %s 杠杆已设置为 %dx", symbol, LEVERAGE)
            return True
        else:
            logger.warning("%s 杠杆设置失败: %s", symbol, data.get("retMsg"))
            return False
    except Exception as e:
        logger.error("%s 杠杆设置异常: %s", symbol, e)
        return False


# ── 账户查询 ──────────────────────────────────────────────────────────────────

def get_usdt_balance() -> float:
    """查询测试网USDT合约余额"""
    params = {"accountType": "CONTRACT", "coin": "USDT"}
    try:
        resp = requests.get(
            f"{BYBIT_TRADE_BASE}/v5/account/wallet-balance",
            headers=_get_headers(params),
            params=params,
            timeout=15,
        )
        data = resp.json()
        if data.get("retCode") != 0:
            logger.error("查询余额失败: %s", data.get("retMsg"))
            return 0.0
        for coin in data["result"]["list"][0].get("coin", []):
            if coin["coin"] == "USDT":
                return float(coin.get("walletBalance", 0))
        return 0.0
    except Exception as e:
        logger.error("查询账户余额失败: %s", e)
        return 0.0


# ── 下单 ──────────────────────────────────────────────────────────────────────

def _place_order(symbol: str, side: str, qty: float, position_idx: int) -> dict | None:
    """内部下单函数"""
    import json
    min_qty, step_size, qty_prec = fetch_lot_size(symbol)
    quantity = _floor(qty, step_size)

    if quantity < min_qty:
        logger.warning("%s 计算数量 %.6f < 最小值 %.6f", symbol, quantity, min_qty)
        return None

    body = json.dumps({
        "category":    "linear",
        "symbol":      symbol,
        "side":        side,
        "orderType":   "Market",
        "qty":         f"{quantity:.{qty_prec}f}",
        "positionIdx": position_idx,
        "timeInForce": "IOC",
    })

    try:
        resp = requests.post(
            f"{BYBIT_TRADE_BASE}/v5/order/create",
            headers=_post_headers(body),
            data=body,
            timeout=15,
        )
        data = resp.json()
        if data.get("retCode") == 0:
            order_id = data["result"].get("orderId", "")
            logger.info("✅ 下单成功: %s %s qty=%.6f orderId=%s", side, symbol, quantity, order_id)
            return {"orderId": order_id, "qty": quantity, "side": side, "symbol": symbol}
        else:
            logger.error("下单失败 %s %s: %s", side, symbol, data.get("retMsg"))
            return None
    except Exception as e:
        logger.error("下单异常 %s %s: %s", side, symbol, e)
        return None


def place_market_long(symbol: str, usdt_amount: float) -> dict | None:
    """开多仓：市价买入（positionIdx=1）"""
    price = fetch_ticker_price(symbol)
    if not price:
        return None
    qty = (usdt_amount * LEVERAGE) / price
    return _place_order(symbol, "Buy", qty, position_idx=1)


def place_market_short(symbol: str, usdt_amount: float) -> dict | None:
    """开空仓：市价卖出（positionIdx=2）"""
    price = fetch_ticker_price(symbol)
    if not price:
        return None
    qty = (usdt_amount * LEVERAGE) / price
    return _place_order(symbol, "Sell", qty, position_idx=2)


def close_long(symbol: str, quantity: float) -> dict | None:
    """平多仓：卖出（positionIdx=1）"""
    return _place_order(symbol, "Sell", quantity, position_idx=1)


def close_short(symbol: str, quantity: float) -> dict | None:
    """平空仓：买入（positionIdx=2）"""
    return _place_order(symbol, "Buy", quantity, position_idx=2)


def _avg_fill_price(order: dict, fallback: float) -> tuple[float, float]:
    """从订单提取成交均价和数量（Bybit直接返回qty）"""
    qty = float(order.get("qty", 0))
    return fallback, qty
