#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ANÁLISIS: valores pre-pump de las monedas que arrancaron
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from io import StringIO

import pandas as pd
import requests

CSV_URLS = {
    "top100": "https://raw.githubusercontent.com/mattluna3/inspector/main/data/cmc/market_history_top100.csv",
    "emerging": "https://raw.githubusercontent.com/mattluna3/inspector/main/data/cmc/market_history_emerging.csv",
    "201_300": "https://raw.githubusercontent.com/emerging2/inspector2/main/data/cmc/market_history_201_300.csv",
}

# Monedas que arrancaron hoy (según oleadas)
MONEDAS_ARRANCARON = {
    "SAND": "2026-10-07 23:30",
    "RAY": "2026-10-08 01:00",
    "FUN": "2026-10-08 05:30",
    "CRV": "2026-10-07 15:30",
    "APEPE": "2026-10-07 20:00",
    "NEAR": "2026-10-08 01:00",
    "PROM": "2026-10-07 23:30",
    "PONS": "2026-10-07 22:00",
}


def cargar_datos():
    dfs = []
    for _, url in CSV_URLS.items():
        try:
            r = requests.get(url, timeout=30)
            df = pd.read_csv(StringIO(r.text))
            dfs.append(df)
        except Exception as e:
            print(f"⚠️ {e}")
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    for c in ["price", "volume_24h", "cmc_rank", "market_cap", "percent_change_24h"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["timestamp", "price", "symbol"])
    df = df[df["price"] > 0]
    df = df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return df


def analizar_pre_pump(df, symbol, hora_pump_str):
    """Analiza los 2h, 4h y 24h ANTES del pump."""
    hora_pump = pd.to_datetime(hora_pump_str, utc=True)
    
    g = df[df["symbol"] == symbol].sort_values("timestamp")
    if len(g) < 40:
        return None
    
    # Datos en el momento del pump
    pump_data = g[g["timestamp"] <= hora_pump].tail(1)
    if pump_data.empty:
        return None
    
    pump_row = pump_data.iloc[0]
    ts_pump = pump_row["timestamp"]
    p_pump = pump_row["price"]
    v_pump = pump_row["volume_24h"]
    mc = pump_row.get("market_cap", 0) or 0
    rank = pump_row.get("cmc_rank", 9999)
    
    # Calcular cambios previos
    def cambio_en_horas(h):
        corte = ts_pump - timedelta(hours=h)
        rec = g[(g["timestamp"] >= corte) & (g["timestamp"] <= ts_pump)]
        if len(rec) < 2:
            return None, None
        p_ini = rec.iloc[0]["price"]
        cambio = ((p_pump - p_ini) / p_ini) * 100 if p_ini > 0 else 0
        return cambio, len(rec)
    
    cambio_1h, _ = cambio_en_horas(1)
    cambio_2h, _ = cambio_en_horas(2)
    cambio_4h, _ = cambio_en_horas(4)
    cambio_24h, _ = cambio_en_horas(24)
    
    # Volumen promedio vs actual
    corte_4h = ts_pump - timedelta(hours=4)
    rec_4h = g[g["timestamp"] < corte_4h].tail(20)
    if len(rec_4h) > 0:
        v_base = rec_4h["volume_24h"].mean()
        vol_ratio = v_pump / v_base if v_base > 0 else 0
    else:
        vol_ratio = 0
    
    # Precio mínimo y máximo de las últimas 24h
    corte_24h = ts_pump - timedelta(hours=24)
    rec_24h = g[g["timestamp"] >= corte_24h]
    if len(rec_24h) > 0:
        p_min_24h = rec_24h["price"].min()
        p_max_24h = rec_24h["price"].max()
        rango_24h = ((p_max_24h - p_min_24h) / p_min_24h) * 100 if p_min_24h > 0 else 0
    else:
        p_min_24h = p_max_24h = rango_24h = 0
    
    return {
        "symbol": symbol,
        "hora_pump": ts_pump,
        "pump": cambio_1h,
        "cambio_1h_antes": cambio_1h,
        "cambio_2h_antes": cambio_2h,
        "cambio_4h_antes": cambio_4h,
        "cambio_24h_antes": cambio_24h,
        "vol_ratio": vol_ratio,
        "vol_m": v_pump / 1e6,
        "mc_m": mc / 1e6,
        "rank": int(rank),
        "rango_24h": rango_24h,
    }


def main():
    print("=" * 100)
    print("🔬 ANÁLISIS PRE-PUMP DE LAS MONEDAS QUE ARRANCARON HOY")
    print("=" * 100)
    
    df = cargar_datos()
    print(f"📊 {len(df):,} filas | {df['symbol'].nunique()} monedas")
    
    resultados = []
    for sym, hora in MONEDAS_ARRANCARON.items():
        r = analizar_pre_pump(df, sym, hora)
        if r:
            resultados.append(r)
    
    if not resultados:
        print("\n⚪ Sin datos")
        return
    
    rdf = pd.DataFrame(resultados)
    
    print(f"\n{'='*100}")
    print(f"📊 VALORES PRE-PUMP")
    print(f"{'='*100}\n")
    
    print(f"{'SYMBOL':<8} {'PUMP':>8} {'1H ANTES':>10} {'2H ANTES':>10} {'4H ANTES':>10} "
          f"{'24H ANTES':>11} {'VOL':>7} {'MC $M':>10} {'RANK':>5} {'RANGO 24H':>10}")
    print("-" * 100)
    
    for _, r in rdf.iterrows():
        print(f"{r['symbol']:<8} "
              f"{r['pump']:>+7.2f}% "
              f"{r['cambio_1h_antes']:>+9.2f}% "
              f"{r['cambio_2h_antes']:>+9.2f}% "
              f"{r['cambio_4h_antes']:>+9.2f}% "
              f"{r['cambio_24h_antes']:>+10.2f}% "
              f"{r['vol_ratio']:>6.2f}x "
              f"{r['mc_m']:>9.1f}M "
              f"{r['rank']:>5} "
              f"{r['rango_24h']:>9.2f}%")
    
    # Promedios
    print(f"\n{'='*100}")
    print(f"📊 PROMEDIOS DEL GRUPO")
    print(f"{'='*100}\n")
    
    print(f"  Pump promedio:           {rdf['pump'].mean():+.2f}%")
    print(f"  Cambio 1h antes:         {rdf['cambio_1h_antes'].mean():+.2f}%")
    print(f"  Cambio 2h antes:         {rdf['cambio_2h_antes'].mean():+.2f}%")
    print(f"  Cambio 4h antes:         {rdf['cambio_4h_antes'].mean():+.2f}%")
    print(f"  Cambio 24h antes:        {rdf['cambio_24h_antes'].mean():+.2f}%")
    print(f"  Vol ratio:               {rdf['vol_ratio'].mean():.2f}x")
    print(f"  MC promedio:             ${rdf['mc_m'].mean():.1f}M")
    print(f"  Rank promedio:           {rdf['rank'].mean():.0f}")
    print(f"  Rango 24h:               {rdf['rango_24h'].mean():.2f}%")


if __name__ == "__main__":
    main()
