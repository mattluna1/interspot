#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ALERTA V5 — SUELO + ARRANQUE + REENTRADA + ACUMULACIÓN + TCB (2 etapas) + BTC 6 ESTADOS

Señales:
  1. 🎯 SUELO         → caída fuerte + frenado + volumen decreciente
  2. 🟡 TEMPRANA      → arranque con volumen empezando (2.0% / 1.2-1.3x)
  3. 🚨 FUERTE        → arranque confirmado (3.0% / 1.3x+)
  4. 📐 REENTRADA     → rebote en Fibo 38-70%
  5. 🚀 ACUMULACIÓN   → QNT-style (rango comprimido + ruptura)
  6. 🚀 TCB           → dos etapas:
       - TCB_TOQUE: toque al 78.6% (alerta inmediata)
       - TCB_CONFIRMADO: 2 velas después, si hay rebote ≥2%

BTC análisis (6 estados): UP / FLAT / DOWN_SOFT / DOWN / BOTTOM / RECOVERY

Sistema de alias: lee secretos.json o SECRETOS_JSON (GitHub Secrets)
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from io import StringIO

import pandas as pd
import requests


# ============================================================
# SISTEMA DE ALIAS
# ============================================================

def _cargar_secretos():
    data = {}
    if os.path.exists("secretos.json"):
        try:
            with open("secretos.json", "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    if not data:
        raw = os.getenv("SECRETOS_JSON")
        if raw:
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
    real_a_alias = dict(data)
    alias_a_real = {v: k for k, v in data.items()}
    return alias_a_real, real_a_alias


ALIAS_A_REAL, REAL_A_ALIAS = _cargar_secretos()


def a_alias(real):
    if not real:
        return real
    return REAL_A_ALIAS.get(str(real).strip().upper(), str(real).strip().upper())


def a_real(alias):
    if not alias:
        return alias
    return ALIAS_A_REAL.get(str(alias).strip(), str(alias).strip())


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
    "xkqy1", "xkqy2", "xkqy3", "xkqy4", "xkqy5", "xkqy6", "xkqy7", "xkqy8",
    "xkqy9", "xkqya", "xkqyb", "xkqyc", "xkqyd", "xkqye", "xkqyf",
    "xkqyg", "xkqyh", "xkqyi", "xkqyj", "xkqyk", "xkqyl", "xkqym", "xkqyn",
    "xkqyo", "xkqyp", "xkqyq", "xkqyr", "xkqys", "xkqyt", "xkqyu", "xkqyv",
    "xkqyw", "xkqyx", "xkqyy", "xkqyz", "xkqy0", "xkqz1", "xkqz2",
}


# ============================================================
# UMBRALES — ARRANQUE
# ============================================================

TEMPRANA_CAMBIO_1H  = 2.0
TEMPRANA_VOL_MIN    = 1.2
TEMPRANA_VOL_MAX    = 1.3

FUERTE_CAMBIO_1H    = 3.0
FUERTE_VOL_MIN      = 1.3

VOL_ABS_MIN         = 500_000

MAX_CAMBIO_4H       = 5.0
MAX_CAMBIO_24H      = 15.0


# ============================================================
# UMBRALES — REENTRADA FIBO
# ============================================================

PUMP_MIN_PCT        = 30.0
PUMP_VENTANA_H      = 48
FIBO_MIN            = 38.0
FIBO_MAX            = 70.0
FIBO_IDEAL          = 61.8
REBOTE_MIN_PCT      = 5.0
REENTRADA_VOL_MIN   = 1.2


# ============================================================
# UMBRALES — SUELO
# ============================================================

SUELO_CAIDA_24H_MIN     = -10.0
SUELO_FRENADO_LO        = -1.0
SUELO_FRENADO_HI        = 1.0
SUELO_DIST_MIN_MAX      = 3.0
SUELO_VOL_DECRECIENTE   = 0.75
SUELO_RANGO_COMPRESION  = 0.60


# ============================================================
# UMBRALES — ACUMULACIÓN (QNT-style)
# ============================================================

ACUM_RANGO_MIN      = 8.0
ACUM_RANGO_MAX      = 15.0
ACUM_POS_RANGO_MIN  = 85.0
ACUM_VOL_CV_MAX     = 0.35
ACUM_DUR_MIN        = 20
ACUM_RUPTURA_MIN    = 4.0


# ============================================================
# UMBRALES — TCB (retroceso Fibo con confirmación en 2 etapas)
# ============================================================

TCB_FIBO_MIN            = 58.0
TCB_FIBO_MAX            = 82.0
TCB_RALLY_MIN_PCT       = 10.0
TCB_VENTANA_H           = 72
TCB_REBOTE_MIN_PCT      = 2.0      # rebote mínimo para confirmar
TCB_VOL_MIN             = 1.0
TCB_CONFIRMACION_VELAS  = 2        # 2 velas (~10 min) para confirmar rebote


# ============================================================
# FILTROS COMUNES
# ============================================================

MC_MIN              = 50_000_000
MC_MAX              = 5_000_000_000
RANK_MAX            = 280

MAX_POR_TIPO        = 10

BTC_STATE_FILE      = "btc_state.json"
ALERTAS_STATE_FILE  = "alertas_state.json"

COOLDOWN_SUELO          = 6
COOLDOWN_ARRANQUE       = 4
COOLDOWN_REENTRADA      = 8
COOLDOWN_ACUM           = 12
COOLDOWN_TCB_TOQUE      = 4
COOLDOWN_TCB_CONFIRMADO = 12


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
    for intento in range(2):
        try:
            r = requests.post(url, data={"chat_id": chat_id, "text": msg}, timeout=20)
            if r.status_code == 200 and r.json().get("ok", False):
                return True
        except Exception as e:
            print(f"Error Telegram (intento {intento+1}): {e}")
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
            if REAL_A_ALIAS and "symbol" in df.columns:
                df["symbol"] = (
                    df["symbol"].astype(str).str.strip().str.upper()
                    .map(lambda s: a_alias(s))
                )
            dfs.append(df)
            print(f"✅ {nombre}: {len(df):,} filas")
        except Exception as e:
            print(f"⚠️ {nombre}: {str(e)[:80]}")

    if not dfs:
        sys.exit("❌ No se pudo cargar ningún CSV")

    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(
        df["timestamp"], utc=True, errors="coerce", format="ISO8601"
    )

    for c in ["price", "volume_24h", "percent_change_24h", "cmc_rank", "market_cap"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["timestamp", "price", "symbol"])
    df = df[df["price"] > 0]
    df = df.drop_duplicates(subset=["timestamp", "symbol"], keep="last")
    df = df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return df


