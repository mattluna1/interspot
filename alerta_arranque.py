#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ALERTA ARRANQUE + REENTRADA — 3 SEÑALES + BTC 6 ESTADOS + DEDUPLICACIÓN

  1. 🟡 TEMPRANA  → arranque con volumen empezando (1.2x+)
  2. 🚨 FUERTE    → arranque confirmado (1.4x+)
  3. 📐 REENTRADA → rebote en Fibo 61.8%

NUEVO: Deduplicación por moneda
  - Solo UNA alerta por moneda cada COOLDOWN_HORAS
  - Prioriza FUERTE > TEMPRANA > REENTRADA
  - Persiste en data/alertas_recientes.json
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from io import StringIO

import pandas as pd
import requests


# ============================================================
# URLs
# ============================================================

CSV_URLS = {
    "top100": (
        "https://raw.githubusercontent.com/mattluna3/inspector/"
        "main/data/cmc/market_history_top100.csv"
    ),
    "emerging": (
        "https://raw.githubusercontent.com/mattluna3/inspector/"
        "main/data/cmc/market_history_emerging.csv"
    ),
    "201_300": (
        "https://raw.githubusercontent.com/emerging2/inspector2/"
        "main/data/cmc/market_history_201_300.csv"
    ),
}

EXCLUIR = {
    "USDT", "USDC", "DAI", "TUSD", "FDUSD", "BUSD", "USDD", "USDE",
    "PYUSD", "USDS", "USD1", "RLUSD", "USD0", "USDSUI", "USDON",
    "USDAI", "AUSD", "USDG", "USDGO", "USX", "EURC", "USDF", "GHO",
    "FRAX", "LUSD", "SUSD", "USDR", "USDY", "USTC", "MIM", "CRVUSD",
    "PAXG", "XAUT", "WBTC", "WETH", "STETH", "WSTETH", "HTX",
}


# ============================================================
# UMBRALES — 3 SEÑALES
# ============================================================

TEMPRANA_CAMBIO_1H  = 1.5
TEMPRANA_VOL_MIN    = 1.2
TEMPRANA_VOL_MAX    = 1.4

FUERTE_CAMBIO_1H    = 1.5
FUERTE_VOL_MIN      = 1.4

VOL_ABS_MIN         = 500_000

MAX_CAMBIO_4H       = 5.0
MAX_CAMBIO_24H      = 15.0


# ============================================================
# UMBRALES — REENTRADA FIBO
# ============================================================

PUMP_MIN_PCT        = 15.0
PUMP_VENTANA_H      = 48
FIBO_MIN            = 55.0
FIBO_MAX            = 68.0
FIBO_IDEAL          = 61.8
REBOTE_MIN_PCT      = 2.0
REENTRADA_VOL_MIN   = 1.2


# ============================================================
# FILTROS COMUNES
# ============================================================

MC_MIN              = 50_000_000
MC_MAX              = 5_000_000_000
RANK_MAX            = 280

MAX_POR_TIPO        = 10

# Archivos de estado persistente
BTC_STATE_FILE      = "btc_state.json"
ALERTAS_FILE        = "data/alertas_recientes.json"
COOLDOWN_HORAS      = 4


# ============================================================
# TELEGRAM
# ============================================================

def enviar_telegram(msg):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("⚠️ Telegram no configurado")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": msg}, timeout=20)
        return r.status_code == 200 and r.json().get("ok", False)
    except Exception as e:
        print(f"Error Telegram: {e}")
        return False


# ============================================================
# CARGA
# ============================================================

def cargar_datos():
    dfs = []
    for nombre, url in CSV_URLS.items():
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text))
            df["_fuente"] = nombre
            dfs.append(df)
            print(f"✅ {nombre}: {len(df):,} filas")
        except Exception as e:
            print(f"⚠️ {nombre}: {str(e)[:80]}")

    if not dfs:
        sys.exit("❌ No se pudo cargar ningún CSV")

    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")

    for c in ["price", "volume_24h", "percent_change_24h", "cmc_rank", "market_cap"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["timestamp", "price", "symbol"])
    df = df[df["price"] > 0]
    df = df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return df


