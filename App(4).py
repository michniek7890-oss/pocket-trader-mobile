from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime, timezone
import time
import threading
import math
import sqlite3
import os
import json
import urllib.parse
import urllib.request
import urllib.error

INSTRUMENTS = [
 {"symbol":"EUR/USD","name":"Euro / dolar","category":"Forex"}, {"symbol":"GBP/USD","name":"Funt / dolar","category":"Forex"},
 {"symbol":"USD/JPY","name":"Dolar / jen","category":"Forex"}, {"symbol":"AUD/USD","name":"Dolar australijski / dolar","category":"Forex"},
 {"symbol":"USD/CAD","name":"Dolar / dolar kanadyjski","category":"Forex"}, {"symbol":"USD/CHF","name":"Dolar / frank","category":"Forex"},
 {"symbol":"NZD/USD","name":"Dolar nowozelandzki / dolar","category":"Forex"}, {"symbol":"EUR/JPY","name":"Euro / jen","category":"Forex"},
 {"symbol":"EUR/GBP","name":"Euro / funt","category":"Forex"}, {"symbol":"XAU/USD","name":"Złoto / dolar","category":"Surowce"},
 {"symbol":"XAG/USD","name":"Srebro / dolar","category":"Surowce"}, {"symbol":"WTI","name":"Ropa WTI","category":"Surowce"},
 {"symbol":"BRENT","name":"Ropa Brent","category":"Surowce"}, {"symbol":"SPX","name":"S&P 500","category":"Indeksy"},
 {"symbol":"NDX","name":"Nasdaq 100","category":"Indeksy"}, {"symbol":"DJI","name":"Dow Jones","category":"Indeksy"},
 {"symbol":"DAX","name":"DAX 40","category":"Indeksy"}, {"symbol":"FTSE","name":"FTSE 100","category":"Indeksy"},
 {"symbol":"AAPL","name":"Apple","category":"Akcje"}, {"symbol":"MSFT","name":"Microsoft","category":"Akcje"},
 {"symbol":"NVDA","name":"NVIDIA","category":"Akcje"}, {"symbol":"TSLA","name":"Tesla","category":"Akcje"},
 {"symbol":"AMZN","name":"Amazon","category":"Akcje"}, {"symbol":"GOOGL","name":"Alphabet","category":"Akcje"},
 {"symbol":"BTC/USD","name":"Bitcoin / dolar","category":"Kryptowaluty"}, {"symbol":"ETH/USD","name":"Ethereum / dolar","category":"Kryptowaluty"},
 {"symbol":"XRP/USD","name":"XRP / dolar","category":"Kryptowaluty"}, {"symbol":"SOL/USD","name":"Solana / dolar","category":"Kryptowaluty"}
]
INSTRUMENT_BY_SYMBOL = {x["symbol"]: x for x in INSTRUMENTS}

app = FastAPI(title="Pocket Trader Mobile DEMO")
DB = Path("trades.db")
_DATA_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 45


