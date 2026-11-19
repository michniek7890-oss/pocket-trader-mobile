from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime
import sqlite3
import os
import json
import urllib.parse
import urllib.request
import urllib.error

app = FastAPI(title="Pocket Trader Mobile DEMO")
DB = Path("trades.db")


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
.signal{font-size:22px;font-weight:bold}.buy{color:var(--green)}.sell{color:var(--red)}.neutral{color:#ffd479}
form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}form button{grid-column:1/-1}
.history{white-space:pre-wrap;line-height:1.6;overflow-wrap:anywhere}
@media(max-width:520px){main{padding:11px}.chart{height:250px}.metrics{grid-template-columns:repeat(2,1fr)}form{grid-template-columns:1fr}.top{align-items:flex-start}}
</style>
</head>
<body><main>
<div class="top"><div><h1>POCKET TRADER</h1><div class="muted">Mobilny terminal analityczny</div></div><span class="pill">TRYB DEMO</span></div>
<div class="panel">
 <div class="top"><div><h2>EUR/USD</h2><div class="muted" id="lastUpdate">Oczekiwanie na dane…</div></div><div class="signal neutral" id="signal">ŁADOWANIE</div></div>
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
 <div class="muted">Świece: dane dostawcy Twelve Data · Linie: EMA 9 i EMA 21</div>