# ============================================================
# HELPERS
# ============================================================

def es_fresco(ts_max, minutos=60):
    ahora = datetime.now(timezone.utc)
    if ts_max.tzinfo is None:
        ts_max = ts_max.replace(tzinfo=timezone.utc)
    return (ahora - ts_max).total_seconds() / 60 <= minutos


def cumple_calidad(mc, rank):
    if mc < MC_MIN or mc > MC_MAX:
        return False
    if rank > RANK_MAX:
        return False
    return True


# ============================================================
# DEDUPLICACIÓN — ALERTAS RECIENTES
# ============================================================

def cargar_alertas_recientes():
    """Carga el registro de alertas recientes (con limpieza por cooldown)."""
    if not os.path.exists(ALERTAS_FILE):
        return {}
    try:
        with open(ALERTAS_FILE, "r") as f:
            data = json.load(f)
        # Limpiar alertas viejas (>COOLDOWN_HORAS)
        ahora = datetime.now(timezone.utc).timestamp()
        data = {k: v for k, v in data.items()
                if (ahora - v) / 3600 < COOLDOWN_HORAS}
        return data
    except Exception:
        return {}


def guardar_alertas_recientes(alertas):
    """Guarda el registro de alertas recientes."""
    os.makedirs(os.path.dirname(ALERTAS_FILE), exist_ok=True)
    with open(ALERTAS_FILE, "w") as f:
        json.dump(alertas, f, indent=2)


def ya_avisado(symbol, alertas_recientes):
    """Comprueba si ya avisamos de esta moneda recientemente."""
    return symbol.upper() in alertas_recientes


def marcar_avisado(symbol, alertas_recientes):
    """Marca la moneda como avisada."""
    alertas_recientes[symbol.upper()] = datetime.now(timezone.utc).timestamp()


# ============================================================
# ANÁLISIS BTC — 6 ESTADOS
# ============================================================

def analizar_btc(df):
    """
    Analiza BTC y clasifica en 6 estados:
      UP / FLAT / DOWN_SOFT / DOWN / BOTTOM / RECOVERY
    """
    g = df[df["symbol"] == "BTC"].sort_values("timestamp")
    if len(g) < 40:
        return {"estado": "UNKNOWN", "cambio_1h": 0, "cambio_4h": 0,
                "cambio_24h": 0, "vol_ratio": 0, "precio": 0}

    ts_max = g["timestamp"].max()

    # Ventana 1h
    corte_1h = ts_max - timedelta(hours=1)
    rec_1h = g[g["timestamp"] >= corte_1h]
    if len(rec_1h) < 3:
        return {"estado": "UNKNOWN", "cambio_1h": 0, "cambio_4h": 0,
                "cambio_24h": 0, "vol_ratio": 0, "precio": 0}

    p_ini_1h = rec_1h.iloc[0]["price"]
    p_fin = g.iloc[-1]["price"]
    cambio_1h = ((p_fin - p_ini_1h) / p_ini_1h) * 100 if p_ini_1h > 0 else 0

    # Ventana 4h
    corte_4h = ts_max - timedelta(hours=4)
    rec_4h = g[g["timestamp"] >= corte_4h]
    p_ini_4h = rec_4h.iloc[0]["price"] if len(rec_4h) > 0 else p_ini_1h
    cambio_4h = ((p_fin - p_ini_4h) / p_ini_4h) * 100 if p_ini_4h > 0 else 0

    # Ventana 24h
    corte_24h = ts_max - timedelta(hours=24)
    rec_24h = g[g["timestamp"] >= corte_24h]
    p_ini_24h = rec_24h.iloc[0]["price"] if len(rec_24h) > 0 else p_ini_4h
    cambio_24h = ((p_fin - p_ini_24h) / p_ini_24h) * 100 if p_ini_24h > 0 else 0

    # Volumen
    v_base = g["volume_24h"].tail(20).mean()
    v_ult = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0

    # Mínimo de las últimas 4h
    p_min_4h = rec_4h["price"].min() if len(rec_4h) > 0 else p_fin
    pos_respecto_min = ((p_fin - p_min_4h) / p_min_4h) * 100 if p_min_4h > 0 else 0

    # === CLASIFICACIÓN DE 6 ESTADOS ===

    # RECOVERY
    if (cambio_1h > 0.5 and
        cambio_4h < -1.0 and
        pos_respecto_min > 1.5):
        estado = "RECOVERY"

    # BOTTOM
    elif (cambio_1h > 0.2 and
          cambio_4h < -1.0 and
          pos_respecto_min > 0.8):
        estado = "BOTTOM"

    # DOWN
    elif cambio_1h < -1.5 or cambio_4h < -3.0:
        estado = "DOWN"

    # DOWN_SOFT
    elif cambio_1h < -0.2:
        estado = "DOWN_SOFT"

    # UP
    elif cambio_1h > 0.5 and cambio_4h > 0.5:
        estado = "UP"

    # FLAT
    else:
        estado = "FLAT"

    return {
        "estado": estado,
        "cambio_1h": cambio_1h,
        "cambio_4h": cambio_4h,
        "cambio_24h": cambio_24h,
        "vol_ratio": vol_ratio,
        "precio": p_fin,
        "p_min_4h": p_min_4h,
        "pos_respecto_min": pos_respecto_min,
    }


