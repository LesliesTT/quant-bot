from __future__ import annotations
import hashlib, hmac, logging, math, time
from urllib.parse import urlencode
import requests
from config import BINANCE_API_KEY, BINANCE_SECRET_KEY, BINANCE_TRADE_BASE, BINANCE_MARKET_BASE
from market_data import fetch_lot_size
logger = logging.getLogger(__name__)

def _sign(params):
    q=urlencode(params)
    return hmac.new(BINANCE_SECRET_KEY.encode(),q.encode(),hashlib.sha256).hexdigest()
def _headers(): return {"X-MBX-APIKEY": BINANCE_API_KEY}
def _floor(v,s):
    if s<=0: return v
    p=max(0,int(round(-math.log10(s)))); f=10**p
    return math.floor(v*f)/f

def get_usdt_balance():
    params={"timestamp":int(time.time()*1000)}; params["signature"]=_sign(params)
    try:
        r=requests.get(f"{BINANCE_TRADE_BASE}/api/v3/account",headers=_headers(),params=params,timeout=15)
        r.raise_for_status()
        for a in r.json().get("balances",[]):
            if a["asset"]=="USDT": return float(a["free"])
        return 0.0
    except Exception as e: logger.error("查询余额失败: %s",e); return 0.0

def place_market_buy(symbol, usdt_amount):
    try:
        pr=requests.get(f"{BINANCE_MARKET_BASE}/api/v3/ticker/price",params={"symbol":symbol},timeout=10)
        price=float(pr.json()["price"])
    except Exception as e: logger.error("获取价格失败: %s",e); return None
    min_qty,step,prec=fetch_lot_size(symbol)
    qty=_floor(usdt_amount/price,step)
    if qty<min_qty: logger.warning("%s 数量%s<最小值%s",symbol,qty,min_qty); return None
    params={"symbol":symbol,"side":"BUY","type":"MARKET","quantity":f"{qty:.{prec}f}","timestamp":int(time.time()*1000)}
    params["signature"]=_sign(params)
    try:
        r=requests.post(f"{BINANCE_TRADE_BASE}/api/v3/order",headers=_headers(),params=params,timeout=15)
        r.raise_for_status(); logger.info("✅ 买入 %s qty=%.6f",symbol,qty); return r.json()
    except requests.HTTPError as e: logger.error("买入失败 %s: %s",symbol,e.response.text[:200]); return None
    except Exception as e: logger.error("买入异常 %s: %s",symbol,e); return None

def place_market_sell(symbol, quantity):
    _,step,prec=fetch_lot_size(symbol); quantity=_floor(quantity,step)
    if quantity<=0: return None
    params={"symbol":symbol,"side":"SELL","type":"MARKET","quantity":f"{quantity:.{prec}f}","timestamp":int(time.time()*1000)}
    params["signature"]=_sign(params)
    try:
        r=requests.post(f"{BINANCE_TRADE_BASE}/api/v3/order",headers=_headers(),params=params,timeout=15)
        r.raise_for_status(); logger.info("✅ 卖出 %s qty=%.6f",symbol,quantity); return r.json()
    except requests.HTTPError as e: logger.error("卖出失败 %s: %s",symbol,e.response.text[:200]); return None
    except Exception as e: logger.error("卖出异常 %s: %s",symbol,e); return None

def _avg_fill_price(order, fallback):
    fills=order.get("fills",[])
    if fills:
        tq=sum(float(f["qty"]) for f in fills)
        tc=sum(float(f["qty"])*float(f["price"]) for f in fills)
        return (tc/tq if tq>0 else fallback), tq
    return fallback, float(order.get("executedQty",0))
