#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ALERTA ARRANQUE — AUTO-DISCOVERY 24/7
Detecta CUALQUIER moneda que arranque en cualquier horario.
Consume CSV de los 3 colectores vía raw URLs.
"""

import os
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests


# ============================================================
# URLs DE COLECTORES
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


# ============================================================
# EXCLUSIONES (stablecoins, wrapped, ETFs, gold)
# ============================================================

EXCLUIR = {
    # Stablecoins
    "USDT", "USDC", "DAI", "TUSD", "FDUSD", "BUSD", "USDD", "USDE",
    "PYUSD", "USDS", "USD1", "RLUSD", "USD0", "USDSUI", "USDON",
    "USDAI", "AUSD", "USDG", "USDGO", "USX", "EURC", "USDF", "GHO",
    "FRAX", "LUSD", "SUSD", "USDR", "USDY", "USTC", "MIM", "CRVUSD",
    # Gold / commodities
    "PAXG", "XAUT",
    # Wrapped / staked
    "WBTC", "WETH", "STETH", "WSTETH", "RETH", "CBETH", "WBETH",
    "WBNB", "WMATIC", "WAVAX", "WSOL",
    # Exchange internal
    "HTX", "BUSD",
}


# ============================================================
# UMBRALES DE DETECCIÓN
# ============================================================

UMBRAL_CAMBIO_MIN = 1.5     # % mínimo desde hace 1h
UMBRAL_VOL_RATIO  = 1.4     # Volumen vs promedio 20 snapshots
UMBRAL_VOL_ABS    = 500_000 # $ mínimo (evitar basura)

MAX_ALERTAS_POR_RUN = 10    # límite anti-spam por corrida


# ============================================================
# TELEGRAM
# ============================================================

def enviar_telegram(msg):
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
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
# CARGA DE DATOS
# ============================================================

def cargar_datos():
    dfs = []
    for nombre, url in CSV_URLS.items():
        try:
            df = pd.read_csv(url)
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
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

    return df


# ============================================================
# DETECCIÓN DE ARRANQUE
# ============================================================

def detectar_arranque(df, symbol):
    g = df[df["symbol"] == symbol].sort_values("timestamp")
    if len(g) < 20:
        return None

    # Última hora
    corte = g["timestamp"].max() - timedelta(hours=1)
    rec = g[g["timestamp"] >= corte]
    if len(rec) < 3:
        return None

    p_ini = rec.iloc[0]["price"]
    p_fin = rec.iloc[-1]["price"]
    if p_ini <= 0:
        return None

    cambio = ((p_fin - p_ini) / p_ini) * 100
    if cambio < UMBRAL_CAMBIO_MIN:
        return None

    # Volumen
    v_base = g["volume_24h"].tail(20).mean()
    v_ult  = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0

    if vol_ratio < UMBRAL_VOL_RATIO:
        return None
    if v_ult < UMBRAL_VOL_ABS:
        return None

    return {
        "symbol": symbol,
        "cambio": cambio,
        "precio": p_fin,
        "vol_ratio": vol_ratio,
        "vol_m": v_ult / 1e6,
        "ts": rec.iloc[-1]["timestamp"],
        "cmc_rank": g.iloc[-1].get("cmc_rank", 9999),
        "market_cap_m": (g.iloc[-1].get("market_cap", 0) or 0) / 1e6,
    }


# ============================================================
# MAIN — CORRE SIEMPRE, SIN FILTRO DE VENTANA
# ============================================================

def main():
    ahora_utc = datetime.now(timezone.utc)
    ahora_lima = ahora_utc - timedelta(hours=5)

    print(f"\n{'='*70}")
    print(f"⚡ ALERTA ARRANQUE — AUTO-DISCOVERY 24/7")
    print(f"   UTC:  {ahora_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Lima: {ahora_lima.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    df = cargar_datos()
    print(f"📊 Total: {len(df):,} filas | {df['symbol'].nunique()} monedas")

    todas = df["symbol"].unique()
    print(f"🔍 Escaneando {len(todas)} monedas...")

    alertas = []
    excluidas = 0

    for sym in todas:
        if sym.upper() in EXCLUIR:
            excluidas += 1
            continue
        r = detectar_arranque(df, sym)
        if r:
            alertas.append(r)

    print(f"   Excluidas: {excluidas}")
    print(f"   Alertas:   {len(alertas)}")

    if not alertas:
        print("✅ Ninguna alerta activa")
        return

    # Ordenar por magnitud
    alertas.sort(key=lambda x: -x["cambio"])

    for a in alertas[:MAX_ALERTAS_POR_RUN]:
        hora_lima = (a["ts"] - timedelta(hours=5)).strftime("%H:%M")
        msg = (
            f"⚡ ARRANQUE DETECTADO\n"
            f"🪙 {a['symbol']} (rank {int(a['cmc_rank'])})\n"
            f"📈 +{a['cambio']:.2f}% en 1h\n"
            f"💰 ${a['precio']:.6f}\n"
            f"📊 Vol: {a['vol_ratio']:.2f}x (${a['vol_m']:.1f}M)\n"
            f"🏦 MC: ${a['market_cap_m']:.1f}M\n"
            f"🕐 {hora_lima} Lima"
        )
        if enviar_telegram(msg):
            print(f"   ✅ {a['symbol']} (+{a['cambio']:.2f}%)")


if __name__ == "__main__":
    main()