def init_db():
    with sqlite3.connect(DB) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset TEXT NOT NULL,
                direction TEXT NOT NULL,
                stake REAL NOT NULL,
                result TEXT NOT NULL,
                profit REAL NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candle_time INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                asset TEXT NOT NULL,
                direction TEXT NOT NULL,
                score INTEGER NOT NULL,
                price REAL NOT NULL,
                reasons TEXT NOT NULL,
                outcome_1m TEXT,
                outcome_2m TEXT,
                outcome_5m TEXT
            )
        """)
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_signal_asset_candle ON signal_history(asset, candle_time)")
        # Remove the legacy global unique constraint on candle_time so different symbols can share a timestamp.
        schema = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='signal_history'").fetchone()
        if schema and "candle_time INTEGER NOT NULL UNIQUE" in (schema[0] or ""):
            con.execute("CREATE TABLE signal_history_new (id INTEGER PRIMARY KEY AUTOINCREMENT, candle_time INTEGER NOT NULL, created_at TEXT NOT NULL, asset TEXT NOT NULL, direction TEXT NOT NULL, score INTEGER NOT NULL, price REAL NOT NULL, reasons TEXT NOT NULL, outcome_1m TEXT, outcome_2m TEXT, outcome_5m TEXT, UNIQUE(asset,candle_time))")
            con.execute("INSERT OR IGNORE INTO signal_history_new SELECT * FROM signal_history")
            con.execute("DROP TABLE signal_history")
            con.execute("ALTER TABLE signal_history_new RENAME TO signal_history")


init_db()


class Trade(BaseModel):
    asset: str = Field(min_length=1, max_length=30)
    direction: str
    stake: float = Field(gt=0, le=1000000)
    result: str


def calculate_ema_series(values, period):
    if len(values) < period:
        return [None] * len(values)
    result = [None] * len(values)
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    result[period - 1] = ema
    for i in range(period, len(values)):
        ema = values[i] * multiplier + ema * (1 - multiplier)
        result[i] = ema
    return result


def calculate_rsi_series(values, period=14):
    result = [None] * len(values)
    if len(values) <= period:
        return result
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    for end in range(period, len(changes) + 1):
        window = changes[end - period:end]
        gains = sum(x for x in window if x > 0) / period
        losses = sum(-x for x in window if x < 0) / period
        if losses == 0:
            value = 100.0 if gains > 0 else 50.0
        else:
            value = 100 - 100 / (1 + gains / losses)
        result[end] = value
    return result


def calculate_sma_series(values, period):
    result = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        result[i] = sum(window) / period
    return result


def calculate_std_series(values, period):
    result = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        mean = sum(window) / period
        result[i] = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
    return result


def calculate_bollinger(closes, period=20, deviations=2):
    middle = calculate_sma_series(closes, period)
    std = calculate_std_series(closes, period)
    upper, lower = [], []
    for m, s in zip(middle, std):
        upper.append(None if m is None or s is None else m + deviations * s)
        lower.append(None if m is None or s is None else m - deviations * s)
    return middle, upper, lower


def calculate_atr_adx(candles, period=14):
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    trs, plus_dm, minus_dm = [], [], []
    for i in range(len(candles)):
        if i == 0:
            trs.append(highs[i] - lows[i])
            plus_dm.append(0.0)
            minus_dm.append(0.0)
            continue
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    atr = [None] * len(candles)
    plus_di = [None] * len(candles)
    minus_di = [None] * len(candles)
    dx = [None] * len(candles)
    adx = [None] * len(candles)
    if len(candles) <= period:
        return atr, adx
    for i in range(period, len(candles)):
        start = i - period + 1
        tr_sum = sum(trs[start:i + 1])
        p_sum = sum(plus_dm[start:i + 1])
        m_sum = sum(minus_dm[start:i + 1])
        atr[i] = tr_sum / period
        if tr_sum > 0:
            plus_di[i] = 100 * p_sum / tr_sum
            minus_di[i] = 100 * m_sum / tr_sum
            denom = plus_di[i] + minus_di[i]
            dx[i] = 0 if denom == 0 else 100 * abs(plus_di[i] - minus_di[i]) / denom
    for i in range(period * 2 - 1, len(candles)):
        vals = [x for x in dx[i - period + 1:i + 1] if x is not None]
        if vals:
            adx[i] = sum(vals) / len(vals)
    return atr, adx


def build_indicators(candles):
    closes = [c["close"] for c in candles]
    ema9 = calculate_ema_series(closes, 9)
    ema21 = calculate_ema_series(closes, 21)
    ema50 = calculate_ema_series(closes, 50)
    ema200 = calculate_ema_series(closes, 200)
    rsi = calculate_rsi_series(closes, 14)
    ema12 = calculate_ema_series(closes, 12)
    ema26 = calculate_ema_series(closes, 26)
    macd = [None if a is None or b is None else a - b for a, b in zip(ema12, ema26)]
    valid_macd = [x for x in macd if x is not None]
    signal_valid = calculate_ema_series(valid_macd, 9)
    macd_signal = [None] * len(closes)
    first = next((i for i, x in enumerate(macd) if x is not None), len(closes))
    for j, value in enumerate(signal_valid):
        if first + j < len(macd_signal):
            macd_signal[first + j] = value
    middle, upper, lower = calculate_bollinger(closes)
    atr, adx = calculate_atr_adx(candles)
    return {
        "ema9": ema9, "ema21": ema21, "ema50": ema50, "ema200": ema200,
        "rsi": rsi, "macd": macd, "macd_signal": macd_signal,
        "bb_middle": middle, "bb_upper": upper, "bb_lower": lower,
        "atr": atr, "adx": adx
    }


def fetch_candles(interval, api_key, symbol="EUR/USD", outputsize=220):
    now = time.time()
    with _CACHE_LOCK:
        cache_key = symbol + "|" + interval
        cached = _DATA_CACHE.get(cache_key)
        if cached and now - cached["time"] < _CACHE_TTL:
            return cached["candles"]
    params = urllib.parse.urlencode({
        "symbol": symbol, "interval": interval,
        "outputsize": str(outputsize), "apikey": api_key
    })
    url = "https://api.twelvedata.com/time_series?" + params
    request = urllib.request.Request(url, headers={"User-Agent": "PocketTraderDemo/2.0"})
    with urllib.request.urlopen(request, timeout=18) as response:
        data = json.loads(response.read().decode("utf-8"))
    if "values" not in data:
        raise ValueError(data.get("message", "Dostawca nie zwrócił notowań."))
    candles = []
    for item in reversed(data["values"]):
        dt = datetime.fromisoformat(item["datetime"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        candles.append({
            "time": int(dt.timestamp()),
            "open": float(item["open"]), "high": float(item["high"]),
            "low": float(item["low"]), "close": float(item["close"])
        })
    if len(candles) < 205:
        raise ValueError(f"Za mało świec dla {interval}: potrzeba co najmniej 205, otrzymano {len(candles)}.")
    with _CACHE_LOCK:
        _DATA_CACHE[cache_key] = {"time": now, "candles": candles}
    return candles


def latest_snapshot(candles):
    ind = build_indicators(candles)
    last = len(candles) - 1
    def val(key):
        return ind[key][last]
    return {
        "close": candles[last]["close"],
        "ema9": val("ema9"), "ema21": val("ema21"),
        "ema50": val("ema50"), "ema200": val("ema200"),
        "rsi": val("rsi"), "macd": val("macd"),
        "macd_signal": val("macd_signal"),
        "adx": val("adx"), "atr": val("atr"),
        "bb_upper": val("bb_upper"), "bb_lower": val("bb_lower"),
        "indicators": ind
    }


def evaluate_ai_signal(candles_by_interval, snapshots):
    m1 = snapshots["1min"]
    candle = candles_by_interval["1min"][-1]
    previous = candles_by_interval["1min"][-2]
    trend_up = all(s["ema50"] is not None and s["ema200"] is not None and s["ema50"] > s["ema200"] for s in snapshots.values())
    trend_down = all(s["ema50"] is not None and s["ema200"] is not None and s["ema50"] < s["ema200"] for s in snapshots.values())
    bullish = (candle["close"] > candle["open"] and previous["close"] < previous["open"]
               and candle["close"] >= previous["open"] and candle["open"] <= previous["close"]) or candle["close"] > previous["high"]
    bearish = (candle["close"] < candle["open"] and previous["close"] > previous["open"]
               and candle["close"] <= previous["open"] and candle["open"] >= previous["close"]) or candle["close"] < previous["low"]
    rsi = m1["rsi"]
    hist = None if m1["macd"] is None or m1["macd_signal"] is None else m1["macd"] - m1["macd_signal"]
    atr_pct = None if not m1["atr"] or not m1["close"] else 100 * m1["atr"] / m1["close"]
    volatility_ok = atr_pct is not None and 0.005 <= atr_pct <= 0.20
    call_score = (60 if trend_up else 0)
    put_score = (60 if trend_down else 0)
    if rsi is not None:
        if 50 < rsi < 68: call_score += 10
        if 32 < rsi < 50: put_score += 10
    if hist is not None:
        if hist > 0: call_score += 10
        if hist < 0: put_score += 10
    if m1["adx"] is not None and m1["adx"] >= 20:
        call_score += 10
        put_score += 10
    if bullish: call_score += 5
    if bearish: put_score += 5
    if volatility_ok:
        call_score += 5
        put_score += 5
    call_band_ok = m1["bb_upper"] is not None and m1["close"] < m1["bb_upper"]
    put_band_ok = m1["bb_lower"] is not None and m1["close"] > m1["bb_lower"]
    call_ok = trend_up and rsi is not None and 50 < rsi < 68 and hist is not None and hist > 0 and m1["adx"] is not None and m1["adx"] >= 20 and bullish and volatility_ok and call_band_ok and call_score >= 85
    put_ok = trend_down and rsi is not None and 32 < rsi < 50 and hist is not None and hist < 0 and m1["adx"] is not None and m1["adx"] >= 20 and bearish and volatility_ok and put_band_ok and put_score >= 85
    signal = "CALL" if call_ok else "PUT" if put_ok else "NO SIGNAL"
    score = call_score if signal == "CALL" else put_score if signal == "PUT" else max(call_score, put_score)
    reasons = []
    reasons.append("Trend M1/M5/M15/H1 zgodny" if trend_up or trend_down else "Brak pełnej zgodności trendu na M1/M5/M15/H1")
    reasons.append("MACD potwierdza momentum" if (signal == "CALL" and hist and hist > 0) or (signal == "PUT" and hist and hist < 0) else "MACD nie potwierdza jednoznacznie kierunku")
    reasons.append("ADX potwierdza siłę trendu" if m1["adx"] is not None and m1["adx"] >= 20 else "Trend za słaby (ADX poniżej 20)")
    reasons.append("Świeca potwierdza kierunek" if (signal == "CALL" and bullish) or (signal == "PUT" and bearish) else "Brak potwierdzenia świecowego")
    if not volatility_ok:
        reasons.append("Zmienność poza zakresem filtra")
    return {
        "signal": signal, "score": int(score), "reasons": reasons,
        "trend": {"M1": "UP" if snapshots["1min"]["ema50"] > snapshots["1min"]["ema200"] else "DOWN",
                  "M5": "UP" if snapshots["5min"]["ema50"] > snapshots["5min"]["ema200"] else "DOWN",
                  "M15": "UP" if snapshots["15min"]["ema50"] > snapshots["15min"]["ema200"] else "DOWN",
                  "H1": "UP" if snapshots["1h"]["ema50"] > snapshots["1h"]["ema200"] else "DOWN"},
        "expiry": "2–3 min (wyłącznie do testów DEMO)" if signal != "NO SIGNAL" else "Brak – NO SIGNAL",
        "rsi": round(rsi, 2) if rsi is not None else None,
        "adx": round(m1["adx"], 2) if m1["adx"] is not None else None,
        "atr": round(m1["atr"], 6) if m1["atr"] is not None else None,
        "atr_pct": round(atr_pct, 4) if atr_pct is not None else None
    }


@app.get("/", response_class=HTMLResponse)
def home():
    return r"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#101827">
<title>Pocket Trader Mobile DEMO</title>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
:root{color-scheme:dark;--bg:#101827;--panel:#1d2a3d;--muted:#9aabc0;--green:#55d6a2;--red:#ff6b78;--line:#304158}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#f4f7fb;font:15px Arial,sans-serif}
main{max-width:900px;margin:auto;padding:16px}.top{display:flex;justify-content:space-between;align-items:center;gap:12px}
h1{font-size:24px;color:var(--green);margin:8px 0}h2{font-size:16px;margin:0 0 12px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:15px;margin:13px 0}
.muted,.note{color:var(--muted);font-size:13px}.note{color:#ffd479;line-height:1.45}
.pill{padding:7px 11px;border-radius:30px;background:#26384e;color:var(--green);font-weight:bold;font-size:12px}
.controls{display:flex;gap:8px;flex-wrap:wrap}button,select,input{font:inherit;border:1px solid var(--line);border-radius:8px;padding:10px;background:#142135;color:#fff}
button{cursor:pointer}button.active,.primary{background:var(--green);color:#10251e;border-color:var(--green);font-weight:bold}
select{min-width:110px}.chart{height:290px;width:100%}.smallchart{height:150px;width:100%}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.metric{background:#142135;border-radius:9px;padding:10px;min-width:0}
.metric span{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}.metric strong{font-size:16px;overflow-wrap:anywhere}
.signal{font-size:22px;font-weight:bold}.buy{color:var(--green)}.sell{color:var(--red)}.neutral{color:#ffd479}.ai-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.ai-box{background:#142135;border-radius:9px;padding:10px}.ai-box strong{display:block;margin-top:5px}.reason-list{padding-left:18px;color:var(--muted);line-height:1.55}.trend-row{display:flex;gap:7px;flex-wrap:wrap}.trend-chip{background:#142135;padding:7px 9px;border-radius:8px;font-size:12px}
form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}form button{grid-column:1/-1}
.history{white-space:pre-wrap;line-height:1.6;overflow-wrap:anywhere}
@media(max-width:520px){main{padding:11px}.chart{height:250px}.metrics{grid-template-columns:repeat(2,1fr)}form{grid-template-columns:1fr}.top{align-items:flex-start}}
</style>
</head>
<body><main>
<div class="top"><div><h1>POCKET TRADER</h1><div class="muted">Mobilny terminal analityczny</div></div><span class="pill">TRYB DEMO</span></div>
<div class="panel" id="instrumentPanel"><h2>WYBÓR INSTRUMENTU</h2>
 <div class="muted">Wybierz kategorię i symbol. Dostępność notowań sprawdzamy u dostawcy po wyborze.</div>
 <div class="controls" id="categoryButtons" style="margin:10px 0">
 <button class="category active" data-category="Forex">Forex</button><button class="category" data-category="Surowce">Surowce</button>
 <button class="category" data-category="Indeksy">Indeksy</button><button class="category" data-category="Akcje">Akcje</button>
 <button class="category" data-category="Kryptowaluty">Kryptowaluty</button></div>
 <input id="instrumentSearch" placeholder="Szukaj symbolu lub nazwy" aria-label="Szukaj instrumentu" style="width:100%;margin-bottom:8px">
 <select id="instrumentSelect" style="width:100%;margin-bottom:8px"></select>
 <div class="muted" id="instrumentStatus">EUR/USD · dostępność notowań do sprawdzenia</div>
 <p class="note">Symbole są kandydatami do sprawdzenia w Twelve Data; dostęp zależy od planu API i sesji. To nie jest pełna lista Pocket Option. OTC Pocket Option wymaga osobnego, zgodnego źródła notowań.</p></div>
<div class="panel">
 <div class="top"><div><h2 id="chartAssetTitle">EUR/USD</h2><div class="muted" id="lastUpdate">Oczekiwanie na dane…</div></div><div class="signal neutral" id="signal">ŁADOWANIE</div></div>
 <div class="controls" style="margin:12px 0">
  <button class="interval active" data-interval="1min">M1</button><button class="interval" data-interval="5min">M5</button>
  <button class="interval" data-interval="15min">M15</button><button class="interval" data-interval="1h">H1</button>
  <button id="refresh" class="primary">Odśwież</button>
 </div>
 <div class="metrics">
  <div class="metric"><span>Cena</span><strong id="price">—</strong></div>
  <div class="metric"><span>EMA 9</span><strong id="ema9">—</strong></div>
  <div class="metric"><span>EMA 21</span><strong id="ema21">—</strong></div>
 </div>
 <div id="priceChart" class="chart"></div>
 <div class="muted">Świece: dane dostawcy Twelve Data · Linie: EMA 9, 21, 50 i 200</div>
</div>
<div class="panel"><h2>RSI 14</h2><div class="metrics"><div class="metric"><span>Wartość</span><strong id="rsi">—</strong></div><div class="metric"><span>Interpretacja</span><strong id="rsiText">—</strong></div></div><div id="rsiChart" class="smallchart"></div></div>
<div class="panel"><h2>MACD (12, 26, 9)</h2><div class="metrics"><div class="metric"><span>MACD</span><strong id="macd">—</strong></div><div class="metric"><span>Linia sygnałowa</span><strong id="macdSignal">—</strong></div><div class="metric"><span>Histogram</span><strong id="macdHist">—</strong></div></div><div id="macdChart" class="smallchart"></div></div>
<div class="panel">
 <h2>POCKET TRADER AI · SELECTIVE / MAXIMUM</h2>
 <div class="ai-grid">
  <div class="ai-box"><span class="muted">Zgodność filtrów</span><strong id="signalScore">— / 100</strong></div>
  <div class="ai-box"><span class="muted">Sugerowane wygaśnięcie</span><strong id="expiry">—</strong></div>
  <div class="ai-box"><span class="muted">ADX M1</span><strong id="adx">—</strong></div>
  <div class="ai-box"><span class="muted">ATR M1</span><strong id="atr">—</strong></div>
 </div>
 <p class="muted">Potwierdzenie trendu: EMA 50/200</p>
 <div class="trend-row" id="trendSummary">M1: — · M5: — · M15: — · H1: —</div>
 <h2 style="margin-top:15px">Uzasadnienie decyzji</h2>
 <ul class="reason-list" id="signalReasons"><li>Oczekiwanie na analizę…</li></ul>
</div>
<div class="panel"><h2>HISTORIA SYGNAŁÓW · wybrany instrument</h2><div id="signalHistory" class="history muted">Brak zapisanych sygnałów.</div></div>
<div class="panel"><h2>BACKTEST · WYNIKI SYGNAŁÓW</h2><div class="controls" style="margin-bottom:8px"><select id="testExpiry"><option value="1">1 minuta</option><option value="2">2 minuty</option><option value="5">5 minut</option></select><button id="runBacktest" class="primary">Przelicz</button></div>
<div class="metrics"><div class="metric"><span>Rozliczone sygnały</span><strong id="btSettled">0</strong></div><div class="metric"><span>Wygrane / przegrane</span><strong id="btWL">0 / 0</strong></div><div class="metric"><span>Skuteczność</span><strong id="btRate">—</strong></div></div><p id="btNote" class="muted">Test kroczący; pominięte sygnały nie są wygranymi ani stratami.</p></div>
<div class="panel"><h2>Dodaj transakcję testową</h2>
<form id="tradeForm"><input id="asset" value="EUR/USD" aria-label="Instrument"><select id="direction"><option value="CALL">CALL</option><option value="PUT">PUT</option></select><input id="stake" type="number" min="0.01" step="0.01" value="10" aria-label="Kwota DEMO"><select id="result"><option value="win">Wygrana testowa</option><option value="loss">Przegrana testowa</option></select><button class="primary" type="submit">Zapisz symulację</button></form></div>
<div class="panel"><h2>Statystyki DEMO</h2><div class="metrics"><div class="metric"><span>Transakcje</span><strong id="count">0</strong></div><div class="metric"><span>Wygrane / przegrane</span><strong id="wl">0 / 0</strong></div><div class="metric"><span>Wynik DEMO</span><strong id="profit">0.00</strong></div></div></div>
<div class="panel"><h2>Historia</h2><div id="history" class="history muted">Brak transakcji</div></div>
<p class="note">DEMO: analiza ma charakter edukacyjny. Sygnały CALL/PUT nie gwarantują wyniku i nie otwierają transakcji. Nie jest to połączenie z Pocket Option ani rekomendacja inwestycyjna. Lista symboli jest katalogiem do sprawdzenia w Twelve Data; OTC nie jest obsługiwane tym źródłem.</p>
</main>
<script>
let currentInterval='1min', priceChart, rsiChart, macdChart, candleSeries, ema9Series, ema21Series, ema50Series, ema200Series, rsiSeries, macdLine, macdSignalLine;
function makeChart(id){
 const el=document.getElementById(id);
 return LightweightCharts.createChart(el,{width:el.clientWidth,height:el.clientHeight,
  layout:{background:{color:'#1d2a3d'},textColor:'#c7d2e1'},
  grid:{vertLines:{color:'#26384e'},horzLines:{color:'#26384e'}},
  rightPriceScale:{borderColor:'#40536b'},timeScale:{borderColor:'#40536b',timeVisible:true,secondsVisible:false},
  crosshair:{mode:0}});
}
let currentCategory='Forex', currentSymbol='EUR/USD', instrumentCatalog=[];
function renderInstrumentOptions(){
 const q=(document.getElementById('instrumentSearch').value||'').toLowerCase().trim();
 const sel=document.getElementById('instrumentSelect');
 const list=instrumentCatalog.filter(x=>x.category===currentCategory && (!q||(x.symbol+' '+x.name).toLowerCase().includes(q)));
 sel.innerHTML=''; list.forEach(x=>{const o=document.createElement('option');o.value=x.symbol;o.textContent=x.symbol+' — '+x.name;sel.appendChild(o);});
 if(list.some(x=>x.symbol===currentSymbol))sel.value=currentSymbol; else if(list.length){currentSymbol=list[0].symbol;sel.value=currentSymbol;}
 setText('instrumentStatus',(sel.value||'Brak instrumentu')+' · dostępność sprawdzana przy pobieraniu danych');setText('chartAssetTitle',currentSymbol);
}
async function initInstrumentSelector(){try{instrumentCatalog=await fetch('/api/instruments').then(r=>r.json());renderInstrumentOptions();}catch(e){setText('instrumentStatus','Nie udało się pobrać listy instrumentów.');}}
document.querySelectorAll('.category').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('.category').forEach(x=>x.classList.remove('active'));b.classList.add('active');currentCategory=b.dataset.category;renderInstrumentOptions();loadSignal();}));
document.getElementById('instrumentSearch').addEventListener('input',renderInstrumentOptions);
document.getElementById('instrumentSelect').addEventListener('change',e=>{currentSymbol=e.target.value;setText('chartAssetTitle',currentSymbol);loadSignal();});
async function refreshSignalHistory(){
 try{
  const rows=await fetch('/api/signal-history?limit=20&asset='+encodeURIComponent(currentSymbol)).then(r=>r.json());
  setText('signalHistory',rows.length?rows.map(x=>new Date(x.candle_time*1000).toLocaleString('pl-PL')+' | '+(x.direction==='NO SIGNAL'?'POMINIĘTO':x.direction)+' | jakość '+x.score+'/100 | 1m: '+(x.outcome_1m||'oczekuje')+' | 2m: '+(x.outcome_2m||'oczekuje')+' | 5m: '+(x.outcome_5m||'oczekuje')).join('\n'):'Brak zapisanych sygnałów. Zostaw aplikację uruchomioną, aby zaczęła zbierać dane.');
 }catch(e){setText('signalHistory','Nie udało się pobrać historii sygnałów.');}
}
async function showBacktest(){
 try{
  const expiry=document.getElementById('testExpiry').value;
  const d=await fetch('/api/backtest?expiry='+expiry+'&asset='+encodeURIComponent(currentSymbol)).then(r=>r.json());
  setText('btSettled',d.settled+' (oczekuje: '+d.pending+')');
  setText('btWL',d.wins+' / '+d.losses);
  setText('btRate',d.win_rate==null?'—':d.win_rate+'%');
  setText('btNote','Zapisane sygnały CALL/PUT: '+d.signals+' · remisy: '+d.ties+'. '+d.note);
 }catch(e){setText('btNote','Nie udało się pobrać wyników testu.');}
}
function initCharts(){
 if(!window.LightweightCharts)return;
 priceChart=makeChart('priceChart');
 candleSeries=priceChart.addCandlestickSeries({upColor:'#55d6a2',downColor:'#ff6b78',borderVisible:false,wickUpColor:'#55d6a2',wickDownColor:'#ff6b78'});
 ema9Series=priceChart.addLineSeries({color:'#f5c451',lineWidth:2,priceLineVisible:false});
 ema21Series=priceChart.addLineSeries({color:'#64a9ff',lineWidth:2,priceLineVisible:false});
  ema50Series=priceChart.addLineSeries({color:'#d18cff',lineWidth:1,priceLineVisible:false});
  ema200Series=priceChart.addLineSeries({color:'#ff8f70',lineWidth:1,priceLineVisible:false});
 rsiChart=makeChart('rsiChart');rsiSeries=rsiChart.addLineSeries({color:'#c49bff',lineWidth:2,priceLineVisible:false});
 macdChart=makeChart('macdChart');macdLine=macdChart.addLineSeries({color:'#55d6a2',lineWidth:2,priceLineVisible:false});
 macdSignalLine=macdChart.addLineSeries({color:'#ffb86b',lineWidth:2,priceLineVisible:false});
 new ResizeObserver(()=>{for(const [chart,id] of [[priceChart,'priceChart'],[rsiChart,'rsiChart'],[macdChart,'macdChart']]){const el=document.getElementById(id);chart.applyOptions({width:el.clientWidth,height:el.clientHeight});}}).observe(document.querySelector('main'));
}
function setText(id,value){document.getElementById(id).textContent=value}
async function loadSignal(){
 setText('lastUpdate','Pobieranie danych…');
 try{
  const response=await fetch('/api/forex/signal?interval='+encodeURIComponent(currentInterval)+'&symbol='+encodeURIComponent(currentSymbol));
  const d=await response.json();if(!response.ok||d.error)throw new Error(d.error||'Błąd pobierania danych');
  setText('price',Number(d.price).toFixed(5));setText('ema9',Number(d.ema9).toFixed(5));setText('ema21',Number(d.ema21).toFixed(5));
  setText('rsi',Number(d.rsi14).toFixed(2));setText('rsiText',d.rsi14>=70?'Wykupienie':d.rsi14<=30?'Wyprzedanie':'Strefa neutralna');
  setText('macd',Number(d.macd).toFixed(6));setText('macdSignal',Number(d.macd_signal).toFixed(6));setText('macdHist',Number(d.macd_hist).toFixed(6));
  const sig=document.getElementById('signal');sig.textContent=d.signal;sig.className='signal '+(d.signal==='CALL'?'buy':d.signal==='PUT'?'sell':'neutral');
  const ai=d.ai||{};setText('signalScore',(ai.score??'—')+' / 100');setText('expiry',ai.expiry||'—');
  setText('adx',ai.adx==null?'—':ai.adx);setText('atr',ai.atr==null?'—':ai.atr);
  const tr=ai.trend||{};setText('trendSummary',['M1','M5','M15','H1'].map(k=>k+': '+(tr[k]||'—')).join(' · '));
  document.getElementById('signalReasons').innerHTML=(ai.reasons||['Brak szczegółów']).map(x=>'<li>'+x+'</li>').join('');
  if(priceChart){
   candleSeries.setData(d.candles);
   ema9Series.setData(d.ema9_series.filter(x=>x.value!==null));
   ema21Series.setData(d.ema21_series.filter(x=>x.value!==null));
    ema50Series.setData(d.ema50_series.filter(x=>x.value!==null));
    ema200Series.setData(d.ema200_series.filter(x=>x.value!==null));
   rsiSeries.setData(d.rsi_series.filter(x=>x.value!==null));
   macdLine.setData(d.macd_series.filter(x=>x.value!==null));
   macdSignalLine.setData(d.macd_signal_series.filter(x=>x.value!==null));
   priceChart.timeScale().fitContent();rsiChart.timeScale().fitContent();macdChart.timeScale().fitContent();
  }
  setText('lastUpdate','Ostatnia aktualizacja: '+new Date().toLocaleTimeString('pl-PL')+' · '+currentInterval+' · '+currentSymbol);
   setText('instrumentStatus',currentSymbol+' · dane dostawcy dostępne');\n   refreshSignalHistory();showBacktest();
 }catch(e){setText('lastUpdate','Błąd: '+e.message);document.getElementById('signal').textContent='BRAK DANYCH';}
}
async function refreshStats(){
 try{
  const [s,h]=await Promise.all([fetch('/api/stats').then(r=>r.json()),fetch('/api/trades').then(r=>r.json())]);
  setText('count',s.count);setText('wl',s.wins+' / '+s.losses);setText('profit',Number(s.profit).toFixed(2));
  document.getElementById('history').textContent=h.length?h.map(t=>`${t.asset} | ${t.direction} | ${t.result} | ${Number(t.profit).toFixed(2)}`).join('\n'):'Brak transakcji';
 }catch(e){setText('history','Nie udało się pobrać historii.');}
}
document.querySelectorAll('.interval').forEach(b=>b.addEventListener('click',()=>{
 document.querySelectorAll('.interval').forEach(x=>x.classList.remove('active'));b.classList.add('active');currentInterval=b.dataset.interval;loadSignal();
}));
document.getElementById('refresh').addEventListener('click',loadSignal);\ndocument.getElementById('runBacktest').addEventListener('click',showBacktest);
document.getElementById('tradeForm').addEventListener('submit',async e=>{
 e.preventDefault();
 const data={asset:document.getElementById('asset').value,direction:document.getElementById('direction').value,stake:Number(document.getElementById('stake').value),result:document.getElementById('result').value};
 try{const r=await fetch('/api/trades',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const d=await r.json();if(!r.ok||d.error)throw new Error(d.error||'Nie udało się zapisać');await refreshStats();}catch(err){alert(err.message);}
});
initCharts();initInstrumentSelector();loadSignal();refreshStats();setInterval(loadSignal,60000);
</script></body></html>"""


