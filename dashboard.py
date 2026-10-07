#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DASHBOARD EN VIVO
Evalúa la data actual de los 3 recolectores CMC.
Muestra: alertas, casi-alertas, estado del mercado, frescura.
"""

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
# EXCLUSIONES
# ============================================================

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
# UMBRALES (mismos que el bot de alertas)
# ============================================================

UMBRAL_CAMBIO_MIN   = 1.5
UMBRAL_CAMBIO_4H    = 5.0
UMBRAL_CAMBIO_24H   = 15.0
UMBRAL_VOL_RATIO    = 1.4
UMBRAL_VOL_ABS      = 500_000

# Para "casi-alertas" (a X% del umbral)
MARGEN_CASI = 0.7   # 0.7 = 70% del umbral


# ============================================================
# UTILIDADES
# ============================================================

def linea(char="─", n=95):
    print(char * n)


def header(texto):
    print()
    linea("═")
    print(f"  {texto}")
    linea("═")


def edad_min(ts):
    ahora = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (ahora - ts).total_seconds() / 60


def formato_edad(minutos):
    if minutos < 1:
        return f"{minutos*60:.0f}s"
    if minutos < 60:
        return f"{minutos:.1f}m"
    return f"{minutos/60:.1f}h"


# ============================================================
# CARGA
# ============================================================

def cargar_datos():
    header("📥 DESCARGANDO DATA DE LOS RECOLECTORES")
    
    dfs = []
    estados = []
    
    for nombre, url in CSV_URLS.items():
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            from io import StringIO
            df = pd.read_csv(StringIO(r.text))
            df["_fuente"] = nombre
            dfs.append(df)
            
            ts_max = pd.to_datetime(df["timestamp"], utc=True).max()
            edad = edad_min(ts_max)
            estados.append((nombre, len(df), edad))
            
            print(f"  ✅ {nombre:<10} {len(df):>7,} filas  |  últ. dato: hace {formato_edad(edad)}")
            
        except Exception as e:
            print(f"  ❌ {nombre:<10} ERROR: {str(e)[:60]}")
            estados.append((nombre, 0, None))
    
    if not dfs:
        sys.exit("\n❌ No se pudo cargar ningún CSV")
    
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    
    for c in ["price", "volume_24h", "percent_change_24h", "cmc_rank", "market_cap"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    
    df = df.dropna(subset=["timestamp", "price", "symbol"])
    df = df[df["price"] > 0]
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    
    return df, estados


# ============================================================
# EVALUACIÓN DE MONEDA
# ============================================================

def evaluar_moneda(df, symbol):
    """
    Retorna dict con la evaluación.
    tipo: 'alerta' | 'casi' | 'normal'
    """
    g = df[df["symbol"] == symbol].sort_values("timestamp")
    if len(g) < 60:
        return None
    
    ts_max = g["timestamp"].max()
    
    # Datos básicos
    mc = g.iloc[-1].get("market_cap", 0) or 0
    rank = g.iloc[-1].get("cmc_rank", 9999)
    precio = g.iloc[-1]["price"]
    
    # Ventana 1h
    corte_1h = ts_max - timedelta(hours=1)
    rec_1h = g[g["timestamp"] >= corte_1h]
    if len(rec_1h) < 3:
        return None
    
    p_ini_1h = rec_1h.iloc[0]["price"]
    if p_ini_1h <= 0:
        return None
    cambio_1h = ((precio - p_ini_1h) / p_ini_1h) * 100
    
    # Ventana 4h
    corte_4h = ts_max - timedelta(hours=4)
    rec_4h = g[g["timestamp"] >= corte_4h]
    if len(rec_4h) < 10:
        return None
    p_ini_4h = rec_4h.iloc[0]["price"]
    cambio_4h = ((precio - p_ini_4h) / p_ini_4h) * 100 if p_ini_4h > 0 else 0
    
    # Ventana 24h
    corte_24h = ts_max - timedelta(hours=24)
    rec_24h = g[g["timestamp"] >= corte_24h]
    p_ini_24h = rec_24h.iloc[0]["price"] if len(rec_24h) > 0 else p_ini_4h
    cambio_24h = ((precio - p_ini_24h) / p_ini_24h) * 100 if p_ini_24h > 0 else 0
    
    # Volumen
    v_base = g["volume_24h"].tail(20).mean()
    v_ult = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0
    
    # ¿Es alerta real?
    es_alerta = (
        cambio_1h >= UMBRAL_CAMBIO_MIN and
        cambio_4h <= UMBRAL_CAMBIO_4H and
        cambio_24h <= UMBRAL_CAMBIO_24H and
        vol_ratio >= UMBRAL_VOL_RATIO and
        v_ult >= UMBRAL_VOL_ABS
    )
    
    # ¿Es casi-alerta? (está cerca pero no cruza)
    es_casi = (
        not es_alerta and
        cambio_1h >= UMBRAL_CAMBIO_MIN * MARGEN_CASI and
        vol_ratio >= UMBRAL_VOL_RATIO * MARGEN_CASI
    )
    
    return {
        "symbol": symbol,
        "rank": int(rank),
        "mc_m": mc / 1e6,
        "precio": precio,
        "cambio_1h": cambio_1h,
        "cambio_4h": cambio_4h,
        "cambio_24h": cambio_24h,
        "vol_ratio": vol_ratio,
        "vol_m": v_ult / 1e6,
        "es_alerta": es_alerta,
        "es_casi": es_casi,
        "ts": ts_max,
    }


# ============================================================
# REPORTES
# ============================================================

def reportar_frescura(estados):
    header("🕐 FRESCURA DE LA DATA")
    
    print(f"\n  {'FUENTE':<12} {'EDAD':>10}  ESTADO")
    linea()
    
    for nombre, filas, edad in estados:
        if edad is None:
            print(f"  {nombre:<12} {'ERROR':>10}  ❌ Sin datos")
            continue
        
        if edad < 6:
            estado = "✅ FRESCA"
        elif edad < 12:
            estado = "🟡 aceptable"
        elif edad < 30:
            estado = "⚠️  vieja"
        else:
            estado = "🔴 MUY VIEJA"
        
        print(f"  {nombre:<12} {formato_edad(edad):>10}  {estado}")


def reportar_alertas(evaluaciones):
    header("🚨 ALERTAS ACTIVAS (disparan ahora)")
    
    alertas = [e for e in evaluaciones if e and e["es_alerta"]]
    
    if not alertas:
        print("\n  ✅ Ninguna alerta activa en este momento\n")
        return
    
    alertas.sort(key=lambda x: -x["cambio_1h"])
    
    print(f"\n  Total: {len(alertas)} alertas\n")
    print(f"  {'SYMBOL':<10} {'RANK':>5} {'1H%':>8} {'4H%':>8} {'24H%':>8} "
          f"{'VOL':>7} {'MC $M':>10} {'PRECIO':>14}")
    linea()
    
    for a in alertas:
        print(f"  {a['symbol']:<10} {a['rank']:>5} "
              f"{a['cambio_1h']:>+7.2f}% {a['cambio_4h']:>+7.2f}% {a['cambio_24h']:>+7.2f}% "
              f"{a['vol_ratio']:>6.2f}x {a['mc_m']:>9.1f}M "
              f"${a['precio']:>12.6f}")


def reportar_casi(evaluaciones):
    header("⏳ CASI-ALERTAS (a punto de disparar)")
    
    casi = [e for e in evaluaciones if e and e["es_casi"]]
    
    if not casi:
        print("\n  ✅ Ninguna casi-alerta\n")
        return
    
    casi.sort(key=lambda x: -x["cambio_1h"])
    
    print(f"\n  Total: {len(casi)} monedas cerca del umbral\n")
    print(f"  {'SYMBOL':<10} {'RANK':>5} {'1H%':>8} {'VOL':>7} "
          f"{'FALTA 1H':>10} {'FALTA VOL':>10}")
    linea()
    
    for c in casi:
        falta_1h = max(0, UMBRAL_CAMBIO_MIN - c["cambio_1h"])
        falta_vol = max(0, UMBRAL_VOL_RATIO - c["vol_ratio"])
        
        print(f"  {c['symbol']:<10} {c['rank']:>5} "
              f"{c['cambio_1h']:>+7.2f}% {c['vol_ratio']:>6.2f}x "
              f"{falta_1h:>+9.2f}% {falta_vol:>+9.2f}x")


def reportar_mercado(evaluaciones):
    header("📊 ESTADO GENERAL DEL MERCADO")
    
    vals = [e for e in evaluaciones if e]
    
    if not vals:
        print("\n  Sin datos\n")
        return
    
    # Contadores
    n_alertas = sum(1 for v in vals if v["es_alerta"])
    n_casi = sum(1 for v in vals if v["es_casi"])
    n_subiendo = sum(1 for v in vals if v["cambio_1h"] > 0)
    n_bajando = sum(1 for v in vals if v["cambio_1h"] < 0)
    
    cambio_1h_medio = sum(v["cambio_1h"] for v in vals) / len(vals)
    cambio_24h_medio = sum(v["cambio_24h"] for v in vals) / len(vals)
    
    vol_ratio_medio = sum(v["vol_ratio"] for v in vals) / len(vals)
    
    print(f"\n  Monedas evaluadas:      {len(vals)}")
    print(f"  Subiendo (1h):          {n_subiendo} ({n_subiendo/len(vals)*100:.1f}%)")
    print(f"  Bajando (1h):           {n_bajando} ({n_bajando/len(vals)*100:.1f}%)")
    print(f"  Cambio 1h promedio:     {cambio_1h_medio:+.2f}%")
    print(f"  Cambio 24h promedio:    {cambio_24h_medio:+.2f}%")
    print(f"  Vol ratio promedio:     {vol_ratio_medio:.2f}x")
    print(f"  Alertas activas:        {n_alertas}")
    print(f"  Casi-alertas:           {n_casi}")
    
    # Sentimiento
    print()
    if cambio_1h_medio > 1:
        print("  🔥 Sentimiento: ALCISTA FUERTE")
    elif cambio_1h_medio > 0.3:
        print("  🟢 Sentimiento: ALCISTA")
    elif cambio_1h_medio > -0.3:
        print("  ⚪ Sentimiento: NEUTRAL")
    elif cambio_1h_medio > -1:
        print("  🟡 Sentimiento: BAJISTA")
    else:
        print("  🔴 Sentimiento: BAJISTA FUERTE")


def reportar_top(evaluaciones, n=15):
    header(f"🏆 TOP {n} MOVERS (1h)")
    
    vals = [e for e in evaluaciones if e]
    vals.sort(key=lambda x: -x["cambio_1h"])
    
    print(f"\n  {'#':>3} {'SYMBOL':<10} {'RANK':>5} {'1H%':>8} {'4H%':>8} "
          f"{'24H%':>8} {'VOL':>7} {'MC $M':>10}")
    linea()
    
    for i, v in enumerate(vals[:n], 1):
        print(f"  {i:>3} {v['symbol']:<10} {v['rank']:>5} "
              f"{v['cambio_1h']:>+7.2f}% {v['cambio_4h']:>+7.2f}% "
              f"{v['cambio_24h']:>+7.2f}% {v['vol_ratio']:>6.2f}x "
              f"{v['mc_m']:>9.1f}M")


# ============================================================
# MAIN
# ============================================================

def main():
    ahora = datetime.now(timezone.utc)
    ahora_lima = ahora - timedelta(hours=5)
    
    print()
    linea("═")
    print(f"  📊 DASHBOARD EN VIVO — ANÁLISIS DE RECOLECTORES CMC")
    print(f"     UTC:  {ahora.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"     Lima: {ahora_lima.strftime('%Y-%m-%d %H:%M:%S')}")
    linea("═")
    
    # Cargar
    df, estados = cargar_datos()
    
    print(f"\n  📊 Total combinado: {len(df):,} filas | {df['symbol'].nunique()} monedas")
    
    # Evaluar todas las monedas
    print(f"\n  🔍 Evaluando todas las monedas...")
    
    evaluaciones = []
    for sym in df["symbol"].unique():
        if sym.upper() in EXCLUIR:
            continue
        r = evaluar_moneda(df, sym)
        if r:
            evaluaciones.append(r)
    
    print(f"  ✅ {len(evaluaciones)} monedas evaluadas")
    
    # Reportes
    reportar_frescura(estados)
    reportar_mercado(evaluaciones)
    reportar_alertas(evaluaciones)
    reportar_casi(evaluaciones)
    reportar_top(evaluaciones, n=15)
    
    print()
    linea("═")
    print("  ✅ DASHBOARD COMPLETADO")
    linea("═")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
