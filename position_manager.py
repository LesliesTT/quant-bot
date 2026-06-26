from __future__ import annotations

"""
持仓状态管理（JSON缓存，兼容 GitHub Actions Cache）
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

from config import INITIAL_CAPITAL

logger = logging.getLogger(__name__)

STATE_FILE = "positions.json"


@dataclass
class DailyStats:
    realized_pnl:   float = 0.0
    trades_opened:  int   = 0
    trades_closed:  int   = 0
    wins:           int   = 0
    losses:         int   = 0


@dataclass
class Position:
    symbol:             str
    side:               str    # "LONG" 或 "SHORT"
    entry_price:        float
    quantity:           float
    quantity_remaining: float
    sl:                 float
    tp1:                float
    tp2:                float
    tp1_hit:            bool
    entry_time:         str
    entry_usdt:         float   # 保证金（非名义价值）


@dataclass
class State:
    positions:            dict[str, Position] = field(default_factory=dict)
    daily_stats:          DailyStats          = field(default_factory=DailyStats)
    cumulative_pnl:       float               = 0.0
    trading_paused_today: bool                = False


# ── 序列化 / 反序列化 ─────────────────────────────────────────────────────────

def _pos_from_dict(d: dict) -> Position:
    d.setdefault("side", "LONG")   # 向后兼容旧数据
    return Position(
        symbol             = d["symbol"],
        side               = d["side"],
        entry_price        = d["entry_price"],
        quantity           = d["quantity"],
        quantity_remaining = d["quantity_remaining"],
        sl                 = d["sl"],
        tp1                = d["tp1"],
        tp2                = d["tp2"],
        tp1_hit            = d["tp1_hit"],
        entry_time         = d["entry_time"],
        entry_usdt         = d["entry_usdt"],
    )


def _state_to_dict(state: State) -> dict:
    return {
        "positions":            {k: asdict(v) for k, v in state.positions.items()},
        "daily_stats":          asdict(state.daily_stats),
        "cumulative_pnl":       state.cumulative_pnl,
        "trading_paused_today": state.trading_paused_today,
    }


def load_state() -> State:
    if not os.path.exists(STATE_FILE):
        logger.info("未找到状态文件，初始化新状态")
        return State()
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        state = State(
            positions            = {k: _pos_from_dict(v) for k, v in d.get("positions", {}).items()},
            daily_stats          = DailyStats(**d.get("daily_stats", {})),
            cumulative_pnl       = d.get("cumulative_pnl", 0.0),
            trading_paused_today = d.get("trading_paused_today", False),
        )
        logger.info("状态已加载：持仓=%d  累计盈亏=%.2f", len(state.positions), state.cumulative_pnl)
        return state
    except Exception as e:
        logger.error("状态文件读取失败: %s，重置状态", e)
        return State()


def save_state(state: State) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_state_to_dict(state), f, ensure_ascii=False, indent=2)
        logger.info("状态已保存")
    except Exception as e:
        logger.error("状态保存失败: %s", e)


# ── 辅助计算 ──────────────────────────────────────────────────────────────────

def current_capital(state: State) -> float:
    """当前可用资本（初始资金 + 累计盈亏）"""
    return max(0.0, INITIAL_CAPITAL + state.cumulative_pnl)


def daily_loss_pct(state: State) -> float:
    """今日亏损占初始资金的比例（负值表示亏损）"""
    cap = INITIAL_CAPITAL + state.cumulative_pnl - state.daily_stats.realized_pnl
    if cap <= 0:
        return -1.0
    return state.daily_stats.realized_pnl / cap


def record_close(state: State, pnl: float, win: bool) -> None:
    """记录一笔平仓"""
    state.cumulative_pnl             += pnl
    state.daily_stats.realized_pnl   += pnl
    state.daily_stats.trades_closed  += 1
    if win:
        state.daily_stats.wins   += 1
    else:
        state.daily_stats.losses += 1