@app.get("/api/instruments")
def get_instruments():
    return INSTRUMENTS


@app.post("/api/trades")
def add_trade(trade: Trade):
    if not trade.asset.strip():
        raise HTTPException(status_code=400, detail="Podaj instrument")
    if trade.direction not in ("CALL", "PUT"):
        raise HTTPException(status_code=400, detail="Nieprawidłowy kierunek")
    if trade.result not in ("win", "loss"):
        raise HTTPException(status_code=400, detail="Nieprawidłowy wynik")
    profit = trade.stake * 0.8 if trade.result == "win" else -trade.stake
    with sqlite3.connect(DB) as con:
        con.execute(
            "INSERT INTO trades (asset, direction, stake, result, profit) VALUES (?, ?, ?, ?, ?)",
            (trade.asset.strip(), trade.direction, trade.stake, trade.result, profit)
        )
    return {"status": "saved", "profit": profit}


@app.get("/api/trades")
def get_trades():
    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 50").fetchall()
        return [dict(row) for row in rows]


@app.get("/api/stats")
def get_stats():
    with sqlite3.connect(DB) as con:
        row = con.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN result='win' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END),
                   COALESCE(SUM(profit), 0)
            FROM trades
        """).fetchone()
    return {"count": row[0], "wins": row[1] or 0, "losses": row[2] or 0, "profit": row[3]}


def save_signal_and_update_results(candles, ai, asset="EUR/USD"):
    """Zapisuje jeden sygnał na zamkniętą świecę M1 i uzupełnia wyniki po czasie."""
    if len(candles) < 2:
        return
    # Ostatnia świeca może być jeszcze otwarta. Logujemy poprzednią, zamkniętą świecę.
    signal_candle = candles[-2]
    signal_time = int(signal_candle["time"])
    direction = ai["signal"]
    # Nie zapisujemy NO SIGNAL jako transakcji; zapisujemy je jako pominięte okazje.
    with sqlite3.connect(DB) as con:
        con.execute("""
            INSERT OR IGNORE INTO signal_history
            (candle_time, created_at, asset, direction, score, price, reasons)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (signal_time, datetime.now(timezone.utc).isoformat(), asset, direction,
              int(ai["score"]), float(signal_candle["close"]),
              json.dumps(ai["reasons"], ensure_ascii=False)))
        # Wynik porównujemy z ceną zamknięcia świecy po upływie danego czasu.
        for row in con.execute("SELECT id, candle_time, direction, price FROM signal_history WHERE asset=? AND direction IN ('CALL','PUT') AND (outcome_1m IS NULL OR outcome_2m IS NULL OR outcome_5m IS NULL)" , (asset,)).fetchall():
            rid, start, side, entry = row
            updates = {}
            for minutes, column in ((1, "outcome_1m"), (2, "outcome_2m"), (5, "outcome_5m")):
                target = start + minutes * 60
                future = next((c for c in candles if int(c["time"]) >= target), None)
                if future is not None:
                    close = float(future["close"])
                    result = "win" if (close > entry if side == "CALL" else close < entry) else "loss" if close != entry else "tie"
                    updates[column] = result
            for column, value in updates.items():
                con.execute(f"UPDATE signal_history SET {column}=? WHERE id=?", (value, rid))


