#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DETECTOR DE OLEADAS — Corre en GitHub Actions
Descarga CSV de los 3 recolectores y detecta la secuencia de oleadas.

FIXES aplicados:
  - Descarta monedas que no subieron en la ventana
  - Busca arranque en la segunda mitad (no en el mínimo absoluto)
  - Verifica volumen real (no 0.00x)
  - Verifica cambio positivo desde el arranque
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
# CONFIGURACIÓN
# ============================================================

HORAS_VENTANA = 24
UMBRAL_ARRANQUE = 3.0       # % desde mínimo para considerar "arrancó"
MIN_SNAPSHOTS = 8
VOL_RATIO_MIN = 1.0         # mínimo volumen para considerar oleada real
CAMBIO_MIN_DESDE_ARRANQUE = 1.0  # % mínimo desde arranque (filtra rebotes falsos)

ENVIAR_TELEGRAM = os.getenv("OLEADAS_TELEGRAM", "0") == "1"


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

    for c in ["price", "volume_24h", "percent_change_24h"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["timestamp", "price", "symbol"])
    df = df[df["price"] > 0]
    df = df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

    return df


# ============================================================
# DETECCIÓN DE OLEADAS
# ============================================================

def detectar_oleadas(df, horas):
    ts_max = df["timestamp"].max()
    ts_min = ts_max - timedelta(hours=horas)
    rec = df[df["timestamp"] >= ts_min].copy()

    print(f"📅 Ventana: {ts_min} → {ts_max}")
    print(f"📊 Filas en ventana: {len(rec):,}")
    print(f"🪙 Monedas en ventana: {rec['symbol'].nunique()}")

    arranques = []

    for symbol, g in rec.groupby("symbol"):
        if symbol.upper() in EXCLUIR:
            continue
        g = g.sort_values("timestamp").reset_index(drop=True)
        if len(g) < MIN_SNAPSHOTS:
            continue

        p_ini = g.iloc[0]["price"]
        p_min = g["price"].min()
        p_fin = g.iloc[-1]["price"]
        if p_ini <= 0 or p_min <= 0:
            continue

        # FIX 1: Descartar si NO subió en la ventana completa
        cambio_total = ((p_fin - p_ini) / p_ini) * 100
        if cambio_total < 0:
            continue

        # FIX 2: Buscar arranque en la SEGUNDA MITAD de la ventana
        mitad = len(g) // 2
        if mitad < 3:
            continue
        p_min_inicial = g[:mitad]["price"].min()
        if p_min_inicial <= 0:
            continue

        objetivo = p_min_inicial * (1 + UMBRAL_ARRANQUE / 100)
        arranque = g[g["price"] >= objetivo]
        if arranque.empty:
            continue

        idx = arranque.index[0]
        ts_arr = g.loc[idx, "timestamp"]
        p_arr = g.loc[idx, "price"]

        # FIX 3: Verificar volumen real (no 0.00x)
        if "volume_24h" not in g.columns:
            continue
        antes = g[g["timestamp"] < ts_arr]["volume_24h"].mean()
        despues = g[g["timestamp"] >= ts_arr]["volume_24h"].mean()
        if not antes or antes <= 0:
            continue
        ratio_vol = despues / antes if despues > 0 else 0

        if ratio_vol < VOL_RATIO_MIN:
            continue

        cambio_desde = ((p_fin - p_arr) / p_arr) * 100

        # FIX 4: Verificar que el cambio desde arranque sea positivo
        if cambio_desde < CAMBIO_MIN_DESDE_ARRANQUE:
            continue

        arranques.append({
            "symbol": symbol,
            "ts_arranque": ts_arr,
            "precio_arranque": p_arr,
            "precio_actual": p_fin,
            "cambio_desde_arranque": cambio_desde,
            "cambio_total_ventana": cambio_total,
            "vol_ratio": ratio_vol,
        })

    if not arranques:
        return rec, pd.DataFrame()

    return rec, pd.DataFrame(arranques).sort_values("ts_arranque")


# ============================================================
# CANDIDATAS (aún no arrancaron)
# ============================================================

def detectar_candidatas(rec, ya_arrancaron):
    candidatas = []

    for symbol, g in rec.groupby("symbol"):
        if symbol in ya_arrancaron:
            continue
        if symbol.upper() in EXCLUIR:
            continue
        g = g.sort_values("timestamp").reset_index(drop=True)
        if len(g) < MIN_SNAPSHOTS:
            continue

        p_ini = g.iloc[0]["price"]
        p_fin = g.iloc[-1]["price"]
        if p_ini <= 0:
            continue

        cambio_24h = ((p_fin - p_ini) / p_ini) * 100

        # Plano o ligeramente bajando (pero NO colapsando)
        if not (-8 <= cambio_24h <= 3):
            continue

        vol_prom = g["volume_24h"].mean() if "volume_24h" in g.columns else 0
        vol_ult = g["volume_24h"].iloc[-3:].mean() if "volume_24h" in g.columns else 0
        if vol_prom <= 0:
            continue
        ratio_vol = vol_ult / vol_prom

        if ratio_vol < 1.2:
            continue
        if vol_ult < 1_000_000:
            continue

        candidatas.append({
            "symbol": symbol,
            "cambio_24h": cambio_24h,
            "vol_ratio": ratio_vol,
            "vol_actual_m": vol_ult / 1e6,
            "precio": p_fin,
        })

    if not candidatas:
        return pd.DataFrame()

    return pd.DataFrame(candidatas).sort_values("vol_ratio", ascending=False)