# ============================================================
# HELPERS
# ============================================================

def es_fresco(ts_max, minutos=60):
    ahora = datetime.now(timezone.utc)
    if ts_max.tzinfo is None:
        ts_max = ts_max.replace(tzinfo=timezone.utc)
    return (ahora - ts_max).total_seconds() / 60 <= minutos


def cumple_calidad(mc, rank):
    if mc < MC_MIN or mc > MC_MAX:
        return False
    if rank > RANK_MAX:
        return False
    return True


def hacer_velas_4h(df):
    """Genera velas de 4h para análisis de ACUM."""
    df = df.copy()
    df["vela"] = df["timestamp"].dt.floor("240min")
    return df.groupby(["symbol", "vela"]).agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("volume_24h", "last"),
        rank=("cmc_rank", "last"),
        mc=("market_cap", "last"),
    ).reset_index().sort_values(["symbol", "vela"]).reset_index(drop=True)


# ============================================================
# ANÁLISIS BTC — 6 ESTADOS
# ============================================================

def analizar_btc(df):
    g = df[df["symbol"] == "xk1"].sort_values("timestamp")
    if len(g) < 40:
        return {"estado": "UNKNOWN", "cambio_1h": 0, "cambio_4h": 0,
                "cambio_24h": 0, "vol_ratio": 0, "precio": 0}

    ts_max = g["timestamp"].max()

    corte_1h = ts_max - timedelta(hours=1)
    rec_1h = g[g["timestamp"] >= corte_1h]
    if len(rec_1h) < 3:
        return {"estado": "UNKNOWN", "cambio_1h": 0, "cambio_4h": 0,
                "cambio_24h": 0, "vol_ratio": 0, "precio": 0}

    p_ini_1h = rec_1h.iloc[0]["price"]
    p_fin = g.iloc[-1]["price"]
    cambio_1h = ((p_fin - p_ini_1h) / p_ini_1h) * 100 if p_ini_1h > 0 else 0

    corte_4h = ts_max - timedelta(hours=4)
    rec_4h = g[g["timestamp"] >= corte_4h]
    p_ini_4h = rec_4h.iloc[0]["price"] if len(rec_4h) > 0 else p_ini_1h
    cambio_4h = ((p_fin - p_ini_4h) / p_ini_4h) * 100 if p_ini_4h > 0 else 0

    corte_24h = ts_max - timedelta(hours=24)
    rec_24h = g[g["timestamp"] >= corte_24h]
    p_ini_24h = rec_24h.iloc[0]["price"] if len(rec_24h) > 0 else p_ini_4h
    cambio_24h = ((p_fin - p_ini_24h) / p_ini_24h) * 100 if p_ini_24h > 0 else 0

    v_base = g["volume_24h"].tail(20).mean()
    v_ult = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0

    p_min_4h = rec_4h["price"].min() if len(rec_4h) > 0 else p_fin
    pos_respecto_min = ((p_fin - p_min_4h) / p_min_4h) * 100 if p_min_4h > 0 else 0

    if cambio_1h > 0.3 and cambio_4h < -0.5 and pos_respecto_min > 1.0:
        estado = "RECOVERY"
    elif -0.3 < cambio_1h < 0.4 and cambio_4h < -1.0:
        estado = "BOTTOM"
    elif cambio_1h < -1.5 or cambio_4h < -3.0:
        estado = "DOWN"
    elif cambio_1h < -0.5:
        estado = "DOWN_SOFT"
    elif cambio_1h > 0.5 and cambio_4h > 0.5:
        estado = "UP"
    else:
        estado = "FLAT"

    return {
        "estado": estado,
        "cambio_1h": cambio_1h,
        "cambio_4h": cambio_4h,
        "cambio_24h": cambio_24h,
        "vol_ratio": vol_ratio,
        "precio": p_fin,
        "p_min_4h": p_min_4h,
        "pos_respecto_min": pos_respecto_min,
    }


def _contexto_btc(btc):
    mapa = {
        "UP":        ("🟢 xk1: subiendo", ""),
        "FLAT":      ("⚪ xk1: neutral", ""),
        "DOWN_SOFT": ("🟡 xk1: bajando suave",
                      "⚠️ Mercado flojo — sé selectivo"),
        "DOWN":      ("🔴 xk1: cayendo fuerte",
                      "🚨 xk1 en caída — no entrar a alts"),
        "BOTTOM":    ("🟣 xk1: fondo — dejó de caer",
                      "⏳ Preparar entradas, esperar confirmación"),
        "RECOVERY":  ("🟢 xk1: recovery — rebotando",
                      "🔥 Momento óptimo para entrar a alts"),
        "UNKNOWN":   ("❓ xk1: sin datos", ""),
    }
    return mapa.get(btc["estado"], ("⚪ xk1: neutral", ""))


# ============================================================
# ESTADO PERSISTENTE
# ============================================================

def cargar_btc_estado_previo():
    if not os.path.exists(BTC_STATE_FILE):
        return None
    try:
        with open(BTC_STATE_FILE, "r") as f:
            data = json.load(f)
        return data.get("estado")
    except Exception:
        return None


