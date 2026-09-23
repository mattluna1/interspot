#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPOT RSI BOT — interspot v9
- Progresión de círculos: sin círculo (aviso) → 1 círculo (BTC 4h) → 2 círculos (BTC 1D)
- 1D de la moneda: 🔴 directo
- 1W de la moneda: 🔴🔴 directo
- Contexto BTC ampliado (4h + 1D + impulso)
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SYMBOLS = [
    "SUI", "ARB", "RAY", "NEAR", "UNI", "ENA",
    "APT", "AVAX", "INJ", "ZEC", "SEI", "DASH", "WLD"
]

CACHE_REMOTE_BASE = (
    "https://raw.githubusercontent.com/mattluna1/"
    "interspot/main/data/cache"
)

STATE_FILE = Path("data/spot_rsi_state.json")
SIGNALS_LOG = Path("data/signals_log.jsonl")

LIMA_OFFSET = timedelta(hours=-5)

# Umbrales moneda
RSI_OS_4H = 34.0
RSI_OB_4H = 70.0
RSI_OS_1D = 30.0
RSI_OB_1D = 65.0
RSI_OS_1W = 30.0
RSI_OB_1W = 70.0

# Contexto BTC (zonas)
BTC_RSI_SOBREVENTA     = 35.0
BTC_RSI_BAJA_SALUDABLE = 48.0
BTC_RSI_CENTRAL        = 52.0
BTC_RSI_SOBRECOMPRA    = 65.0

# Deltas BTC
BTC_D_UP_FUERTE        =  0.34
BTC_D_UP_INDECISO      =  0.10
BTC_D_DOWN_INDECISO    = -0.10
BTC_D_DOWN_FUERTE      = -0.34
BTC_DELTA_CRUCE        =  1.50

# Impulso corto
BTC_SALTO_15M  = 5.0
BTC_RSI15_ALTO = 45.0
BTC_RSI15_BAJO = 55.0
BTC_RSI1_ALTO  = 48.0
BTC_RSI1_BAJO  = 52.0


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


