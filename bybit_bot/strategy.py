from __future__ import annotations

"""
多时间框架趋势跟踪策略（双向：做多 + 做空）
做多入场：日线确认大方向向上 → 4H趋势共振 → 1H等待入场时机
做空入场：日线确认大方向向下 → 4H趋势共振 → 1H等待入场时机
最低要求：score >= 7/9，且满足三重共振关键条件
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Literal

from config import (
    EMA_FAST, EMA_SLOW, EMA_TREND,
    RSI_PERIOD, RSI_LONG_MIN, RSI_LONG_MAX, RSI_SHORT_MIN, RSI_SHORT_MAX,
    ATR_PERIOD, ATR_SL_MULTIPLIER,
    TP1_RISK_RATIO, TP2_RISK_RATIO,
    RISK_PER_TRADE_PCT, LEVERAGE,
)

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    symbol:        str
    direction:     Literal["BUY", "SELL", "NEUTRAL"]
    stars:         int          # 1–5星
    price:         float
    atr:           float
    sl:            float        # 止损价
    tp1:           float        # 第一止盈
    tp2:           float        # 第二止盈
    position_usdt: float        # 建议入场USDT金额（名义价值/杠杆）
    score:         int          # 原始评分 (0–9)
    reasons:       list[str] = field(default_factory=list)


# ── 技术指标函数 ──────────────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta.clip(upper=0))
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_f = _ema(series, fast)
    ema_s = _ema(series, slow)
    line  = ema_f - ema_s
    sig   = _ema(line, signal)
    hist  = line - sig
    return line, sig, hist


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


# ── 主分析函数 ────────────────────────────────────────────────────────────────

def analyze(
    symbol:    str,
    df_1d:     pd.DataFrame,
    df_4h:     pd.DataFrame,
    df_1h:     pd.DataFrame,
    available_capital: float,
) -> SignalResult:

    price   = float(df_1h["close"].iloc[-1])
    atr_1h  = _atr(df_1h, ATR_PERIOD)
    atr_val = float(atr_1h.iloc[-1])

    # ── 公共指标 ──────────────────────────────────────────────
    ema200   = _ema(df_1d["close"], EMA_TREND).iloc[-1]
    ema50d   = _ema(df_1d["close"], EMA_SLOW).iloc[-1]
    ema20d   = _ema(df_1d["close"], EMA_FAST).iloc[-1]

    ema20_4h = _ema(df_4h["close"], EMA_FAST)
    ema50_4h = _ema(df_4h["close"], EMA_SLOW)
    macd_l4, macd_s4, macd_h4 = _macd(df_4h["close"])

    ema20_1h = _ema(df_1h["close"], EMA_FAST)
    ema50_1h = _ema(df_1h["close"], EMA_SLOW)
    rsi_1h   = _rsi(df_1h["close"], RSI_PERIOD)
    macd_l1, macd_s1, macd_h1 = _macd(df_1h["close"])

    current_rsi = float(rsi_1h.iloc[-1])

    # ── 做多信号评分（9分制）──────────────────────────────────
    long_score   = 0
    long_reasons = []

    # 日线（2分）
    day_above_200 = float(df_1d["close"].iloc[-1]) > ema200
    day_ema_align = ema20d > ema50d
    if day_above_200:
        long_score += 1; long_reasons.append("✅ 日线：价格在EMA200上方（大趋势向上）")
    else:
        long_reasons.append("❌ 日线：价格低于EMA200（大趋势偏空）")
    if day_ema_align:
        long_score += 1; long_reasons.append("✅ 日线：EMA20 > EMA50（多头排列）")
    else:
        long_reasons.append("❌ 日线：EMA20 < EMA50（空头排列）")

    # 4H（3分）
    h4_ema_bull     = ema20_4h.iloc[-1] > ema50_4h.iloc[-1]
    h4_macd_cross   = macd_l4.iloc[-1] > macd_s4.iloc[-1]
    h4_momentum_up  = float(macd_h4.iloc[-1]) > float(macd_h4.iloc[-2])
    if h4_ema_bull:    long_score += 1; long_reasons.append("✅ 4H：EMA多头排列")
    if h4_macd_cross:  long_score += 1; long_reasons.append("✅ 4H：MACD金叉（多头信号）")
    if h4_momentum_up: long_score += 1; long_reasons.append("✅ 4H：MACD动量增强")

    # 1H（4分）
    h1_price_above_ema20 = price > float(ema20_1h.iloc[-1])
    h1_ema_align         = float(ema20_1h.iloc[-1]) > float(ema50_1h.iloc[-1])
    h1_rsi_long_ok       = RSI_LONG_MIN <= current_rsi <= RSI_LONG_MAX
    h1_macd_pos          = float(macd_h1.iloc[-1]) > 0
    if h1_price_above_ema20: long_score += 1; long_reasons.append("✅ 1H：价格 > EMA20")
    if h1_ema_align:          long_score += 1; long_reasons.append("✅ 1H：EMA20 > EMA50")
    if h1_rsi_long_ok:
        long_score += 1; long_reasons.append(f"✅ 1H：RSI={current_rsi:.1f}（做多区间 {RSI_LONG_MIN}-{RSI_LONG_MAX}）")
    else:
        long_reasons.append(f"⚪ 1H：RSI={current_rsi:.1f}（超出做多区间，跳过）")
    if h1_macd_pos:           long_score += 1; long_reasons.append("✅ 1H：MACD直方图 > 0")

    long_key_ok = (
        day_above_200 and day_ema_align
        and h4_ema_bull and h4_macd_cross
        and h1_rsi_long_ok and h1_price_above_ema20
    )

    # ── 做空信号评分（9分制）──────────────────────────────────
    short_score   = 0
    short_reasons = []

    # 日线（2分）
    day_below_200  = float(df_1d["close"].iloc[-1]) < ema200
    day_ema_bear   = ema20d < ema50d
    if day_below_200:
        short_score += 1; short_reasons.append("✅ 日线：价格在EMA200下方（大趋势向下）")
    else:
        short_reasons.append("❌ 日线：价格高于EMA200（大趋势偏多）")
    if day_ema_bear:
        short_score += 1; short_reasons.append("✅ 日线：EMA20 < EMA50（空头排列）")
    else:
        short_reasons.append("❌ 日线：EMA20 > EMA50（多头排列）")

    # 4H（3分）
    h4_ema_bear     = ema20_4h.iloc[-1] < ema50_4h.iloc[-1]
    h4_macd_death   = macd_l4.iloc[-1] < macd_s4.iloc[-1]
    h4_momentum_dn  = float(macd_h4.iloc[-1]) < float(macd_h4.iloc[-2])
    if h4_ema_bear:    short_score += 1; short_reasons.append("✅ 4H：EMA空头排列")
    if h4_macd_death:  short_score += 1; short_reasons.append("✅ 4H：MACD死叉（空头信号）")
    if h4_momentum_dn: short_score += 1; short_reasons.append("✅ 4H：MACD动量减弱")

    # 1H（4分）
    h1_price_below_ema20 = price < float(ema20_1h.iloc[-1])
    h1_ema_bear          = float(ema20_1h.iloc[-1]) < float(ema50_1h.iloc[-1])
    h1_rsi_short_ok      = RSI_SHORT_MIN <= current_rsi <= RSI_SHORT_MAX
    h1_macd_neg          = float(macd_h1.iloc[-1]) < 0
    if h1_price_below_ema20: short_score += 1; short_reasons.append("✅ 1H：价格 < EMA20")
    if h1_ema_bear:           short_score += 1; short_reasons.append("✅ 1H：EMA20 < EMA50")
    if h1_rsi_short_ok:
        short_score += 1; short_reasons.append(f"✅ 1H：RSI={current_rsi:.1f}（做空区间 {RSI_SHORT_MIN}-{RSI_SHORT_MAX}）")
    else:
        short_reasons.append(f"⚪ 1H：RSI={current_rsi:.1f}（超出做空区间，跳过）")
    if h1_macd_neg:           short_score += 1; short_reasons.append("✅ 1H：MACD直方图 < 0")

    short_key_ok = (
        day_below_200 and day_ema_bear
        and h4_ema_bear and h4_macd_death
        and h1_rsi_short_ok and h1_price_below_ema20
    )

    # ── 决策：优先选评分更高的方向 ───────────────────────────
    go_long  = long_score >= 7 and long_key_ok
    go_short = short_score >= 7 and short_key_ok

    if go_long and go_short:
        # 两个都满足，选分数更高的
        if long_score >= short_score:
            go_short = False
        else:
            go_long = False

    if go_long:
        direction = "BUY"
        score     = long_score
        reasons   = long_reasons
        sl        = price - atr_val * ATR_SL_MULTIPLIER
        risk_per  = price - sl
        tp1       = price + risk_per * TP1_RISK_RATIO
        tp2       = price + risk_per * TP2_RISK_RATIO
    elif go_short:
        direction = "SELL"
        score     = short_score
        reasons   = short_reasons
        sl        = price + atr_val * ATR_SL_MULTIPLIER
        risk_per  = sl - price
        tp1       = price - risk_per * TP1_RISK_RATIO
        tp2       = price - risk_per * TP2_RISK_RATIO
    else:
        # 保持观望，选评分较高的方向展示
        direction = "NEUTRAL"
        if long_score >= short_score:
            score   = long_score
            reasons = long_reasons
            reasons.append(f"⚪ 做多评分 {long_score}/9，未达三重共振要求（≥7），保持观望")
        else:
            score   = short_score
            reasons = short_reasons
            reasons.append(f"⚪ 做空评分 {short_score}/9，未达三重共振要求（≥7），保持观望")
        sl  = price - atr_val * ATR_SL_MULTIPLIER
        tp1 = price + (price - sl) * TP1_RISK_RATIO
        tp2 = price + (price - sl) * TP2_RISK_RATIO
        risk_per = price - sl

    # ── 星级换算（0-9分 → 1-5星）──────────────────────────────
    stars = min(5, max(1, round(score / 9 * 5)))

    # ── 仓位大小（固定比例风险，考虑杠杆）──────────────────────
    risk_usdt     = available_capital * RISK_PER_TRADE_PCT
    sl_pct        = abs(risk_per) / price if price > 0 else 0.01
    # 有杠杆时名义仓位更大，但实际亏损仍受sl_pct控制
    position_usdt = (risk_usdt / sl_pct) / LEVERAGE if sl_pct > 0 else 0
    # 单仓保证金不超过总资金1/3
    position_usdt = min(position_usdt, available_capital * 0.33)

    return SignalResult(
        symbol        = symbol,
        direction     = direction,
        stars         = stars,
        price         = price,
        atr           = atr_val,
        sl            = sl,
        tp1           = tp1,
        tp2           = tp2,
        position_usdt = position_usdt,
        score         = score,
        reasons       = reasons,
    )