def guardar_btc_estado(estado):
    try:
        with open(BTC_STATE_FILE, "w") as f:
            json.dump({
                "estado": estado,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, f)
    except Exception:
        pass


def cargar_alertas_state():
    if not os.path.exists(ALERTAS_STATE_FILE):
        return {}
    try:
        with open(ALERTAS_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def guardar_alertas_state(state):
    try:
        with open(ALERTAS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def paso_cooldown(state, symbol, tipo, horas):
    sym_state = state.get(symbol, {})
    ultimo = sym_state.get(tipo)
    if not ultimo:
        return True
    try:
        t = datetime.fromisoformat(ultimo)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 3600 >= horas
    except Exception:
        return True


def registrar_alerta(state, symbol, tipo):
    if symbol not in state:
        state[symbol] = {}
    state[symbol][tipo] = datetime.now(timezone.utc).isoformat()


# ============================================================
# ALERTA CAMBIO DE ESTADO BTC
# ============================================================

def enviar_alerta_cambio_btc(estado_previo, btc):
    if estado_previo == btc["estado"]:
        return False

    transiciones = {
        ("UP", "FLAT"):          ("🟡", "xk1 PERDIENDO FUERZA",
                                  "Toma ganancias, mercado girando"),
        ("UP", "DOWN_SOFT"):     ("🟡", "xk1 GIRANDO A LA BAJA",
                                  "Cuidado con alts"),
        ("UP", "DOWN"):          ("🔴", "xk1 GIRÓ A LA BAJA",
                                  "🚨 NO ENTRAR A ALTS"),
        ("FLAT", "DOWN_SOFT"):   ("🟡", "xk1 EMPIEZA A CAER",
                                  "Cuidado con alts"),
        ("FLAT", "DOWN"):        ("🔴", "xk1 CAYENDO FUERTE",
                                  "🚨 NO ENTRAR A ALTS"),
        ("DOWN_SOFT", "DOWN"):   ("🔴", "xk1 CAYENDO FUERTE",
                                  "🚨 NO ENTRAR A ALTS"),
        ("DOWN", "BOTTOM"):      ("🟣", "xk1 TOCA FONDO",
                                  "⏳ Preparar entradas en alts"),
        ("DOWN_SOFT", "BOTTOM"): ("🟣", "xk1 TOCA FONDO",
                                  "⏳ Preparar entradas en alts"),
        ("DOWN", "RECOVERY"):    ("🟢", "xk1 REBOTA",
                                  "🔥 MOMENTO ÓPTIMO — entradas en alts"),
        ("BOTTOM", "RECOVERY"):  ("🟢", "xk1 REBOTA",
                                  "🔥 MOMENTO ÓPTIMO — entradas en alts"),
        ("BOTTOM", "UP"):        ("🟢", "xk1 CONFIRMA SUBIDA",
                                  "🟢 Buen momento para alts"),
        ("RECOVERY", "UP"):      ("🟢", "xk1 CONFIRMA SUBIDA",
                                  "🟢 Buen momento para alts"),
        ("RECOVERY", "DOWN"):    ("🔴", "xk1 RECAÓ",
                                  "🚨 Rebote fallido — no entrar"),
    }

    clave = (estado_previo, btc["estado"])
    if clave not in transiciones:
        return False

    emoji, titulo, accion = transiciones[clave]
    hora_lima = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%H:%M")

    msg = (
        f"{emoji} {titulo}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 {a_real('xk1')}: ${btc['precio']:,.2f}\n"
        f"📊 1h: {btc['cambio_1h']:+.2f}% | 4h: {btc['cambio_4h']:+.2f}%\n"
        f"📊 24h: {btc['cambio_24h']:+.2f}% | Vol: {btc['vol_ratio']:.2f}x\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{accion}\n"
        f"🕐 {hora_lima} Lima"
    )

    return enviar_telegram(msg)


# ============================================================
# DETECCIÓN — SUELO
# ============================================================

def detectar_suelo(df, symbol):
    g = df[df["symbol"] == symbol].sort_values("timestamp")
    if len(g) < 40:
        return None

    ts_max = g["timestamp"].max()
    if not es_fresco(ts_max):
        return None

    mc = g.iloc[-1].get("market_cap", 0) or 0
    rank = g.iloc[-1].get("cmc_rank", 9999)
    if not cumple_calidad(mc, rank):
        return None

    corte_4h  = ts_max - timedelta(hours=4)
    corte_12h = ts_max - timedelta(hours=12)
    corte_24h = ts_max - timedelta(hours=24)

    rec_4h  = g[g["timestamp"] >= corte_4h]
    rec_12h = g[g["timestamp"] >= corte_12h]
    rec_24h = g[g["timestamp"] >= corte_24h]

    if len(rec_4h) < 3 or len(rec_24h) < 5:
        return None

    p_ini_24h = rec_24h.iloc[0]["price"]
    p_actual  = g.iloc[-1]["price"]
    p_ini_4h  = rec_4h.iloc[0]["price"]

    if p_ini_24h <= 0 or p_ini_4h <= 0:
        return None

    cambio_24h = ((p_actual - p_ini_24h) / p_ini_24h) * 100
    cambio_4h  = ((p_actual - p_ini_4h) / p_ini_4h) * 100

    if cambio_24h > SUELO_CAIDA_24H_MIN:
        return None
    if cambio_4h < SUELO_FRENADO_LO or cambio_4h > SUELO_FRENADO_HI:
        return None

    p_min_24h = rec_24h["price"].min()
    if p_min_24h <= 0:
        return None
    dist_min = ((p_actual - p_min_24h) / p_min_24h) * 100
    if dist_min > SUELO_DIST_MIN_MAX:
        return None

    if "volume_24h" not in g.columns:
        return None
    rec_12h_ant = rec_12h[rec_12h["timestamp"] < corte_4h]
    if len(rec_12h_ant) < 3:
        return None

    v_reciente = rec_4h["volume_24h"].iloc[-1]
    v_anterior = rec_12h_ant["volume_24h"].mean()
    if v_anterior <= 0:
        return None
    compresion_vol = v_reciente / v_anterior
    if compresion_vol > SUELO_VOL_DECRECIENTE:
        return None

    rango_reciente = rec_4h["price"].max() - rec_4h["price"].min()
    rango_anterior = (rec_12h_ant["price"].max() - rec_12h_ant["price"].min())
    if rango_anterior <= 0:
        return None
    compresion_rango = rango_reciente / rango_anterior
    if compresion_rango > SUELO_RANGO_COMPRESION:
        return None

    v_ult = g["volume_24h"].iloc[-1]
    if v_ult < VOL_ABS_MIN:
        return None

    return {
        "tipo": "SUELO",
        "symbol": symbol,
        "cambio_24h": cambio_24h,
        "cambio_4h": cambio_4h,
        "precio": p_actual,
        "p_min_24h": p_min_24h,
        "dist_min": dist_min,
        "compresion_vol": compresion_vol,
        "compresion_rango": compresion_rango,
        "vol_ratio": compresion_vol,
        "vol_m": v_ult / 1e6,
        "cmc_rank": int(rank),
        "market_cap_m": mc / 1e6,
        "ts": ts_max,
    }


# ============================================================
# DETECCIÓN — ARRANQUE
# ============================================================

def detectar_arranque(df, symbol):
    g = df[df["symbol"] == symbol].sort_values("timestamp")
    if len(g) < 40:
        return None

    ts_max = g["timestamp"].max()
    if not es_fresco(ts_max):
        return None

    mc = g.iloc[-1].get("market_cap", 0) or 0
    rank = g.iloc[-1].get("cmc_rank", 9999)
    if not cumple_calidad(mc, rank):
        return None

    corte_1h = ts_max - timedelta(hours=1)
    rec_1h = g[g["timestamp"] >= corte_1h]
    if len(rec_1h) < 3:
        return None
    p_ini_1h = rec_1h.iloc[0]["price"]
    p_fin = rec_1h.iloc[-1]["price"]
    if p_ini_1h <= 0:
        return None
    cambio_1h = ((p_fin - p_ini_1h) / p_ini_1h) * 100

    corte_4h = ts_max - timedelta(hours=4)
    rec_4h = g[g["timestamp"] >= corte_4h]
    if len(rec_4h) < 3:
        return None
    p_ini_4h = rec_4h.iloc[0]["price"]
    cambio_4h = ((p_fin - p_ini_4h) / p_ini_4h) * 100 if p_ini_4h > 0 else 0

    corte_24h = ts_max - timedelta(hours=24)
    rec_24h = g[g["timestamp"] >= corte_24h]
    p_ini_24h = rec_24h.iloc[0]["price"] if len(rec_24h) > 0 else p_ini_4h
    cambio_24h = ((p_fin - p_ini_24h) / p_ini_24h) * 100 if p_ini_24h > 0 else 0

    if cambio_4h > MAX_CAMBIO_4H:
        return None
    if cambio_24h > MAX_CAMBIO_24H:
        return None

    v_base = g["volume_24h"].tail(20).mean()
    v_ult = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0
    if v_ult < VOL_ABS_MIN:
        return None

    señal = None
    if cambio_1h >= FUERTE_CAMBIO_1H and vol_ratio >= FUERTE_VOL_MIN:
        señal = "FUERTE"
    elif (cambio_1h >= TEMPRANA_CAMBIO_1H and
          TEMPRANA_VOL_MIN <= vol_ratio < TEMPRANA_VOL_MAX):
        señal = "TEMPRANA"

    if señal is None:
        return None

    return {
        "tipo": "ARRANQUE",
        "señal": señal,
        "symbol": symbol,
        "cambio_1h": cambio_1h,
        "cambio_4h": cambio_4h,
        "cambio_24h": cambio_24h,
        "precio": p_fin,
        "vol_ratio": vol_ratio,
        "vol_m": v_ult / 1e6,
        "ts": ts_max,
        "cmc_rank": int(rank),
        "market_cap_m": mc / 1e6,
    }


# ============================================================
# DETECCIÓN — REENTRADA FIBO
# ============================================================

def detectar_reentrada_fibo(df, symbol):
    g = df[df["symbol"] == symbol].sort_values("timestamp")
    if len(g) < 40:
        return None

    ts_max = g["timestamp"].max()
    if not es_fresco(ts_max):
        return None

    mc = g.iloc[-1].get("market_cap", 0) or 0
    rank = g.iloc[-1].get("cmc_rank", 9999)
    if not cumple_calidad(mc, rank):
        return None

    corte_48 = ts_max - timedelta(hours=PUMP_VENTANA_H)
    rec_48 = g[g["timestamp"] >= corte_48]
    if len(rec_48) < 5:
        return None

    p_min_48 = rec_48["price"].min()
    p_max_48 = rec_48["price"].max()
    p_actual = rec_48.iloc[-1]["price"]

    if p_min_48 <= 0 or p_max_48 <= 0:
        return None

    pump_total = ((p_max_48 - p_min_48) / p_min_48) * 100
    if pump_total < PUMP_MIN_PCT:
        return None

    rango = p_max_48 - p_min_48
    if rango <= 0:
        return None

    fibo_382 = p_max_48 - rango * 0.382
    fibo_500 = p_max_48 - rango * 0.500
    fibo_618 = p_max_48 - rango * 0.618

    pos_actual = ((p_max_48 - p_actual) / rango) * 100
    if not (FIBO_MIN <= pos_actual <= FIBO_MAX):
        return None

    corte_12 = ts_max - timedelta(hours=12)
    rec_12 = g[g["timestamp"] >= corte_12]
    if len(rec_12) < 3:
        return None
    p_min_pullback = rec_12["price"].min()
    if p_min_pullback <= 0:
        return None

    rebote_pct = ((p_actual - p_min_pullback) / p_min_pullback) * 100
    if rebote_pct < REBOTE_MIN_PCT:
        return None

    v_base = g["volume_24h"].tail(20).mean()
    v_ult = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0
    if vol_ratio < REENTRADA_VOL_MIN:
        return None

    distancia_fibo = abs(pos_actual - FIBO_IDEAL)

    return {
        "tipo": "REENTRADA",
        "symbol": symbol,
        "pump_total": pump_total,
        "p_max": p_max_48,
        "p_min": p_min_48,
        "p_actual": p_actual,
        "p_min_pullback": p_min_pullback,
        "fibo_382": fibo_382,
        "fibo_500": fibo_500,
        "fibo_618": fibo_618,
        "pos_actual": pos_actual,
        "rebote_pct": rebote_pct,
        "vol_ratio": vol_ratio,
        "vol_m": v_ult / 1e6,
        "distancia_fibo": distancia_fibo,
        "cmc_rank": int(rank),
        "market_cap_m": mc / 1e6,
        "ts": ts_max,
    }


# ============================================================
# DETECCIÓN — ACUMULACIÓN (QNT-style)
# ============================================================

def detectar_acumulacion(velas_4h, btc_estado):
    """
    Detecta acumulación QNT-style.
    SOLO se busca si BTC está en FLAT, DOWN_SOFT o RECOVERY.
    """
    if btc_estado not in ("FLAT", "DOWN_SOFT", "RECOVERY"):
        return []

    resultados = []

    for sym, g in velas_4h.groupby("symbol"):
        if sym == "xk1" or sym in EXCLUIR:
            continue

        g = g.sort_values("vela").reset_index(drop=True)
        if len(g) < ACUM_DUR_MIN + 5:
            continue

        rank = g.iloc[-1].get("rank", 9999)
        mc = g.iloc[-1].get("mc", 0) or 0
        if not cumple_calidad(mc, rank):
            continue

        ultimas = g.tail(ACUM_DUR_MIN)

        p_min = ultimas["low"].min()
        p_max = ultimas["high"].max()
        if p_min <= 0:
            continue

        rango_pct = ((p_max - p_min) / p_min) * 100
        if rango_pct < ACUM_RANGO_MIN or rango_pct > ACUM_RANGO_MAX:
            continue

        p_actual = ultimas.iloc[-1]["close"]
        if p_max <= p_min:
            continue

        pos_rango = ((p_actual - p_min) / (p_max - p_min)) * 100
        if pos_rango < ACUM_POS_RANGO_MIN:
            continue

        vol_medio = ultimas["volume"].mean()
        vol_std = ultimas["volume"].std()
        vol_cv = vol_std / vol_medio if vol_medio > 0 else 1

        if vol_cv > ACUM_VOL_CV_MAX:
            continue

        cambio_reciente = 0
        if len(g) >= 5:
            cambio_reciente = ((g.iloc[-1]["close"] - g.iloc[-5]["close"])
                               / g.iloc[-5]["close"]) * 100

        if cambio_reciente < ACUM_RUPTURA_MIN and pos_rango < 90:
            continue

        resultados.append({
            "tipo": "ACUMULACION",
            "symbol": sym,
            "rango_pct": rango_pct,
            "pos_rango": pos_rango,
            "vol_medio_m": vol_medio / 1e6,
            "vol_cv": vol_cv,
            "cambio_reciente": cambio_reciente,
            "p_actual": p_actual,
            "p_max": p_max,
            "p_min": p_min,
            "rank": int(rank),
            "mc": mc,
        })

    return resultados


# ============================================================
# DETECCIÓN — TCB (retroceso Fibo con confirmación en 2 etapas)
# ============================================================

def detectar_tcb(df, symbol, btc_estado):
    """
    Detecta TCB en dos etapas:
    1. TCB_TOQUE: el precio toca el 78.6% (alerta inmediata)
    2. TCB_CONFIRMADO: 2 velas después, si hay rebote ≥2%
    Solo si BTC en FLAT, DOWN_SOFT o RECOVERY.
    """
    if btc_estado not in ("FLAT", "DOWN_SOFT", "RECOVERY"):
        return None

    g = df[df["symbol"] == symbol].sort_values("timestamp")
    if len(g) < 50:
        return None

    ts_max = g["timestamp"].max()
    if not es_fresco(ts_max):
        return None

    mc = g.iloc[-1].get("market_cap", 0) or 0
    rank = g.iloc[-1].get("cmc_rank", 9999)
    if not cumple_calidad(mc, rank):
        return None

    # Ventana: últimas 72h
    corte_72 = ts_max - timedelta(hours=TCB_VENTANA_H)
    rec_72 = g[g["timestamp"] >= corte_72].reset_index(drop=True)
    if len(rec_72) < 20:
        return None

    # Rally previo (mín → máx)
    p_min = rec_72["price"].min()
    idx_min = rec_72["price"].idxmin()
    p_max = rec_72.loc[idx_min:]["price"].max()
    idx_max = rec_72.loc[idx_min:]["price"].idxmax()

    if p_min <= 0 or p_max <= p_min:
        return None

    rango = p_max - p_min
    rango_pct = (rango / p_min) * 100

    if rango_pct < TCB_RALLY_MIN_PCT:
        return None

    # Corrección post-rally: mínimo después del máximo
    post_rally = rec_72.loc[idx_max:].copy()
    if len(post_rally) < 5:
        return None

    p_min_corr = post_rally["price"].min()
    if p_min_corr <= 0:
        return None

    # Retroceso Fibo actual
    fibo_retroceso = ((p_max - p_min_corr) / rango) * 100

    # Zona objetivo: 58-82%
    if not (TCB_FIBO_MIN <= fibo_retroceso <= TCB_FIBO_MAX):
        return None

    # Filtro de volumen
    v_base = g["volume_24h"].tail(20).mean()
    v_ult = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0
    if vol_ratio < TCB_VOL_MIN:
        return None

    p_actual = rec_72.iloc[-1]["price"]

    # Clasificar zona
    if fibo_retroceso < 65:
        zona_fibo = "61.8%"
    elif fibo_retroceso < 74:
        zona_fibo = "intermedia"
    else:
        zona_fibo = "78.6%"

    # === DETERMINAR ETAPA ===
    idx_min_corr = post_rally["price"].idxmin()
    idx_min_corr_global = idx_max + (idx_min_corr - idx_max)
    velas_desde_toque = len(rec_72) - 1 - idx_min_corr_global

    # === ETAPA 1: TOQUE (solo 78.6%, primeras 2 velas) ===
    if zona_fibo == "78.6%" and 0 <= velas_desde_toque <= TCB_CONFIRMACION_VELAS:
        return {
            "tipo": "TCB_TOQUE",
            "symbol": symbol,
            "rango_pct": rango_pct,
            "p_min": p_min,
            "p_max": p_max,
            "p_actual": p_actual,
            "p_min_corr": p_min_corr,
            "fibo_retroceso": fibo_retroceso,
            "zona_fibo": zona_fibo,
            "velas_desde_toque": velas_desde_toque,
            "vol_ratio": vol_ratio,
            "vol_m": v_ult / 1e6,
            "cmc_rank": int(rank),
            "market_cap_m": mc / 1e6,
            "ts": ts_max,
        }

    # === ETAPA 2: CONFIRMACIÓN (solo si el toque fue hace ≤2 velas) ===
    if zona_fibo == "78.6%" and velas_desde_toque <= TCB_CONFIRMACION_VELAS + 1:
        post_min = rec_72.loc[idx_min_corr:]["price"]
        if len(post_min) < 2:
            return None

        ventana_rebote = post_min.head(TCB_CONFIRMACION_VELAS + 1)
        p_rebote = ventana_rebote.max()
        rebote_pct = ((p_rebote - p_min_corr) / p_min_corr) * 100

        if rebote_pct >= TCB_REBOTE_MIN_PCT:
            return {
                "tipo": "TCB_CONFIRMADO",
                "symbol": symbol,
                "rango_pct": rango_pct,
                "p_min": p_min,
                "p_max": p_max,
                "p_actual": p_actual,
                "p_min_corr": p_min_corr,
                "p_rebote": p_rebote,
                "fibo_retroceso": fibo_retroceso,
                "zona_fibo": zona_fibo,
                "rebote_pct": rebote_pct,
                "velas_desde_toque": velas_desde_toque,
                "vol_ratio": vol_ratio,
                "vol_m": v_ult / 1e6,
                "cmc_rank": int(rank),
                "market_cap_m": mc / 1e6,
                "ts": ts_max,
            }

    return None


# ============================================================
# ENVÍO DE MENSAJES
# ============================================================

def enviar_alerta_suelo(a, btc):
    hora_lima = (a["ts"] - timedelta(hours=5)).strftime("%H:%M")
    linea_btc, aviso_btc = _contexto_btc(btc)

    msg = (
        f"🎯 SUELO DETECTADO\n"
        f"🪙 {a_real(a['symbol'])} (rank {a['cmc_rank']})\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📉 Caída 24h: {a['cambio_24h']:+.2f}%\n"
        f"📊 Frenado 4h: {a['cambio_4h']:+.2f}%\n"
        f"💰 Precio: ${a['precio']:.6f}\n"
        f"🏦 Cerca del mínimo: +{a['dist_min']:.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Volumen decreciente: {a['compresion_vol']:.2f}x\n"
        f"📊 Rango comprimido: {a['compresion_rango']:.2f}x\n"
        f"💧 Vol: ${a['vol_m']:.1f}M\n"
        f"🏦 MC: ${a['market_cap_m']:.1f}M\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{linea_btc}\n"
        f"🕐 {hora_lima} Lima\n"
        f"⏳ ESPERAR CONFIRMACIÓN de arranque"
    )
    if aviso_btc:
        msg += f"\n{aviso_btc}"

    return enviar_telegram(msg)


def enviar_alerta_arranque(a, btc):
    hora_lima = (a["ts"] - timedelta(hours=5)).strftime("%H:%M")

    if a["señal"] == "FUERTE":
        emoji = "🚨"
        titulo = "ARRANQUE FUERTE"
        nota = "✅ Volumen CONFIRMA"
    else:
        emoji = "🟡"
        titulo = "ARRANQUE TEMPRANO"
        nota = "⚠️ Verificar gráfico"

    linea_btc, aviso_btc = _contexto_btc(btc)

    msg = (
        f"{emoji} {titulo}\n"
        f"🪙 {a_real(a['symbol'])} (rank {a['cmc_rank']})\n"
        f"📈 +{a['cambio_1h']:.2f}% en 1h\n"
        f"📊 4h: {a['cambio_4h']:+.2f}% | 24h: {a['cambio_24h']:+.2f}%\n"
        f"💰 ${a['precio']:.6f}\n"
        f"📊 Vol: {a['vol_ratio']:.2f}x (${a['vol_m']:.1f}M)\n"
        f"🏦 MC: {a['market_cap_m']:.1f}M\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{linea_btc}\n"
        f"🕐 {hora_lima} Lima\n"
        f"{nota}"
    )
    if aviso_btc:
        msg += f"\n{aviso_btc}"

    return enviar_telegram(msg)


def enviar_alerta_reentrada(a, btc):
    hora_lima = (a["ts"] - timedelta(hours=5)).strftime("%H:%M")
    linea_btc, aviso_btc = _contexto_btc(btc)

    msg = (
        f"📐 REENTRADA FIBO\n"
        f"🪙 {a_real(a['symbol'])} (rank {a['cmc_rank']})\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Pump previo 48h: +{a['pump_total']:.1f}%\n"
        f"   Máx: ${a['p_max']:.6f}\n"
        f"   Mín: ${a['p_min']:.6f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Fibo actual: {a['pos_actual']:.1f}% (objetivo 61.8%)\n"
        f"💰 Precio: ${a['p_actual']:.6f}\n"
        f"📉 Niveles clave:\n"
        f"   • 38.2% → ${a['fibo_382']:.6f}\n"
        f"   • 50.0% → ${a['fibo_500']:.6f}\n"
        f"   • 61.8% → ${a['fibo_618']:.6f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Rebote: +{a['rebote_pct']:.2f}% desde pullback\n"
        f"📊 Vol: {a['vol_ratio']:.2f}x (${a['vol_m']:.1f}M)\n"
        f"🏦 MC: ${a['market_cap_m']:.1f}M\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{linea_btc}\n"
        f"🕐 {hora_lima} Lima"
    )
    if aviso_btc:
        msg += f"\n{aviso_btc}"

    return enviar_telegram(msg)


def enviar_alerta_acumulacion(a, btc):
    hora_lima = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%H:%M")
    linea_btc, aviso_btc = _contexto_btc(btc)

    msg = (
        f"🚀 ACUMULACIÓN DETECTADA\n"
        f"🪙 {a_real(a['symbol'])} (rank {a['rank']})\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Rango: {a['rango_pct']:.1f}%\n"
        f"🎯 Posición: {a['pos_rango']:.0f}% del rango\n"
        f"📈 Cambio reciente: {a['cambio_reciente']:+.2f}%\n"
        f"💧 Volumen medio: ${a['vol_medio_m']:.1f}M\n"
        f"📊 Consistencia: {a['vol_cv']:.2f}\n"
        f"💰 Precio: ${a['p_actual']:.6f}\n"
        f"🎯 Resistencia: ${a['p_max']:.6f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{linea_btc}\n"
        f"🕐 {hora_lima} Lima\n"
        f"💡 QNT-style: posible explosión"
    )
    if aviso_btc:
        msg += f"\n{aviso_btc}"

    return enviar_telegram(msg)


def enviar_alerta_tcb(a, btc):
    hora_lima = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%H:%M")
    linea_btc, aviso_btc = _contexto_btc(btc)

    if a["tipo"] == "TCB_TOQUE":
        msg = (
            f"🎯 TOQUE 78.6% — {a_real(a['symbol'])}\n"
            f"🪙 rank {a['cmc_rank']}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Rally previo: +{a['rango_pct']:.1f}%\n"
            f"🎯 Retroceso: {a['fibo_retroceso']:.1f}% (78.6%)\n"
            f"💰 Precio: ${a['p_actual']:.6f}\n"
            f"📉 Mín corrección: ${a['p_min_corr']:.6f}\n"
            f"⏱️ Hace {a['velas_desde_toque']} velas\n"
            f"💧 Vol: ${a['vol_m']:.1f}M | {a['vol_ratio']:.2f}x\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👀 REVISA EL GRÁFICO\n"
            f"   Esperando confirmación (2 velas)\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{linea_btc}\n"
            f"🕐 {hora_lima} Lima"
        )
    else:
        msg = (
            f"✅ TCB CONFIRMADO — {a_real(a['symbol'])}\n"
            f"🪙 rank {a['cmc_rank']}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Rally previo: +{a['rango_pct']:.1f}%\n"
            f"🎯 Retroceso: {a['fibo_retroceso']:.1f}% ({a['zona_fibo']})\n"
            f"💰 Precio: ${a['p_actual']:.6f}\n"
            f"📈 Rebote confirmado: +{a['rebote_pct']:.2f}%\n"
            f"⏱️ {a['velas_desde_toque']} velas desde el toque\n"
            f"💧 Vol: ${a['vol_m']:.1f}M | {a['vol_ratio']:.2f}x\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{linea_btc}\n"
            f"🕐 {hora_lima} Lima\n"
            f"✅ ENTRADA CONFIRMADA"
        )

    if aviso_btc:
        msg += f"\n{aviso_btc}"

    return enviar_telegram(msg)


# ============================================================
# MAIN
# ============================================================

def main():
    ahora = datetime.now(timezone.utc)
    ahora_lima = ahora - timedelta(hours=5)

    print(f"\n{'='*70}")
    print(f"🎯 SUELO + ⚡ ARRANQUE + 📐 REENTRADA + 🚀 ACUM + 🚀 TCB — xk1 6 ESTADOS")
    print(f"   UTC:  {ahora.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Lima: {ahora_lima.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    df = cargar_datos()
    print(f"📊 Total: {len(df):,} filas | {df['symbol'].nunique()} monedas")

    # === ANÁLISIS xk1 ===
    btc = analizar_btc(df)
    linea_btc, aviso_btc = _contexto_btc(btc)

    print(f"\n🔍 CONTEXTO {a_real('xk1')}:")
    print(f"   {linea_btc}")
    if aviso_btc:
        print(f"   {aviso_btc}")

    # === AVISO DE CAMBIO DE ESTADO xk1 ===
    estado_previo = cargar_btc_estado_previo()
    if estado_previo and estado_previo != btc["estado"]:
        print(f"\n🔄 Cambio {a_real('xk1')}: {estado_previo} → {btc['estado']}")
        if enviar_alerta_cambio_btc(estado_previo, btc):
            print(f"   📱 Alerta de cambio {a_real('xk1')} enviada")
    guardar_btc_estado(btc["estado"])

    # === CARGAR ESTADO DE ALERTAS (cooldown) ===
    alertas_state = cargar_alertas_state()

    # === ESCANEO ===
    todas = df["symbol"].unique()
    print(f"\n🔍 Escaneando {len(todas)} monedas...")

    suelos = []
    fuertes = []
    tempranas = []
    reentradas = []
    acumulaciones = []
    tcb_detectados = []

    for sym in todas:
        if sym.upper() in EXCLUIR or sym.upper() == "xk1":
            continue

        r0 = detectar_suelo(df, sym)
        if r0:
            suelos.append(r0)

        r1 = detectar_arranque(df, sym)
        if r1:
            if r1["señal"] == "FUERTE":
                fuertes.append(r1)
            else:
                tempranas.append(r1)

        r2 = detectar_reentrada_fibo(df, sym)
        if r2:
            reentradas.append(r2)

        r5 = detectar_tcb(df, sym, btc["estado"])
        if r5:
            tcb_detectados.append(r5)

    # === ESCANEO ACUM (velas 4h) ===
    print(f"\n📊 Generando velas 4h para ACUM...")
    velas_4h = hacer_velas_4h(df)
    acum_list = detectar_acumulacion(velas_4h, btc["estado"])
    acumulaciones.extend(acum_list)

    print(f"\n   🎯 SUELOS:        {len(suelos)}")
    print(f"   🚨 FUERTES:       {len(fuertes)}")
    print(f"   🟡 TEMPRANAS:     {len(tempranas)}")
    print(f"   📐 REENTRADAS:    {len(reentradas)}")
    print(f"   🚀 ACUMULACIONES: {len(acumulaciones)}")
    print(f"   🚀 TCB DETECTADOS: {len(tcb_detectados)}")

    # ═══════════════════════════════════════════════════════════
    # BLOQUEO GRADUAL SEGÚN xk1
    # ═══════════════════════════════════════════════════════════
    enviadas = 0
    btc_estado = btc.get("estado", "FLAT")

    bloquear_tempranas  = btc_estado in ("DOWN", "DOWN_SOFT")
    bloquear_fuertes    = btc_estado == "DOWN"
    bloquear_reentradas = btc_estado == "DOWN"

    print(f"\n📋 Política {a_real('xk1')} {btc_estado}:")
    print(f"   SUELOS:        ✅ siempre permitidos (preparación)")
    print(f"   TEMPRANAS:     {'🚫 bloqueadas' if bloquear_tempranas else '✅ permitidas'}")
    print(f"   FUERTES:       {'🚫 bloqueadas' if bloquear_fuertes else '✅ permitidas'}")
    print(f"   REENTRADAS:    {'🚫 bloqueadas' if bloquear_reentradas else '✅ permitidas'}")
    print(f"   ACUMULACIONES: {'✅ permitidas' if btc_estado in ('FLAT', 'DOWN_SOFT', 'RECOVERY') else '🚫 bloqueadas (BTC ' + btc_estado + ')'}")
    print(f"   TCB:           {'✅ permitidos' if btc_estado in ('FLAT', 'DOWN_SOFT', 'RECOVERY') else '🚫 bloqueados (BTC ' + btc_estado + ')'}")

    # ─── SUELOS ───
    suelos.sort(key=lambda x: x["dist_min"])
    for a in suelos[:MAX_POR_TIPO]:
        if not paso_cooldown(alertas_state, a["symbol"], "SUELO", COOLDOWN_SUELO):
            continue
        if enviar_alerta_suelo(a, btc):
            enviadas += 1
            registrar_alerta(alertas_state, a["symbol"], "SUELO")
            print(f"   🎯 SUELO: {a_real(a['symbol'])} "
                  f"(24h {a['cambio_24h']:+.1f}% | "
                  f"vol {a['compresion_vol']:.2f}x)")

    # ─── ACUMULACIONES ───
    acumulaciones.sort(key=lambda x: (-x["pos_rango"], x["vol_cv"]))
    for a in acumulaciones[:MAX_POR_TIPO]:
        if not paso_cooldown(alertas_state, a["symbol"], "ACUM", COOLDOWN_ACUM):
            continue
        if enviar_alerta_acumulacion(a, btc):
            enviadas += 1
            registrar_alerta(alertas_state, a["symbol"], "ACUM")
            print(f"   🚀 ACUM: {a_real(a['symbol'])} "
                  f"(rango {a['rango_pct']:.1f}% | pos {a['pos_rango']:.0f}% | "
                  f"vol cv {a['vol_cv']:.2f})")

    # ─── TCB ───
    for a in tcb_detectados[:MAX_POR_TIPO]:
        tipo_cooldown = "TCB_TOQUE" if a["tipo"] == "TCB_TOQUE" else "TCB_CONFIRMADO"
        cooldown = COOLDOWN_TCB_TOQUE if a["tipo"] == "TCB_TOQUE" else COOLDOWN_TCB_CONFIRMADO

        if not paso_cooldown(alertas_state, a["symbol"], tipo_cooldown, cooldown):
            continue
        if enviar_alerta_tcb(a, btc):
            enviadas += 1
            registrar_alerta(alertas_state, a["symbol"], tipo_cooldown)
            if a["tipo"] == "TCB_TOQUE":
                print(f"   🎯 TOQUE 78.6%: {a_real(a['symbol'])} "
                      f"(fibo {a['fibo_retroceso']:.1f}% | "
                      f"hace {a['velas_desde_toque']} velas)")
            else:
                print(f"   ✅ CONFIRMADO: {a_real(a['symbol'])} "
                      f"(fibo {a['fibo_retroceso']:.1f}% | "
                      f"rebote {a['rebote_pct']:+.1f}%)")

    # ─── FUERTES ───
    if not bloquear_fuertes:
        fuertes.sort(key=lambda x: -x["cambio_1h"])
        for a in fuertes[:MAX_POR_TIPO]:
            if not paso_cooldown(alertas_state, a["symbol"], "ARRANQUE", COOLDOWN_ARRANQUE):
                continue
            if enviar_alerta_arranque(a, btc):
                enviadas += 1
                registrar_alerta(alertas_state, a["symbol"], "ARRANQUE")
                print(f"   🚨 FUERTE: {a_real(a['symbol'])} (+{a['cambio_1h']:.2f}% | "
                      f"vol {a['vol_ratio']:.2f}x)")
    elif fuertes:
        print(f"\n   ⏸️ {len(fuertes)} FUERTES bloqueadas por {a_real('xk1')} {btc_estado}")

    # ─── TEMPRANAS ───
    if not bloquear_tempranas:
        tempranas.sort(key=lambda x: -x["cambio_1h"])
        for a in tempranas[:MAX_POR_TIPO]:
            if not paso_cooldown(alertas_state, a["symbol"], "ARRANQUE", COOLDOWN_ARRANQUE):
                continue
            if enviar_alerta_arranque(a, btc):
                enviadas += 1
                registrar_alerta(alertas_state, a["symbol"], "ARRANQUE")
                print(f"   🟡 TEMPRANA: {a_real(a['symbol'])} (+{a['cambio_1h']:.2f}% | "
                      f"vol {a['vol_ratio']:.2f}x)")
    elif tempranas:
        print(f"   ⏸️ {len(tempranas)} TEMPRANAS bloqueadas por {a_real('xk1')} {btc_estado}")

    # ─── REENTRADAS ───
    if not bloquear_reentradas:
        reentradas.sort(key=lambda x: (x["distancia_fibo"], -x["vol_ratio"]))
        for a in reentradas[:MAX_POR_TIPO]:
            if not paso_cooldown(alertas_state, a["symbol"], "REENTRADA", COOLDOWN_REENTRADA):
                continue
            if enviar_alerta_reentrada(a, btc):
                enviadas += 1
                registrar_alerta(alertas_state, a["symbol"], "REENTRADA")
                print(f"   📐 REENTRADA: {a_real(a['symbol'])} "
                      f"(fibo {a['pos_actual']:.1f}% | vol {a['vol_ratio']:.2f}x)")
    elif reentradas:
        print(f"   ⏸️ {len(reentradas)} REENTRADAS bloqueadas por {a_real('xk1')} {btc_estado}")

    # === GUARDAR ESTADO DE ALERTAS ===
    guardar_alertas_state(alertas_state)

    # === RESUMEN ===
    print(f"\n{'='*70}")
    print(f"📢 RESUMEN")
    print(f"   {linea_btc}")
    print(f"   Alertas enviadas: {enviadas}")
    print(f"{'='*70}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
