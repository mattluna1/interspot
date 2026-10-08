#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST: ¿Qué pasa en los últimos 15-30 min ANTES del pump?
Analiza solo las velas inmediatamente previas.
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

MONEDAS_TEST = [
    "SAND", "FUN", "RAY", "PONS", "PEAQ", "MANA",
    "ORCA", "FLUID", "MET", "W", "CRV", "CVX",
    "STONK", "CHIP", "CASHCAT", "JTO", "TIA", "ZBCN",
    "LDO", "HNT", "ARB", "OP", "APT", "INJ",
    "FIL", "ATOM", "DOT", "NEAR", "AVAX", "LINK",
]


def cargar_datos():
    dfs = []
    for _, url in CSV_URLS.items():
        try:
            r = requests.get(url, timeout=30)
            df = pd.read_csv(StringIO(r.text))
            dfs.append(df)
        except Exception:
            continue
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    for c in ["price", "volume_24h"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["timestamp", "price", "symbol"])
    df = df[df["price"] > 0]
    df = df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return df


def detectar_pump(g, idx, ventana=12):
    """Detecta si hay pump en las próximas N velas (1h)."""
    if idx + ventana > len(g):
        return None
    ventana_post = g.iloc[idx:idx + ventana]
    p_ini = ventana_post.iloc[0]["price"]
    p_max = ventana_post["price"].max()
    if p_ini <= 0:
        return None
    pump_pct = ((p_max - p_ini) / p_ini) * 100
    if pump_pct < 5.0:
        return None
    return {"pump_pct": pump_pct}


def analizar_previo_inmediato(g, idx):
    """
    Analiza los últimos 3, 6, 12 snapshots antes del pump.
    """
    if idx < 15:
        return None
    
    # Precio actual
    p_now = g.iloc[idx]["price"]
    
    # Snapshots inmediatamente previos
    resultados = {}
    
    for ventana in [3, 6, 12]:  # 15 min, 30 min, 1h
        if idx < ventana:
            continue
        
        rec = g.iloc[idx-ventana:idx]
        p_ini = rec.iloc[0]["price"]
        cambio = ((p_now - p_ini) / p_ini) * 100 if p_ini > 0 else 0
        
        # Volumen promedio
        v_prom = rec["volume_24h"].mean()
        
        # Volumen del último snapshot vs promedio de la ventana
        v_ult = g.iloc[idx-1]["volume_24h"]
        vol_ratio_ventana = v_ult / v_prom if v_prom > 0 else 0
        
        resultados[f"cambio_{ventana}"] = cambio
        resultados[f"vol_ratio_{ventana}"] = vol_ratio_ventana
        resultados[f"v_prom_{ventana}"] = v_prom
    
    # Aceleración del volumen (últimas 2 velas vs 10 anteriores)
    v_ultimas_2 = g.iloc[idx-2:idx]["volume_24h"].mean()
    v_antes_10 = g.iloc[idx-12:idx-2]["volume_24h"].mean()
    aceleracion = v_ultimas_2 / v_antes_10 if v_antes_10 > 0 else 0
    
    # Cambio en la última vela (5 min)
    if idx >= 2:
        p_ant = g.iloc[idx-1]["price"]
        p_prev = g.iloc[idx-2]["price"]
        cambio_ultima = ((p_ant - p_prev) / p_prev) * 100 if p_prev > 0 else 0
    else:
        cambio_ultima = 0
    
    return {
        "cambio_3": resultados.get("cambio_3", 0),
        "cambio_6": resultados.get("cambio_6", 0),
        "cambio_12": resultados.get("cambio_12", 0),
        "vol_ratio_3": resultados.get("vol_ratio_3", 0),
        "vol_ratio_6": resultados.get("vol_ratio_6", 0),
        "vol_ratio_12": resultados.get("vol_ratio_12", 0),
        "aceleracion": aceleracion,
        "cambio_ultima_vela": cambio_ultima,
    }


