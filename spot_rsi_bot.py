#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPOT RSI BOT — interspot v3 (simplificado)
- Alerta cuando una moneda alcanza RSI extremo (sobreventa o sobrecompra)
- En cada alerta incluye el estado de BTC para contexto
- Sin clasificaciones. Tú decides.
- Lee pulso completo (12h)
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
    "SUI", "ARB", "RAY", "NEAR", "UNI", "ENA",
    "APT", "AVAX", "INJ", "ZEC", "SEI", "DASH",
]

CACHE_REMOTE_BASE = (
    "https://raw.githubusercontent.com/mattluna1/"
    "interspot/main/data/cache"
)

STATE_FILE = Path("data/spot_rsi_state.json")
SIGNALS_LOG = Path("data/signals_log.jsonl")

LIMA_OFFSET = timedelta(hours=-5)

# Umbrales RSI (mismos para 4h, 1D, 1W)
RSI_SOBREVENTA   = 34.0
RSI_SOBRECOMPRA  = 70.0

# ============================================================
# HELPERS
# ============================================================

def hora_lima():
    return datetime.now(timezone.utc) + LIMA_OFFSET


def leer_cache_remoto(symbol):
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
    return {"pulso": pulso}


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


def log_senal(symbol, tipo, detalle):
    SIGNALS_LOG.parent.mkdir(exist_ok=True)
    registro = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "tipo": tipo,
        **detalle,
    }
    with SIGNALS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def detectar_cruces(pulso, campo_rsi, umbral_bajo, umbral_alto):
    """
    Detecta si en el pulso hubo:
      - entrada en sobreventa (cruzó ↓ umbral_bajo)
      - salida de sobreventa (cruzó ↑ umbral_bajo)
      - entrada en sobrecompra (cruzó ↑ umbral_alto)
      - salida de sobrecompra (cruzó ↓ umbral_alto)
    """
    entra_ov = sale_ov = entra_ob = sale_ob = False
    prev = None
    for p in pulso:
        v = p.get(campo_rsi)
        if v is None:
            continue
        if prev is not None:
            if prev >= umbral_bajo and v < umbral_bajo:
                entra_ov = True
                sale_ov = False
            if prev < umbral_bajo and v >= umbral_bajo:
                sale_ov = True
                entra_ov = False
            if prev <= umbral_alto and v > umbral_alto:
                entra_ob = True
                sale_ob = False
            if prev > umbral_alto and v <= umbral_alto:
                sale_ob = True
                entra_ob = False
        prev = v
    return entra_ov, sale_ov, entra_ob, sale_ob


