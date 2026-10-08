#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST: ¿La compresión precede al pump?
Mide la probabilidad de que una compresión termine en pump.
Sin Fibo. Sin teoría. Solo estadística pura.
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


# ============================================================
# CONFIGURACIÓN
# ============================================================

# Definición de compresión
VENTANA_COMPRESION_VELAS = 12       # 12 snapshots = 1h aprox (5 min c/u)
RANGO_MAX_COMPRESION = 1.5          # rango máximo 1.5% para considerarlo compresión
RATIO_VOLUMEN_MIN = 0.8             # volumen mínimo vs promedio

# Definición de pump
VENTANA_PUMP_VELAS = 24             # 24 snapshots = 2h para ver si explota
PUMP_MIN_PCT = 3.0                  # subida mínima 3% para considerarlo pump

# Monedas a analizar
MONEDAS_TEST = [
    "SAND", "FUN", "RAY", "PONS", "PEAQ", "MANA",
    "ORCA", "FLUID", "MET", "W", "CRV", "CVX",
    "STONK", "CHIP", "CASHCAT", "JTO", "TIA", "ZBCN",
    "LDO", "HNT", "ARB", "OP", "APT", "INJ",
    "FIL", "ATOM", "DOT", "NEAR", "AVAX", "LINK",
]


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
            dfs.append(df)
            print(f"✅ {nombre}: {len(df):,} filas")
        except Exception as e:
            print(f"⚠️ {nombre}: {str(e)[:80]}")

    if not dfs:
        sys.exit("❌ No se pudo cargar ningún CSV")

    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")

    for c in ["price", "volume_24h"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["timestamp", "price", "symbol"])
    df = df[df["price"] > 0]
    df = df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return df


# ============================================================
# ANÁLISIS
# ============================================================