</div>
<div class="panel"><h2>RSI 14</h2><div class="metrics"><div class="metric"><span>Wartość</span><strong id="rsi">—</strong></div><div class="metric"><span>Interpretacja</span><strong id="rsiText">—</strong></div></div><div id="rsiChart" class="smallchart"></div></div>
<div class="panel"><h2>MACD (12, 26, 9)</h2><div class="metrics"><div class="metric"><span>MACD</span><strong id="macd">—</strong></div><div class="metric"><span>Linia sygnałowa</span><strong id="macdSignal">—</strong></div><div class="metric"><span>Histogram</span><strong id="macdHist">—</strong></div></div><div id="macdChart" class="smallchart"></div></div>
<div class="panel"><h2>Dodaj transakcję testową</h2>
<form id="tradeForm"><input id="asset" value="EUR/USD" aria-label="Instrument"><select id="direction"><option value="CALL">CALL</option><option value="PUT">PUT</option></select><input id="stake" type="number" min="0.01" step="0.01" value="10" aria-label="Kwota DEMO"><select id="result"><option value="win">Wygrana testowa</option><option value="loss">Przegrana testowa</option></select><button class="primary" type="submit">Zapisz symulację</button></form></div>
<div class="panel"><h2>Statystyki DEMO</h2><div class="metrics"><div class="metric"><span>Transakcje</span><strong id="count">0</strong></div><div class="metric"><span>Wygrane / przegrane</span><strong id="wl">0 / 0</strong></div><div class="metric"><span>Wynik DEMO</span><strong id="profit">0.00</strong></div></div></div>
<div class="panel"><h2>Historia</h2><div id="history" class="history muted">Brak transakcji</div></div>
<p class="note">DEMO: analiza ma charakter edukacyjny. Sygnały CALL/PUT nie gwarantują wyniku i nie otwierają transakcji. Nie jest to połączenie z Pocket Option ani rekomendacja inwestycyjna.</p>
</main>
<script>
let currentInterval='1min', priceChart, rsiChart, macdChart, candleSeries, ema9Series, ema21Series, rsiSeries, macdLine, macdSignalLine;
function makeChart(id){
 const el=document.getElementById(id);
 return LightweightCharts.createChart(el,{width:el.clientWidth,height:el.clientHeight,
  layout:{background:{color:'#1d2a3d'},textColor:'#c7d2e1'},
  grid:{vertLines:{color:'#26384e'},horzLines:{color:'#26384e'}},
  rightPriceScale:{borderColor:'#40536b'},timeScale:{borderColor:'#40536b',timeVisible:true,secondsVisible:false},
  crosshair:{mode:0}});
}
function initCharts(){
 if(!window.LightweightCharts)return;
 priceChart=makeChart('priceChart');
 candleSeries=priceChart.addCandlestickSeries({upColor:'#55d6a2',downColor:'#ff6b78',borderVisible:false,wickUpColor:'#55d6a2',wickDownColor:'#ff6b78'});
 ema9Series=priceChart.addLineSeries({color:'#f5c451',lineWidth:2,priceLineVisible:false});
 ema21Series=priceChart.addLineSeries({color:'#64a9ff',lineWidth:2,priceLineVisible:false});
 rsiChart=makeChart('rsiChart');rsiSeries=rsiChart.addLineSeries({color:'#c49bff',lineWidth:2,priceLineVisible:false});
 macdChart=makeChart('macdChart');macdLine=macdChart.addLineSeries({color:'#55d6a2',lineWidth:2,priceLineVisible:false});
 macdSignalLine=macdChart.addLineSeries({color:'#ffb86b',lineWidth:2,priceLineVisible:false});
 new ResizeObserver(()=>{for(const [chart,id] of [[priceChart,'priceChart'],[rsiChart,'rsiChart'],[macdChart,'macdChart']]){const el=document.getElementById(id);chart.applyOptions({width:el.clientWidth,height:el.clientHeight});}}).observe(document.querySelector('main'));
}
function setText(id,value){document.getElementById(id).textContent=value}
async function loadSignal(){
 setText('lastUpdate','Pobieranie danych…');
 try{
  const response=await fetch('/api/forex/signal?interval='+encodeURIComponent(currentInterval));
  const d=await response.json();if(!response.ok||d.error)throw new Error(d.error||'Błąd pobierania danych');
  setText('price',Number(d.price).toFixed(5));setText('ema9',Number(d.ema9).toFixed(5));setText('ema21',Number(d.ema21).toFixed(5));
  setText('rsi',Number(d.rsi14).toFixed(2));setText('rsiText',d.rsi14>=70?'Wykupienie':d.rsi14<=30?'Wyprzedanie':'Strefa neutralna');
  setText('macd',Number(d.macd).toFixed(6));setText('macdSignal',Number(d.macd_signal).toFixed(6));setText('macdHist',Number(d.macd_hist).toFixed(6));
  const sig=document.getElementById('signal');sig.textContent=d.signal;sig.className='signal '+(d.signal==='CALL'?'buy':d.signal==='PUT'?'sell':'neutral');
  if(priceChart){
   candleSeries.setData(d.candles);
   ema9Series.setData(d.ema9_series.filter(x=>x.value!==null));
   ema21Series.setData(d.ema21_series.filter(x=>x.value!==null));
   rsiSeries.setData(d.rsi_series.filter(x=>x.value!==null));
   macdLine.setData(d.macd_series.filter(x=>x.value!==null));
   macdSignalLine.setData(d.macd_signal_series.filter(x=>x.value!==null));
   priceChart.timeScale().fitContent();rsiChart.timeScale().fitContent();macdChart.timeScale().fitContent();
  }
  setText('lastUpdate','Ostatnia aktualizacja: '+new Date().toLocaleTimeString('pl-PL')+' · '+currentInterval);
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
document.getElementById('refresh').addEventListener('click',loadSignal);
document.getElementById('tradeForm').addEventListener('submit',async e=>{
 e.preventDefault();
 const data={asset:document.getElementById('asset').value,direction:document.getElementById('direction').value,stake:Number(document.getElementById('stake').value),result:document.getElementById('result').value};
 try{const r=await fetch('/api/trades',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const d=await r.json();if(!r.ok||d.error)throw new Error(d.error||'Nie udało się zapisać');await refreshStats();}catch(err){alert(err.message);}
});
initCharts();loadSignal();refreshStats();setInterval(loadSignal,60000);
</script></body></html>"""


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


@app.get("/api/forex/signal")
def forex_signal(interval: str = Query("1min", pattern="^(1min|5min|15min|1h)$")):
    api_key = os.getenv("TWELVE_DATA_API_KEY")
    if not api_key:
        return {"error": "Brak klucza API w ustawieniach Render."}
    params = urllib.parse.urlencode({
        "symbol": "EUR/USD", "interval": interval, "outputsize": "100",
        "apikey": api_key
    })
    url = "https://api.twelvedata.com/time_series?" + params
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "PocketTraderDemo/1.0"})
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        if "values" not in data:
            return {"error": data.get("message", "Dostawca danych nie zwrócił notowań.")}
        candles_raw = list(reversed(data["values"]))
        candles = []
        closes = []
        for item in candles_raw:
            dt = datetime.fromisoformat(item["datetime"])
            candles.append({
                "time": int(dt.timestamp()),
                "open": float(item["open"]), "high": float(item["high"]),
                "low": float(item["low"]), "close": float(item["close"])
            })
            closes.append(float(item["close"]))
        if len(closes) < 35:
            return {"error": "Za mało danych do obliczenia wskaźników."}

        e9 = calculate_ema_series(closes, 9)
        e21 = calculate_ema_series(closes, 21)
        rsi = calculate_rsi_series(closes, 14)
        ema12 = calculate_ema_series(closes, 12)
        ema26 = calculate_ema_series(closes, 26)
        macd_values = [None if a is None or b is None else a-b for a,b in zip(ema12,ema26)]
        valid_macd = [x for x in macd_values if x is not None]
        signal_valid = calculate_ema_series(valid_macd, 9)
        macd_signal = [None] * len(closes)
        start = next((i for i,x in enumerate(macd_values) if x is not None), len(closes))
        for j,value in enumerate(signal_valid):
            macd_signal[start+j] = value

        last_macd = macd_values[-1]
        last_signal = macd_signal[-1]
        last_rsi = rsi[-1]
        signal = "NO SIGNAL"
        if e9[-1] is not None and e21[-1] is not None and last_rsi is not None and last_macd is not None and last_signal is not None:
            if e9[-1] > e21[-1] and 50 < last_rsi < 70 and last_macd > last_signal:
                signal = "CALL"
            elif e9[-1] < e21[-1] and 30 < last_rsi < 50 and last_macd < last_signal:
                signal = "PUT"

        def points(values):
            return [{"time": candles[i]["time"], "value": round(v, 6)}
                    for i,v in enumerate(values) if v is not None]

        return {
            "mode": "DEMO", "symbol": "EUR/USD", "interval": interval,
            "price": round(closes[-1], 5), "ema9": round(e9[-1], 5),
            "ema21": round(e21[-1], 5), "rsi14": round(last_rsi, 2),
            "macd": round(last_macd, 6), "macd_signal": round(last_signal, 6),
            "macd_hist": round(last_macd-last_signal, 6), "signal": signal,
            "candles": candles, "ema9_series": points(e9),
            "ema21_series": points(e21), "rsi_series": points(rsi),
            "macd_series": points(macd_values),
            "macd_signal_series": points(macd_signal),
            "note": "Sygnał testowy. Nie otwiera transakcji i nie gwarantuje zysku."
        }
    except urllib.error.URLError:
        return {"error": "Nie udało się połączyć z dostawcą danych."}
    except (KeyError, ValueError, TypeError):
        return {"error": "Dane dostawcy mają nieoczekiwany format."}
    except Exception:
        return {"error": "Wystąpił błąd analizy danych. Spróbuj ponownie."}
    