def estado_btc_lineas(btc):
    """Devuelve 3 líneas con el estado de BTC."""
    if not btc or btc.get("rsi4h") is None:
        return "🌐 BTC: sin datos"
    lineas = [
        f"🌐 BTC: ${btc['precio']:.2f}",
        f"   RSI4h={btc['rsi4h']:.1f} | RSI1D={btc['rsi1d'] if btc.get('rsi1d') is not None else '—'} | RSI1W={btc['rsi1w'] if btc.get('rsi1w') is not None else '—'}",
    ]
    # Interpretación simple de BTC
    r4 = btc["rsi4h"]
    if r4 >= RSI_SOBRECOMPRA:
        lineas.append("   ⚠️ BTC sobrecomprado → altcoin arriba puede ser trampa")
    elif r4 <= RSI_SOBREVENTA:
        lineas.append("   ✅ BTC en suelo → altcoin barata puede ser buena")
    else:
        lineas.append("   ➖ BTC neutral")
    return "\n".join(lineas)


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70, flush=True)
    print("🪙 SPOT RSI BOT — interspot v3 (simple)", flush=True)
    print(f"   {len(SYMBOLS)} monedas | alertas de extremos + contexto BTC", flush=True)
    print(f"   Sobreventa: < {RSI_SOBREVENTA}  |  Sobrecompra: > {RSI_SOBRECOMPRA}", flush=True)
    print("=" * 70, flush=True)
    print(f"\nHora UTC: {datetime.now(timezone.utc).isoformat()}", flush=True)

    # ── Contexto BTC ──
    print("\n🌐 BTC CONTEXTO", flush=True)
    btc_data = leer_cache_remoto("BTC")
    btc = {}
    if btc_data and btc_data.get("pulso"):
        u = btc_data["pulso"][-1]
        btc = {
            "precio": u.get("price"),
            "rsi4h": u.get("rsi4h"),
            "rsi1d": u.get("rsi1d"),
            "rsi1w": u.get("rsi1w"),
        }
        print(f"   Precio: ${btc['precio']:.2f}", flush=True)
        print(f"   RSI4h: {btc['rsi4h']:.1f}", flush=True)
        if btc['rsi4h'] is not None:
            if btc['rsi4h'] >= RSI_SOBRECOMPRA:
                print(f"   ⚠️ BTC sobrecomprado", flush=True)
            elif btc['rsi4h'] <= RSI_SOBREVENTA:
                print(f"   ✅ BTC en suelo", flush=True)
            else:
                print(f"   ➖ BTC neutral", flush=True)
    else:
        print("   ⚠️ Sin datos de BTC", flush=True)

    estado = cargar_estado()
    ahora_lima = hora_lima().strftime("%Y-%m-%d %H:%M")
    señales = []

    for symbol in SYMBOLS:
        print(f"\n🔍 {symbol}", flush=True)
        data = leer_cache_remoto(symbol)
        if not data or not data.get("pulso"):
            print(f"   ⚠️ sin datos", flush=True)
            continue

        pulso = data["pulso"]
        if len(pulso) < 2:
            print(f"   ⚠️ pulso insuficiente", flush=True)
            continue

        u = pulso[-1]
        rsi4h = u.get("rsi4h")
        rsi1d = u.get("rsi1d")
        rsi1w = u.get("rsi1w")
        precio = u.get("price")

        if rsi4h is None or precio is None:
            print(f"   ⚠️ sin RSI4h o precio", flush=True)
            continue

        rsi1d_str = f"{rsi1d:.1f}" if rsi1d is not None else "—"
        rsi1w_str = f"{rsi1w:.1f}" if rsi1w is not None else "—"
        print(f"   Precio: ${precio:.6f} | RSI4h={rsi4h:.1f} | RSI1D={rsi1d_str} | RSI1W={rsi1w_str}", flush=True)

        prev = estado.get(symbol, {})

        # Detectar cruces en 4h, 1D, 1W
        e_ov_4h, s_ov_4h, e_ob_4h, s_ob_4h = detectar_cruces(pulso, "rsi4h", RSI_SOBREVENTA, RSI_SOBRECOMPRA)
        e_ov_1d, s_ov_1d, e_ob_1d, s_ob_1d = detectar_cruces(pulso, "rsi1d", RSI_SOBREVENTA, RSI_SOBRECOMPRA)
        e_ov_1w, s_ov_1w, e_ob_1w, s_ob_1w = detectar_cruces(pulso, "rsi1w", RSI_SOBREVENTA, RSI_SOBRECOMPRA)

        btc_txt = estado_btc_lineas(btc)

        # ─────────────────────────────────────
        # ALERTA: ENTRÓ EN SOBREVENTA (4h)
        # ─────────────────────────────────────
        if e_ov_4h:
            msg = (
                f"🟢 {symbol} ENTRÓ EN SOBREVENTA (4h)\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 Precio: ${precio:.6f}\n"
                f"📊 RSI4h: {rsi4h:.1f}  (cruzó ↓ {RSI_SOBREVENTA})\n"
                f"📊 RSI1D: {rsi1d_str}\n"
                f"📊 RSI1W: {rsi1w_str}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{btc_txt}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 {ahora_lima} Lima"
            )
            print(f"   🟢 4h entró sobreventa", flush=True)
            enviar_telegram(msg)
            señales.append(("SOBREVENTA-4H", symbol))
            log_senal(symbol, "SOBREVENTA_4H", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

        # ─────────────────────────────────────
        # ALERTA: ENTRÓ EN SOBRECOMPRA (4h)
        # ─────────────────────────────────────
        if e_ob_4h:
            msg = (
                f"🔴 {symbol} ENTRÓ EN SOBRECOMPRA (4h)\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 Precio: ${precio:.6f}\n"
                f"📊 RSI4h: {rsi4h:.1f}  (cruzó ↑ {RSI_SOBRECOMPRA})\n"
                f"📊 RSI1D: {rsi1d_str}\n"
                f"📊 RSI1W: {rsi1w_str}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{btc_txt}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 {ahora_lima} Lima"
            )
            print(f"   🔴 4h entró sobrecompra", flush=True)
            enviar_telegram(msg)
            señales.append(("SOBRECOMPRA-4H", symbol))
            log_senal(symbol, "SOBRECOMPRA_4H", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

        # ─────────────────────────────────────
        # ALERTA: SALIÓ DE SOBREVENTA (4h) → posible COMPRA
        # ─────────────────────────────────────
        if s_ov_4h:
            msg = (
                f"🟢🟢 {symbol} SALIÓ DE SOBREVENTA (4h)\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 Precio: ${precio:.6f}\n"
                f"📊 RSI4h: {rsi4h:.1f}  (cruzó ↑ {RSI_SOBREVENTA})\n"
                f"📊 RSI1D: {rsi1d_str}\n"
                f"📊 RSI1W: {rsi1w_str}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{btc_txt}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💡 Momento típico de COMPRA si BTC acompaña\n"
                f"🕐 {ahora_lima} Lima"
            )
            print(f"   🟢🟢 4h salió sobreventa → COMPRA", flush=True)
            enviar_telegram(msg)
            señales.append(("COMPRA-4H", symbol))
            log_senal(symbol, "COMPRA_4H", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

        # ─────────────────────────────────────
        # ALERTA: SALIÓ DE SOBRECOMPRA (4h) → posible VENTA
        # ─────────────────────────────────────
        if s_ob_4h:
            msg = (
                f"🔴🔴 {symbol} SALIÓ DE SOBRECOMPRA (4h)\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 Precio: ${precio:.6f}\n"
                f"📊 RSI4h: {rsi4h:.1f}  (cruzó ↓ {RSI_SOBRECOMPRA})\n"
                f"📊 RSI1D: {rsi1d_str}\n"
                f"📊 RSI1W: {rsi1w_str}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{btc_txt}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💡 Momento típico de VENTA si BTC confirma giro\n"
                f"🕐 {ahora_lima} Lima"
            )
            print(f"   🔴🔴 4h salió sobrecompra → VENTA", flush=True)
            enviar_telegram(msg)
            señales.append(("VENTA-4H", symbol))
            log_senal(symbol, "VENTA_4H", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

        # ─────────────────────────────────────
        # ALERTAS 1D (contexto)
        # ─────────────────────────────────────
        if e_ov_1d:
            enviar_telegram(
                f"🟡 {symbol} — 1D ENTRÓ EN SOBREVENTA\n"
                f"RSI1D={rsi1d_str} | RSI4h={rsi4h:.1f} | RSI1W={rsi1w_str}\n"
                f"Precio: ${precio:.6f}\n"
                f"{btc_txt}\n"
                f"🕐 {ahora_lima}"
            )
            print(f"   🟡 1D entró sobreventa", flush=True)
        if e_ob_1d:
            enviar_telegram(
                f"🟠 {symbol} — 1D ENTRÓ EN SOBRECOMPRA\n"
                f"RSI1D={rsi1d_str} | RSI4h={rsi4h:.1f} | RSI1W={rsi1w_str}\n"
                f"Precio: ${precio:.6f}\n"
                f"{btc_txt}\n"
                f"🕐 {ahora_lima}"
            )
            print(f"   🟠 1D entró sobrecompra", flush=True)

        # ─────────────────────────────────────
        # ALERTAS 1W (contexto mayor)
        # ─────────────────────────────────────
        if e_ov_1w:
            enviar_telegram(
                f"🟡 {symbol} — 1W ENTRÓ EN SOBREVENTA\n"
                f"RSI1W={rsi1w_str} | RSI1D={rsi1d_str} | RSI4h={rsi4h:.1f}\n"
                f"Precio: ${precio:.6f}\n"
                f"{btc_txt}\n"
                f"🕐 {ahora_lima}"
            )
            print(f"   🟡 1W entró sobreventa", flush=True)
        if e_ob_1w:
            enviar_telegram(
                f"🟠 {symbol} — 1W ENTRÓ EN SOBRECOMPRA\n"
                f"RSI1W={rsi1w_str} | RSI1D={rsi1d_str} | RSI4h={rsi4h:.1f}\n"
                f"Precio: ${precio:.6f}\n"
                f"{btc_txt}\n"
                f"🕐 {ahora_lima}"
            )
            print(f"   🟠 1W entró sobrecompra", flush=True)

        estado[symbol] = {**prev, "rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w}

    guardar_estado(estado)

    print("\n" + "=" * 70, flush=True)
    if señales:
        print(f"🎯 {len(señales)} alerta(s):", flush=True)
        for tipo, sym in señales:
            print(f"   {tipo} {sym}", flush=True)
    else:
        print("Sin alertas.", flush=True)
    print("=" * 70, flush=True)
    print("🏁 TERMINADO", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
