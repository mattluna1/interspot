#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ALERTA ARRANQUE — VERSIÓN MEJORADA
- Filtros de frescura (4h + 24h)
- Doble señal: FUERTE (pump real) / DÉBIL (movimiento sin volumen)
- Calibrado a las 11 monedas que explotaron
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
    "PAXG", "XAUT",
    "WBTC", "WETH", "STETH", "WSTETH", "RETH", "CBETH", "WBETH",
    "WBNB", "WMATIC", "WAVAX", "WSOL",
    "HTX",
}


# ============================================================
# UMBRALES — DOS NIVELES DE SEÑAL
# ============================================================

# Señal FUERTE (pump real con volumen)
FUERTE_CAMBIO_1H    = 1.5      # +1.5% en 1h
FUERTE_VOL_RATIO    = 1.4      # 1.4x volumen promedio
FUERTE_VOL_ABS      = 500_000

# Señal DÉBIL (movimiento sin volumen anómalo)
DEBIL_CAMBIO_1H     = 1.5      # +1.5% en 1h
DEBIL_VOL_RATIO     = 1.0      # 1.0x = volumen normal
DEBIL_VOL_ABS       = 500_000

# Filtros de frescura (aplican a ambas señales)
MAX_CAMBIO_4H       = 5.0      # descarta si ya subió +5% en 4h
MAX_CAMBIO_24H      = 15.0     # descarta si ya subió +15% en 24h

# Filtros de calidad (perfil SAND/FUN)
MC_MIN              = 50_000_000       # $50M mínimo
MC_MAX              = 5_000_000_000    # $5B máximo
RANK_MAX            = 280              # top 280

MAX_ALERTAS_POR_RUN = 15


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
# DETECCIÓN — DOBLE SEÑAL
# ============================================================

def detectar_arranque(df, symbol):
    g = df[df["symbol"] == symbol].sort_values("timestamp")
    if len(g) < 40:
        return None

    ts_max = g["timestamp"].max()

    # Filtro de frescura del dato
    ahora = datetime.now(timezone.utc)
    if ts_max.tzinfo is None:
        ts_max = ts_max.replace(tzinfo=timezone.utc)
    if (ahora - ts_max).total_seconds() / 60 > 60:
        return None

    # Filtro de calidad (MC y rank)
    mc = g.iloc[-1].get("market_cap", 0) or 0
    rank = g.iloc[-1].get("cmc_rank", 9999)
    if mc < MC_MIN or mc > MC_MAX:
        return None
    if rank > RANK_MAX:
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

    # Filtros de frescura
    if cambio_4h > MAX_CAMBIO_4H:
        return None
    if cambio_24h > MAX_CAMBIO_24H:
        return None

    # Volumen
    v_base = g["volume_24h"].tail(20).mean()
    v_ult = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0
    if v_ult < FUERTE_VOL_ABS:
        return None

    # === CLASIFICACIÓN DE SEÑAL ===
    señal = None

    # FUERTE: cambio alto + volumen alto
    if (cambio_1h >= FUERTE_CAMBIO_1H and
        vol_ratio >= FUERTE_VOL_RATIO):
        señal = "FUERTE"

    # DÉBIL: cambio alto + volumen normal/bajo
    elif (cambio_1h >= DEBIL_CAMBIO_1H and
          vol_ratio >= DEBIL_VOL_RATIO):
        señal = "DÉBIL"

    if señal is None:
        return None

    return {
        "symbol": symbol,
        "señal": señal,
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
# MAIN
# ============================================================

def main():
    ahora = datetime.now(timezone.utc)
    ahora_lima = ahora - timedelta(hours=5)

    print(f"\n{'='*70}")
    print(f"⚡ ARRANQUE — DOBLE SEÑAL (FUERTE + DÉBIL)")
    print(f"   UTC:  {ahora.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Lima: {ahora_lima.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    df = cargar_datos()
    print(f"📊 Total: {len(df):,} filas | {df['symbol'].nunique()} monedas")

    todas = df["symbol"].unique()
    print(f"🔍 Escaneando {len(todas)} monedas...")

    alertas = []
    for sym in todas:
        if sym.upper() in EXCLUIR:
            continue
        r = detectar_arranque(df, sym)
        if r:
            alertas.append(r)

    fuertes = [a for a in alertas if a["señal"] == "FUERTE"]
    debiles = [a for a in alertas if a["señal"] == "DÉBIL"]

    print(f"   🚨 FUERTES: {len(fuertes)}")
    print(f"   🟡 DÉBILES: {len(debiles)}")

    if not alertas:
        print("✅ Ninguna alerta")
        return

    # Ordenar: fuertes primero, luego débiles
    fuertes.sort(key=lambda x: -x["cambio_1h"])
    debiles.sort(key=lambda x: -x["cambio_1h"])

    # Enviar FUERTES
    for a in fuertes[:MAX_ALERTAS_POR_RUN]:
        hora_lima = (a["ts"] - timedelta(hours=5)).strftime("%H:%M")
        msg = (
            f"🚨 ARRANQUE FUERTE\n"
            f"🪙 {a['symbol']} (rank {a['cmc_rank']})\n"
            f"📈 +{a['cambio_1h']:.2f}% en 1h\n"
            f"📊 4h: {a['cambio_4h']:+.2f}% | 24h: {a['cambio_24h']:+.2f}%\n"
            f"💰 ${a['precio']:.6f}\n"
            f"📊 Vol: {a['vol_ratio']:.2f}x (${a['vol_m']:.1f}M)\n"
            f"🏦 MC: ${a['market_cap_m']:.1f}M\n"
            f"🕐 {hora_lima} Lima\n"
            f"✅ Volumen confirma"
        )
        if enviar_telegram(msg):
            print(f"   🚨 FUERTE: {a['symbol']} (+{a['cambio_1h']:.2f}% | vol {a['vol_ratio']:.2f}x)")

    # Enviar DÉBILES
    for a in debiles[:MAX_ALERTAS_POR_RUN]:
        hora_lima = (a["ts"] - timedelta(hours=5)).strftime("%H:%M")
        msg = (
            f"🟡 ARRANQUE DÉBIL\n"
            f"🪙 {a['symbol']} (rank {a['cmc_rank']})\n"
            f"📈 +{a['cambio_1h']:.2f}% en 1h\n"
            f"📊 4h: {a['cambio_4h']:+.2f}% | 24h: {a['cambio_24h']:+.2f}%\n"
            f"💰 ${a['precio']:.6f}\n"
            f"📊 Vol: {a['vol_ratio']:.2f}x (${a['vol_m']:.1f}M)\n"
            f"🏦 MC: ${a['market_cap_m']:.1f}M\n"
            f"🕐 {hora_lima} Lima\n"
            f"⚠️ Sin volumen anómalo — verificar gráfico"
        )
        if enviar_telegram(msg):
            print(f"   🟡 DÉBIL: {a['symbol']} (+{a['cambio_1h']:.2f}% | vol {a['vol_ratio']:.2f}x)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
