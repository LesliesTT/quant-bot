from __future__ import annotations
import json, logging
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
logger=logging.getLogger(__name__)
STATE_FILE=Path("position_state.json")

@dataclass
class Position:
    symbol:str; entry_price:float; quantity:float; quantity_remaining:float
    sl:float; tp1:float; tp2:float; tp1_hit:bool=False; entry_time:str=""; entry_usdt:float=0.0

@dataclass
class DailyStats:
    date:str=""; trades_opened:int=0; trades_closed:int=0
    wins:int=0; losses:int=0; realized_pnl:float=0.0

@dataclass
class State:
    initial_capital:float=1000.0; total_realized_pnl:float=0.0
    trading_paused_today:bool=False
    positions:dict=field(default_factory=dict)
    daily_stats:DailyStats=field(default_factory=DailyStats)

def load_state():
    if STATE_FILE.exists():
        try:
            raw=json.loads(STATE_FILE.read_text(encoding="utf-8"))
            s=State(initial_capital=raw.get("initial_capital",1000.0),
                    total_realized_pnl=raw.get("total_realized_pnl",0.0),
                    trading_paused_today=raw.get("trading_paused_today",False))
            for sym,pd_ in raw.get("positions",{}).items(): s.positions[sym]=Position(**pd_)
            ds=raw.get("daily_stats",{}); s.daily_stats=DailyStats(**ds) if ds else DailyStats()
            today=date.today().isoformat()
            if s.daily_stats.date!=today:
                s.daily_stats=DailyStats(date=today); s.trading_paused_today=False
                logger.info("🌅 新的一天，日统计已重置")
            return s
        except Exception as e: logger.error("加载状态失败:%s",e)
    s=State(); s.daily_stats=DailyStats(date=date.today().isoformat()); return s

def save_state(state):
    data={"initial_capital":state.initial_capital,"total_realized_pnl":state.total_realized_pnl,
          "trading_paused_today":state.trading_paused_today,
          "positions":{sym:asdict(p) for sym,p in state.positions.items()},
          "daily_stats":asdict(state.daily_stats)}
    STATE_FILE.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding="utf-8")

def current_capital(s): return s.initial_capital+s.total_realized_pnl
def daily_loss_pct(s):
    cap=current_capital(s); return s.daily_stats.realized_pnl/cap if cap else 0.0
def record_close(s,pnl,win):
    s.total_realized_pnl+=pnl; s.daily_stats.realized_pnl+=pnl
    s.daily_stats.trades_closed+=1
    if win: s.daily_stats.wins+=1
    else: s.daily_stats.losses+=1