def analizar_btc(btc, prev_btc):
    rsi4  = btc.get("rsi4h")
    rsi1d = btc.get("rsi1d")
    rsi15 = btc.get("rsi15")
    rsi1h = btc.get("rsi1h")

    delta_4h = None
    if rsi4 is not None and prev_btc.get("rsi4h") is not None:
        delta_4h = rsi4 - prev_btc["rsi4h"]

    delta_1d = None
    if rsi1d is not None and prev_btc.get("rsi1d") is not None:
        delta_1d = rsi1d - prev_btc["rsi1d"]

    impulso_up = False
    impulso_down = False
    if rsi15 is not None and rsi1h is not None:
        prev_rsi15 = prev_btc.get("rsi15")
        subida_15m = (prev_rsi15 is not None and (rsi15 - prev_rsi15) >= BTC_SALTO_15M)
        bajada_15m = (prev_rsi15 is not None and (prev_rsi15 - rsi15) >= BTC_SALTO_15M)
        if (rsi15 >= BTC_RSI15_ALTO and rsi1h >= BTC_RSI1_ALTO) or subida_15m:
            impulso_up = True
        if (rsi15 <= BTC_RSI15_BAJO and rsi1h <= BTC_RSI1_BAJO) or bajada_15m:
            impulso_down = True

    dir_4h, modo_4h, razon_4h = "flat", "neutro", "sin datos"
    if rsi4 is None:
        razon_4h = "RSI4h N/A"
    elif rsi4 >= BTC_RSI_SOBRECOMPRA:
        if impulso_down:
            dir_4h, modo_4h = "down", "indeciso"
            razon_4h = "sobrecompra + impulso DOWN"
        elif delta_4h is not None:
            if delta_4h > BTC_D_UP_FUERTE:
                dir_4h, modo_4h = "up", "fuerte"
                razon_4h = f"sobrecompra Δ{delta_4h:+.2f}"
            elif delta_4h > BTC_D_UP_INDECISO:
                dir_4h, modo_4h = "up", "indeciso"
                razon_4h = f"sobrecompra Δ{delta_4h:+.2f}"
            elif delta_4h < BTC_D_DOWN_FUERTE:
                dir_4h, modo_4h = "down", "fuerte"
                razon_4h = f"sobrecompra Δ{delta_4h:+.2f} gira"
            elif delta_4h < BTC_D_DOWN_INDECISO:
                dir_4h, modo_4h = "down", "indeciso"
                razon_4h = f"sobrecompra Δ{delta_4h:+.2f}"
    elif rsi4 >= BTC_RSI_CENTRAL:
        if impulso_down:
            dir_4h, modo_4h = "down", "indeciso"
            razon_4h = "central + impulso DOWN"
        elif delta_4h is not None and delta_4h < -BTC_DELTA_CRUCE:
            dir_4h, modo_4h = "down", "indeciso"
            razon_4h = f"central Δ{delta_4h:+.2f} gira DOWN"
        else:
            dir_4h, modo_4h = "up", "fuerte"
            razon_4h = f"RSI4h {rsi4:.2f} alta saludable"
    elif rsi4 >= BTC_RSI_BAJA_SALUDABLE:
        if impulso_up:
            dir_4h, modo_4h = "up", "indeciso"
            razon_4h = "central + impulso UP"
        elif impulso_down:
            dir_4h, modo_4h = "down", "indeciso"
            razon_4h = "central + impulso DOWN"
        elif delta_4h is not None:
            if delta_4h > BTC_D_UP_FUERTE:
                dir_4h, modo_4h = "up", "fuerte"
                razon_4h = f"central Δ{delta_4h:+.2f}"
            elif delta_4h < BTC_D_DOWN_FUERTE:
                dir_4h, modo_4h = "down", "fuerte"
                razon_4h = f"central Δ{delta_4h:+.2f}"
    elif rsi4 >= BTC_RSI_SOBREVENTA:
        if impulso_up:
            dir_4h, modo_4h = "up", "indeciso"
            razon_4h = "baja saludable + impulso UP"
        elif delta_4h is not None and delta_4h > BTC_DELTA_CRUCE:
            dir_4h, modo_4h = "up", "indeciso"
            razon_4h = f"baja saludable Δ{delta_4h:+.2f} gira UP"
        else:
            dir_4h, modo_4h = "down", "fuerte"
            razon_4h = f"RSI4h {rsi4:.2f} baja saludable"
    else:
        if impulso_up:
            dir_4h, modo_4h = "up", "indeciso"
            razon_4h = "sobreventa + impulso UP"
        elif delta_4h is not None:
            if delta_4h < BTC_D_DOWN_FUERTE:
                dir_4h, modo_4h = "down", "fuerte"
                razon_4h = f"sobreventa Δ{delta_4h:+.2f} sigue"
            elif delta_4h > BTC_D_UP_FUERTE:
                dir_4h, modo_4h = "up", "fuerte"
                razon_4h = f"sobreventa Δ{delta_4h:+.2f} gira"

    dir_1d, modo_1d, razon_1d = "flat", "neutro", "sin datos"
    if rsi1d is None:
        razon_1d = "RSI1d N/A"
    else:
        if rsi1d >= RSI_OB_1D:
            if delta_1d is not None and delta_1d < -0.5:
                dir_1d, modo_1d = "down", "fuerte"
                razon_1d = f"1D techo girando Δ{delta_1d:+.2f}"
            elif delta_1d is not None and delta_1d > 0.5:
                dir_1d, modo_1d = "up", "fuerte"
                razon_1d = f"1D techo subiendo Δ{delta_1d:+.2f}"
            else:
                dir_1d, modo_1d = "up", "indeciso"
                razon_1d = "1D techo plano"
        elif rsi1d <= RSI_OS_1D:
            if delta_1d is not None and delta_1d > 0.5:
                dir_1d, modo_1d = "up", "fuerte"
                razon_1d = f"1D suelo girando Δ{delta_1d:+.2f}"
            elif delta_1d is not None and delta_1d < -0.5:
                dir_1d, modo_1d = "down", "fuerte"
                razon_1d = f"1D suelo cayendo Δ{delta_1d:+.2f}"
            else:
                dir_1d, modo_1d = "down", "indeciso"
                razon_1d = "1D suelo plano"
        else:
            if delta_1d is not None:
                if delta_1d > 1.0:
                    dir_1d, modo_1d = "up", "fuerte"
                    razon_1d = f"1D media subiendo Δ{delta_1d:+.2f}"
                elif delta_1d < -1.0:
                    dir_1d, modo_1d = "down", "fuerte"
                    razon_1d = f"1D media bajando Δ{delta_1d:+.2f}"

    return {
        "dir_4h": dir_4h, "modo_4h": modo_4h, "razon_4h": razon_4h,
        "dir_1d": dir_1d, "modo_1d": modo_1d, "razon_1d": razon_1d,
        "delta_4h": delta_4h, "delta_1d": delta_1d,
        "impulso_up": impulso_up, "impulso_down": impulso_down,
    }


