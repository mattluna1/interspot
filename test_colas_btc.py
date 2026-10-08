#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST: ¿Las colas largas de BTC preceden tendencias?
Analiza velas con colas dominantes y mide si después hay movimiento.
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
    for c in ["price"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["timestamp", "price"])
    df = df[df["price"] > 0]
    df = df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return df


def construir_velas_1h(g):
    """Convierte snapshots de 5 min en velas de 1h."""
    g = g.set_index("timestamp").sort_index()
    agg = g["price"].resample("1h").agg(["first", "max", "min", "last"])
    agg.columns = ["open", "high", "low", "close"]
    return agg.dropna()


def detectar_cola_larga(row, ratio_cola_min=0.6, ratio_cuerpo_max=0.3):
    """
    Detecta si una vela tiene cola dominante.
    Retorna: tipo ('inferior' | 'superior' | None) y la magnitud.
    """
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    rango = h - l
    if rango <= 0:
        return None, 0
    
    cuerpo = abs(c - o)
    cola_sup = h - max(o, c)
    cola_inf = min(o, c) - l
    
    # Ratio cola/cuerpo
    ratio_cola_sup = cola_sup / rango
    ratio_cola_inf = cola_inf / rango
    ratio_cuerpo = cuerpo / rango
    
    if ratio_cuerpo > ratio_cuerpo_max:
        return None, 0
    
    if ratio_cola_inf >= ratio_cola_min:
        return "inferior", ratio_cola_inf
    if ratio_cola_sup >= ratio_cola_min:
        return "superior", ratio_cola_sup
    return None, 0


def analizar_tendencia_post(velas, idx, direccion, ventana=24, umbral=2.0):
    """
    Mide si después de la vela idx, el precio se movió en la dirección esperada.
    ventana: 24 velas de 1h = 24h
    umbral: 2% de movimiento mínimo
    """
    if idx + ventana > len(velas):
        return None
    
    precio_base = velas.iloc[idx]["close"]
    ventana_velas = velas.iloc[idx+1:idx+ventana+1]
    
    p_max = ventana_velas["high"].max()
    p_min = ventana_velas["low"].min()
    
    subida_pct = ((p_max - precio_base) / precio_base) * 100
    bajada_pct = ((precio_base - p_min) / precio_base) * 100
    
    if direccion == "inferior":
        # Cola inferior → esperamos subida
        exito = subida_pct >= umbral
        return {
            "movimiento": subida_pct,
            "exito": exito,
            "direccion_esperada": "subida",
        }
    else:
        # Cola superior → esperamos bajada
        exito = bajada_pct >= umbral
        return {
            "movimiento": bajada_pct,
            "exito": exito,
            "direccion_esperada": "bajada",
        }


def main():
    print("=" * 80)
    print("🔬 TEST: Colas largas de BTC → ¿preceden tendencia?")
    print("=" * 80)
    
    df = cargar_datos()
    print(f"📊 {len(df):,} filas | {df['symbol'].nunique()} monedas")
    
    btc = df[df["symbol"] == "BTC"].sort_values("timestamp")
    print(f"📊 BTC: {len(btc)} snapshots")
    
    if len(btc) < 200:
        print("⚠️ Pocos datos de BTC")
        return
    
    velas = construir_velas_1h(btc)
    print(f"📊 Velas 1h: {len(velas)}")
    
    # Buscar colas largas en cada vela
    colas = []
    for i in range(len(velas) - 24):
        row = velas.iloc[i]
        tipo, magnitud = detectar_cola_larga(row)
        if tipo is None:
            continue
        
        # Medir tendencia después
        resultado = analizar_tendencia_post(velas, i, tipo, ventana=24, umbral=2.0)
        if resultado is None:
            continue
        
        colas.append({
            "timestamp": velas.index[i],
            "tipo": tipo,
            "magnitud": magnitud,
            "precio": row["close"],
            "subida_max_pct": resultado["movimiento"],
            "exito": resultado["exito"],
        })
    
    if not colas:
        print("\n⚪ No se detectaron colas largas")
        return
    
    df_colas = pd.DataFrame(colas)
    
    print(f"\n{'='*80}")
    print(f"📊 RESULTADOS: {len(df_colas)} colas largas detectadas")
    print(f"{'='*80}\n")
    
    # Separar por tipo
    inferiores = df_colas[df_colas["tipo"] == "inferior"]
    superiores = df_colas[df_colas["tipo"] == "superior"]
    
    print(f"  Colas INFERIORES (hammer): {len(inferiores)}")
    if len(inferiores) > 0:
        tasa_inf = inferiores["exito"].mean() * 100
        mov_prom_inf = inferiores["subida_max_pct"].mean()
        print(f"    → Subieron >2% en 24h: {tasa_inf:.1f}%")
        print(f"    → Subida promedio: {mov_prom_inf:+.2f}%")
    
    print(f"\n  Colas SUPERIORES (shooting star): {len(superiores)}")
    if len(superiores) > 0:
        tasa_sup = superiores["exito"].mean() * 100
        mov_prom_sup = superiores["subida_max_pct"].mean()
        print(f"    → Bajaron >2% en 24h: {tasa_sup:.1f}%")
        print(f"    → Bajada promedio: {mov_prom_sup:+.2f}%")
    
    # Últimas 10 colas
    print(f"\n{'='*80}")
    print(f"📋 ÚLTIMAS 10 COLAS")
    print(f"{'='*80}")
    print(f"{'FECHA':<20} {'TIPO':<10} {'MAG':>6} {'MOV 24H':>10} {'ÉXITO':>8}")
    print("-" * 80)
    for _, r in df_colas.tail(10).iterrows():
        fecha = r["timestamp"].strftime("%m-%d %H:%M")
        mov = f"{r['subida_max_pct']:+.2f}%"
        exito = "✅" if r["exito"] else "❌"
        print(f"{fecha:<20} {r['tipo']:<10} {r['magnitud']:>5.2f} {mov:>10} {exito:>8}")
    
    # Veredicto
    print(f"\n{'='*80}")
    print(f"🎯 VEREDICTO")
    print(f"{'='*80}")
    
    if len(inferiores) > 0:
        tasa_inf = inferiores["exito"].mean() * 100
        if tasa_inf > 60:
            print(f"\n  ✅ COLAS INFERIORES PREDICEN SUBIDA ({tasa_inf:.1f}%)")
            print(f"  Cuando BTC hace hammer, sube >2% en 24h")
        elif tasa_inf > 50:
            print(f"\n  🟡 COLAS INFERIORES: señal moderada ({tasa_inf:.1f}%)")
        else:
            print(f"\n  ❌ COLAS INFERIORES: sin poder predictivo ({tasa_inf:.1f}%)")
    
    if len(superiores) > 0:
        tasa_sup = superiores["exito"].mean() * 100
        if tasa_sup > 60:
            print(f"\n  ✅ COLAS SUPERIORES PREDICEN BAJADA ({tasa_sup:.1f}%)")
        elif tasa_sup > 50:
            print(f"\n  🟡 COLAS SUPERIORES: señal moderada ({tasa_sup:.1f}%)")
        else:
            print(f"\n  ❌ COLAS SUPERIORES: sin poder predictivo ({tasa_sup:.1f}%)")


if __name__ == "__main__":
    main()
