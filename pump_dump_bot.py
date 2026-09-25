#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PUMP/DUMP BOT — interspot
- Lee CoinBeacon /pumping/events cada 5 min
- Solo pump_10m y dump_10m
- Filtro: % mínimo 3%
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Monedas a seguir
SYMBOLS = [
    "AAVE", "JST", "ACE", "ONDO", "XLM", "BONK",
    "SAGA", "ETHFI", "QNT", "XRP", "AVAX", "SEI", "SUI",
]

# Filtros
PCT_MIN_PUMP = 3.0
PCT_MIN_DUMP = -3.0

STATE_FILE = Path("data/pump_dump_state.json")
SIGNALS_LOG = Path("data/pump_dump_log.jsonl")

LIMA_OFFSET = timedelta(hours=-5)

COINBEACON_URL = "https://api.coinbeacon.io/pumping/events"


def hora_lima():
    return datetime.now(timezone.utc) + LIMA_OFFSET


def cargar_estado():
    if not STATE_FILE.exists():
        return {"vistos": []}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "vistos" in data:
            return data
    except Exception:
        pass
    return {"vistos": []}


def guardar_estado(estado):
    STATE_FILE.parent.mkdir(exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2)


def log_senal(registro):
    SIGNALS_LOG.parent.mkdir(exist_ok=True)
    with SIGNALS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def enviar_telegram(msg):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("      ⚠️ Telegram no configurado", flush=True)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = f"chat_id={urllib.parse.quote(str(chat_id))}&text={urllib.parse.quote(msg)}".encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8")).get("ok", False)
    except Exception as e:
        print(f"      ⚠️ Telegram: {str(e)[:60]}", flush=True)
        return False


def consultar_pumping_events():
    token = os.environ.get("COINBEACON_TOKEN4")
    if not token:
        print("⚠️ COINBEACON_TOKEN4 no configurado", flush=True)
        return []

    types = "pump_10m,dump_10m"
    url = f"{COINBEACON_URL}?exchange=binance&types={types}&pair=USDT&limit=500"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Cookie": f"access_token={token}",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("items", [])
    except Exception as e:
        print(f"⚠️ CoinBeacon: {str(e)[:80]}", flush=True)
        return []


def clasificar_evento(ev):
    tipo = ev.get("type", "")
    vol_conf = ev.get("volConfirmed", False)
    rvol = ev.get("rvol", 0)

    if "pump" in tipo:
        emoji = "🚀"
        tipo_str = "PUMP 10m"
    elif "dump" in tipo:
        emoji = "💥"
        tipo_str = "DUMP 10m"
    else:
        emoji = "⚡"
        tipo_str = tipo

    if vol_conf:
        vol_txt = f"⚡ Volumen confirmado {rvol:.2f}×"
    else:
        vol_txt = f"Volumen {rvol:.2f}×"

    return emoji, tipo_str, vol_txt


def main():
    print("=" * 70, flush=True)
    print("🚀 PUMP/DUMP BOT — CoinBeacon (10m)", flush=True)
    print(f"   {len(SYMBOLS)} monedas: {', '.join(SYMBOLS)}", flush=True)
    print(f"   % min: {PCT_MIN_PUMP}", flush=True)
    print("=" * 70, flush=True)
    print(f"\nHora UTC: {datetime.now(timezone.utc).isoformat()}", flush=True)

    estado = cargar_estado()
    vistos = set(estado.get("vistos", []))

    print("\n📡 Consultando CoinBeacon...", flush=True)
    eventos = consultar_pumping_events()
    print(f"   Total eventos: {len(eventos)}", flush=True)

    if not eventos:
        print("   ⚠️ Sin eventos", flush=True)
        return

    eventos_filtrados = []
    for ev in eventos:
        symbol_full = ev.get("symbol", "")
        symbol_base = symbol_full.replace("USDT", "")
        if symbol_base in SYMBOLS:
            eventos_filtrados.append(ev)

    print(f"   Eventos nuestras monedas: {len(eventos_filtrados)}", flush=True)

    enviadas = 0

    for ev in eventos_filtrados:
        symbol_full = ev.get("symbol", "")
        symbol_base = symbol_full.replace("USDT", "")
        tipo = ev.get("type", "")
        spotted_at = ev.get("spottedAt", 0)

        clave = f"{symbol_full}_{tipo}_{spotted_at}"
        if clave in vistos:
            continue

        pct = ev.get("pct", 0)
        rvol = ev.get("rvol", 0)
        vol_ok = ev.get("volConfirmed", False)

        print(f"   → {symbol_base} {tipo}: pct={pct:+.2f}% vol={vol_ok} rvol={rvol:.2f}", flush=True)

        # Filtro por % mínimo
        if "pump" in tipo and pct < PCT_MIN_PUMP:
            continue
        if "dump" in tipo and pct > PCT_MIN_DUMP:
            continue

        price = ev.get("price", 0)
        prev_price = ev.get("prevPrice", 0)
        bias = ev.get("bias", "")
        clasif = ev.get("classification", "")
        quote_vol = ev.get("quoteVolume24h", 0)

        emoji, tipo_str, vol_txt = clasificar_evento(ev)

        ts_ev = datetime.fromtimestamp(spotted_at / 1000, tz=timezone.utc) + LIMA_OFFSET
        ts_str = ts_ev.strftime("%H:%M")

        msg = (
            f"{emoji} {symbol_base} — {tipo_str}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Precio: ${price:.8f}\n"
            f"📊 Cambio: {pct:+.2f}%\n"
            f"📊 Previo: ${prev_price:.8f}\n"
            f"{vol_txt}\n"
            f"🧭 {bias.upper()} | {clasif}\n"
            f"💰 Vol 24h: ${quote_vol:,.0f}\n"
            f"🕐 {ts_str} Lima\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )

        if enviar_telegram(msg):
            enviadas += 1
            vistos.add(clave)
            log_senal({
                "ts": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol_base,
                "tipo": tipo,
                "pct": pct,
                "price": price,
                "rvol": rvol,
                "vol_conf": vol_ok,
                "bias": bias,
                "clasif": clasif,
            })
            print(f"   {emoji} {symbol_base} {tipo} {pct:+.2f}% → enviado", flush=True)

    todos_vistos = list(vistos)
    if len(todos_vistos) > 3000:
        todos_vistos = todos_vistos[-3000:]

    estado["vistos"] = todos_vistos
    estado["updated_at"] = datetime.now(timezone.utc).isoformat()
    guardar_estado(estado)

    print("\n" + "=" * 70, flush=True)
    print(f"🎯 Alertas enviadas: {enviadas}", flush=True)
    print("=" * 70, flush=True)
    print("🏁 TERMINADO", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