def btc_resumen(btc, ctx):
    if not btc or btc.get("rsi4h") is None:
        return "🌐 BTC: sin datos"
    r1d = f"{btc['rsi1d']:.1f}" if btc.get('rsi1d') is not None else "—"
    r1w = f"{btc['rsi1w']:.1f}" if btc.get('rsi1w') is not None else "—"
    lineas = [
        f"🌐 BTC: ${btc['precio']:.2f}",
        f"   4h={btc['rsi4h']:.1f} | 1D={r1d} | 1W={r1w}",
        f"   ➡️ 4h: {ctx['dir_4h'].upper()} {ctx['modo_4h'].upper()} ({ctx['razon_4h']})",
        f"   ➡️ 1D: {ctx['dir_1d'].upper()} {ctx['modo_1d'].upper()} ({ctx['razon_1d']})",
    ]
    return "\n".join(lineas)


def main():
    print("=" * 70, flush=True)
    print("🪙 SPOT RSI BOT — interspot v9 (círculos progresivos)", flush=True)
    print(f"   {len(SYMBOLS)} monedas", flush=True)
    print("=" * 70, flush=True)
    print(f"\nHora UTC: {datetime.now(timezone.utc).isoformat()}", flush=True)

    estado = cargar_estado()
    prev_btc = estado.get("_BTC", {})

    print("\n🌐 BTC CONTEXTO", flush=True)
    btc_data = leer_cache_remoto("BTC")
    btc = {}
    ctx_btc = {}
    if btc_data and btc_data.get("pulso"):
        u = btc_data["pulso"][-1]
        btc = {
            "precio": u.get("price"),
            "rsi4h": u.get("rsi4h"),
            "rsi1d": u.get("rsi1d"),
            "rsi1w": u.get("rsi1w"),
            "rsi15": u.get("rsi15"),
            "rsi1h": u.get("rsi1h"),
        }
        ctx_btc = analizar_btc(btc, prev_btc)
        print(f"   ${btc['precio']:.2f} | RSI4h={btc['rsi4h']:.1f} | RSI1D={btc['rsi1d']:.1f} | RSI1W={btc['rsi1w']:.1f}", flush=True)
        print(f"   4h: {ctx_btc['dir_4h'].upper()} {ctx_btc['modo_4h'].upper()} ({ctx_btc['razon_4h']})", flush=True)
        print(f"   1D: {ctx_btc['dir_1d'].upper()} {ctx_btc['modo_1d'].upper()} ({ctx_btc['razon_1d']})", flush=True)
    else:
        print("   ⚠️ Sin datos de BTC", flush=True)

    ahora_lima = hora_lima().strftime("%Y-%m-%d %H:%M")
    enviadas = []

    for symbol in SYMBOLS:
        print(f"\n🔍 {symbol}", flush=True)
        data = leer_cache_remoto(symbol)
        if not data or not data.get("pulso"):
            print(f"   ⚠️ sin datos", flush=True)
            continue

        pulso = data["pulso"]
        u = pulso[-1]
        rsi4h = u.get("rsi4h")
        rsi1d = u.get("rsi1d")
        rsi1w = u.get("rsi1w")
        precio = u.get("price")

        if rsi4h is None or precio is None:
            print(f"   ⚠️ sin RSI4h o precio", flush=True)
            continue

        r4 = f"{rsi4h:.1f}"
        r1d = f"{rsi1d:.1f}" if rsi1d is not None else "—"
        r1w = f"{rsi1w:.1f}" if rsi1w is not None else "—"
        print(f"   ${precio:.6f} | RSI4h={r4} | RSI1D={r1d} | RSI1W={r1w}", flush=True)

        prev = estado.get(symbol, {})
        btc_txt = btc_resumen(btc, ctx_btc) if ctx_btc else "🌐 BTC: sin datos"

        # ═══════════════════════════════════════════════
        # 4H OB — progresión 0 → 1 → 2 círculos
        # ═══════════════════════════════════════════════
        lado_4h_ob = prev.get("4h_ob_lado", "fuera")
        nivel_ob = prev.get("4h_ob_nivel", 0)  # 0, 1, 2

        # Reset cuando sale de OB
        if rsi4h < RSI_OB_4H - 2:  # margen para evitar ruido
            if lado_4h_ob != "fuera":
                print(f"   🔄 4h OB reset (salió de zona)", flush=True)
            lado_4h_ob = "fuera"
            nivel_ob = 0

        # Nivel 1: moneda entra en OB (sin círculo)
        if rsi4h >= RSI_OB_4H and nivel_ob == 0:
            enviar_telegram(
                f"{symbol} — 4h ENTRÓ EN SOBRECOMPRA\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 ${precio:.6f}\n"
                f"📊 RSI4h={r4} | RSI1D={r1d} | RSI1W={r1w}\n"
                f"📌 Aviso: vigilar confirmación de BTC\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{btc_txt}\n"
                f"🕐 {ahora_lima}"
            )
            print(f"   ⚪ 4h OB nivel 1 (aviso)", flush=True)
            lado_4h_ob = "dentro"
            nivel_ob = 1
            enviadas.append(("4h-OB-aviso", symbol))
            log_senal(symbol, "4H_OB_N1", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

        # Nivel 2: BTC gira bajista (4h) → 1 círculo
        if nivel_ob == 1 and ctx_btc.get("dir_4h") == "down":
            enviar_telegram(
                f"🔴 {symbol} — 4h SOBRECOMPRA CONFIRMADA\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 ${precio:.6f}\n"
                f"📊 RSI4h={r4} | RSI1D={r1d} | RSI1W={r1w}\n"
                f"✅ BTC 4h {ctx_btc['modo_4h']} ({ctx_btc['razon_4h']})\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{btc_txt}\n"
                f"🕐 {ahora_lima}"
            )
            print(f"   🔴 4h OB nivel 2 (BTC 4h gira)", flush=True)
            nivel_ob = 2
            enviadas.append(("4h-OB-confirmado", symbol))
            log_senal(symbol, "4H_OB_N2", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

        # Nivel 3: BTC gira bajista (1D) → 2 círculos
        if nivel_ob == 2 and ctx_btc.get("dir_1d") == "down":
            enviar_telegram(
                f"🔴🔴 {symbol} — 4h SOBRECOMPRA CONFIRMADA FUERTE\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 ${precio:.6f}\n"
                f"📊 RSI4h={r4} | RSI1D={r1d} | RSI1W={r1w}\n"
                f"✅ BTC 4h {ctx_btc['modo_4h']} + BTC 1D {ctx_btc['modo_1d']}\n"
                f"🔥 Confirmación mayor: BTC girando en diario\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{btc_txt}\n"
                f"🕐 {ahora_lima}"
            )
            print(f"   🔴🔴 4h OB nivel 3 (BTC 1D gira)", flush=True)
            nivel_ob = 3
            enviadas.append(("4h-OB-fuerte", symbol))
            log_senal(symbol, "4H_OB_N3", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

        # ═══════════════════════════════════════════════
        # 4H OS — progresión 0 → 1 → 2 círculos
        # ═══════════════════════════════════════════════
        lado_4h_os = prev.get("4h_os_lado", "fuera")
        nivel_os = prev.get("4h_os_nivel", 0)

        if rsi4h > RSI_OS_4H + 2:
            if lado_4h_os != "fuera":
                print(f"   🔄 4h OS reset", flush=True)
            lado_4h_os = "fuera"
            nivel_os = 0

        # Nivel 1
        if rsi4h <= RSI_OS_4H and nivel_os == 0:
            enviar_telegram(
                f"{symbol} — 4h ENTRÓ EN SOBREVENTA\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 ${precio:.6f}\n"
                f"📊 RSI4h={r4} | RSI1D={r1d} | RSI1W={r1w}\n"
                f"📌 Aviso: vigilar confirmación de BTC\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{btc_txt}\n"
                f"🕐 {ahora_lima}"
            )
            print(f"   ⚪ 4h OS nivel 1 (aviso)", flush=True)
            lado_4h_os = "dentro"
            nivel_os = 1
            enviadas.append(("4h-OS-aviso", symbol))
            log_senal(symbol, "4H_OS_N1", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

        # Nivel 2
        if nivel_os == 1 and ctx_btc.get("dir_4h") == "up":
            enviar_telegram(
                f"🟢 {symbol} — 4h SOBREVENTA CONFIRMADA\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 ${precio:.6f}\n"
                f"📊 RSI4h={r4} | RSI1D={r1d} | RSI1W={r1w}\n"
                f"✅ BTC 4h {ctx_btc['modo_4h']} ({ctx_btc['razon_4h']})\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{btc_txt}\n"
                f"🕐 {ahora_lima}"
            )
            print(f"   🟢 4h OS nivel 2 (BTC 4h gira)", flush=True)
            nivel_os = 2
            enviadas.append(("4h-OS-confirmado", symbol))
            log_senal(symbol, "4H_OS_N2", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

        # Nivel 3
        if nivel_os == 2 and ctx_btc.get("dir_1d") == "up":
            enviar_telegram(
                f"🟢🟢 {symbol} — 4h SOBREVENTA CONFIRMADA FUERTE\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 ${precio:.6f}\n"
                f"📊 RSI4h={r4} | RSI1D={r1d} | RSI1W={r1w}\n"
                f"✅ BTC 4h {ctx_btc['modo_4h']} + BTC 1D {ctx_btc['modo_1d']}\n"
                f"🔥 Confirmación mayor: BTC girando en diario\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{btc_txt}\n"
                f"🕐 {ahora_lima}"
            )
            print(f"   🟢🟢 4h OS nivel 3 (BTC 1D gira)", flush=True)
            nivel_os = 3
            enviadas.append(("4h-OS-fuerte", symbol))
            log_senal(symbol, "4H_OS_N3", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

        # ═══════════════════════════════════════════════
        # 1D — directo con 1 círculo
        # ═══════════════════════════════════════════════
        lado_1d = prev.get("1d_lado")
        if lado_1d is None:
            lado_1d = "esperando_OB" if (rsi1d is None or rsi1d < RSI_OB_1D) else "esperando_OS"

        if rsi1d is not None:
            if lado_1d == "esperando_OB" and rsi1d >= RSI_OB_1D:
                enviar_telegram(
                    f"🔴 {symbol} — 1D ENTRÓ EN SOBRECOMPRA → VENTA\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 ${precio:.6f}\n"
                    f"📊 RSI1D={r1d} (cruzó ↑ {RSI_OB_1D})\n"
                    f"📊 RSI4h={r4} | RSI1W={r1w}\n"
                    f"🎯 Señal de VENTA\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"{btc_txt}\n"
                    f"🕐 {ahora_lima}"
                )
                print(f"   🔴 1D OB → VENTA", flush=True)
                lado_1d = "esperando_OS"
                enviadas.append(("1D-OB", symbol))
                log_senal(symbol, "1D_OB", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

            elif lado_1d == "esperando_OS" and rsi1d <= RSI_OS_1D:
                enviar_telegram(
                    f"🟢 {symbol} — 1D ENTRÓ EN SOBREVENTA → COMPRA\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 ${precio:.6f}\n"
                    f"📊 RSI1D={r1d} (cruzó ↓ {RSI_OS_1D})\n"
                    f"📊 RSI4h={r4} | RSI1W={r1w}\n"
                    f"🎯 Señal de COMPRA\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"{btc_txt}\n"
                    f"🕐 {ahora_lima}"
                )
                print(f"   🟢 1D OS → COMPRA", flush=True)
                lado_1d = "esperando_OB"
                enviadas.append(("1D-OS", symbol))
                log_senal(symbol, "1D_OS", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

        # ═══════════════════════════════════════════════
        # 1W — directo con 2 círculos
        # ═══════════════════════════════════════════════
        lado_1w = prev.get("1w_lado")
        if lado_1w is None:
            lado_1w = "esperando_OB" if (rsi1w is None or rsi1w < RSI_OB_1W) else "esperando_OS"

        if rsi1w is not None:
            if lado_1w == "esperando_OB" and rsi1w >= RSI_OB_1W:
                enviar_telegram(
                    f"🔴🔴 {symbol} — 1W ENTRÓ EN SOBRECOMPRA → VENTA FUERTE\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 ${precio:.6f}\n"
                    f"📊 RSI1W={r1w} (cruzó ↑ {RSI_OB_1W})\n"
                    f"📊 RSI1D={r1d} | RSI4h={r4}\n"
                    f"🔥 Techo semanal\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"{btc_txt}\n"
                    f"🕐 {ahora_lima}"
                )
                print(f"   🔴🔴 1W OB → VENTA FUERTE", flush=True)
                lado_1w = "esperando_OS"
                enviadas.append(("1W-OB", symbol))
                log_senal(symbol, "1W_OB", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

            elif lado_1w == "esperando_OS" and rsi1w <= RSI_OS_1W:
                enviar_telegram(
                    f"🟢🟢 {symbol} — 1W ENTRÓ EN SOBREVENTA → COMPRA FUERTE\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 ${precio:.6f}\n"
                    f"📊 RSI1W={r1w} (cruzó ↓ {RSI_OS_1W})\n"
                    f"📊 RSI1D={r1d} | RSI4h={r4}\n"
                    f"🔥 Suelo semanal\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"{btc_txt}\n"
                    f"🕐 {ahora_lima}"
                )
                print(f"   🟢🟢 1W OS → COMPRA FUERTE", flush=True)
                lado_1w = "esperando_OB"
                enviadas.append(("1W-OS", symbol))
                log_senal(symbol, "1W_OS", {"rsi4h": rsi4h, "rsi1d": rsi1d, "rsi1w": rsi1w, "precio": precio})

        estado[symbol] = {
            **prev,
            "4h_ob_lado": lado_4h_ob,
            "4h_ob_nivel": nivel_ob,
            "4h_os_lado": lado_4h_os,
            "4h_os_nivel": nivel_os,
            "1d_lado": lado_1d,
            "1w_lado": lado_1w,
            "rsi4h": rsi4h,
            "rsi1d": rsi1d,
            "rsi1w": rsi1w,
        }

    estado["_BTC"] = {
        "rsi4h": btc.get("rsi4h"),
        "rsi1d": btc.get("rsi1d"),
        "rsi15": btc.get("rsi15"),
        "rsi1h": btc.get("rsi1h"),
    }

    guardar_estado(estado)

    print("\n" + "=" * 70, flush=True)
    if enviadas:
        print(f"🎯 {len(enviadas)} alerta(s):", flush=True)
        for tipo, sym in enviadas:
            print(f"   {tipo} {sym}", flush=True)
    else:
        print("Sin alertas nuevas.", flush=True)
    print("=" * 70, flush=True)
    print("🏁 TERMINADO", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
