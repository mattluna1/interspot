#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DASHBOARD EN VIVO — Corre en GitHub Actions
Descarga CSV de los 3 recolectores y muestra el estado actual.
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

UMBRAL_CAMBIO_MIN   = 1.5
UMBRAL_CAMBIO_4H    = 5.0
UMBRAL_CAMBIO_24H   = 15.0
UMBRAL_VOL_RATIO    = 1.4
UMBRAL_VOL_ABS      = 500_000

MARGEN_CASI = 0.7
MIN_SNAPSHOTS = 40
MAX_EDAD_ULTIMO_MIN = 60   # descarta si último snapshot >60 min

ENVIAR_TELEGRAM = os.getenv("DASHBOARD_TELEGRAM", "0") == "1"


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
# HELPERS
# ============================================================

def linea(c="─", n=95):
    print(c * n)


def header(t):
    print()
    linea("═")
    print(f"  {t}")
    linea("═")


def edad_min(ts):
    ahora = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (ahora - ts).total_seconds() / 60


def formato_edad(m):
    if m < 1:
        return f"{m*60:.0f}s"
    if m < 60:
        return f"{m:.1f}m"
    return f"{m/60:.1f}h"


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
        sys.exit("❌ No se pudo cargar ningún CSV")

    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    for c in ["price", "volume_24h", "percent_change_24h", "cmc_rank", "market_cap"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["timestamp", "price", "symbol"])
    df = df[df["price"] > 0]

    # ✅ FIX 1: dedup por timestamp + symbol (keep=last prioriza datos más recientes)
    df = df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")

    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return df, estados


# ============================================================
# EVALUACIÓN
# ============================================================

