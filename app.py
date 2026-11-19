
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from pathlib import Path
import sqlite3

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
    asset: str
    direction: str
    stake: float
    result: str

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!DOCTYPE html>
    <html lang="pl">
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <meta charset="utf-8">
      <title>Pocket Trader DEMO</title>
      <style>
        body {
          font-family: Arial, sans-serif;
          background: #101827;
          color: white;
          padding: 20px;
          max-width: 520px;
          margin: auto;
        }
        h1 { color: #55d6a2; }
        .panel {
          background: #1d2a3d;
          padding: 18px;
          border-radius: 14px;
          margin: 14px 0;
        }
        input, select, button {
          box-sizing: border-box;
          width: 100%;
          padding: 13px;
          margin: 7px 0;
          border-radius: 8px;
          border: 0;
          font-size: 16px;
        }
        button {
          background: #55d6a2;
          font-weight: bold;
        }
        .note { color: #ffd479; font-size: 14px; }
        #history { white-space: pre-line; }
      </style>
    </head>
    <body>
      <h1>Pocket Trader</h1>
      <p>Panel demonstracyjny — DEMO</p>
      <p class="note">
        Brak połączenia z Pocket Option i brak prawdziwych sygnałów.
        Wszystkie transakcje są tylko symulacją.
      </p>

      <div class="panel">
        <h3>Dodaj transakcję testową</h3>
        <input id="asset" value="EUR/USD" placeholder="Instrument">
        <select id="direction">
          <option value="CALL">CALL</option>
          <option value="PUT">PUT</option>
        </select>
        <input id="stake" type="number" min="0.01" step="0.01"
          value="10" placeholder="Kwota testowa">
        <select id="result">
          <option value="win">Wygrana testowa</option>
          <option value="loss">Przegrana testowa</option>
        </select>
        <button onclick="addTrade()">Zapisz symulację</button>
      </div>

      <div class="panel">
        <h3>Statystyki DEMO</h3>
        <p id="stats">Ładowanie...</p>
      </div>

      <div class="panel">
        <h3>Historia</h3>
        <div id="history">Ładowanie...</div>
      </div>

      <script>
        async function addTrade() {
          const data = {
            asset: document.getElementById("asset").value,
            direction: document.getElementById("direction").value,
            stake: Number(document.getElementById("stake").value),
            result: document.getElementById("result").value
          };
          const response = await fetch("/api/trades", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(data)
          });
          if (!response.ok) {
            alert("Nie udało się zapisać. Sprawdź kwotę i dane.");
            return;
          }
          await refresh();
        }

        async function refresh() {
          const s = await fetch("/api/stats").then(r => r.json());
          document.getElementById("stats").textContent =
            "Transakcje: " + s.count +
            " | Wygrane: " + s.wins +
            " | Przegrane: " + s.losses +
            " | Wynik DEMO: " + s.profit.toFixed(2);

          const h = await fetch("/api/trades").then(r => r.json());
          document.getElementById("history").textContent =
            h.length ? h.map(t =>
              t.asset + " | " + t.direction + " | " +
              t.result + " | " + t.profit.toFixed(2)
            ).join("\\n") : "Brak transakcji";
        }
        refresh();
      </script>
    </body>
    </html>
    """

@app.post("/api/trades")
def add_trade(trade: Trade):
    if not trade.asset.strip():
        return {"error": "Podaj instrument"}
    if trade.stake <= 0:
        return {"error": "Kwota musi być większa od zera"}
    if trade.direction not in ("CALL", "PUT"):
        return {"error": "Nieprawidłowy kierunek"}
    if trade.result not in ("win", "loss"):
        return {"error": "Nieprawidłowy wynik"}

    # Przykładowa wypłata 80% — założenie symulacji, nie kurs brokera.
    profit = trade.stake * 0.8 if trade.result == "win" else -trade.stake

    with sqlite3.connect(DB) as con:
        con.execute(
            "INSERT INTO trades (asset, direction, stake, result, profit) VALUES (?, ?, ?, ?, ?)",
            (trade.asset.strip(), trade.direction, trade.stake,
             trade.result, profit)
        )
    return {"status": "saved", "profit": profit}

@app.get("/api/trades")
def get_trades():
    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT 50"
        ).fetchall()
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
    return {
        "count": row[0],
        "wins": row[1] or 0,
        "losses": row[2] or 0,
        "profit": row[3]
    }


# --- ANALIZA FOREX: TWELVE DATA (TRYB DEMO) ---
import os
import json
import urllib.parse
import urllib.request
import urllib.error


def calculate_ema(values, period):
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for value in values[period:]:
        ema = (value * multiplier) + (ema * (1 - multiplier))
    return ema


def calculate_rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    recent = changes[-period:]
    gains = [x for x in recent if x > 0]
    losses = [-x for x in recent if x < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


@app.get("/api/forex/signal")
def forex_signal():
    api_key = os.getenv("TWELVE_DATA_API_KEY")
    if not api_key:
        return {"error": "Brak klucza API w ustawieniach Render."}

    params = urllib.parse.urlencode({
        "symbol": "EUR/USD",
        "interval": "1min",
        "outputsize": "100",
        "apikey": api_key
    })
    url = "https://api.twelvedata.com/time_series?" + params

    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "PocketTraderDemo/1.0"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))

        if "values" not in data:
            return {"error": "Dostawca danych nie zwrócił notowań. Sprawdź limit API i ustawienia klucza."}

        candles = list(reversed(data["values"]))
        closes = [float(candle["close"]) for candle in candles]

        if len(closes) < 35:
            return {"error": "Za mało danych do obliczenia wskaźników."}

        ema9 = calculate_ema(closes, 9)
        ema21 = calculate_ema(closes, 21)
        rsi = calculate_rsi(closes, 14)

        macd_values = []
        for i in range(26, len(closes) + 1):
            subset = closes[:i]
            macd_values.append(
                calculate_ema(subset, 12) - calculate_ema(subset, 26)
            )

        macd = macd_values[-1]
        macd_signal = calculate_ema(macd_values, 9)

        signal = "NO SIGNAL"
        if ema9 > ema21 and 50 < rsi < 70 and macd > macd_signal:
            signal = "CALL"
        elif ema9 < ema21 and 30 < rsi < 50 and macd < macd_signal:
            signal = "PUT"

        return {
            "mode": "DEMO",
            "symbol": "EUR/USD",
            "interval": "1min",
            "price": round(closes[-1], 5),
            "ema9": round(ema9, 5),
            "ema21": round(ema21, 5),
            "rsi14": round(rsi, 2),
            "macd": round(macd, 6),
            "macd_signal": round(macd_signal, 6),
            "signal": signal,
            "note": "Sygnał testowy, nie gwarantuje zysku. Nie otwiera transakcji."
        }

    except urllib.error.URLError:
        return {"error": "Nie udało się połączyć z dostawcą danych."}
    except Exception:
        return {"error": "Wystąpił błąd analizy danych. Spróbuj ponownie."}