# ============================================================
# REPORTE
# ============================================================

def reportar(df, horas):
    print()
    print("=" * 78)
    print(f"🌊 DETECTOR DE OLEADAS — últimas {horas}h")
    print("=" * 78)

    rec, arranques = detectar_oleadas(df, horas)

    if arranques.empty:
        print("\n⚪ Ninguna moneda arrancó en la ventana")
        return

    arranques["bloque"] = arranques["ts_arranque"].dt.floor("30min")

    print(f"\n📊 Monedas que arrancaron: {len(arranques)}")

    for bloque, grupo in arranques.groupby("bloque"):
        print(f"\n🌊 OLEADA {bloque.strftime('%H:%M')} UTC "
              f"({(bloque - timedelta(hours=5)).strftime('%H:%M')} Lima) — "
              f"{len(grupo)} monedas")
        print("-" * 78)
        print(f"{'SYMBOL':<10} {'ARRANQUE':<10} {'CAMBIO':>8} {'TOTAL':>8} {'VOL':>7} {'PRECIO ACT':>14}")
        for _, r in grupo.iterrows():
            print(f"{r['symbol']:<10} "
                  f"{r['ts_arranque'].strftime('%H:%M:%S'):<10} "
                  f"{r['cambio_desde_arranque']:>+7.2f}% "
                  f"{r['cambio_total_ventana']:>+7.2f}% "
                  f"{r['vol_ratio']:>6.2f}x "
                  f"${r['precio_actual']:>12.6f}")

    ya_arrancaron = set(arranques["symbol"])
    candidatas = detectar_candidatas(rec, ya_arrancaron)

    print()
    print("=" * 78)
    print("🎯 PRÓXIMA OLEADA (aún NO arrancaron pero con volumen subiendo)")
    print("=" * 78)

    if candidatas.empty:
        print("⚪ Ninguna candidata con el setup")
        return

    print(f"\n{'SYMBOL':<10} {'24H':>8} {'VOL RATIO':>11} {'VOL $M':>9} {'PRECIO':>14}")
    print("-" * 78)
    for _, r in candidatas.head(20).iterrows():
        print(f"{r['symbol']:<10} "
              f"{r['cambio_24h']:>+7.2f}% "
              f"{r['vol_ratio']:>10.2f}x "
              f"{r['vol_actual_m']:>8.1f}M "
              f"${r['precio']:>12.6f}")


# ============================================================
# TELEGRAM RESUMEN
# ============================================================

def enviar_resumen_telegram(df, horas):
    rec, arranques = detectar_oleadas(df, horas)

    if arranques.empty:
        return

    arranques["bloque"] = arranques["ts_arranque"].dt.floor("30min")

    ahora_lima = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%H:%M")

    lineas = [f"🌊 OLEADAS (últimas {horas}h)"]
    lineas.append(f"🕐 {ahora_lima} Lima")
    lineas.append(f"━━━━━━━━━━━━━━━━━━━")

    for bloque, grupo in arranques.groupby("bloque"):
        hora_lima = (bloque - timedelta(hours=5)).strftime("%H:%M")
        lineas.append(f"\n🌊 {hora_lima} Lima — {len(grupo)} monedas")
        for _, r in grupo.head(5).iterrows():
            lineas.append(f"  {r['symbol']} +{r['cambio_desde_arranque']:.1f}% "
                          f"(vol {r['vol_ratio']:.2f}x)")

    ya_arrancaron = set(arranques["symbol"])
    candidatas = detectar_candidatas(rec, ya_arrancaron)

    if not candidatas.empty:
        lineas.append(f"\n🎯 PRÓXIMA OLEADA:")
        for _, r in candidatas.head(8).iterrows():
            lineas.append(f"  {r['symbol']} 24h {r['cambio_24h']:+.1f}% "
                          f"(vol {r['vol_ratio']:.2f}x)")

    msg = "\n".join(lineas)
    if enviar_telegram(msg):
        print("\n📱 Resumen enviado a Telegram")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    ahora = datetime.now(timezone.utc)
    ahora_lima = ahora - timedelta(hours=5)

    print("=" * 78)
    print("🌊 DETECTOR DE OLEADAS — GITHUB EDITION")
    print(f"   UTC:  {ahora.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Lima: {ahora_lima.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 78)

    df = cargar_datos()

    print(f"✅ {len(df):,} filas | {df['symbol'].nunique()} monedas")

    reportar(df, HORAS_VENTANA)

    if ENVIAR_TELEGRAM:
        enviar_resumen_telegram(df, HORAS_VENTANA)

    print()
    print("=" * 78)
    print("✅ COMPLETADO")
    print("=" * 78)