def analizar_moneda(df, symbol):
    """
    Escanea una moneda buscando TODAS las compresiones.
    Para cada compresión, mira si en las próximas 2h hay pump.
    """
    g = df[df["symbol"] == symbol].sort_values("timestamp").reset_index(drop=True)
    if len(g) < 100:
        return []

    resultados = []

    for idx in range(VENTANA_COMPRESION_VELAS, len(g) - VENTANA_PUMP_VELAS):
        # === Analizar ventana de compresión ===
        ventana = g.iloc[idx - VENTANA_COMPRESION_VELAS:idx]
        if len(ventana) < VENTANA_COMPRESION_VELAS:
            continue

        precios = ventana["price"].tolist()
        p_min = min(precios)
        p_max = max(precios)
        if p_min <= 0:
            continue

        rango_pct = ((p_max - p_min) / p_min) * 100

        # ¿Es compresión?
        if rango_pct > RANGO_MAX_COMPRESION:
            continue

        # Volumen de la compresión vs promedio 40 velas antes
        v_comp = ventana["volume_24h"].mean()
        v_hist = g.iloc[max(0, idx - 40):idx]["volume_24h"].mean()
        if v_hist <= 0:
            continue

        vol_ratio = v_comp / v_hist

        # === Mirar las próximas velas ===
        ventana_post = g.iloc[idx:idx + VENTANA_PUMP_VELAS]
        if len(ventana_post) < VENTANA_PUMP_VELAS:
            continue

        p_ini_post = ventana_post.iloc[0]["price"]
        p_max_post = ventana_post["price"].max()
        if p_ini_post <= 0:
            continue

        pump_pct = ((p_max_post - p_ini_post) / p_ini_post) * 100

        # ¿Hubo pump?
        hubo_pump = pump_pct >= PUMP_MIN_PCT

        # ¿Cuánto duró el pump?
        idx_max_post = ventana_post["price"].idxmax()
        velas_pump = idx_max_post - idx if idx_max_post > idx else 0

        # ¿Dónde cayó después?
        p_fin_post = ventana_post.iloc[-1]["price"]
        caida_post = ((p_fin_post - p_max_post) / p_max_post) * 100

        resultados.append({
            "symbol": symbol,
            "ts_compresion": g.iloc[idx]["timestamp"],
            "rango_compresion": rango_pct,
            "vol_ratio_compresion": vol_ratio,
            "pump_pct": pump_pct,
            "hubo_pump": hubo_pump,
            "velas_hasta_pump": velas_pump,
            "caida_post_pump": caida_post,
        })

    return resultados


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 78)
    print("🔬 TEST: ¿La compresión precede al pump?")
    print("=" * 78)
    print(f"  Definición compresión:")
    print(f"    - Ventana: {VENTANA_COMPRESION_VELAS} snapshots")
    print(f"    - Rango máximo: {RANGO_MAX_COMPRESION}%")
    print(f"  Definición pump:")
    print(f"    - Ventana: {VENTANA_PUMP_VELAS} snapshots")
    print(f"    - Subida mínima: {PUMP_MIN_PCT}%")
    print()

    df = cargar_datos()
    print(f"📊 {len(df):,} filas | {df['symbol'].nunique()} monedas")

    print(f"\n🔍 Analizando {len(MONEDAS_TEST)} monedas...")

    todos = []
    for sym in MONEDAS_TEST:
        r = analizar_moneda(df, sym)
        if r:
            todos.extend(r)

    if not todos:
        print("\n⚪ No se encontraron compresiones")
        return

    rdf = pd.DataFrame(todos)

    print(f"\n{'='*78}")
    print(f"📊 RESULTADOS: {len(rdf)} compresiones detectadas")
    print(f"{'='*78}")

    # Estadísticas generales
    n_total = len(rdf)
    n_pump = rdf["hubo_pump"].sum()
    tasa = n_pump / n_total * 100 if n_total > 0 else 0

    print(f"\n  Total compresiones:        {n_total}")
    print(f"  Con pump posterior:        {n_pump} ({tasa:.1f}%)")
    print(f"  Sin pump posterior:        {n_total - n_pump} ({100-tasa:.1f}%)")

    # === Comparar compresiones que pumpearon vs las que no ===
    con_pump = rdf[rdf["hubo_pump"]]
    sin_pump = rdf[~rdf["hubo_pump"]]

    print(f"\n{'='*78}")
    print(f"📈 DIFERENCIAS: Compresiones que pumpearon vs no")
    print(f"{'='*78}")

    if len(con_pump) > 0 and len(sin_pump) > 0:
        print(f"\n  {'MÉTRICA':<30} {'CON PUMP':>12} {'SIN PUMP':>12} {'DIFERENCIA':>12}")
        print(f"  {'-'*68}")

        # Rango de compresión
        rango_con = con_pump["rango_compresion"].mean()
        rango_sin = sin_pump["rango_compresion"].mean()
        print(f"  {'Rango compresión (%)':<30} {rango_con:>11.2f}% {rango_sin:>11.2f}% {rango_con-rango_sin:>+11.2f}%")

        # Volumen
        vol_con = con_pump["vol_ratio_compresion"].mean()
        vol_sin = sin_pump["vol_ratio_compresion"].mean()
        print(f"  {'Vol ratio compresión':<30} {vol_con:>11.2f}x {vol_sin:>11.2f}x {vol_con-vol_sin:>+11.2f}x")

        # Pump promedio
        print(f"  {'Pump promedio (%)':<30} {con_pump['pump_pct'].mean():>11.2f}%")

    # === Detalle de los pumps fuertes ===
    if len(con_pump) > 0:
        print(f"\n{'='*78}")
        print(f"🚀 TOP 15 PUMPS (post-compresión)")
        print(f"{'='*78}")

        top = con_pump.nlargest(15, "pump_pct")
        print(f"\n  {'SYMBOL':<10} {'COMPRESIÓN':<20} {'RANGO':>8} {'VOL':>8} {'PUMP':>8} {'CAÍDA POST':>12}")
        print(f"  {'-'*68}")
        for _, r in top.iterrows():
            print(f"  {r['symbol']:<10} "
                  f"{r['ts_compresion'].strftime('%m-%d %H:%M'):<20} "
                  f"{r['rango_compresion']:>7.2f}% "
                  f"{r['vol_ratio_compresion']:>7.2f}x "
                  f"{r['pump_pct']:>+7.2f}% "
                  f"{r['caida_post_pump']:>+11.2f}%")

    # === Distribución de pumps ===
    print(f"\n{'='*78}")
    print(f"📊 DISTRIBUCIÓN DE PUMPS POST-COMPRESIÓN")
    print(f"{'='*78}")

    buckets = [
        ("Sin pump (<3%)", 0, 3),
        ("Pump débil (3-5%)", 3, 5),
        ("Pump medio (5-10%)", 5, 10),
        ("Pump fuerte (10-20%)", 10, 20),
        ("Pump violento (>20%)", 20, 999),
    ]

    for nombre, pmin, pmax in buckets:
        n = len(con_pump[(con_pump["pump_pct"] >= pmin) & (con_pump["pump_pct"] < pmax)])
        n_sin = len(sin_pump) if pmin == 0 else 0
        pct = (n / n_total) * 100 if n_total > 0 else 0
        barra = "█" * int(pct / 2)
        print(f"  {nombre:<25} {n:>4} ({pct:>5.1f}%) {barra}")

    # === Veredicto ===
    print(f"\n{'='*78}")
    print(f"🎯 VEREDICTO")
    print(f"{'='*78}")

    if tasa > 60:
        print(f"\n  ✅ LA COMPRESIÓN PRECEDE AL PUMP ({tasa:.1f}%)")
        print(f"  Cuando hay compresión, es probable un pump")
    elif tasa > 40:
        print(f"\n  🟡 LA COMPRESIÓN ES SEÑAL MODERADA ({tasa:.1f}%)")
        print(f"  Puede haber pump, pero no es garantía")
    elif tasa > 25:
        print(f"\n  🟠 LA COMPRESIÓN ES SEÑAL DÉBIL ({tasa:.1f}%)")
        print(f"  No hay ventaja clara sobre el azar")
    else:
        print(f"\n  ❌ LA COMPRESIÓN NO PREDICE PUMP ({tasa:.1f}%)")
        print(f"  La tasa es demasiado baja para ser útil")

    # Tasa base de referencia
    print(f"\n  💡 Referencia: tasa base de pump aleatorio ~20-30%")
    print(f"     Tu tasa: {tasa:.1f}%")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