def evaluar_moneda(df, symbol):
    g = df[df["symbol"] == symbol].sort_values("timestamp")
    if len(g) < MIN_SNAPSHOTS:
        return None

    ts_max = g["timestamp"].max()

    # ✅ FIX 2: descartar si el último snapshot es muy viejo
    if edad_min(ts_max) > MAX_EDAD_ULTIMO_MIN:
        return None

    mc = g.iloc[-1].get("market_cap", 0) or 0
    rank = g.iloc[-1].get("cmc_rank", 9999)
    precio = g.iloc[-1]["price"]

    corte_1h = ts_max - timedelta(hours=1)
    rec_1h = g[g["timestamp"] >= corte_1h]
    if len(rec_1h) < 3:
        return None
    p_ini_1h = rec_1h.iloc[0]["price"]
    if p_ini_1h <= 0:
        return None
    cambio_1h = ((precio - p_ini_1h) / p_ini_1h) * 100

    corte_4h = ts_max - timedelta(hours=4)
    rec_4h = g[g["timestamp"] >= corte_4h]
    if len(rec_4h) < 6:
        return None
    p_ini_4h = rec_4h.iloc[0]["price"]
    cambio_4h = ((precio - p_ini_4h) / p_ini_4h) * 100 if p_ini_4h > 0 else 0

    corte_24h = ts_max - timedelta(hours=24)
    rec_24h = g[g["timestamp"] >= corte_24h]
    p_ini_24h = rec_24h.iloc[0]["price"] if len(rec_24h) > 0 else p_ini_4h
    cambio_24h = ((precio - p_ini_24h) / p_ini_24h) * 100 if p_ini_24h > 0 else 0

    v_base = g["volume_24h"].tail(20).mean()
    v_ult = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0

    es_alerta = (
        cambio_1h >= UMBRAL_CAMBIO_MIN and
        cambio_4h <= UMBRAL_CAMBIO_4H and
        cambio_24h <= UMBRAL_CAMBIO_24H and
        vol_ratio >= UMBRAL_VOL_RATIO and
        v_ult >= UMBRAL_VOL_ABS
    )
    es_casi = (
        not es_alerta and
        cambio_1h >= UMBRAL_CAMBIO_MIN * MARGEN_CASI and
        vol_ratio >= UMBRAL_VOL_RATIO * MARGEN_CASI
    )

    return {
        "symbol": symbol, "rank": int(rank), "mc_m": mc / 1e6, "precio": precio,
        "cambio_1h": cambio_1h, "cambio_4h": cambio_4h, "cambio_24h": cambio_24h,
        "vol_ratio": vol_ratio, "vol_m": v_ult / 1e6,
        "es_alerta": es_alerta, "es_casi": es_casi, "ts": ts_max,
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
            est = "✅ FRESCA"
        elif edad < 12:
            est = "🟡 aceptable"
        elif edad < 30:
            est = "⚠️  vieja"
        else:
            est = "🔴 MUY VIEJA"
        print(f"  {nombre:<12} {formato_edad(edad):>10}  {est}")


def reportar_mercado(evals):
    header("📊 ESTADO GENERAL DEL MERCADO")
    vals = [e for e in evals if e]
    if not vals:
        print("\n  Sin datos\n")
        return

    n_alertas = sum(1 for v in vals if v["es_alerta"])
    n_casi = sum(1 for v in vals if v["es_casi"])
    n_sub = sum(1 for v in vals if v["cambio_1h"] > 0)
    n_baj = sum(1 for v in vals if v["cambio_1h"] < 0)

    cambio_1h_med = sum(v["cambio_1h"] for v in vals) / len(vals)
    cambio_24h_med = sum(v["cambio_24h"] for v in vals) / len(vals)
    vol_ratio_med = sum(v["vol_ratio"] for v in vals) / len(vals)

    print(f"\n  Monedas evaluadas:      {len(vals)}")
    print(f"  Subiendo (1h):          {n_sub} ({n_sub/len(vals)*100:.1f}%)")
    print(f"  Bajando (1h):           {n_baj} ({n_baj/len(vals)*100:.1f}%)")
    print(f"  Cambio 1h promedio:     {cambio_1h_med:+.2f}%")
    print(f"  Cambio 24h promedio:    {cambio_24h_med:+.2f}%")
    print(f"  Vol ratio promedio:     {vol_ratio_med:.2f}x")
    print(f"  Alertas activas:        {n_alertas}")
    print(f"  Casi-alertas:           {n_casi}")

    print()
    if cambio_1h_med > 1:
        print("  🔥 Sentimiento: ALCISTA FUERTE")
    elif cambio_1h_med > 0.3:
        print("  🟢 Sentimiento: ALCISTA")
    elif cambio_1h_med > -0.3:
        print("  ⚪ Sentimiento: NEUTRAL")
    elif cambio_1h_med > -1:
        print("  🟡 Sentimiento: BAJISTA")
    else:
        print("  🔴 Sentimiento: BAJISTA FUERTE")


def reportar_alertas(evals):
    header("🚨 ALERTAS ACTIVAS (disparan ahora)")
    alertas = [e for e in evals if e and e["es_alerta"]]
    if not alertas:
        print("\n  ✅ Ninguna alerta activa\n")
        return
    alertas.sort(key=lambda x: -x["cambio_1h"])
    print(f"\n  Total: {len(alertas)}\n")
    print(f"  {'SYMBOL':<10} {'RANK':>5} {'1H%':>8} {'4H%':>8} {'24H%':>8} "
          f"{'VOL':>7} {'MC $M':>10} {'PRECIO':>14}")
    linea()
    for a in alertas:
        print(f"  {a['symbol']:<10} {a['rank']:>5} "
              f"{a['cambio_1h']:>+7.2f}% {a['cambio_4h']:>+7.2f}% {a['cambio_24h']:>+7.2f}% "
              f"{a['vol_ratio']:>6.2f}x {a['mc_m']:>9.1f}M ${a['precio']:>12.6f}")


def reportar_casi(evals):
    header("⏳ CASI-ALERTAS (a punto de disparar)")
    casi = [e for e in evals if e and e["es_casi"]]
    if not casi:
        print("\n  ✅ Ninguna casi-alerta\n")
        return
    casi.sort(key=lambda x: -x["cambio_1h"])
    print(f"\n  Total: {len(casi)}\n")
    print(f"  {'SYMBOL':<10} {'RANK':>5} {'1H%':>8} {'VOL':>7} {'FALTA 1H':>10} {'FALTA VOL':>10}")
    linea()
    for c in casi:
        falta_1h = max(0, UMBRAL_CAMBIO_MIN - c["cambio_1h"])
        falta_vol = max(0, UMBRAL_VOL_RATIO - c["vol_ratio"])
        print(f"  {c['symbol']:<10} {c['rank']:>5} "
              f"{c['cambio_1h']:>+7.2f}% {c['vol_ratio']:>6.2f}x "
              f"{falta_1h:>+9.2f}% {falta_vol:>+9.2f}x")


def reportar_top(evals, n=15):
    header(f"🏆 TOP {n} MOVERS (1h)")
    vals = [e for e in evals if e]
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
# TELEGRAM RESUMEN
# ============================================================

def enviar_resumen_telegram(evals, estados):
    vals = [e for e in evals if e]
    if not vals:
        return

    n_alertas = sum(1 for v in vals if v["es_alerta"])
    n_casi = sum(1 for v in vals if v["es_casi"])
    cambio_1h_med = sum(v["cambio_1h"] for v in vals) / len(vals)

    if cambio_1h_med > 0.3:
        sent = "🟢 ALCISTA"
    elif cambio_1h_med < -0.3:
        sent = "🔴 BAJISTA"
    else:
        sent = "⚪ NEUTRAL"

    alertas = sorted([e for e in vals if e["es_alerta"]], key=lambda x: -x["cambio_1h"])
    lineas_alertas = ""
    for a in alertas[:5]:
        lineas_alertas += f"\n  {a['symbol']} +{a['cambio_1h']:.2f}% (vol {a['vol_ratio']:.2f}x)"
    if not lineas_alertas:
        lineas_alertas = "\n  (ninguna)"

    casi = sorted([e for e in vals if e["es_casi"]], key=lambda x: -x["cambio_1h"])
    lineas_casi = ""
    for c in casi[:5]:
        lineas_casi += f"\n  {c['symbol']} +{c['cambio_1h']:.2f}% (vol {c['vol_ratio']:.2f}x)"
    if not lineas_casi:
        lineas_casi = "\n  (ninguna)"

    ahora_lima = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%H:%M")

    msg = (
        f"📊 DASHBOARD CMC\n"
        f"🕐 {ahora_lima} Lima\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Sentimiento: {sent}\n"
        f"Cambio 1h medio: {cambio_1h_med:+.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🚨 Alertas: {n_alertas}{lineas_alertas}\n"
        f"⏳ Casi-alertas: {n_casi}{lineas_casi}"
    )

    if enviar_telegram(msg):
        print("\n📱 Resumen enviado a Telegram")


# ============================================================
# MAIN
# ============================================================

def main():
    ahora = datetime.now(timezone.utc)
    ahora_lima = ahora - timedelta(hours=5)

    print()
    linea("═")
    print(f"  📊 DASHBOARD EN VIVO — CMC RECOLECTORES")
    print(f"     UTC:  {ahora.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"     Lima: {ahora_lima.strftime('%Y-%m-%d %H:%M:%S')}")
    linea("═")

    df, estados = cargar_datos()
    print(f"\n  📊 Total: {len(df):,} filas | {df['symbol'].nunique()} monedas")

    print(f"\n  🔍 Evaluando monedas...")
    evals = []
    for sym in df["symbol"].unique():
        if sym.upper() in EXCLUIR:
            continue
        r = evaluar_moneda(df, sym)
        if r:
            evals.append(r)
    print(f"  ✅ {len(evals)} monedas evaluadas")

    reportar_frescura(estados)
    reportar_mercado(evals)
    reportar_alertas(evals)
    reportar_casi(evals)
    reportar_top(evals, n=15)

    if ENVIAR_TELEGRAM:
        enviar_resumen_telegram(evals, estados)

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