@app.get("/api/signal-history")
def get_signal_history(limit: int = Query(30, ge=1, le=200), asset: str = Query("EUR/USD", min_length=1, max_length=30)):
    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM signal_history WHERE asset=? ORDER BY candle_time DESC LIMIT ?", (asset, limit)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["reasons"] = json.loads(item["reasons"])
        result.append(item)
    return result


@app.get("/api/backtest")
def get_backtest(expiry: int = Query(1, ge=1, le=5), asset: str = Query("EUR/USD", min_length=1, max_length=30)):
    if expiry not in (1, 2, 5):
        raise HTTPException(status_code=400, detail="Dozwolone wygaśnięcia: 1, 2 lub 5 minut.")
    column = f"outcome_{expiry}m"
    with sqlite3.connect(DB) as con:
        total, wins, losses, ties, pending = con.execute(
            f"SELECT COUNT(*), SUM({column}='win'), SUM({column}='loss'), SUM({column}='tie'), SUM({column} IS NULL) FROM signal_history WHERE direction IN ('CALL','PUT') AND asset=?",
            (asset,)
        ).fetchone()
    wins, losses, ties, pending = wins or 0, losses or 0, ties or 0, pending or 0
    settled = wins + losses + ties
    return {"expiry_minutes": int(expiry), "signals": total, "settled": settled,
            "wins": wins, "losses": losses, "ties": ties, "pending": pending,
            "win_rate": round(100 * wins / (wins + losses), 2) if wins + losses else None,
            "note": "Test kroczący od momentu wdrożenia. Nie jest to test całej historii rynku."}


