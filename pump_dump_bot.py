#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PUMP/DUMP BOT — interspot
- Lee CoinBeacon /pumping/events cada 5 min
- Solo pump_5m y dump_5m
- Solo el evento más reciente por moneda (evita ráfagas)
- Cooldown 15 min por symbol+tipo+bias+clasif
- Filtro: % mínimo 2.5% + volumen confirmado
- Rotación de log a 1 MB
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
PCT_MIN_PUMP = 2.5
PCT_MIN_DUMP = -2.5
COOLDOWN_MIN = 15

STATE_FILE = Path("data/pump_dump_state.json")
SIGNALS_LOG = Path("data/pump_dump_log.jsonl")

LIMA_OFFSET = timedelta(hours=-5)

COINBEACON_URL = "https://api.coinbeacon.io/pumping/events"


def hora_lima():
    return datetime.now(timezone.utc) + LIMA_OFFSET


def cargar_estado():
    if not STATE_FILE.exists():
        return {"vistos": [], "cooldown": {}}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if "vistos" not in data:
                data["vistos"] = []
            if "cooldown" not in data:
                data["cooldown"] = {}
            return data
    except Exception:
        pass
    return {"vistos": [], "cooldown": {}}


def guardar_estado(estado):
    STATE_FILE.parent.mkdir(exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2)


def log_senal(registro):
    SIGNALS_LOG.parent.mkdir(exist_ok=True)
    # Rotación: si supera 1 MB, conservar solo últimas 1000 líneas
    if SIGNALS_LOG.exists() and SIGNALS_LOG.stat().st_size > 1_000_000:
        try:
            with SIGNALS_LOG.open("r", encoding="utf-8") as f:
                lineas = f.readlines()
            with SIGNALS_LOG.open("w", encoding="utf-8") as f:
                f.writelines(lineas[-1000:])
        except Exception:
            pass
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

    types = "pump_5m,dump_5m"
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
        tipo_str = "PUMP 5m"
    elif "dump" in tipo:
        emoji = "💥"
        tipo_str = "DUMP 5m"
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
    print("🚀 PUMP/DUMP BOT — CoinBeacon (5m)", flush=True)
    print(f"   {len(SYMBOLS)} monedas: {', '.join(SYMBOLS)}", flush=True)
    print(f"   % min: {PCT_MIN_PUMP} | Cooldown: {COOLDOWN_MIN} min", flush=True)
    print("=" * 70, flush=True)
    print(f"\nHora UTC: {datetime.now(timezone.utc).isoformat()}", flush=True)

    estado = cargar_estado()
    vistos = set(estado.get("vistos", []))
    cooldowns = estado.get("cooldown", {})

    print("\n📡 Consultando CoinBeacon...", flush=True)
    eventos = consultar_pumping_events()
    print(f"   Total eventos: {len(eventos)}", flush=True)

    if not eventos:
        print("   ⚠️ Sin eventos", flush=True)
        return

    # Filtrar por nuestras monedas
    eventos_filtrados = []
    for ev in eventos:
        symbol_full = ev.get("symbol", "")
        symbol_base = symbol_full.replace("USDT", "")
        if symbol_base in SYMBOLS:
            eventos_filtrados.append(ev)

    print(f"   Eventos nuestras monedas: {len(eventos_filtrados)}", flush=True)

    # ═══════════════════════════════════════════════
    # Agrupar por moneda: solo el evento MÁS RECIENTE de cada una
    # ═══════════════════════════════════════════════
    eventos_por_moneda = {}
    for ev in eventos_filtrados:
        symbol_base = ev.get("symbol", "").replace("USDT", "")
        spotted_at = ev.get("spottedAt", 0)
        actual = eventos_por_moneda.get(symbol_base)
        if actual is None or spotted_at > actual.get("spottedAt", 0):
            eventos_por_moneda[symbol_base] = ev

    eventos_filtrados = list(eventos_por_moneda.values())
    print(f"   Eventos únicos por moneda: {len(eventos_filtrados)}", flush=True)

    enviadas = 0
    ahora_ts = datetime.now(timezone.utc).timestamp()

    for ev in eventos_filtrados:
        symbol_full = ev.get("symbol", "")
        symbol_base = symbol_full.replace("USDT", "")
        tipo = ev.get("type", "")
        spotted_at = ev.get("spottedAt", 0)
        pct = ev.get("pct", 0)
        rvol = ev.get("rvol", 0)
        vol_ok = ev.get("volConfirmed", False)
        bias = ev.get("bias", "")
        clasif = ev.get("classification", "")

         # Clave de evento único
        clave_evento = f"{symbol_full}_{tipo}_{spotted_at}"
        if clave_evento in vistos:
            print(f"   ⏭️ {symbol_base} {tipo}: ya visto", flush=True)
            continue

        print(f"   → {symbol_base} {tipo}: pct={pct:+.2f}% vol={vol_ok} rvol={rvol:.2f} | {bias}/{clasif}", flush=True)

        # FILTRO 1: volumen confirmado
        if not vol_ok:
            vistos.add(clave_evento)
            continue

        # FILTRO 2: % mínimo
        if "pump" in tipo and pct < PCT_MIN_PUMP:
            vistos.add(clave_evento)
            continue
        if "dump" in tipo and pct > PCT_MIN_DUMP:
            vistos.add(clave_evento)
            continue

        # FILTRO 3: cooldown
        clave_cooldown = f"{symbol_base}_{tipo}_{bias}_{clasif}"
        ultimo = cooldowns.get(clave_cooldown, 0)
        if ahora_ts - ultimo < COOLDOWN_MIN * 60:
            print(f"      ⏸️ Cooldown activo ({int((ahora_ts-ultimo)/60)} min)", flush=True)
            vistos.add(clave_evento)
            continue

        price = ev.get("price", 0)
        prev_price = ev.get("prevPrice", 0)
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
            vistos.add(clave_evento)
            cooldowns[clave_cooldown] = ahora_ts
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

    # Limitar historial
    todos_vistos = list(vistos)
    if len(todos_vistos) > 3000:
        todos_vistos = todos_vistos[-3000:]

    # Limitar cooldowns a los últimos 200
    if len(cooldowns) > 200:
        cooldowns_ordenados = sorted(cooldowns.items(), key=lambda x: x[1], reverse=True)[:200]
        cooldowns = dict(cooldowns_ordenados)

    estado["vistos"] = todos_vistos
    estado["cooldown"] = cooldowns
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
