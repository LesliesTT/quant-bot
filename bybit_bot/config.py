from __future__ import annotations
import os

# ── Bybit API（测试网）────────────────────────────────────────────────────────
BYBIT_API_KEY    = os.getenv("BINANCE_API_KEY",    "YOUR_BYBIT_TESTNET_API_KEY")
BYBIT_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "YOUR_BYBIT_TESTNET_SECRET_KEY")

# 行情端点（Bybit主网，数据更完整）
BYBIT_MARKET_BASE = "https://api.bybit.com"
# 交易端点（Bybit测试网）
BYBIT_TRADE_BASE  = "https://api-testnet.bybit.com"

# ── Discord ──────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "YOUR_DISCORD_WEBHOOK_URL_HERE")

# ── 交易品种 ─────────────────────────────────────────────────────────────────
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

# ── 合约参数 ─────────────────────────────────────────────────────────────────
LEVERAGE    = 5           # 杠杆倍数
MARGIN_TYPE = "CROSSED"   # 全仓保证金

# ── 风控参数 ─────────────────────────────────────────────────────────────────
INITIAL_CAPITAL          = 1000.0   # 模拟初始资金 $1000 USDT
RISK_PER_TRADE_PCT       = 0.015    # 单笔最大亏损 1.5%
MAX_CONCURRENT_POSITIONS = 3        # 最多同时持仓数
DAILY_LOSS_LIMIT_PCT     = 0.03     # 日亏损上限 3%，超过则暂停当日开仓
ATR_PERIOD               = 14       # ATR周期
ATR_SL_MULTIPLIER        = 1.5      # 止损 = ATR × 1.5
TP1_RISK_RATIO           = 1.5      # TP1 盈亏比（1.5R）
TP2_RISK_RATIO           = 3.0      # TP2 盈亏比（3.0R）
TP1_CLOSE_RATIO          = 0.5      # TP1触发时平仓比例（50%）

# ── 技术指标参数 ─────────────────────────────────────────────────────────────
EMA_FAST   = 20
EMA_SLOW   = 50
EMA_TREND  = 200    # 日线趋势均线
RSI_PERIOD = 14
# 做多RSI区间
RSI_LONG_MIN  = 40
RSI_LONG_MAX  = 70
# 做空RSI区间
RSI_SHORT_MIN = 30
RSI_SHORT_MAX = 65

# ── 扫描间隔 ─────────────────────────────────────────────────────────────────
SCAN_INTERVAL_MINUTES = 60
