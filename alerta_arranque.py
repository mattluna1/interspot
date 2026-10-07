#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ALERTA ARRANQUE EN TIEMPO REAL
Consume los CSV de los repositorios colectores vía raw URLs.
Corre cada 10 min desde GitHub Actions en ventanas calientes.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests


# ============================================================
# CONFIGURACIÓN — URLs de los colectores
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
# CONFIGURACIÓN — Alertas
# ============================================================

# Ventanas calientes (UTC)
VENTANAS = [
    ("Asia Open",      0,  4),
    ("US Afternoon",  18, 21),
]

# Umbrales
UMBRAL_CAMBIO_MIN = 1.5     # % mínimo de subida en 1h
UMBRAL_VOL_RATIO  = 1.4     # Volumen vs promedio 20 snapshots
UMBRAL_VOL_ABS    = 500_000 # Volumen mínimo en $ (evitar basura)


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
# CARGA DE DATOS REMOTOS
# ============================================================

def cargar_watchlist():
    """Lee watchlist.txt. Si no existe, usa lista por defecto."""
    path = Path("watchlist.txt")
    if not path.exists():
        return {"KCS", "BGB", "MX", "HTX"}
    return set(path.read_text().strip().split("\n"))


def cargar_datos():
    """Descarga y combina los CSV de los 3 colectores."""
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

def en_ventana_caliente():
    ahora_utc = datetime.now(timezone.utc)
    for nombre, ini, fin in VENTANAS:
        if ini <= ahora_utc.hour < fin:
            return nombre
    return None


def detectar_arranque(df, symbol):
    """Detecta si una moneda arrancó en la última hora."""
    g = df[df["symbol"] == symbol].sort_values("timestamp")
    if len(g) < 20:
        return None

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

    if vol_ratio < UMBRAL_VOL_RATIO or v_ult < UMBRAL_VOL_ABS:
        return None

    return {
        "symbol": symbol,
        "cambio": cambio,
        "precio": p_fin,
        "vol_ratio": vol_ratio,
        "vol_m": v_ult / 1e6,
        "ts": rec.iloc[-1]["timestamp"],
        "cmc_rank": g.iloc[-1].get("cmc_rank", 9999),
    }


# ============================================================
# MAIN
# ============================================================

def main():
    ahora_utc = datetime.now(timezone.utc)
    print(f"\n{'='*70}")
    print(f"⚡ ALERTA ARRANQUE — {ahora_utc.isoformat()}")
    print(f"{'='*70}")

    ventana = en_ventana_caliente()
    if not ventana:
        print("🔇 Fuera de ventana caliente — sin alertas")
        return

    print(f"🔥 Ventana: {ventana}")

    watchlist = cargar_watchlist()
    print(f"👁 Watchlist: {', '.join(sorted(watchlist))}")

    df = cargar_datos()
    print(f"📊 Total combinado: {len(df):,} filas | "
          f"{df['symbol'].nunique()} monedas")

    alertas = []
    for sym in watchlist:
        r = detectar_arranque(df, sym)
        if r:
            alertas.append(r)

    if not alertas:
        print("✅ Ninguna alerta activa")
        return

    for a in alertas:
        hora_lima = (a["ts"] - timedelta(hours=5)).strftime("%H:%M")
        msg = (
            f"⚡ ARRANQUE DETECTADO\n"
            f"🪙 {a['symbol']} (rank {int(a['cmc_rank'])})\n"
            f"📈 +{a['cambio']:.2f}% en 1h\n"
            f"💰 ${a['precio']:.6f}\n"
            f"📊 Vol: {a['vol_ratio']:.2f}x (${a['vol_m']:.1f}M)\n"
            f"🔥 Ventana: {ventana}\n"
            f"🕐 {hora_lima} Lima"
        )
        if enviar_telegram(msg):
            print(f"   ✅ Alerta enviada: {a['symbol']}")


if __name__ == "__main__":
    main()
