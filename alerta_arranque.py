#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ALERTA ARRANQUE + REENTRADA — 3 SEÑALES
  1. 🟡 TEMPRANA  → arranque con volumen empezando a entrar (1.2x+)
  2. 🚨 FUERTE    → arranque confirmado con volumen (1.4x+)
  3. 📐 REENTRADA → rebote en Fibo 61.8% tras pullback
"""

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

# 🟡 TEMPRANA: volumen empezando a entrar
TEMPRANA_CAMBIO_1H  = 1.5       # +1.5% en 1h
TEMPRANA_VOL_MIN    = 1.2       # volumen 1.2x (empezando)
TEMPRANA_VOL_MAX    = 1.4       # hasta 1.4x (arriba ya es FUERTE)

# 🚨 FUERTE: volumen confirmado
FUERTE_CAMBIO_1H    = 1.5       # +1.5% en 1h
FUERTE_VOL_MIN      = 1.4       # volumen 1.4x+

# Comunes
VOL_ABS_MIN         = 500_000   # $500K mínimo

# Filtros de frescura (aplican a TEMPRANA y FUERTE)
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
# DETECCIÓN — ARRANQUE (TEMPRANA + FUERTE)
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

    # Ventana 1h
    corte_1h = ts_max - timedelta(hours=1)
    rec_1h = g[g["timestamp"] >= corte_1h]
    if len(rec_1h) < 3:
        return None
    p_ini_1h = rec_1h.iloc[0]["price"]
    p_fin = rec_1h.iloc[-1]["price"]
    if p_ini_1h <= 0:
        return None
    cambio_1h = ((p_fin - p_ini_1h) / p_ini_1h) * 100

    # Ventana 4h
    corte_4h = ts_max - timedelta(hours=4)
    rec_4h = g[g["timestamp"] >= corte_4h]
    if len(rec_4h) < 6:
        return None
    p_ini_4h = rec_4h.iloc[0]["price"]
    cambio_4h = ((p_fin - p_ini_4h) / p_ini_4h) * 100 if p_ini_4h > 0 else 0

    # Ventana 24h
    corte_24h = ts_max - timedelta(hours=24)
    rec_24h = g[g["timestamp"] >= corte_24h]
    p_ini_24h = rec_24h.iloc[0]["price"] if len(rec_24h) > 0 else p_ini_4h
    cambio_24h = ((p_fin - p_ini_24h) / p_ini_24h) * 100 if p_ini_24h > 0 else 0

    # Frescura
    if cambio_4h > MAX_CAMBIO_4H:
        return None
    if cambio_24h > MAX_CAMBIO_24H:
        return None

    # Volumen
    v_base = g["volume_24h"].tail(20).mean()
    v_ult = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0
    if v_ult < VOL_ABS_MIN:
        return None

    # === CLASIFICACIÓN ===
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

    # Pump previo 48h
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

    # Confirmación de rebote
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

def enviar_alerta_arranque(a):
    hora_lima = (a["ts"] - timedelta(hours=5)).strftime("%H:%M")

    if a["señal"] == "FUERTE":
        emoji = "🚨"
        titulo = "ARRANQUE FUERTE"
        nota = "✅ Volumen CONFIRMA — entrada con confianza"
    else:  # TEMPRANA
        emoji = "🟡"
        titulo = "ARRANQUE TEMPRANO"
        nota = "⚠️ Volumen empezando (1.2-1.4x) — verificar gráfico"

    msg = (
        f"{emoji} {titulo}\n"
        f"🪙 {a['symbol']} (rank {a['cmc_rank']})\n"
        f"📈 +{a['cambio_1h']:.2f}% en 1h\n"
        f"📊 4h: {a['cambio_4h']:+.2f}% | 24h: {a['cambio_24h']:+.2f}%\n"
        f"💰 ${a['precio']:.6f}\n"
        f"📊 Vol: {a['vol_ratio']:.2f}x (${a['vol_m']:.1f}M)\n"
        f"🏦 MC: ${a['market_cap_m']:.1f}M\n"
        f"🕐 {hora_lima} Lima\n"
        f"{nota}"
    )
    return enviar_telegram(msg)


def enviar_alerta_reentrada(a):
    hora_lima = (a["ts"] - timedelta(hours=5)).strftime("%H:%M")
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
        f"🕐 {hora_lima} Lima\n"
        f"💡 Segunda entrada tras pullback"
    )
    return enviar_telegram(msg)


# ============================================================
# MAIN
# ============================================================

def main():
    ahora = datetime.now(timezone.utc)
    ahora_lima = ahora - timedelta(hours=5)

    print(f"\n{'='*70}")
    print(f"⚡ ARRANQUE (TEMPRANA + FUERTE) + 📐 REENTRADA FIBO")
    print(f"   UTC:  {ahora.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Lima: {ahora_lima.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    df = cargar_datos()
    print(f"📊 Total: {len(df):,} filas | {df['symbol'].nunique()} monedas")

    todas = df["symbol"].unique()
    print(f"🔍 Escaneando {len(todas)} monedas...")

    fuertes = []
    tempranas = []
    reentradas = []

    for sym in todas:
        if sym.upper() in EXCLUIR:
            continue

        # Arranque (FUERTE o TEMPRANA)
        r1 = detectar_arranque(df, sym)
        if r1:
            if r1["señal"] == "FUERTE":
                fuertes.append(r1)
            else:
                tempranas.append(r1)

        # Reentrada Fibo
        r2 = detectar_reentrada_fibo(df, sym)
        if r2:
            reentradas.append(r2)

    print(f"\n   🚨 FUERTES:    {len(fuertes)}")
    print(f"   🟡 TEMPRANAS:  {len(tempranas)}")
    print(f"   📐 REENTRADAS: {len(reentradas)}")

    # 1) FUERTES primero (prioridad máxima)
    fuertes.sort(key=lambda x: -x["cambio_1h"])
    for a in fuertes[:MAX_POR_TIPO]:
        if enviar_alerta_arranque(a):
            print(f"   🚨 FUERTE: {a['symbol']} (+{a['cambio_1h']:.2f}% | "
                  f"vol {a['vol_ratio']:.2f}x)")

    # 2) TEMPRANAS
    tempranas.sort(key=lambda x: -x["cambio_1h"])
    for a in tempranas[:MAX_POR_TIPO]:
        if enviar_alerta_arranque(a):
            print(f"   🟡 TEMPRANA: {a['symbol']} (+{a['cambio_1h']:.2f}% | "
                  f"vol {a['vol_ratio']:.2f}x)")

    # 3) REENTRADAS FIBO
    reentradas.sort(key=lambda x: (x["distancia_fibo"], -x["vol_ratio"]))
    for a in reentradas[:MAX_POR_TIPO]:
        if enviar_alerta_reentrada(a):
            print(f"   📐 REENTRADA: {a['symbol']} (fibo {a['pos_actual']:.1f}% | "
                  f"rebote +{a['rebote_pct']:.2f}% | vol {a['vol_ratio']:.2f}x)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
