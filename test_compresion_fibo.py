#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST: Compresión → Fibo 50% → Rebote
Analiza las monedas que ya pumpearon para validar la teoría.
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

# Monedas que pumpearon (para analizar)
MONEDAS_TEST = [
    "SAND", "FUN", "RAY", "PONS", "PEAQ", "MANA",
    "ORCA", "FLUID", "MET", "W", "CRV", "CVX",
    "STONK", "CHIP", "CASHCAT", "JTO", "TIA", "ZBCN",
]


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
# ANÁLISIS DE COMPRESIÓN
# ============================================================

def detectar_compresion(g, idx, ventana_velas=12):
    """
    Analiza si hay compresión en las velas ANTES de idx.
    ventana_velas = 12 (aprox 1h con snapshots cada 5 min)
    """
    if idx < ventana_velas:
        return None

    ventana = g.iloc[idx - ventana_velas:idx]
    if len(ventana) < ventana_velas:
        return None

    precios = ventana["price"].tolist()
    p_min = min(precios)
    p_max = max(precios)
    if p_min <= 0:
        return None

    rango_pct = ((p_max - p_min) / p_min) * 100

    # Precios deben estar comprimidos (rango <3%)
    if rango_pct > 3.0:
        return None

    # Volumen promedio de la compresión
    v_prom = ventana["volume_24h"].mean()

    return {
        "idx_inicio": idx - ventana_velas,
        "idx_fin": idx,
        "p_min": p_min,
        "p_max": p_max,
        "rango_pct": rango_pct,
        "v_prom": v_prom,
        "fibo_50": (p_max + p_min) / 2,  # 50% de la compresión
        "fibo_618": p_max - (p_max - p_min) * 0.618,  # 61.8% desde arriba
        "fibo_382": p_min + (p_max - p_min) * 0.382,  # 38.2% desde abajo
    }


def detectar_pump(g, idx, ventana_velas=12):
    """Detecta si hay un pump en las velas siguientes."""
    if idx + ventana_velas > len(g):
        return None

    ventana = g.iloc[idx:idx + ventana_velas]
    if len(ventana) < ventana_velas:
        return None

    p_ini = ventana.iloc[0]["price"]
    p_max = ventana["price"].max()
    if p_ini <= 0:
        return None

    pump_pct = ((p_max - p_ini) / p_ini) * 100

    # Debe ser pump real (>5%)
    if pump_pct < 5.0:
        return None

    # Timestamp del máximo
    idx_max = ventana["price"].idxmax()
    ts_max = g.loc[idx_max, "timestamp"]

    return {
        "pump_pct": pump_pct,
        "p_ini": p_ini,
        "p_max": p_max,
        "ts_max": ts_max,
        "idx_max": idx_max,
    }


def detectar_regreso_fibo(g, idx_max, compresion, ventana_velas=36):
    """
    Detecta si después del pump, el precio regresa al Fibo 50% de la compresión.
    ventana_velas = 36 (aprox 3h con snapshots cada 5 min)
    """
    if idx_max + ventana_velas > len(g):
        return None

    ventana = g.iloc[idx_max:idx_max + ventana_velas]
    if len(ventana) < 10:
        return None

    fibo_50 = compresion["fibo_50"]
    fibo_618 = compresion["fibo_618"]
    fibo_382 = compresion["fibo_382"]

    # Buscar si el precio tocó el Fibo 50% (con margen de ±0.5%)
    margen = 0.005
    fibo_50_min = fibo_50 * (1 - margen)
    fibo_50_max = fibo_50 * (1 + margen)

    toco_50 = False
    toco_618 = False
    toco_382 = False

    for _, row in ventana.iterrows():
        p = row["price"]
        if fibo_50_min <= p <= fibo_50_max:
            toco_50 = True
        if abs(p - fibo_618) / fibo_618 < margen:
            toco_618 = True
        if abs(p - fibo_382) / fibo_382 < margen:
            toco_382 = True

    # Detectar rebote post-Fibo
    p_min_post = ventana["price"].min()
    p_fin = ventana.iloc[-1]["price"]
    rebote_post = ((p_fin - p_min_post) / p_min_post) * 100 if p_min_post > 0 else 0

    return {
        "toco_50": toco_50,
        "toco_618": toco_618,
        "toco_382": toco_382,
        "p_min_post": p_min_post,
        "p_fin": p_fin,
        "rebote_post": rebote_post,
    }


