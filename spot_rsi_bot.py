#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPOT RSI BOT — interspot
- LONG only (spot)
- RSI 4h principal + RSI 1D/1W como contexto
- COMPRA: RSI4h cruza ↑ 34 (salió de sobreventa)
- VENTA:  RSI4h cruza ↓ 70 (salió de sobrecompra)
- ALERTAS: Entrada y salida de zona (4 avisos por ciclo)
- FILTRO: BTC RSI4h < 70 (no compra si BTC sobrecomprado)
- LEE PULSO COMPLETO (12h) para no perder cruces rápidos
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# CONFIGURACIÓN
# ============================================================

SYMBOLS = [
    "SUI", "RAY", "NEAR", "UNI", "ENA", "APT",
    "AVAX", "INJ", "ZEC", "SEI", "PEPE", "DASH",
]

# Monedas que están en el cache del recolector
IN_CACHE = {"RAY", "UNI", "ENA", "INJ", "ZEC", "DASH", "BTC"}

CACHE_REMOTE_BASE = (
    "https://raw.githubusercontent.com/mattluna1/"
    "interspot/main/data/cache"
)

STATE_FILE = Path("data/spot_rsi_state.json")
SIGNALS_LOG = Path("data/signals_log.jsonl")

LIMA_OFFSET = timedelta(hours=-5)

# Umbrales RSI 4h
RSI_OVERSOLD   = 34.0   # moneda: cruce ↑ → COMPRA
RSI_OVERBOUGHT = 70.0   # moneda: cruce ↓ → VENDE

# Filtro BTC
BTC_RSI_MAX = 70.0
BTC_RSI_MIN = 30.0

# ============================================================
# HELPERS
# ============================================================

def hora_lima():
    return datetime.now(timezone.utc) + LIMA_OFFSET

def leer_cache_remoto(symbol):
    """Lee el JSON del cache remoto (repo interspot)."""
    url = f"{CACHE_REMOTE_BASE}/{symbol}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"      ⚠️ cache {symbol}: {str(e)[:60]}", flush=True)
        return None

    pulso = data.get("pulso", [])
    if not pulso:
        return None

    # Devolvemos el pulso completo (ya no solo el último)
    return {
        "pulso": pulso,
        "updated_at": data.get("updated_at"),
    }

def enviar_telegram(msg):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("      ⚠️ Telegram no configurado", flush=True)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = f"chat_id={urllib.parse.quote(str(chat_id))}&text={urllib.parse.quote(msg)}".encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8")).get("ok", False)
    except Exception as e:
        print(f"      ⚠️ Telegram: {str(e)[:60]}", flush=True)
        return False

def cargar_estado():
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def guardar_estado(estado):
    STATE_FILE.parent.mkdir(exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2)

def log_senal(symbol, tipo, rsi4h, rsi1d, rsi1w, precio):
    """Guarda un histórico de señales en JSONL."""
    SIGNALS_LOG.parent.mkdir(exist_ok=True)
    registro = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "tipo": tipo,  # "BUY" o "SELL"
        "rsi4h": rsi4h,
        "rsi1d": rsi1d,
        "rsi1w": rsi1w,
        "precio": precio,
    }
    with SIGNALS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")

def clasificar_señal(rsi4h, rsi1d, rsi1w):
    """
    Clasifica la señal según el contexto del 1D y 1W.
    Devuelve: ("FUERTE", emoji) / ("MEDIA", emoji) / ("DÉBIL", emoji)
    """
    # Para COMPRA (RSI4h salió de sobreventa)
    if rsi1d is not None and rsi1w is not None:
        if rsi1d < 40 or rsi1w < 40:
            return "FUERTE", "🟢🟢"
        elif rsi1d < 60 or rsi1w < 60:
            return "MEDIA", "🟢"
        else:
            return "DÉBIL", "🟡"
    # Sin datos de 1D/1W
    return "MEDIA", "🟢"

def clasificar_venta(rsi4h, rsi1d, rsi1w):
    """
    Clasifica la señal de VENTA según el contexto del 1D y 1W.
    """
    if rsi1d is not None and rsi1w is not None:
        if rsi1d > 60 or rsi1w > 60:
            return "FUERTE", "🔴🔴"
        elif rsi1d > 40 or rsi1w > 40:
            return "MEDIA", "🔴"
        else:
            return "DÉBIL", "🟠"
    return "MEDIA", "🔴"

# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70, flush=True)
    print("🪙 SPOT RSI BOT — interspot", flush=True)
    print(f"   {len(SYMBOLS)} monedas | pulso completo (12h)", flush=True)
    print(f"   COMPRA: RSI4h cruza ↑ {RSI_OVERSOLD} (salió de sobreventa)", flush=True)
    print(f"   VENTA:  RSI4h cruza ↓ {RSI_OVERBOUGHT} (salió de sobrecompra)", flush=True)
    print(f"   Filtro BTC: RSI4h < {BTC_RSI_MAX}", flush=True)
    print("=" * 70, flush=True)
    print(f"\nHora UTC: {datetime.now(timezone.utc).isoformat()}", flush=True)

    # ── Contexto BTC ──
    print("\n🌐 BTC CONTEXTO", flush=True)
    btc_data = leer_cache_remoto("BTC")
    btc_rsi4 = None
    btc_precio = None
    if btc_data and btc_data.get("pulso"):
        ultimo = btc_data["pulso"][-1]
        btc_rsi4 = ultimo.get("rsi4h")
        btc_precio = ultimo.get("price")

    if btc_rsi4 is None:
        print("   ⚠️ Sin RSI4h de BTC → modo permisivo (sin filtro)", flush=True)
        btc_ok = True
    else:
        print(f"   Precio: ${btc_precio:.2f}", flush=True)
        print(f"   RSI4h:  {btc_rsi4:.1f}", flush=True)
        if btc_rsi4 >= BTC_RSI_MAX:
            print(f"   ⛔ BTC sobrecomprado (>= {BTC_RSI_MAX}) → sin compras", flush=True)
            btc_ok = False
        elif btc_rsi4 < BTC_RSI_MIN:
            print(f"   ⚠️ BTC en pánico (< {BTC_RSI_MIN}) → avisar", flush=True)
            btc_ok = True
        else:
            print(f"   ✅ BTC OK para compras", flush=True)
            btc_ok = True

    # ── Análisis de monedas ──
    estado = cargar_estado()
    ahora_lima = hora_lima().strftime("%Y-%m-%d %H:%M")
    señales = []

    for symbol in SYMBOLS:
        print(f"\n🔍 {symbol}", flush=True)
        data = leer_cache_remoto(symbol)
        if not data or not data.get("pulso"):
            print(f"   ⚠️ sin datos en cache", flush=True)
            continue

        pulso = data["pulso"]
        if len(pulso) < 2:
            print(f"   ⚠️ pulso insuficiente ({len(pulso)} muestras)", flush=True)
            continue

        # Extraer los RSI4h del pulso (últimas 144 muestras = 12h)
        rsi4h_list = [p.get("rsi4h") for p in pulso if p.get("rsi4h") is not None]
        if len(rsi4h_list) < 2:
            print(f"   ⚠️ sin suficientes RSI4h en pulso", flush=True)
            continue

        # Datos actuales (último pulso)
        ultimo = pulso[-1]
        rsi4_actual = ultimo.get("rsi4h")
        rsi1d = ultimo.get("rsi1d")
        rsi1w = ultimo.get("rsi1w")
        precio = ultimo.get("price")

        if rsi4_actual is None or precio is None:
            print(f"   ⚠️ sin RSI4h o precio", flush=True)
            continue

        # Estado guardado (para anti-spam y para saber el RSI previo)
        prev = estado.get(symbol, {})
        last_signal = prev.get("last_signal")
        rsi4_prev_guardado = prev.get("rsi4h")

        # ── Detección de cruces en TODO el pulso ──
        # Recorremos el pulso buscando cruces que no hayamos avisado
        cruce_entrada_sobreventa = False   # RSI4h cruza ↓ 34
        cruce_salida_sobreventa = False    # RSI4h cruza ↑ 34 → COMPRA
        cruce_entrada_sobrecompra = False  # RSI4h cruza ↑ 70
        cruce_salida_sobrecompra = False   # RSI4h cruza ↓ 70 → VENDE

        # Recorremos desde el principio del pulso
        rsi_prev = None
        for p in pulso:
            rsi_actual_pulso = p.get("rsi4h")
            if rsi_actual_pulso is None:
                continue
            if rsi_prev is not None:
                # Detectar cruces ↓ 34 (entrada en sobreventa)
                if rsi_prev >= RSI_OVERSOLD and rsi_actual_pulso < RSI_OVERSOLD:
                    cruce_entrada_sobreventa = True
                    cruce_salida_sobreventa = False  # reset
                # Detectar cruces ↑ 34 (salida de sobreventa → COMPRA)
                if rsi_prev < RSI_OVERSOLD and rsi_actual_pulso >= RSI_OVERSOLD:
                    cruce_salida_sobreventa = True
                    cruce_entrada_sobreventa = False
                # Detectar cruces ↑ 70 (entrada en sobrecompra)
                if rsi_prev <= RSI_OVERBOUGHT and rsi_actual_pulso > RSI_OVERBOUGHT:
                    cruce_entrada_sobrecompra = True
                    cruce_salida_sobrecompra = False
                # Detectar cruces ↓ 70 (salida de sobrecompra → VENDE)
                if rsi_prev > RSI_OVERBOUGHT and rsi_actual_pulso <= RSI_OVERBOUGHT:
                    cruce_salida_sobrecompra = True
                    cruce_entrada_sobrecompra = False
            rsi_prev = rsi_actual_pulso

        # ── Procesar señales ──

        # SEÑAL COMPRA (salió de sobreventa)
        if cruce_salida_sobreventa and last_signal != "BUY":
            if not btc_ok:
                print(f"   ⏭️ COMPRA bloqueada: BTC sobrecomprado", flush=True)
            else:
                nivel, emoji = clasificar_señal(rsi4_actual, rsi1d, rsi1w)
                nota_btc = ""
                if btc_rsi4 is not None and btc_rsi4 < BTC_RSI_MIN:
                    nota_btc = f"⚠️ BTC RSI4h bajo ({btc_rsi4:.1f})\n"

                msg = (
                    f"{emoji} COMPRA {nivel} — {symbol}\n"
                    f"📈 Precio: ${precio:.6f}\n"
                    f"📊 RSI4h: salió de sobreventa (cruzó ↑ {RSI_OVERSOLD})\n"
                    + (f"📅 RSI 1D: {rsi1d:.1f}\n" if rsi1d is not None else "")
                    + (f"📅 RSI 1W: {rsi1w:.1f}\n" if rsi1w is not None else "")
                    + nota_btc
                    + f"🕐 {ahora_lima} Lima\n"
                    f"━━━━━━━━━━━━━━━━━━━"
                )
                print(f"   ✅ {emoji} BUY {nivel}", flush=True)
                enviar_telegram(msg)
                señales.append(("BUY", symbol, nivel))
                log_senal(symbol, "BUY", rsi4_actual, rsi1d, rsi1w, precio)
                estado[symbol] = {**prev, "rsi4h": rsi4_actual, "last_signal": "BUY"}
                continue

        # SEÑAL VENTA (salió de sobrecompra)
        if cruce_salida_sobrecompra and last_signal != "SELL":
            nivel, emoji = clasificar_venta(rsi4_actual, rsi1d, rsi1w)
            msg = (
                f"{emoji} VENDE {nivel} — {symbol}\n"
                f"📈 Precio: ${precio:.6f}\n"
                f"📊 RSI4h: salió de sobrecompra (cruzó ↓ {RSI_OVERBOUGHT})\n"
                + (f"📅 RSI 1D: {rsi1d:.1f}\n" if rsi1d is not None else "")
                + (f"📅 RSI 1W: {rsi1w:.1f}\n" if rsi1w is not None else "")
                + f"🕐 {ahora_lima} Lima\n"
                f"━━━━━━━━━━━━━━━━━━━"
            )
            print(f"   ✅ {emoji} SELL {nivel}", flush=True)
            enviar_telegram(msg)
            señales.append(("SELL", symbol, nivel))
            log_senal(symbol, "SELL", rsi4_actual, rsi1d, rsi1w, precio)
            estado[symbol] = {**prev, "rsi4h": rsi4_actual, "last_signal": "SELL"}
            continue

        # ── Alertas de ENTRADA en zona (solo informativas) ──
        if cruce_entrada_sobreventa:
            msg = (
                f"🟡 {symbol} ENTRÓ en SOBREVENTA (4h)\n"
                f"📈 Precio: ${precio:.6f}\n"
                f"📊 RSI4h: {rsi4_actual:.1f} (cruzó ↓ {RSI_OVERSOLD})\n"
                + (f"📅 RSI 1D: {rsi1d:.1f}\n" if rsi1d is not None else "")
                + (f"📅 RSI 1W: {rsi1w:.1f}\n" if rsi1w is not None else "")
                + f"🕐 {ahora_lima} Lima\n"
                f"━━━━━━━━━━━━━━━━━━━"
            )
            enviar_telegram(msg)
            print(f"   🟡 ENTRÓ en sobreventa", flush=True)

        if cruce_entrada_sobrecompra:
            msg = (
                f"🟠 {symbol} ENTRÓ en SOBRECOMPRA (4h)\n"
                f"📈 Precio: ${precio:.6f}\n"
                f"📊 RSI4h: {rsi4_actual:.1f} (cruzó ↑ {RSI_OVERBOUGHT})\n"
                + (f"📅 RSI 1D: {rsi1d:.1f}\n" if rsi1d is not None else "")
                + (f"📅 RSI 1W: {rsi1w:.1f}\n" if rsi1w is not None else "")
                + f"🕐 {ahora_lima} Lima\n"
                f"━━━━━━━━━━━━━━━━━━━"
            )
            enviar_telegram(msg)
            print(f"   🟠 ENTRÓ en sobrecompra", flush=True)

        # ── Sin cruce ──
        print(f"   ⏳ sin cruce (RSI4h={rsi4_actual:.1f})", flush=True)
        estado[symbol] = {**prev, "rsi4h": rsi4_actual}

    guardar_estado(estado)

    print("\n" + "=" * 70, flush=True)
    if señales:
        print(f"🎯 {len(señales)} señal(es):", flush=True)
        for op, sym, nivel in señales:
            print(f"   {op} {sym} ({nivel})", flush=True)
    else:
        print("Sin señales de compra/venta.", flush=True)
    print("=" * 70, flush=True)
    print("🏁 TERMINADO", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
