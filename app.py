
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