def analizar_moneda(df, symbol):
    """Analiza una moneda completa buscando el patrón compresión→pump→fibo."""
    g = df[df["symbol"] == symbol].sort_values("timestamp").reset_index(drop=True)
    if len(g) < 100:
        return None

    resultados = []

    # Buscar compresiones seguidas de pump
    for idx in range(20, len(g) - 50):
        comp = detectar_compresion(g, idx, 12)
        if not comp:
            continue

        pump = detectar_pump(g, idx, 12)
        if not pump:
            continue

        # Tenemos compresión + pump → analizar regreso
        regreso = detectar_regreso_fibo(g, pump["idx_max"], comp, 36)
        if not regreso:
            continue

        resultados.append({
            "symbol": symbol,
            "idx_compresion": comp["idx_fin"],
            "ts_compresion": g.iloc[comp["idx_fin"]]["timestamp"],
            "rango_compresion": comp["rango_pct"],
            "pump_pct": pump["pump_pct"],
            "ts_max": pump["ts_max"],
            "fibo_50": comp["fibo_50"],
            "toco_50": regreso["toco_50"],
            "toco_618": regreso["toco_618"],
            "rebote_post": regreso["rebote_post"],
        })

    return resultados


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 78)
    print("🔬 TEST: Compresión → Pump → Regreso a Fibo 50%")
    print("=" * 78)

    df = cargar_datos()
    print(f"📊 {len(df):,} filas | {df['symbol'].nunique()} monedas")

    print(f"\n🔍 Analizando {len(MONEDAS_TEST)} monedas...")

    todos_resultados = []
    for sym in MONEDAS_TEST:
        r = analizar_moneda(df, sym)
        if r:
            todos_resultados.extend(r)

    if not todos_resultados:
        print("\n⚪ No se detectó el patrón en ninguna moneda")
        return

    rdf = pd.DataFrame(todos_resultados)

    print(f"\n{'='*78}")
    print(f"📊 RESULTADOS: {len(rdf)} casos encontrados")
    print(f"{'='*78}")

    print(f"\n{'SYMBOL':<10} {'COMPRESION':<20} {'PUMP':>8} {'FIBO 50':>12} {'TOCÓ 50%':>10} {'REBOTE':>10}")
    print("-" * 78)
    for _, r in rdf.iterrows():
        toco = "✅ SÍ" if r["toco_50"] else "❌ No"
        print(f"{r['symbol']:<10} "
              f"{r['ts_compresion'].strftime('%m-%d %H:%M'):<20} "
              f"{r['pump_pct']:>+7.2f}% "
              f"${r['fibo_50']:>10.6f} "
              f"{toco:>10} "
              f"{r['rebote_post']:>+9.2f}%")

    # Estadísticas
    print(f"\n{'='*78}")
    print(f"📈 ESTADÍSTICAS")
    print(f"{'='*78}")

    n_total = len(rdf)
    n_toco_50 = rdf["toco_50"].sum()
    n_toco_618 = rdf["toco_618"].sum()

    print(f"\n  Total casos:                {n_total}")
    print(f"  Tocó Fibo 50%:              {n_toco_50} ({n_toco_50/n_total*100:.1f}%)")
    print(f"  Tocó Fibo 61.8%:            {n_toco_618} ({n_toco_618/n_total*100:.1f}%)")

    if n_toco_50 > 0:
        rebotes = rdf[rdf["toco_50"]]["rebote_post"]
        print(f"\n  Rebote promedio post-Fibo:  {rebotes.mean():+.2f}%")
        print(f"  Rebote mediano post-Fibo:   {rebotes.median():+.2f}%")
        print(f"  Rebote máximo:              {rebotes.max():+.2f}%")
        print(f"  Rebote mínimo:              {rebotes.min():+.2f}%")

    # Veredicto
    print(f"\n{'='*78}")
    print(f"🎯 VEREDICTO")
    print(f"{'='*78}")

    tasa = n_toco_50 / n_total * 100 if n_total > 0 else 0

    if tasa > 70:
        print(f"\n  ✅ TEORÍA CONFIRMADA ({tasa:.1f}%)")
        print(f"  El precio SUELE regresar al Fibo 50% de la compresión")
    elif tasa > 50:
        print(f"\n  🟡 TEORÍA PARCIALMENTE VÁLIDA ({tasa:.1f}%)")
        print(f"  El precio regresa al Fibo 50% en más de la mitad de los casos")
    else:
        print(f"\n  ❌ TEORÍA NO CONFIRMADA ({tasa:.1f}%)")
        print(f"  El precio NO suele regresar al Fibo 50% de la compresión")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