def _contexto_btc(btc):
    """Retorna (linea, aviso) con los 6 estados."""
    mapa = {
        "UP":        ("🟢 BTC: subiendo", ""),
        "FLAT":      ("⚪ BTC: neutral", ""),
        "DOWN_SOFT": ("🟡 BTC: cayendo lento",
                      "⏸️ BTC bajando — esperar suelo"),
        "DOWN":      ("🔴 BTC: cayendo fuerte",
                      "🚨 BTC en caída — no entrar a alts"),
        "BOTTOM":    ("🟣 BTC: fondo — rebotando",
                      "🎯 BTC muestra rebote — preparar entradas"),
        "RECOVERY":  ("🟢 BTC: recovery — rebote confirmado",
                      "🔥 Momento óptimo para entrar a alts"),
        "UNKNOWN":   ("❓ BTC: sin datos", ""),
    }
    return mapa.get(btc["estado"], ("⚪ BTC: neutral", ""))


# ============================================================
# CAMBIO DE ESTADO BTC
# ============================================================

def cargar_btc_estado_previo():
    if not os.path.exists(BTC_STATE_FILE):
        return None
    try:
        with open(BTC_STATE_FILE, "r") as f:
            data = json.load(f)
        return data.get("estado")
    except Exception:
        return None


def guardar_btc_estado(estado):
    try:
        with open(BTC_STATE_FILE, "w") as f:
            json.dump({
                "estado": estado,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, f)
    except Exception:
        pass


def enviar_alerta_cambio_btc(estado_previo, btc):
    if estado_previo == btc["estado"]:
        return False

    transiciones = {
        ("UP", "FLAT"):          ("🟡", "BTC PERDIENDO FUERZA",
                                  "Toma ganancias, mercado girando"),
        ("UP", "DOWN_SOFT"):     ("🟡", "BTC GIRANDO A LA BAJA",
                                  "Cuidado con alts"),
        ("UP", "DOWN"):          ("🔴", "BTC GIRÓ A LA BAJA",
                                  "🚨 NO ENTRAR A ALTS"),
        ("FLAT", "DOWN_SOFT"):   ("🟡", "BTC EMPIEZA A CAER",
                                  "Cuidado con alts"),
        ("FLAT", "DOWN"):        ("🔴", "BTC CAYENDO FUERTE",
                                  "🚨 NO ENTRAR A ALTS"),
        ("DOWN_SOFT", "DOWN"):   ("🔴", "BTC CAYENDO FUERTE",
                                  "🚨 NO ENTRAR A ALTS"),
        ("DOWN", "BOTTOM"):      ("🟣", "BTC TOCA FONDO",
                                  "⏳ Preparar entradas en alts"),
        ("DOWN_SOFT", "BOTTOM"): ("🟣", "BTC TOCA FONDO",
                                  "⏳ Preparar entradas en alts"),
        ("DOWN", "RECOVERY"):    ("🟢", "BTC REBOTA",
                                  "🔥 MOMENTO ÓPTIMO — entradas en alts"),
        ("BOTTOM", "RECOVERY"):  ("🟢", "BTC REBOTA",
                                  "🔥 MOMENTO ÓPTIMO — entradas en alts"),
        ("BOTTOM", "UP"):        ("🟢", "BTC CONFIRMA SUBIDA",
                                  "🟢 Buen momento para alts"),
        ("RECOVERY", "UP"):      ("🟢", "BTC CONFIRMA SUBIDA",
                                  "🟢 Buen momento para alts"),
        ("RECOVERY", "DOWN"):    ("🔴", "BTC RECAÓ",
                                  "🚨 Rebote fallido — no entrar"),
        ("BOTTOM", "DOWN_SOFT"): ("🟡", "BTC FALSO FONDO",
                                  "⚠️ Rebote fallido — volvió a caer"),
    }

    clave = (estado_previo, btc["estado"])
    if clave not in transiciones:
        return False

    emoji, titulo, accion = transiciones[clave]
    hora_lima = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%H:%M")

    msg = (
        f"{emoji} {titulo}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 BTC: ${btc['precio']:,.2f}\n"
        f"📊 1h: {btc['cambio_1h']:+.2f}% | 4h: {btc['cambio_4h']:+.2f}%\n"
        f"📊 24h: {btc['cambio_24h']:+.2f}% | Vol: {btc['vol_ratio']:.2f}x\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{accion}\n"
        f"🕐 {hora_lima} Lima"
    )

    return enviar_telegram(msg)


# ============================================================
# DETECCIÓN — ARRANQUE
# ============================================================

def detectar_arranque(df, symbol):
    g = df[df["symbol"] == symbol].sort_values("timestamp")
    if len(g) < 40:
        return None

    ts_max = g["timestamp"].max()
    if not es_fresco(ts_max):
        return None

    mc = g.iloc[-1].get("market_cap", 0) or 0
    rank = g.iloc[-1].get("cmc_rank", 9999)
    if not cumple_calidad(mc, rank):
        return None

    corte_1h = ts_max - timedelta(hours=1)
    rec_1h = g[g["timestamp"] >= corte_1h]
    if len(rec_1h) < 3:
        return None
    p_ini_1h = rec_1h.iloc[0]["price"]
    p_fin = rec_1h.iloc[-1]["price"]
    if p_ini_1h <= 0:
        return None
    cambio_1h = ((p_fin - p_ini_1h) / p_ini_1h) * 100

    corte_4h = ts_max - timedelta(hours=4)
    rec_4h = g[g["timestamp"] >= corte_4h]
    if len(rec_4h) < 6:
        return None
    p_ini_4h = rec_4h.iloc[0]["price"]
    cambio_4h = ((p_fin - p_ini_4h) / p_ini_4h) * 100 if p_ini_4h > 0 else 0

    corte_24h = ts_max - timedelta(hours=24)
    rec_24h = g[g["timestamp"] >= corte_24h]
    p_ini_24h = rec_24h.iloc[0]["price"] if len(rec_24h) > 0 else p_ini_4h
    cambio_24h = ((p_fin - p_ini_24h) / p_ini_24h) * 100 if p_ini_24h > 0 else 0

    if cambio_4h > MAX_CAMBIO_4H:
        return None
    if cambio_24h > MAX_CAMBIO_24H:
        return None

    v_base = g["volume_24h"].tail(20).mean()
    v_ult = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0
    if v_ult < VOL_ABS_MIN:
        return None

    señal = None
    if cambio_1h >= FUERTE_CAMBIO_1H and vol_ratio >= FUERTE_VOL_MIN:
        señal = "FUERTE"
    elif (cambio_1h >= TEMPRANA_CAMBIO_1H and
          TEMPRANA_VOL_MIN <= vol_ratio < TEMPRANA_VOL_MAX):
        señal = "TEMPRANA"

    if señal is None:
        return None

    return {
        "tipo": "ARRANQUE",
        "señal": señal,
        "symbol": symbol,
        "cambio_1h": cambio_1h,
        "cambio_4h": cambio_4h,
        "cambio_24h": cambio_24h,
        "precio": p_fin,
        "vol_ratio": vol_ratio,
        "vol_m": v_ult / 1e6,
        "ts": ts_max,
        "cmc_rank": int(rank),
        "market_cap_m": mc / 1e6,
    }


# ============================================================
# DETECCIÓN — REENTRADA FIBO
# ============================================================

def detectar_reentrada_fibo(df, symbol):
    g = df[df["symbol"] == symbol].sort_values("timestamp")
    if len(g) < 40:
        return None

    ts_max = g["timestamp"].max()
    if not es_fresco(ts_max):
        return None

    mc = g.iloc[-1].get("market_cap", 0) or 0
    rank = g.iloc[-1].get("cmc_rank", 9999)
    if not cumple_calidad(mc, rank):
        return None

    corte_48 = ts_max - timedelta(hours=PUMP_VENTANA_H)
    rec_48 = g[g["timestamp"] >= corte_48]
    if len(rec_48) < 20:
        return None

    p_min_48 = rec_48["price"].min()
    p_max_48 = rec_48["price"].max()
    p_actual = rec_48.iloc[-1]["price"]

    if p_min_48 <= 0 or p_max_48 <= 0:
        return None

    pump_total = ((p_max_48 - p_min_48) / p_min_48) * 100
    if pump_total < PUMP_MIN_PCT:
        return None

    rango = p_max_48 - p_min_48
    if rango <= 0:
        return None

    fibo_382 = p_max_48 - rango * 0.382
    fibo_500 = p_max_48 - rango * 0.500
    fibo_618 = p_max_48 - rango * 0.618

    pos_actual = ((p_max_48 - p_actual) / rango) * 100
    if not (FIBO_MIN <= pos_actual <= FIBO_MAX):
        return None

    corte_12 = ts_max - timedelta(hours=12)
    rec_12 = g[g["timestamp"] >= corte_12]
    if len(rec_12) < 5:
        return None
    p_min_pullback = rec_12["price"].min()
    if p_min_pullback <= 0:
        return None

    rebote_pct = ((p_actual - p_min_pullback) / p_min_pullback) * 100
    if rebote_pct < REBOTE_MIN_PCT:
        return None

    v_base = g["volume_24h"].tail(20).mean()
    v_ult = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0
    if vol_ratio < REENTRADA_VOL_MIN:
        return None

    distancia_fibo = abs(pos_actual - FIBO_IDEAL)

    return {
        "tipo": "REENTRADA",
        "symbol": symbol,
        "pump_total": pump_total,
        "p_max": p_max_48,
        "p_min": p_min_48,
        "p_actual": p_actual,
        "p_min_pullback": p_min_pullback,
        "fibo_382": fibo_382,
        "fibo_500": fibo_500,
        "fibo_618": fibo_618,
        "pos_actual": pos_actual,
        "rebote_pct": rebote_pct,
        "vol_ratio": vol_ratio,
        "vol_m": v_ult / 1e6,
        "distancia_fibo": distancia_fibo,
        "cmc_rank": int(rank),
        "market_cap_m": mc / 1e6,
        "ts": ts_max,
    }


# ============================================================
# ENVÍO DE MENSAJES
# ============================================================

def enviar_alerta_arranque(a, btc):
    hora_lima = (a["ts"] - timedelta(hours=5)).strftime("%H:%M")

    if a["señal"] == "FUERTE":
        emoji = "🚨"
        titulo = "ARRANQUE FUERTE"
        nota = "✅ Volumen CONFIRMA"
    else:
        emoji = "🟡"
        titulo = "ARRANQUE TEMPRANO"
        nota = "⚠️ Verificar gráfico"

    linea_btc, aviso_btc = _contexto_btc(btc)

    msg = (
        f"{emoji} {titulo}\n"
        f"🪙 {a['symbol']} (rank {a['cmc_rank']})\n"
        f"📈 +{a['cambio_1h']:.2f}% en 1h\n"
        f"📊 4h: {a['cambio_4h']:+.2f}% | 24h: {a['cambio_24h']:+.2f}%\n"
        f"💰 ${a['precio']:.6f}\n"
        f"📊 Vol: {a['vol_ratio']:.2f}x (${a['vol_m']:.1f}M)\n"
        f"🏦 MC: ${a['market_cap_m']:.1f}M\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{linea_btc}\n"
        f"🕐 {hora_lima} Lima\n"
        f"{nota}"
    )
    if aviso_btc:
        msg += f"\n{aviso_btc}"

    return enviar_telegram(msg)


def enviar_alerta_reentrada(a, btc):
    hora_lima = (a["ts"] - timedelta(hours=5)).strftime("%H:%M")
    linea_btc, aviso_btc = _contexto_btc(btc)

    msg = (
        f"📐 REENTRADA FIBO\n"
        f"🪙 {a['symbol']} (rank {a['cmc_rank']})\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Pump previo 48h: +{a['pump_total']:.1f}%\n"
        f"   Máx: ${a['p_max']:.6f}\n"
        f"   Mín: ${a['p_min']:.6f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Fibo actual: {a['pos_actual']:.1f}% (objetivo 61.8%)\n"
        f"💰 Precio: ${a['p_actual']:.6f}\n"
        f"📉 Niveles clave:\n"
        f"   • 38.2% → ${a['fibo_382']:.6f}\n"
        f"   • 50.0% → ${a['fibo_500']:.6f}\n"
        f"   • 61.8% → ${a['fibo_618']:.6f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Rebote: +{a['rebote_pct']:.2f}% desde pullback\n"
        f"📊 Vol: {a['vol_ratio']:.2f}x (${a['vol_m']:.1f}M)\n"
        f"🏦 MC: ${a['market_cap_m']:.1f}M\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{linea_btc}\n"
        f"🕐 {hora_lima} Lima"
    )
    if aviso_btc:
        msg += f"\n{aviso_btc}"

    return enviar_telegram(msg)


# ============================================================
# MAIN
# ============================================================

def main():
    ahora = datetime.now(timezone.utc)
    ahora_lima = ahora - timedelta(hours=5)

    print(f"\n{'='*70}")
    print(f"⚡ ARRANQUE + 📐 REENTRADA — BTC 6 ESTADOS + DEDUPLICACIÓN")
    print(f"   UTC:  {ahora.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Lima: {ahora_lima.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    df = cargar_datos()
    print(f"📊 Total: {len(df):,} filas | {df['symbol'].nunique()} monedas")

    # === ANÁLISIS BTC ===
    btc = analizar_btc(df)
    linea_btc, aviso_btc = _contexto_btc(btc)

    print(f"\n🔍 CONTEXTO BTC:")
    print(f"   {linea_btc}")
    if aviso_btc:
        print(f"   {aviso_btc}")

    # === AVISO DE CAMBIO DE ESTADO BTC ===
    estado_previo = cargar_btc_estado_previo()
    if estado_previo and estado_previo != btc["estado"]:
        print(f"\n🔄 Cambio BTC: {estado_previo} → {btc['estado']}")
        if enviar_alerta_cambio_btc(estado_previo, btc):
            print(f"   📱 Alerta de cambio BTC enviada")
    guardar_btc_estado(btc["estado"])

    # === ESCANEO ===
    todas = df["symbol"].unique()
    print(f"\n🔍 Escaneando {len(todas)} monedas...")

    fuertes = []
    tempranas = []
    reentradas = []

    for sym in todas:
        if sym.upper() in EXCLUIR or sym.upper() == "BTC":
            continue

        r1 = detectar_arranque(df, sym)
        if r1:
            if r1["señal"] == "FUERTE":
                fuertes.append(r1)
            else:
                tempranas.append(r1)

        r2 = detectar_reentrada_fibo(df, sym)
        if r2:
            reentradas.append(r2)

    print(f"\n   🚨 FUERTES:    {len(fuertes)}")
    print(f"   🟡 TEMPRANAS:  {len(tempranas)}")
    print(f"   📐 REENTRADAS: {len(reentradas)}")

    # ═══════════════════════════════════════════════════════════
    # BLOQUEO GRADUAL SEGÚN BTC
    # ═══════════════════════════════════════════════════════════
    btc_estado = btc.get("estado", "FLAT")
    bloquear_todas = btc_estado in ("DOWN", "DOWN_SOFT")

    print(f"\n📋 Política BTC {btc_estado}:")
    if bloquear_todas:
        print(f"   🚫 BLOQUEO TOTAL — BTC bajando (alts siguen a BTC)")
    else:
        print(f"   ✅ Alertas permitidas")

    if bloquear_todas:
        print(f"\n{'='*70}")
        print(f"📢 RESUMEN")
        print(f"   {linea_btc}")
        print(f"   Alertas enviadas: 0")
        print(f"{'='*70}")
        return

    # ═══════════════════════════════════════════════════════════
    # DEDUPLICACIÓN POR MONEDA
    # ═══════════════════════════════════════════════════════════
    alertas_recientes = cargar_alertas_recientes()

    # Combinar todas las alertas en una sola lista con prioridad
    todas_alertas = []
    for a in fuertes:
        todas_alertas.append({**a, "señal_tipo": "FUERTE", "prioridad": 1})
    for a in tempranas:
        todas_alertas.append({**a, "señal_tipo": "TEMPRANA", "prioridad": 2})
    for a in reentradas:
        todas_alertas.append({**a, "señal_tipo": "REENTRADA", "prioridad": 3})

    # Agrupar por símbolo (conservar la de mayor prioridad)
    por_simbolo = {}
    for a in todas_alertas:
        sym = a["symbol"]
        if sym not in por_simbolo:
            por_simbolo[sym] = a
        else:
            if a["prioridad"] < por_simbolo[sym]["prioridad"]:
                por_simbolo[sym] = a

    # Filtrar las ya avisadas
    nuevas = []
    for sym, alerta in por_simbolo.items():
        if ya_avisado(sym, alertas_recientes):
            print(f"   ⏭️ {sym} ya avisado en las últimas {COOLDOWN_HORAS}h")
            continue
        nuevas.append(alerta)

    print(f"\n   📊 Alertas únicas: {len(nuevas)} (de {len(todas_alertas)} totales)")

    if not nuevas:
        print(f"\n{'='*70}")
        print(f"📢 RESUMEN")
        print(f"   {linea_btc}")
        print(f"   Alertas enviadas: 0 (todas en cooldown)")
        print(f"{'='*70}")
        return

    # Ordenar por prioridad (FUERTE primero)
    nuevas.sort(key=lambda x: (x["prioridad"], -x.get("cambio_1h", 0)))

    # ═══════════════════════════════════════════════════════════
