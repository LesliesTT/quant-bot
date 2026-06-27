from __future__ import annotations

"""
Discord 通知模块（Bybit 合约版）
"""

import logging
import time
import requests
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from position_manager import Position, State

from config import DISCORD_WEBHOOK_URL, LEVERAGE

logger = logging.getLogger(__name__)

FOOTER = f"QuantBot v2 · 合约模拟盘 · Bybit Testnet · {LEVERAGE}x杠杆"


def _send(payload: dict) -> None:
    if not DISCORD_WEBHOOK_URL or "YOUR_DISCORD" in DISCORD_WEBHOOK_URL:
        logger.warning("Discord Webhook未配置，跳过通知")
        return
    for attempt in range(3):
        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            if resp.status_code == 429:
                wait = float(resp.json().get("retry_after", 1.0))
                logger.warning("Discord限流，%.1fs后重试(第%d次)", wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return
        except Exception as e:
            logger.error("Discord通知失败: %s", e)
            return


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def notify_open(pos: "Position", state: "State") -> None:
    """开仓通知"""
    side_str = "🟢 做多 LONG" if pos.side == "LONG" else "🔴 做空 SHORT"
    color    = 0x00ff88 if pos.side == "LONG" else 0xff4444

    payload = {
        "embeds": [{
            "title":       f"🚀 开仓 {pos.symbol}",
            "description": f"**方向：{side_str}**",
            "color":       color,
            "fields": [
                {"name": "入场价",   "value": f"`{pos.entry_price:.4f}`",   "inline": True},
                {"name": "数量",     "value": f"`{pos.quantity:.4f}`",       "inline": True},
                {"name": "保证金",   "value": f"`${pos.entry_usdt:.2f}`",    "inline": True},
                {"name": "止损",     "value": f"`{pos.sl:.4f}`",             "inline": True},
                {"name": "TP1",     "value": f"`{pos.tp1:.4f}`",             "inline": True},
                {"name": "TP2",     "value": f"`{pos.tp2:.4f}`",             "inline": True},
                {"name": "累计盈亏", "value": f"`${state.cumulative_pnl:.2f}`", "inline": True},
            ],
            "footer": {"text": FOOTER},
            "timestamp": _now_str(),
        }]
    }
    _send(payload)


def notify_close(
    pos: "Position",
    close_price: float,
    close_qty: float,
    reason: str,
    pnl: float,
    state: "State",
) -> None:
    """平仓通知"""
    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
    color   = 0x00ff00 if pnl >= 0 else 0xff0000

    payload = {
        "embeds": [{
            "title":       f"💰 平仓 {pos.symbol} — {reason}",
            "color":       color,
            "fields": [
                {"name": "方向",     "value": f"`{'LONG' if pos.side == 'LONG' else 'SHORT'}`", "inline": True},
                {"name": "平仓价",   "value": f"`{close_price:.4f}`",   "inline": True},
                {"name": "平仓量",   "value": f"`{close_qty:.4f}`",     "inline": True},
                {"name": "本次盈亏", "value": f"`{pnl_str}`",           "inline": True},
                {"name": "累计盈亏", "value": f"`${state.cumulative_pnl:.2f}`", "inline": True},
            ],
            "footer": {"text": FOOTER},
            "timestamp": _now_str(),
        }]
    }
    _send(payload)


def notify_paused(reason: str) -> None:
    """暂停交易通知"""
    payload = {
        "embeds": [{
            "title":       "⚠️ 今日交易已暂停",
            "description": reason,
            "color":       0xffaa00,
            "footer":      {"text": FOOTER},
            "timestamp":   _now_str(),
        }]
    }
    _send(payload)


def notify_startup(symbols: list[str], capital: float) -> None:
    """启动通知"""
    payload = {
        "embeds": [{
            "title":       "🤖 量化机器人已启动",
            "description": f"监控品种：{', '.join(symbols)}",
            "color":       0x0099ff,
            "fields": [
                {"name": "当前资产", "value": f"`${capital:.2f}`", "inline": True},
                {"name": "杠杆",     "value": f"`{LEVERAGE}x`",    "inline": True},
                {"name": "模式",     "value": "`合约双向`",         "inline": True},
            ],
            "footer":    {"text": FOOTER},
            "timestamp": _now_str(),
        }]
    }
    _send(payload)


def notify_daily_summary(state: "State") -> None:
    """每日汇报"""
    from position_manager import current_capital
    stats   = state.daily_stats
    cap     = current_capital(state)
    win_rate = (stats.wins / stats.trades_closed * 100) if stats.trades_closed > 0 else 0
    pnl_str  = f"+${stats.realized_pnl:.2f}" if stats.realized_pnl >= 0 else f"-${abs(stats.realized_pnl):.2f}"
    color    = 0x00ff00 if stats.realized_pnl >= 0 else 0xff4444

    payload = {
        "embeds": [{
            "title": "📊 每日交易汇报",
            "color": color,
            "fields": [
                {"name": "今日盈亏",   "value": f"`{pnl_str}`",              "inline": True},
                {"name": "累计盈亏",   "value": f"`${state.cumulative_pnl:.2f}`", "inline": True},
                {"name": "当前资产",   "value": f"`${cap:.2f}`",             "inline": True},
                {"name": "开仓次数",   "value": f"`{stats.trades_opened}`",  "inline": True},
                {"name": "平仓次数",   "value": f"`{stats.trades_closed}`",  "inline": True},
                {"name": "胜率",       "value": f"`{win_rate:.1f}%`",        "inline": True},
                {"name": "当前持仓",   "value": f"`{len(state.positions)}个`", "inline": True},
            ],
            "footer":    {"text": FOOTER},
            "timestamp": _now_str(),
        }]
    }
    _send(payload)