def main():
    print("=" * 90)
    print("🔬 TEST: ¿Qué pasa en los últimos 15-30 min ANTES del pump?")
    print("=" * 90)
    
    df = cargar_datos()
    print(f"📊 {len(df):,} filas")
    
    con_pump = []
    sin_pump = []
    
    for sym in MONEDAS_TEST:
        g = df[df["symbol"] == sym].sort_values("timestamp").reset_index(drop=True)
        if len(g) < 50:
            continue
        
        for idx in range(15, len(g) - 12):
            previo = analizar_previo_inmediato(g, idx)
            if not previo:
                continue
            
            pump = detectar_pump(g, idx, 12)
            
            if pump:
                previo["pump_pct"] = pump["pump_pct"]
                previo["symbol"] = sym
                con_pump.append(previo)
            else:
                previo["symbol"] = sym
                sin_pump.append(previo)
    
    print(f"\n  Casos con pump:  {len(con_pump)}")
    print(f"  Casos sin pump:  {len(sin_pump)}")
    
    if not con_pump or not sin_pump:
        print("⚠️ Datos insuficientes")
        return
    
    df_con = pd.DataFrame(con_pump)
    df_sin = pd.DataFrame(sin_pump)
    
    print(f"\n{'='*90}")
    print(f"📊 COMPARACIÓN: últimas velas antes del pump")
    print(f"{'='*90}\n")
    
    print(f"  {'MÉTRICA':<28} {'CON PUMP':>12} {'SIN PUMP':>12} {'DIFERENCIA':>12}")
    print(f"  {'-'*68}")
    
    metricas = [
        ("Cambio últimas 3 velas (15m)", "cambio_3"),
        ("Cambio últimas 6 velas (30m)", "cambio_6"),
        ("Cambio últimas 12 velas (1h)", "cambio_12"),
        ("Cambio última vela (5m)", "cambio_ultima_vela"),
        ("Vol ratio últimas 3 velas", "vol_ratio_3"),
        ("Vol ratio últimas 6 velas", "vol_ratio_6"),
        ("Vol ratio últimas 12 velas", "vol_ratio_12"),
        ("Aceleración volumen (2v/10v)", "aceleracion"),
    ]
    
    for nombre, key in metricas:
        v_con = df_con[key].mean()
        v_sin = df_sin[key].mean()
        diff = v_con - v_sin
        print(f"  {nombre:<28} {v_con:>11.3f} {v_sin:>11.3f} {diff:>+11.3f}")
    
    # === Análisis de umbrales ===
    print(f"\n{'='*90}")
    print(f"📊 ¿Cuántos pumps tienen ACELERACIÓN o VOLUMEN alto en las últimas 3 velas?")
    print(f"{'='*90}\n")
    
    for umbral in [1.2, 1.5, 2.0, 3.0, 5.0]:
        n_con_acel = (df_con["aceleracion"] > umbral).sum()
        n_sin_acel = (df_sin["aceleracion"] > umbral).sum()
        pct_con = n_con_acel / len(df_con) * 100
        pct_sin = n_sin_acel / len(df_sin) * 100
        
        print(f"  Aceleración >{umbral}x:")
        print(f"    Con pump:  {n_con_acel:>5} ({pct_con:>5.1f}%)")
        print(f"    Sin pump:  {n_sin_acel:>5} ({pct_sin:>5.1f}%)")
        print(f"    Diferencia: {pct_con - pct_sin:+.1f}%")
        print()
    
    # === Análisis de cambio en última vela ===
    print(f"{'='*90}")
    print(f"📊 ¿El precio sube en la última vela antes del pump?")
    print(f"{'='*90}\n")
    
    for umbral in [0.1, 0.3, 0.5, 1.0]:
        n_con = (df_con["cambio_ultima_vela"] > umbral).sum()
        n_sin = (df_sin["cambio_ultima_vela"] > umbral).sum()
        pct_con = n_con / len(df_con) * 100
        pct_sin = n_sin / len(df_sin) * 100
        print(f"  Cambio última vela >{umbral}%:")
        print(f"    Con pump:  {n_con:>5} ({pct_con:>5.1f}%)")
        print(f"    Sin pump:  {n_sin:>5} ({pct_sin:>5.1f}%)")
        print(f"    Diferencia: {pct_con - pct_sin:+.1f}%")
        print()


if __name__ == "__main__":
    main()