@app.get("/api/forex/signal")
def forex_signal(interval: str = Query("1min", pattern="^(1min|5min|15min|1h)$"), symbol: str = Query("EUR/USD", min_length=1, max_length=30)):
    api_key = os.getenv("TWELVE_DATA_API_KEY")
    if not api_key:
        return {"error": "Brak klucza API w ustawieniach Render."}
    if symbol not in INSTRUMENT_BY_SYMBOL:
        raise HTTPException(status_code=400, detail="Instrument nie znajduje się na liście wyboru.")
    intervals = ["1min", "5min", "15min", "1h"]
    try:
        candles_by_interval = {tf: fetch_candles(tf, api_key, symbol=symbol) for tf in intervals}
        snapshots = {tf: latest_snapshot(candles_by_interval[tf]) for tf in intervals}
        ai = evaluate_ai_signal(candles_by_interval, snapshots)
        save_signal_and_update_results(candles_by_interval["1min"], ai, asset=symbol)

        chart_candles = candles_by_interval[interval]
        chart_ind = snapshots[interval]["indicators"]
        chart_last = len(chart_candles) - 1

        def points(values, candles):
            return [{"time": candles[i]["time"], "value": round(v, 6)}
                    for i, v in enumerate(values) if v is not None]

        def last_value(key):
            return chart_ind[key][chart_last]

        return {
            "mode": "DEMO", "symbol": symbol, "interval": interval,
            "price": round(chart_candles[-1]["close"], 5),
            "ema9": round(last_value("ema9"), 5),
            "ema21": round(last_value("ema21"), 5),
            "rsi14": round(last_value("rsi"), 2),
            "macd": round(last_value("macd"), 6),
            "macd_signal": round(last_value("macd_signal"), 6),
            "macd_hist": round(last_value("macd") - last_value("macd_signal"), 6),
            "signal": ai["signal"], "ai": ai,
            "candles": chart_candles,
            "ema9_series": points(chart_ind["ema9"], chart_candles),
            "ema21_series": points(chart_ind["ema21"], chart_candles),
            "ema50_series": points(chart_ind["ema50"], chart_candles),
            "ema200_series": points(chart_ind["ema200"], chart_candles),
            "rsi_series": points(chart_ind["rsi"], chart_candles),
            "macd_series": points(chart_ind["macd"], chart_candles),
            "macd_signal_series": points(chart_ind["macd_signal"], chart_candles),
            "note": "Analiza edukacyjna DEMO. Ocena jakości nie jest prawdopodobieństwem wygranej. Sygnały nie otwierają transakcji. Notowania Twelve Data mogą różnić się od OTC Pocket Option."
        }
    except urllib.error.URLError:
        return {"error": "Nie udało się połączyć z dostawcą danych."}
    except ValueError as exc:
        return {"error": str(exc)}
    except (KeyError, TypeError, IndexError):
        return {"error": "Dane dostawcy mają nieoczekiwany format."}
    except Exception:
        return {"error": "Wystąpił błąd analizy danych. Spróbuj ponownie."}
