from __future__ import annotations
import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Literal
from config import EMA_FAST, EMA_SLOW, EMA_TREND, RSI_PERIOD, RSI_MIN, RSI_MAX, ATR_PERIOD, ATR_SL_MULTIPLIER, TP1_RISK_RATIO, TP2_RISK_RATIO, RISK_PER_TRADE_PCT
logger = logging.getLogger(__name__)

@dataclass
class SignalResult:
    symbol: str
    direction: Literal["BUY", "NEUTRAL"]
    stars: int
    price: float
    atr: float
    sl: float
    tp1: float
    tp2: float
    position_usdt: float
    score: int
    reasons: list = field(default_factory=list)

def _ema(s, p): return s.ewm(span=p, adjust=False).mean()
def _rsi(s, p=14):
    d=s.diff(); g=d.clip(lower=0); l=(-d.clip(upper=0))
    ag=g.ewm(com=p-1,adjust=False).mean(); al=l.ewm(com=p-1,adjust=False).mean()
    rs=ag/al.replace(0,np.nan); return 100-100/(1+rs)
def _macd(s,f=12,sl=26,sig=9):
    l=_ema(s,f)-_ema(s,sl); sg=_ema(l,sig); return l,sg,l-sg
def _atr(df,p=14):
    pc=df["close"].shift(1)
    tr=pd.concat([df["high"]-df["low"],(df["high"]-pc).abs(),(df["low"]-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(com=p-1,adjust=False).mean()

def analyze(symbol, df_1d, df_4h, df_1h, available_capital):
    price=float(df_1h["close"].iloc[-1]); reasons=[]; score=0
    ema200=_ema(df_1d["close"],EMA_TREND).iloc[-1]
    ema50d=_ema(df_1d["close"],EMA_SLOW).iloc[-1]
    ema20d=_ema(df_1d["close"],EMA_FAST).iloc[-1]
    day_above_200=float(df_1d["close"].iloc[-1])>ema200
    day_ema_align=ema20d>ema50d
    if day_above_200: score+=1; reasons.append("✅ 日线：价格在EMA200上方")
    else: reasons.append("❌ 日线：价格低于EMA200")
    if day_ema_align: score+=1; reasons.append("✅ 日线：EMA20>EMA50（多头排列）")
    ema20_4h=_ema(df_4h["close"],EMA_FAST); ema50_4h=_ema(df_4h["close"],EMA_SLOW)
    ml4,ms4,mh4=_macd(df_4h["close"])
    h4_ema=ema20_4h.iloc[-1]>ema50_4h.iloc[-1]
    h4_macd=ml4.iloc[-1]>ms4.iloc[-1]
    h4_mom=float(mh4.iloc[-1])>float(mh4.iloc[-2])
    if h4_ema: score+=1; reasons.append("✅ 4H：EMA多头排列")
    if h4_macd: score+=1; reasons.append("✅ 4H：MACD金叉")
    if h4_mom: score+=1; reasons.append("✅ 4H：动量增强")
    ema20_1h=_ema(df_1h["close"],EMA_FAST); ema50_1h=_ema(df_1h["close"],EMA_SLOW)
    rsi1=_rsi(df_1h["close"],RSI_PERIOD); ml1,ms1,mh1=_macd(df_1h["close"]); atr1=_atr(df_1h,ATR_PERIOD)
    cur_rsi=float(rsi1.iloc[-1]); atr_val=float(atr1.iloc[-1])
    h1_p=float(df_1h["close"].iloc[-1])>float(ema20_1h.iloc[-1])
    h1_e=float(ema20_1h.iloc[-1])>float(ema50_1h.iloc[-1])
    h1_r=RSI_MIN<=cur_rsi<=RSI_MAX
    h1_m=float(mh1.iloc[-1])>0
    if h1_p: score+=1; reasons.append("✅ 1H：价格>EMA20")
    if h1_e: score+=1; reasons.append("✅ 1H：EMA20>EMA50")
    if h1_r: score+=1; reasons.append(f"✅ 1H：RSI={cur_rsi:.1f}（入场区间）")
    else: reasons.append(f"⚪ 1H：RSI={cur_rsi:.1f}（超出区间）")
    if h1_m: score+=1; reasons.append("✅ 1H：MACD>0")
    stars=min(5,max(1,round(score/9*5)))
    key_ok=(day_above_200 and day_ema_align and h4_ema and h4_macd and h1_r and h1_p)
    direction="BUY" if (score>=7 and key_ok) else "NEUTRAL"
    if direction=="NEUTRAL": reasons.append(f"⚪ 评分{score}/9，未达三重共振，保持观望")
    sl=price-atr_val*ATR_SL_MULTIPLIER; risk=price-sl
    tp1=price+risk*TP1_RISK_RATIO; tp2=price+risk*TP2_RISK_RATIO
    risk_usdt=available_capital*RISK_PER_TRADE_PCT
    sl_pct=risk/price
    pos_usdt=min(risk_usdt/sl_pct if sl_pct>0 else 0, available_capital*0.33)
    return SignalResult(symbol=symbol,direction=direction,stars=stars,price=price,atr=atr_val,sl=sl,tp1=tp1,tp2=tp2,position_usdt=pos_usdt,score=score,reasons=reasons)
