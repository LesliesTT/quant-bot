from __future__ import annotations

"""
量化趋势跟踪机器人 v1
────────────────────────────────────────────────────────
策略：多时间框架趋势跟踪（日线 + 4H + 1H 三重共振）
风控：单笔最大亏损1.5% · 日亏损≤3%自动暂停 · ATR动态止损
止盈：TP1=1.5R平50% → 止损移至成本 → TP2=3.0R清仓
交易所：Binance 现货测试网（testnet.binance.vision）
────────────────────────────────────────────────────────
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from config import (
    SYMBOLS, SCAN_INTERVAL_MINUTES,
    MAX_CONCURRENT_POSITIONS, DAILY_LOSS_LIMIT_PCT,
    TP1_CLOSE_RATIO, INITIAL_CAPITAL,
)
from market_data import fetch_klines, fetch_ticker_price
from strategy import analyze
from trade_executor import place_market_buy, place_market_sell, get_usdt_balance, _avg_fill_price
from position_manager import (
    State, Position,
    load_state, save_state,
    current_capital, daily_loss_pct, record_close,
)
from discord_notifier import (
    notify_open, notify_close, notify_paused,
    notify_startup, notify_daily_summary,
)

# ── 日志配置 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("quant_bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ── 持仓管理（止损 & 止盈）──────────────────────────────────────────────────

def manage_open_positions(state: State) -> State:
    """检查所有持仓，触发止损/止盈则执行"""
    for sym in list(state.positions.keys()):
        pos = state.positions[sym]
        price = fetch_ticker_price(sym)
        if price is None:
            logger.warning("  %s 价格获取失败，跳过检查", sym)
            continue

        sl_dist  = (price - pos.sl)  / pos.entry_price * 100
        tp1_dist = (pos.tp1 - price) / pos.entry_price * 100
        tp2_dist = (pos.tp2 - price) / pos.entry_price * 100

        logger.info(
            "  持仓 %s | 现价=%.4f | SL=%.4f(%.1f%%) | TP1=%.4f(%.1f%%) | TP2=%.4f(%.1f%%)",
            sym, price,
            pos.sl, sl_dist, pos.tp1, tp1_dist, pos.tp2, tp2_dist,
        )

        # ── 触发止损 ────────────────────────────────────────────
        if price <= pos.sl:
            logger.info("  🛡 %s 触发止损！现价=%.4f  SL=%.4f", sym, price, pos.sl)
            order = place_market_sell(sym, pos.quantity_remaining)
            if order:
                actual_price, actual_qty = _avg_fill_price(order, price)
                pnl = (actual_price - pos.entry_price) * actual_qty
                record_close(state, pnl, win=False)
                notify_close(pos, actual_price, actual_qty, "止损 🛡", pnl, state)
                del state.positions[sym]
                logger.info("  ❌ %s 止损平仓 盈亏=%.2f USDT", sym, pnl)
            continue

        # ── 触发 TP1（未触发时检查）────────────────────────────
        if not pos.tp1_hit and price >= pos.tp1:
            close_qty = pos.quantity * TP1_CLOSE_RATIO
            logger.info("  🎯 %s 触发TP1！现价=%.4f  TP1=%.4f", sym, price, pos.tp1)
            order = place_market_sell(sym, close_qty)
            if order:
                actual_price, actual_qty = _avg_fill_price(order, price)
                pnl = (actual_price - pos.entry_price) * actual_qty
                record_close(state, pnl, win=True)
                notify_close(pos, actual_price, actual_qty, "TP1 🎯", pnl, state)
                pos.tp1_hit           = True
                pos.quantity_remaining -= actual_qty
                pos.sl                = pos.entry_price
                logger.info("  ✅ %s TP1平仓50%% 盈亏=%.2f USDT  止损移至成本 %.4f", sym, pnl, pos.sl)
            continue

        # ── 触发 TP2（TP1已触发后检查）─────────────────────────
        if pos.tp1_hit and price >= pos.tp2:
            logger.info("  🎯 %s 触发TP2！现价=%.4f  TP2=%.4f", sym, price, pos.tp2)
            order = place_market_sell(sym, pos.quantity_remaining)
            if order:
                actual_price, actual_qty = _avg_fill_price(order, price)
                pnl = (actual_price - pos.entry_price) * actual_qty
                record_close(state, pnl, win=True)
                notify_close(pos, actual_price, actual_qty, "TP2 🎯", pnl, state)
                del state.positions[sym]
                logger.info("  ✅ %s TP2全部平仓 盈亏=%.2f USDT", sym, pnl)

    return state


# ── 信号扫描 & 开仓 ──────────────────────────────────────────────────────────

def scan_and_open(state: State) -> State:
    """扫描所有品种，满足条件则开仓"""
    cap = current_capital(state)
    logger.info("🔍 扫描信号  资产=$%.2f  持仓=%d/%d", cap, len(state.positions), MAX_CONCURRENT_POSITIONS)

    for sym in SYMBOLS:
        if sym in state.positions:
            logger.info("  %s 已持仓，跳过扫描", sym)
            continue

        if len(state.positions) >= MAX_CONCURRENT_POSITIONS:
            logger.info("  持仓已满（%d/%d），停止扫描", len(state.positions), MAX_CONCURRENT_POSITIONS)
            break

        try:
            df_1d = fetch_klines(sym, "1d", limit=300)
            df_4h = fetch_klines(sym, "4h", limit=200)
            df_1h = fetch_klines(sym, "1h", limit=100)

            if df_1d is None or df_4h is None or df_1h is None:
                logger.warning("  %s K线数据不完整，跳过", sym)
                continue

            sig = analyze(sym, df_1d, df_4h, df_1h, cap)

            logger.info("  %s → %s %d星 评分=%d/9", sym, sig.direction, sig.stars, sig.score)
            for r in sig.reasons:
                logger.info("    %s", r)

            if sig.direction != "BUY":
                continue

            logger.info("  🛒 %s 发出BUY信号，准备开仓  预算=$%.2f", sym, sig.position_usdt)
            order = place_market_buy(sym, sig.position_usdt)
            if order is None:
                logger.error("  %s 下单失败", sym)
                continue

            actual_price, actual_qty = _avg_fill_price(order, sig.price)
            if actual_qty <= 0:
                logger.error("  %s 成交数量为0", sym)
                continue

            pos = Position(
                symbol             = sym,
                entry_price        = actual_price,
                quantity           = actual_qty,
                quantity_remaining = actual_qty,
                sl                 = sig.sl,
                tp1                = sig.tp1,
                tp2                = sig.tp2,
                tp1_hit            = False,
                entry_time         = datetime.now(timezone.utc).isoformat(),
                entry_usdt         = actual_price * actual_qty,
            )
            state.positions[sym] = pos
            state.daily_stats.trades_opened += 1
            notify_open(pos, state)
            logger.info(
                "  ✅ %s 开仓成功  价格=%.4f  数量=%.6f  SL=%.4f  TP1=%.4f  TP2=%.4f",
                sym, actual_price, actual_qty, sig.sl, sig.tp1, sig.tp2,
            )
            time.sleep(0.5)

        except Exception as e:
            logger.error("  %s 分析/下单异常: %s", sym, e, exc_info=True)

    return state


# ── 单次运行逻辑 ─────────────────────────────────────────────────────────────

def run_once(state: State) -> State:
    """GitHub Actions 每次触发执行的核心逻辑"""

    loss_pct = daily_loss_pct(state)
    if loss_pct <= -DAILY_LOSS_LIMIT_PCT:
        if not state.trading_paused_today:
            msg = f"今日亏损已达 {abs(loss_pct)*100:.1f}%（上限{DAILY_LOSS_LIMIT_PCT*100:.0f}%），暂停今日新开仓"
            logger.warning("⚠️ %s", msg)
            notify_paused(msg)
            state.trading_paused_today = True
        else:
            logger.info("⚠️ 今日交易暂停中（亏损保护已触发）")
        return manage_open_positions(state)

    state = manage_open_positions(state)
    state = scan_and_open(state)

    return state


# ── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="量化趋势跟踪机器人 v1")
    parser.add_argument("--once",    action="store_true", help="单次运行（GitHub Actions模式）")
    parser.add_argument("--summary", action="store_true", help="发送每日汇报到Discord")
    args = parser.parse_args()

    logger.info("══════════════════════════════════════════════")
    logger.info("  量化趋势跟踪机器人 v1 启动")
    logger.info("  策略：多时间框架趋势 | 测试网现货 | 风控1.5%%")
    logger.info("══════════════════════════════════════════════")

    state = load_state()
    cap   = current_capital(state)
    logger.info("当前资产：$%.2f  持仓：%d个  今日盈亏：$%.2f",
                cap, len(state.positions), state.daily_stats.realized_pnl)

    if args.summary:
        notify_daily_summary(state)
        logger.info("每日汇报已推送")
        return

    if args.once:
        state = run_once(state)
        save_state(state)
        logger.info("单次运行完成，状态已保存")
    else:
        notify_startup(SYMBOLS, cap)
        while True:
            try:
                state = run_once(state)
                save_state(state)
            except Exception as e:
                logger.error("运行异常: %s", e, exc_info=True)
            next_scan = SCAN_INTERVAL_MINUTES
            logger.info("💤 下次扫描：%d 分钟后", next_scan)
            time.sleep(next_scan * 60)


if __name__ == "__main__":
    main()
