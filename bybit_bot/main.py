from __future__ import annotations

"""
量化趋势跟踪机器人 v2
────────────────────────────────────────────────────────
策略：多时间框架趋势跟踪（日线 + 4H + 1H 三重共振）
方向：双向交易（做多 LONG + 做空 SHORT）
风控：单笔最大亏损1.5% · 日亏损≤3%自动暂停 · ATR动态止损
止盈：TP1=1.5R平50% → 止损移至成本 → TP2=3.0R清仓
交易所：Bybit 合约测试网（api-testnet.bybit.com）
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
    TP1_CLOSE_RATIO, INITIAL_CAPITAL, LEVERAGE,
    BYBIT_API_KEY, BYBIT_SECRET_KEY,
)
from market_data import fetch_klines, fetch_ticker_price
from strategy import analyze
from trade_executor import (
    enable_hedge_mode, setup_symbol,
    place_market_long, place_market_short,
    close_long, close_short,
    get_usdt_balance, _avg_fill_price,
)
from position_manager import (
    State, Position,
    load_state, save_state,
    current_capital, daily_loss_pct, record_close,
)
from discord_notifier import (
    notify_open, notify_close, notify_paused,
    notify_startup, notify_daily_summary,
)

# ── 日志配置 ──────────────────────────────────────────────────────────────────
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


# ── PNL计算 ───────────────────────────────────────────────────────────────────

def _pnl(pos: Position, close_price: float, qty: float) -> float:
    """计算盈亏（含杠杆）"""
    if pos.side == "LONG":
        return (close_price - pos.entry_price) * qty * LEVERAGE
    else:  # SHORT
        return (pos.entry_price - close_price) * qty * LEVERAGE


# ── 持仓管理（止损 & 止盈）───────────────────────────────────────────────────

def manage_open_positions(state: State) -> State:
    """检查所有持仓，触发止损/止盈则执行"""
    for sym in list(state.positions.keys()):
        pos   = state.positions[sym]
        price = fetch_ticker_price(sym)
        if price is None:
            logger.warning("  %s 价格获取失败，跳过检查", sym)
            continue

        is_long  = pos.side == "LONG"
        sl_dist  = ((price - pos.sl) / pos.entry_price * 100) if is_long else ((pos.sl - price) / pos.entry_price * 100)
        tp1_dist = ((pos.tp1 - price) / pos.entry_price * 100) if is_long else ((price - pos.tp1) / pos.entry_price * 100)

        logger.info(
            "  持仓 %s [%s] | 现价=%.4f | SL=%.4f(%.1f%%) | TP1=%.4f(%.1f%%) | TP2=%.4f",
            sym, pos.side, price, pos.sl, sl_dist, pos.tp1, tp1_dist, pos.tp2,
        )

        # ── 触发止损 ────────────────────────────────────────────
        sl_triggered = (price <= pos.sl) if is_long else (price >= pos.sl)
        if sl_triggered:
            logger.info("  🛡 %s [%s] 触发止损！现价=%.4f  SL=%.4f", sym, pos.side, price, pos.sl)
            order = close_long(sym, pos.quantity_remaining) if is_long else close_short(sym, pos.quantity_remaining)
            if order:
                actual_price, actual_qty = _avg_fill_price(order, price)
                pnl = _pnl(pos, actual_price, actual_qty)
                record_close(state, pnl, win=False)
                notify_close(pos, actual_price, actual_qty, "止损 🛡", pnl, state)
                del state.positions[sym]
                logger.info("  ❌ %s 止损平仓 盈亏=%.2f USDT", sym, pnl)
            continue

        # ── 触发 TP1 ─────────────────────────────────────────────
        tp1_triggered = (price >= pos.tp1) if is_long else (price <= pos.tp1)
        if not pos.tp1_hit and tp1_triggered:
            close_qty = pos.quantity * TP1_CLOSE_RATIO
            logger.info("  🎯 %s [%s] 触发TP1！现价=%.4f  TP1=%.4f", sym, pos.side, price, pos.tp1)
            order = close_long(sym, close_qty) if is_long else close_short(sym, close_qty)
            if order:
                actual_price, actual_qty = _avg_fill_price(order, price)
                pnl = _pnl(pos, actual_price, actual_qty)
                record_close(state, pnl, win=True)
                notify_close(pos, actual_price, actual_qty, "TP1 🎯", pnl, state)
                pos.tp1_hit            = True
                pos.quantity_remaining -= actual_qty
                pos.sl                 = pos.entry_price   # 止损移至成本
                logger.info("  ✅ %s TP1平仓50%% 盈亏=%.2f USDT  止损移至成本 %.4f", sym, pnl, pos.sl)
            continue

        # ── 触发 TP2 ─────────────────────────────────────────────
        tp2_triggered = (price >= pos.tp2) if is_long else (price <= pos.tp2)
        if pos.tp1_hit and tp2_triggered:
            logger.info("  🎯 %s [%s] 触发TP2！现价=%.4f  TP2=%.4f", sym, pos.side, price, pos.tp2)
            order = close_long(sym, pos.quantity_remaining) if is_long else close_short(sym, pos.quantity_remaining)
            if order:
                actual_price, actual_qty = _avg_fill_price(order, price)
                pnl = _pnl(pos, actual_price, actual_qty)
                record_close(state, pnl, win=True)
                notify_close(pos, actual_price, actual_qty, "TP2 🎯", pnl, state)
                del state.positions[sym]
                logger.info("  ✅ %s TP2全部平仓 盈亏=%.2f USDT", sym, pnl)

    return state


# ── 信号扫描 & 开仓 ───────────────────────────────────────────────────────────

def scan_and_open(state: State) -> State:
    """扫描所有品种，满足条件则开仓"""
    cap = current_capital(state)
    logger.info("🔍 扫描信号  资产=$%.2f  持仓=%d/%d", cap, len(state.positions), MAX_CONCURRENT_POSITIONS)

    actual_balance = get_usdt_balance()
    logger.info("  交易所实际余额: $%.2f", actual_balance)

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

            if sig.direction == "NEUTRAL":
                continue

            # ── 初始化合约设置 ────────────────────────────────────
            setup_symbol(sym)

            if actual_balance < sig.position_usdt:
                logger.warning("  %s 实际余额不足: 交易所$%.2f < 需要$%.2f，跳过开仓", sym, actual_balance, sig.position_usdt)
                continue

            if sig.direction == "BUY":
                logger.info("  🟢 %s 发出做多信号，准备开多仓  保证金=$%.2f", sym, sig.position_usdt)
                order = place_market_long(sym, sig.position_usdt)
                side  = "LONG"
            else:  # SELL
                logger.info("  🔴 %s 发出做空信号，准备开空仓  保证金=$%.2f", sym, sig.position_usdt)
                order = place_market_short(sym, sig.position_usdt)
                side  = "SHORT"

            if order is None:
                logger.error("  %s 下单失败", sym)
                continue

            actual_price, actual_qty = _avg_fill_price(order, sig.price)
            if actual_qty <= 0:
                logger.error("  %s 成交数量为0", sym)
                continue

            pos = Position(
                symbol             = sym,
                side               = side,
                entry_price        = actual_price,
                quantity           = actual_qty,
                quantity_remaining = actual_qty,
                sl                 = sig.sl,
                tp1                = sig.tp1,
                tp2                = sig.tp2,
                tp1_hit            = False,
                entry_time         = datetime.now(timezone.utc).isoformat(),
                entry_usdt         = sig.position_usdt,
            )
            state.positions[sym] = pos
            state.daily_stats.trades_opened += 1
            notify_open(pos, state)
            logger.info(
                "  ✅ %s [%s] 开仓成功  价格=%.4f  数量=%.6f  SL=%.4f  TP1=%.4f  TP2=%.4f",
                sym, side, actual_price, actual_qty, sig.sl, sig.tp1, sig.tp2,
            )
            time.sleep(0.5)

        except Exception as e:
            logger.error("  %s 分析/下单异常: %s", sym, e, exc_info=True)

    return state


# ── 单次运行逻辑 ──────────────────────────────────────────────────────────────

def run_once(state: State) -> State:
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


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="量化趋势跟踪机器人 v2")
    parser.add_argument("--once",    action="store_true", help="单次运行（GitHub Actions模式）")
    parser.add_argument("--summary", action="store_true", help="发送每日汇报到Discord")
    args = parser.parse_args()

    logger.info("══════════════════════════════════════════════")
    logger.info("  量化趋势跟踪机器人 v2 启动")
    logger.info("  策略：多时间框架趋势 | 合约模拟盘 | 双向交易 | %dx杠杆", LEVERAGE)
    logger.info("══════════════════════════════════════════════")

    if not BYBIT_API_KEY or not BYBIT_SECRET_KEY:
        logger.error("未设置 BYBIT_API_KEY / BYBIT_SECRET_KEY，请创建 .env 文件（参考 .env.example）")
        sys.exit(1)

    # 初始化：启用对冲模式（双向持仓）
    enable_hedge_mode()

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
            logger.info("💤 下次扫描：%d 分钟后", SCAN_INTERVAL_MINUTES)
            time.sleep(SCAN_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
