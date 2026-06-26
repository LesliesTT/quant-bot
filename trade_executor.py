from __future__ import annotations

"""
纸面交易执行器（Paper Trading）
────────────────────────────────────────────────────────
使用真实行情价格模拟开仓/平仓，不调用任何交易所 API
所有盈亏基于真实市场价格计算，结果与真实合约交易一致
────────────────────────────────────────────────────────
"""

import logging
import math

from config import LEVERAGE
from market_data import fetch_ticker_price

logger = logging.getLogger(__name__)

# 各品种合约规格（固定值，无需实时查询）
_LOT_SPECS: dict[str, tuple[float, float, int]] = {
    "BTCUSDT":  (0.001, 0.001, 3),
    "ETHUSDT":  (0.01,  0.01,  2),
    "BNBUSDT":  (0.01,  0.01,  2),
    "SOLUSDT":  (0.1,   0.1,   1),
    "XRPUSDT":  (1.0,   1.0,   0),
}
_DEFAULT_LOT = (0.01, 0.01, 2)


def _floor(value: float, step: float) -> float:
    if step <= 0:
        return value
    precision = max(0, int(round(-math.log10(step))))
    factor = 10 ** precision
    return math.floor(value * factor) / factor


def fetch_lot_size(symbol: str) -> tuple[float, float, int]:
    return _LOT_SPECS.get(symbol, _DEFAULT_LOT)


# ── 初始化（纸面交易无需实际设置）────────────────────────────────────────────

def enable_hedge_mode() -> bool:
    logger.info("✅ [纸面交易] 对冲模式已启用（模拟）")
    return True


def setup_symbol(symbol: str) -> bool:
    logger.info("✅ [纸面交易] %s 杠杆已设置为 %dx（模拟）", symbol, LEVERAGE)
    return True


def get_usdt_balance() -> float:
    """纸面交易余额由 position_manager 的 current_capital 管理"""
    return 0.0


# ── 模拟下单 ──────────────────────────────────────────────────────────────────

def _simulate_order(symbol: str, side: str, usdt_amount: float) -> dict | None:
    """模拟市价开仓，返回成交信息"""
    price = fetch_ticker_price(symbol)
    if not price:
        logger.error("[纸面交易] %s 获取价格失败，跳过", symbol)
        return None

    min_qty, step_size, qty_prec = fetch_lot_size(symbol)
    # 名义仓位 = 保证金 × 杠杆
    raw_qty  = (usdt_amount * LEVERAGE) / price
    quantity = _floor(raw_qty, step_size)

    if quantity < min_qty:
        logger.warning("[纸面交易] %s 计算数量 %.6f < 最小值 %.6f", symbol, quantity, min_qty)
        return None

    logger.info(
        "📝 [纸面交易] %s %s 模拟成交  价格=%.4f  数量=%.{prec}f  保证金=$%.2f  名义=$%.2f".format(prec=qty_prec),
        symbol, side, price, quantity, usdt_amount, usdt_amount * LEVERAGE,
    )
    return {"qty": quantity, "price": price, "side": side, "symbol": symbol, "paper": True}


def _simulate_close(symbol: str, side: str, quantity: float) -> dict | None:
    """模拟市价平仓"""
    price = fetch_ticker_price(symbol)
    if not price:
        logger.error("[纸面交易] %s 获取平仓价格失败", symbol)
        return None

    _, step_size, qty_prec = fetch_lot_size(symbol)
    quantity = _floor(quantity, step_size)

    logger.info(
        "📝 [纸面交易] %s %s 模拟平仓  价格=%.4f  数量=%.{prec}f".format(prec=qty_prec),
        symbol, side, price, quantity,
    )
    return {"qty": quantity, "price": price, "side": side, "symbol": symbol, "paper": True}


# ── 开仓接口 ──────────────────────────────────────────────────────────────────

def place_market_long(symbol: str, usdt_amount: float) -> dict | None:
    """模拟开多仓"""
    return _simulate_order(symbol, "Buy", usdt_amount)


def place_market_short(symbol: str, usdt_amount: float) -> dict | None:
    """模拟开空仓"""
    return _simulate_order(symbol, "Sell", usdt_amount)


def close_long(symbol: str, quantity: float) -> dict | None:
    """模拟平多仓"""
    return _simulate_close(symbol, "Sell", quantity)


def close_short(symbol: str, quantity: float) -> dict | None:
    """模拟平空仓"""
    return _simulate_close(symbol, "Buy", quantity)


# ── 成交价提取 ────────────────────────────────────────────────────────────────

def _avg_fill_price(order: dict, fallback: float) -> tuple[float, float]:
    """从模拟订单提取成交价格和数量"""
    price = float(order.get("price", fallback))
    qty   = float(order.get("qty",   0))
    return price, qty
