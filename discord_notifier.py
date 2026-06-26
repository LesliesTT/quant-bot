from __future__ import annotations
import logging
from datetime import datetime, timezone
import requests
from config import DISCORD_WEBHOOK_URL

logger = logging.getLogger(__name__)
COLOR_BUY=0x00C805; COLOR_SELL=0xFF3B30; COLOR_INFO=0x5865F2
COLOR_WARN=0xFFCC00; COLOR_PROFIT=0x00C805; COLOR_LOSS=0xFF3B30

def _post(payload):
    if "YOUR_DISCORD_WEBHOOK_URL" in DISCORD_WEBHOOK_URL:
        logger.warning("Discord Webhook未配置"); return False
    try:
        r=requests.post(DISCORD_WEBHOOK_URL,json=payload,timeout=10)
        return r.status_code in (200,204)
    except Exception as e: logger.error("Discord推送异常:%s",e); return False

def _ts(): return datetime.now(timezone.utc).isoformat()
def _fmt(p):
    if p>=10000: return f"{p:,.2f}"
    if p>=100: return f"{p:.2f}"
    if p>=1: return f"{p:.4f}"
    return f"{p:.6f}"

def notify_open(pos, state):
    from position_manager import current_capital
    cap=current_capital(state)
    sl_pct=abs(pos.entry_price-pos.sl)/pos.entry_price*100
    tp1_pct=abs(pos.tp1-pos.entry_price)/pos.entry_price*100
    tp2_pct=abs(pos.tp2-pos.entry_price)/pos.entry_price*100
    rr=f"1:{(pos.tp2-pos.entry_price)/max(pos.entry_price-pos.sl,1e-8):.1f}"
    embed={"title":f"📈 开仓 — **{pos.symbol}**","color":COLOR_BUY,"fields":[
        {"name":"💰 入场价","value":f"`{_fmt(pos.entry_price)}`","inline":True},
        {"name":"📦 数量","value":f"`{pos.quantity}`","inline":True},
        {"name":"💵 投入","value":f"`${pos.entry_usdt:.2f}`","inline":True},
        {"name":"🛡 止损","value":f"`{_fmt(pos.sl)}`(-{sl_pct:.1f}%)","inline":True},
        {"name":"🎯 TP1","value":f"`{_fmt(pos.tp1)}`(+{tp1_pct:.1f}%)","inline":True},
        {"name":"🎯 TP2","value":f"`{_fmt(pos.tp2)}`(+{tp2_pct:.1f}%)","inline":True},
        {"name":"⚖️ 盈亏比","value":f"`{rr}`","inline":True},
        {"name":"💼 资产","value":f"`${cap:.2f}`","inline":True},
    ],"footer":{"text":"QuantBot v1 · 模拟盘"},"timestamp":_ts()}
    _post({"embeds":[embed]})

def notify_close(pos, close_price, close_qty, reason, pnl, state):
    from position_manager import current_capital
    cap=current_capital(state); win=pnl>=0
    color=COLOR_PROFIT if win else COLOR_LOSS; e="🟢" if win else "🔴"
    embed={"title":f"📤 平仓 — **{pos.symbol}** ({reason})","color":color,"fields":[
        {"name":"入场价","value":f"`{_fmt(pos.entry_price)}`","inline":True},
        {"name":"平仓价","value":f"`{_fmt(close_price)}`","inline":True},
        {"name":"数量","value":f"`{close_qty}`","inline":True},
        {"name":f"{e} 盈亏","value":f"`${pnl:+.2f}`","inline":True},
        {"name":"💼 资产","value":f"`${cap:.2f}`","inline":True},
        {"name":"📊 今日盈亏","value":f"`${state.daily_stats.realized_pnl:+.2f}`","inline":True},
    ],"footer":{"text":"QuantBot v1 · 模拟盘"},"timestamp":_ts()}
    _post({"embeds":[embed]})

def notify_daily_summary(state):
    from position_manager import current_capital
    ds=state.daily_stats; cap=current_capital(state)
    wr=ds.wins/ds.trades_closed*100 if ds.trades_closed>0 else 0
    e="📈" if ds.realized_pnl>=0 else "📉"
    embed={"title":f"{e} 每日汇报 — {ds.date}","color":COLOR_INFO,"fields":[
        {"name":"开仓","value":str(ds.trades_opened),"inline":True},
        {"name":"平仓","value":str(ds.trades_closed),"inline":True},
        {"name":"胜/负","value":f"{ds.wins}/{ds.losses}","inline":True},
        {"name":"胜率","value":f"{wr:.1f}%","inline":True},
        {"name":"今日盈亏","value":f"`${ds.realized_pnl:+.2f}`","inline":True},
        {"name":"累计资产","value":f"`${cap:.2f}`","inline":True},
    ],"footer":{"text":"QuantBot v1 · 模拟盘"},"timestamp":_ts()}
    _post({"embeds":[embed]})

def notify_paused(reason):
    _post({"embeds":[{"title":"⚠️ 交易暂停","description":reason,"color":COLOR_WARN,"footer":{"text":"QuantBot v1"},"timestamp":_ts()}]})

def notify_startup(symbols, capital):
    _post({"embeds":[{"title":"🚀 QuantBot v1 启动","description":f"**策略**：多时间框架趋势跟踪\n**品种**：{', '.join(symbols)}\n**资金**：${capital:.2f} USDT\n**风控**：单笔≤1.5% · 日亏损≤3%自动暂停\n**止盈**：TP1=1.5R(平50%) · TP2=3.0R(清仓)\n📡 每15分钟扫描 · Binance Testnet","color":COLOR_INFO,"footer":{"text":"QuantBot v1 · 模拟盘"},"timestamp":_ts()}]})
