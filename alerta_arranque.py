#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ALERTA V6 FINAL — SUELO + ARRANQUE + REENTRADA + ACUMULACIÓN + TCB + BTC 6 ESTADOS

Parches aplicados:
  - P1 TCB: 6 velas 5m por bloque, ventana deslizante hasta 4 bloques (2h)
  - P2 ACUM: pre-ruptura / ruptura inicial (dispara antes)
  - P3 bugs: format="mixed", refs/heads/main/, sin 201_300, EXCLUIR lower, USDGO/USDf
  - P4 201_300: archivos diarios de los últimos 3 días (hoy, ayer, anteayer)
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
#   - top100 / emerging / 201_300: archivos diarios
# ============================================================

CSV_URLS_FIJAS = {}  # VACÍO: ya no usamos archivos acumulados

CSV_BASES_DIARIAS = {
    "top100": (
        "https://raw.githubusercontent.com/mattluna3/inspector/"
        "refs/heads/main/data/cmc/market_history_top100"
    ),
    "emerging": (
        "https://raw.githubusercontent.com/mattluna3/inspector/"
        "refs/heads/main/data/cmc/market_history_emerging"
    ),
    "201_300": (
        "https://raw.githubusercontent.com/emerging2/inspector2/"
        "refs/heads/main/data/cmc/market_history_201_300"
    ),
}

DIAS_ATRAS = 2  # hoy + 2 días atrás = 3 días totales


def construir_urls_diarias():
    """
    Genera las URLs de los archivos diarios de los últimos 3 días
    (hoy, ayer, anteayer) para las fuentes que migraron a archivos por día.
    Devuelve dict: nombre_fuente -> lista de URLs.
    """
    hoy = datetime.now(timezone.utc).date()
    urls = {}
    for nombre, base in CSV_BASES_DIARIAS.items():
        lista = []
        for i in range(DIAS_ATRAS + 1):
            dia = hoy - timedelta(days=i)
            lista.append(f"{base}_{dia.isoformat()}.csv")
        urls[nombre] = lista
    return urls


# [P3.4] stables y wrapped, incluyendo USDGO y USDf
EXCLUIR = {
    "xkqy1", "xkqy2", "xkqy3", "xkqy4", "xkqy5", "xkqy6", "xkqy7", "xkqy8",
    "xkqy9", "xkqya", "xkqyb", "xkqyc", "xkqyd", "xkqye", "xkqyf",
    "xkqyg", "xkqyh", "xkqyi", "xkqyj", "xkqyk", "xkqyl", "xkqym", "xkqyn",
    "xkqyo", "xkqyp", "xkqyq", "xkqyr", "xkqys", "xkqyt", "xkqyu", "xkqyv",
    "xkqyw", "xkqyx", "xkqyy", "xkqyz", "xkqy0", "xkqz1", "xkqz2",
    "usdgo", "usdf",
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
# UMBRALES — ACUMULACIÓN  [P2]
# ============================================================

ACUM_RANGO_MIN       = 8.0
ACUM_RANGO_MAX       = 15.0
ACUM_VOL_CV_MAX      = 0.35
ACUM_DUR_MIN         = 20
ACUM_DIST_RESIST_MAX = 1.5
ACUM_RUPTURA_MAX     = 2.0
ACUM_CAMBIO_MIN      = 0.0
ACUM_CAMBIO_MAX      = 2.5
ACUM_SQUEEZE_MAX     = 0.60


# ============================================================
# UMBRALES — TCB  [P1]
# ============================================================

TCB_FIBO_MIN            = 58.0
TCB_FIBO_MAX            = 82.0
TCB_RALLY_MIN_PCT       = 10.0
TCB_VENTANA_H           = 72
TCB_VOL_MIN             = 1.0

TCB_CONFIRMACION_VELAS    = 6      # velas por bloque
TCB_MAX_BLOQUES           = 4      # 4 bloques × 6 velas = 24 velas = 2h
TCB_MOV_MIN_PCT           = 0.5
TCB_MOV_MAX_PCT           = 3.0
TCB_SUBIDAS_MIN           = 4
TCB_SEGUIMIENTO_MIN_ANTES = 15     # NUEVO: 15 min antes del cierre
TCB_SEGUIMIENTO_H_DESPUES = 2      # NUEVO: 2h después del cierre
TCB_REBOTE_MIN_PCT        = 2.0    # NUEVO: rebote mínimo post-cierre

VENTANAS_TOQUE = {
    0:  ("ASIA", True),
    4:  ("LIMA_03", True),
    8:  ("REGISTRO_EUROPA", False),
    12: ("USA", True),
    16: ("USA_CIERRE", True),
    20: ("LIMA_15", True),
}


# ============================================================
# FILTROS COMUNES
# ============================================================

MC_MIN              = 50_000_000
MC_MAX              = 5_000_000_000
RANK_MAX            = 300

MAX_POR_TIPO        = 10

BTC_STATE_FILE      = "btc_state.json"
ALERTAS_STATE_FILE  = "alertas_state.json"

COOLDOWN_SUELO          = 6
COOLDOWN_ARRANQUE       = 4
COOLDOWN_REENTRADA      = 8
COOLDOWN_ACUM           = 12
COOLDOWN_TCB_TOQUE      = 4
COOLDOWN_TCB_CONFIRMADO = 12
COOLDOWN_EUROPA         = 4


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
# CARGA  [P3.1] format="mixed" + headers anti-caché
#        [P4] 201_300 une los 3 archivos diarios
# ============================================================

def cargar_datos():
    dfs = []

    # --- Fuentes con archivo único (vacío ahora) ---
    for nombre, url in CSV_URLS_FIJAS.items():
        try:
            r = requests.get(url, timeout=30, headers={
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            })
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text), low_memory=False)
            df["_fuente"] = nombre
            df["_archivo"] = url.rsplit("/", 1)[-1]
            if REAL_A_ALIAS and "symbol" in df.columns:
                df["symbol"] = (
                    df["symbol"].astype(str).str.strip().str.upper()
                    .map(lambda s: a_alias(s))
                )
            dfs.append(df)
            print(f"✅ {nombre} {url.rsplit('/',1)[-1]}: {len(df):,} filas")
        except Exception as e:
            print(f"⚠️ {nombre}: {str(e)[:80]}")

    # --- Fuentes con archivos diarios ---
    urls_diarias = construir_urls_diarias()
    for nombre, urls in urls_diarias.items():
        for url in urls:
            try:
                r = requests.get(url, timeout=30, headers={
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                })
                if r.status_code == 404:
                    print(f"   ⚠️ {nombre} {url.rsplit('/',1)[-1]}: no existe (404)")
                    continue
                r.raise_for_status()
                df = pd.read_csv(StringIO(r.text), low_memory=False)
                df["_fuente"] = nombre
                df["_archivo"] = url.rsplit("/", 1)[-1]
                if REAL_A_ALIAS and "symbol" in df.columns:
                    df["symbol"] = (
                        df["symbol"].astype(str).str.strip().str.upper()
                        .map(lambda s: a_alias(s))
                    )
                dfs.append(df)
                print(f"✅ {nombre} {url.rsplit('/',1)[-1]}: {len(df):,} filas")
            except Exception as e:
                print(f"⚠️ {nombre} {url.rsplit('/',1)[-1]}: {str(e)[:80]}")

    if not dfs:
        sys.exit("❌ No se pudo cargar ningún CSV")

    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(
        df["timestamp"], utc=True, errors="coerce", format="mixed"
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


# [P1] Ventana deslizante hasta TCB_MAX_BLOQUES bloques de 6 velas (2h)
def inicio_movimiento_5m(df, symbol, ts_cierre_vela,
                         minutos_antes=TCB_SEGUIMIENTO_MIN_ANTES,
                         horas_despues=TCB_SEGUIMIENTO_H_DESPUES,
                         n_velas=TCB_CONFIRMACION_VELAS,
                         min_mov_pct=TCB_MOV_MIN_PCT,
                         max_mov_pct=TCB_MOV_MAX_PCT,
                         min_subidas=TCB_SUBIDAS_MIN):
    """
    Vigila velas de 5m en el rango:
      - minutos_antes antes del cierre de la vela 4h
      - horas_despues horas después del cierre de la vela 4h
    Busca confirmación de rebote en ese rango con ventana deslizante.
    """
    ts_inicio = ts_cierre_vela - timedelta(minutes=minutos_antes)
    ts_fin = ts_cierre_vela + timedelta(hours=horas_despues)

    s = (df[df["symbol"] == symbol]
         .set_index("timestamp")["price"].sort_index())
    p5 = s.resample("5min").last().dropna()
    p5 = p5[(p5.index >= ts_inicio) & (p5.index <= ts_fin)]

    if len(p5) < n_velas + 1:
        return None

    p_min_rango = float(p5.min())

    # Ventana deslizante de n_velas
    for i in range(len(p5) - n_velas):
        bloque = p5.iloc[i:i + n_velas + 1]
        if len(bloque) < n_velas + 1:
            break

        ema9 = bloque.ewm(span=9, adjust=False).mean()
        p_ult = float(bloque.iloc[-1])

        if p_ult <= float(ema9.iloc[-1]):
            continue

        subidas = int((bloque.diff().dropna() > 0).sum())
        if subidas < min_subidas:
            continue

        mov_ventana = (p_ult / float(bloque.iloc[0]) - 1) * 100
        if mov_ventana < min_mov_pct:
            continue

        mov_desde_min = (p_ult / p_min_rango - 1) * 100 if p_min_rango > 0 else 0
        if mov_desde_min > max_mov_pct:
            continue

        return {
            "p_ult": p_ult,
            "subidas": subidas,
            "mov_ventana": mov_ventana,
            "mov_desde_min": mov_desde_min,
            "ts_ult": bloque.index[-1],
        }

    return None


# ============================================================
# ANÁLISIS BTC — 6 ESTADOS (sin cambios)
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
        "estado": estado, "cambio_1h": cambio_1h, "cambio_4h": cambio_4h,
        "cambio_24h": cambio_24h, "vol_ratio": vol_ratio, "precio": p_fin,
        "p_min_4h": p_min_4h, "pos_respecto_min": pos_respecto_min,
    }


def _contexto_btc(btc):
    mapa = {
        "UP":        ("🟢 xk1: subiendo", ""),
        "FLAT":      ("⚪ xk1: neutral", ""),
        "DOWN_SOFT": ("🟡 xk1: bajando suave", "⚠️ Mercado flojo — sé selectivo"),
        "DOWN":      ("🔴 xk1: cayendo fuerte", "🚨 xk1 en caída — no entrar a alts"),
        "BOTTOM":    ("🟣 xk1: fondo — dejó de caer", "⏳ Preparar entradas, esperar confirmación"),
        "RECOVERY":  ("🟢 xk1: recovery — rebotando", "🔥 Momento óptimo para entrar a alts"),
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
            return json.load(f).get("estado")
    except Exception:
        return None


def guardar_btc_estado(estado):
    try:
        with open(BTC_STATE_FILE, "w") as f:
            json.dump({"estado": estado, "updated_at": datetime.now(timezone.utc).isoformat()}, f)
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
# ALERTA CAMBIO DE ESTADO BTC (sin cambios)
# ============================================================

def enviar_alerta_cambio_btc(estado_previo, btc):
    if estado_previo == btc["estado"]:
        return False
    transiciones = {
        ("UP", "FLAT"):          ("🟡", "xk1 PERDIENDO FUERZA", "Toma ganancias, mercado girando"),
        ("UP", "DOWN_SOFT"):     ("🟡", "xk1 GIRANDO A LA BAJA", "Cuidado con alts"),
        ("UP", "DOWN"):          ("🔴", "xk1 GIRÓ A LA BAJA", "🚨 NO ENTRAR A ALTS"),
        ("FLAT", "DOWN_SOFT"):   ("🟡", "xk1 EMPIEZA A CAER", "Cuidado con alts"),
        ("FLAT", "DOWN"):        ("🔴", "xk1 CAYENDO FUERTE", "🚨 NO ENTRAR A ALTS"),
        ("DOWN_SOFT", "DOWN"):   ("🔴", "xk1 CAYENDO FUERTE", "🚨 NO ENTRAR A ALTS"),
        ("DOWN", "BOTTOM"):      ("🟣", "xk1 TOCA FONDO", "⏳ Preparar entradas en alts"),
        ("DOWN_SOFT", "BOTTOM"): ("🟣", "xk1 TOCA FONDO", "⏳ Preparar entradas en alts"),
        ("DOWN", "RECOVERY"):    ("🟢", "xk1 REBOTA", "🔥 MOMENTO ÓPTIMO — entradas en alts"),
        ("BOTTOM", "RECOVERY"):  ("🟢", "xk1 REBOTA", "🔥 MOMENTO ÓPTIMO — entradas en alts"),
        ("BOTTOM", "UP"):        ("🟢", "xk1 CONFIRMA SUBIDA", "🟢 Buen momento para alts"),
        ("RECOVERY", "UP"):      ("🟢", "xk1 CONFIRMA SUBIDA", "🟢 Buen momento para alts"),
        ("RECOVERY", "DOWN"):    ("🔴", "xk1 RECAÓ", "🚨 Rebote fallido — no entrar"),
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
# DETECCIÓN — SUELO (sin cambios)
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
        "tipo": "SUELO", "symbol": symbol,
        "cambio_24h": cambio_24h, "cambio_4h": cambio_4h,
        "precio": p_actual, "p_min_24h": p_min_24h, "dist_min": dist_min,
        "compresion_vol": compresion_vol, "compresion_rango": compresion_rango,
        "vol_ratio": compresion_vol, "vol_m": v_ult / 1e6,
        "cmc_rank": int(rank), "market_cap_m": mc / 1e6, "ts": ts_max,
    }


# ============================================================
# DETECCIÓN — ARRANQUE (sin cambios)
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
        "tipo": "ARRANQUE", "señal": señal, "symbol": symbol,
        "cambio_1h": cambio_1h, "cambio_4h": cambio_4h, "cambio_24h": cambio_24h,
        "precio": p_fin, "vol_ratio": vol_ratio, "vol_m": v_ult / 1e6,
        "ts": ts_max, "cmc_rank": int(rank), "market_cap_m": mc / 1e6,
    }


# ============================================================
# DETECCIÓN — REENTRADA FIBO (sin cambios)
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
        "tipo": "REENTRADA", "symbol": symbol,
        "pump_total": pump_total, "p_max": p_max_48, "p_min": p_min_48,
        "p_actual": p_actual, "p_min_pullback": p_min_pullback,
        "fibo_382": fibo_382, "fibo_500": fibo_500, "fibo_618": fibo_618,
        "pos_actual": pos_actual, "rebote_pct": rebote_pct,
        "vol_ratio": vol_ratio, "vol_m": v_ult / 1e6,
        "distancia_fibo": distancia_fibo,
        "cmc_rank": int(rank), "market_cap_m": mc / 1e6, "ts": ts_max,
    }


# ============================================================
# DETECCIÓN — ACUMULACIÓN  [P2]
# ============================================================

def detectar_acumulacion(velas_4h, btc_estado, ahora=None):
    if btc_estado not in ("FLAT", "DOWN_SOFT", "RECOVERY"):
        return []
    ahora = ahora or datetime.now(timezone.utc)
    resultados = []

    for sym, g in velas_4h.groupby("symbol"):
        if str(sym).lower() in EXCLUIR or str(sym).lower() == "xk1":
            continue

        g = g.sort_values("vela").reset_index(drop=True)
        rank = g.iloc[-1].get("rank", 9999)
        mc = g.iloc[-1].get("mc", 0) or 0
        if not cumple_calidad(mc, rank):
            continue

        cerradas = g[g["vela"] + timedelta(hours=4) <= ahora]
        if len(cerradas) < ACUM_DUR_MIN + 5:
            continue

        base = cerradas.tail(ACUM_DUR_MIN)
        p_min, p_max = base["low"].min(), base["high"].max()
        if p_min <= 0 or p_max <= p_min:
            continue

        rango_pct = (p_max / p_min - 1) * 100
        if not (ACUM_RANGO_MIN <= rango_pct <= ACUM_RANGO_MAX):
            continue

        vol_medio = base["volume"].mean()
        vol_cv = base["volume"].std() / vol_medio if vol_medio and vol_medio > 0 else 1
        if vol_cv > ACUM_VOL_CV_MAX:
            continue

        p_actual = cerradas.iloc[-1]["close"]
        dist_res = (p_max / p_actual - 1) * 100
        if not (-ACUM_RUPTURA_MAX <= dist_res <= ACUM_DIST_RESIST_MAX):
            continue

        cambio_reciente = 0.0
        if len(cerradas) >= 5:
            cambio_reciente = (cerradas.iloc[-1]["close"] / cerradas.iloc[-5]["close"] - 1) * 100
        if cambio_reciente < ACUM_CAMBIO_MIN:
            continue
        if cambio_reciente > ACUM_CAMBIO_MAX:
            continue

        u6 = base.tail(6)
        squeeze = (u6["high"].max() - u6["low"].min()) / (p_max - p_min)
        if squeeze > ACUM_SQUEEZE_MAX:
            continue

        resultados.append({
            "tipo": "ACUMULACION",
            "fase": "RUPTURA INICIAL" if dist_res < 0 else "PRE-RUPTURA",
            "symbol": sym,
            "rango_pct": rango_pct,
            "pos_rango": (p_actual - p_min) / (p_max - p_min) * 100,
            "dist_resist": dist_res,
            "squeeze": squeeze,
            "vol_medio_m": vol_medio / 1e6,
            "vol_cv": vol_cv,
            "cambio_reciente": cambio_reciente,
            "p_actual": p_actual,
            "p_max": p_max, "p_min": p_min,
            "rank": int(rank), "mc": mc,
        })

    return resultados


# ============================================================
# DETECCIÓN — TCB  [P1 + ventana deslizante 4 bloques]
# ============================================================

def detectar_tcb(df, symbol, btc_estado):
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

    g4 = g.copy()
    g4["vela_4h"] = g4["timestamp"].dt.floor("4h")
    velas = g4.groupby("vela_4h").agg(price=("price", "last")).reset_index()
    velas["timestamp"] = velas["vela_4h"]
    velas = velas[velas["timestamp"] + timedelta(hours=4) <= ts_max]
    velas = velas.reset_index(drop=True)
    if len(velas) < 20:
        return None

    corte_72 = velas["timestamp"].max() - timedelta(hours=TCB_VENTANA_H)
    rec_72 = velas[velas["timestamp"] >= corte_72].reset_index(drop=True)
    if len(rec_72) < 10:
        return None

    p_min = rec_72["price"].min()
    idx_min = rec_72["price"].idxmin()
    p_max = rec_72.loc[idx_min:]["price"].max()
    idx_max = rec_72.loc[idx_min:]["price"].idxmax()
    if p_min <= 0 or p_max <= p_min:
        return None

    rango = p_max - p_min
    rango_pct = rango / p_min * 100
    if rango_pct < TCB_RALLY_MIN_PCT:
        return None

    fibo_618 = p_max - rango * 0.618
    fibo_786 = p_max - rango * 0.786
    fibo_100 = p_min

    post_rally = rec_72.loc[idx_max:]
    if len(post_rally) < 2:
        return None

    TOLERANCIA = 1.5
    idx_toque, fibo_tocado = None, None
    for idx in (rec_72.index[-1], rec_72.index[-2]):
        if idx < idx_max:
            continue
        pv = rec_72.loc[idx, "price"]
        if abs(pv - fibo_786) / fibo_786 * 100 <= TOLERANCIA:
            idx_toque, fibo_tocado = idx, "78.6%"
            break
        if abs(pv - fibo_618) / fibo_618 * 100 <= TOLERANCIA:
            idx_toque, fibo_tocado = idx, "61.8%"
            break
        if abs(pv - fibo_100) / fibo_100 * 100 <= TOLERANCIA:
            idx_toque, fibo_tocado = idx, "100%"
            break
    if idx_toque is None:
        return None

    p_toque = float(rec_72.loc[idx_toque, "price"])
    ts_toque = rec_72.loc[idx_toque, "timestamp"]
    hora_utc = ts_toque.hour

    cfg = VENTANAS_TOQUE.get(hora_utc)
    if cfg is None:
        return None
    ventana, con_alerta = cfg

    ts_cierre_toque = ts_toque + timedelta(hours=4)

    # === NUEVO: Filtro de rebote post-cierre ===
    precios_post = g[
        (g["timestamp"] > ts_cierre_toque) &
        (g["timestamp"] <= ts_cierre_toque + timedelta(minutes=30))
    ]

    if len(precios_post) > 0:
        p_max_post = float(precios_post["price"].max())
        p_min_post = float(precios_post["price"].min())
        mov_arriba = (p_max_post - p_toque) / p_toque * 100 if p_toque > 0 else 0
        mov_abajo = (p_toque - p_min_post) / p_toque * 100 if p_toque > 0 else 0

        if max(mov_arriba, mov_abajo) < TCB_REBOTE_MIN_PCT:
            return None
    else:
        return None
    # === FIN filtro de rebote ===

    v_base = g["volume_24h"].tail(20).mean()
    v_ult = g["volume_24h"].iloc[-1]
    vol_ratio = v_ult / v_base if v_base > 0 else 0
    if vol_ratio < TCB_VOL_MIN:
        return None

    p_actual = float(g.iloc[-1]["price"])
    rebote_pct = 0.0
    p_rebote = p_actual

    if con_alerta:
        mov = inicio_movimiento_5m(df, symbol, ts_cierre_toque)
        if mov is None:
            return None
        rebote_pct = mov["mov_desde_min"]
        p_rebote = mov["p_ult"]

    return {
        "tipo": "TCB_CONFIRMADO", "symbol": symbol,
        "rango_pct": rango_pct,
        "p_min": p_min, "p_max": p_max, "p_actual": p_actual,
        "p_toque": p_toque, "p_rebote": p_rebote,
        "fibo_tocado": fibo_tocado, "rebote_pct": rebote_pct,
        "vol_ratio": vol_ratio, "vol_m": v_ult / 1e6,
        "cmc_rank": int(rank), "market_cap_m": mc / 1e6,
        "ts": ts_max, "ts_toque": ts_toque,
        "hora_utc": hora_utc, "ventana": ventana,
    }


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
        emoji, titulo, nota = "🚨", "ARRANQUE FUERTE", "✅ Volumen CONFIRMA"
    else:
        emoji, titulo, nota = "🟡", "ARRANQUE TEMPRANO", "⚠️ Verificar gráfico"
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
        f"💡 {a['fase']} | resistencia a {a['dist_resist']:+.2f}%"
    )
    if aviso_btc:
        msg += f"\n{aviso_btc}"
    return enviar_telegram(msg)


def enviar_alerta_tcb(a, btc):
    hora_lima = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%H:%M")
    linea_btc, aviso_btc = _contexto_btc(btc)

    ventana = a.get("ventana", "")
    if ventana == "USA":
        emoji_ventana = "🔥 APERTURA USA"
    elif ventana == "USA_CIERRE":
        emoji_ventana = "🔥 CIERRE USA (15:00 Lima)"
    elif ventana == "ASIA":
        emoji_ventana = "✅ ASIA"
    elif ventana == "LIMA_03":
        emoji_ventana = "🌙🇵🇪 CIERRE 03:00 LIMA"
    elif ventana == "LIMA_15":
        emoji_ventana = "🔥🇵🇪 15:00 LIMA ⭐"
    else:
        emoji_ventana = ""

    linea_timing = ""

    msg = (
        f"✅ TCB CONFIRMADO — {a_real(a['symbol'])}\n"
        f"🪙 {a_real(a['symbol'])} (rank {a['cmc_rank']})\n"
        f"📅 {emoji_ventana} ({a['hora_utc']:02d}:00 UTC)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Rally previo: +{a['rango_pct']:.1f}%\n"
        f"   Máx: ${a['p_max']:.6f}\n"
        f"   Mín: ${a['p_min']:.6f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Tocó Fibo {a['fibo_tocado']} en ${a['p_toque']:.6f}\n"
        f"{linea_timing}"
        f"📈 Rebote confirmado: +{a['rebote_pct']:.2f}%\n"
        f"💰 Precio actual: ${a['p_actual']:.6f}\n"
        f"💧 Vol: ${a['vol_m']:.1f}M | {a['vol_ratio']:.2f}x\n"
        f"🏦 MC: ${a['market_cap_m']:.1f}M\n"
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

    btc = analizar_btc(df)
    linea_btc, aviso_btc = _contexto_btc(btc)
    print(f"\n🔍 CONTEXTO {a_real('xk1')}:")
    print(f"   {linea_btc}")
    if aviso_btc:
        print(f"   {aviso_btc}")

    estado_previo = cargar_btc_estado_previo()
    if estado_previo and estado_previo != btc["estado"]:
        print(f"\n🔄 Cambio {a_real('xk1')}: {estado_previo} → {btc['estado']}")
        if enviar_alerta_cambio_btc(estado_previo, btc):
            print(f"   📱 Alerta de cambio {a_real('xk1')} enviada")
    guardar_btc_estado(btc["estado"])

    alertas_state = cargar_alertas_state()
    todas = df["symbol"].unique()
    print(f"\n🔍 Escaneando {len(todas)} monedas...")

    suelos, fuertes, tempranas = [], [], []
    reentradas, acumulaciones, tcb_detectados = [], [], []

    for sym in todas:
        if str(sym).lower() in EXCLUIR or str(sym).lower() == "xk1":
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

    print(f"\n📊 Generando velas 4h para ACUM...")
    velas_4h = hacer_velas_4h(df)
    acumulaciones = detectar_acumulacion(velas_4h, btc["estado"], ahora)

    # === DEBUG CANDIDATAS ACUM (solo consola) ===
    print(f"\n🔍 CANDIDATAS ACUM (cerca de resistencia, dist_resist <= 3%):")
    print(f"   {'SYM':<12} {'dist_res':<10} {'cambio':<10} {'rango':<8} {'squeeze':<8} {'vol_cv':<8} {'¿Pasa?'}")
    print(f"   {'-'*80}")

    candidatas_count = 0
    for sym, g in velas_4h.groupby("symbol"):
        if str(sym).lower() in EXCLUIR or str(sym).lower() == "xk1":
            continue

        g = g.sort_values("vela").reset_index(drop=True)
        rank = g.iloc[-1].get("rank", 9999)
        mc = g.iloc[-1].get("mc", 0) or 0
        if not cumple_calidad(mc, rank):
            continue

        cerradas = g[g["vela"] + timedelta(hours=4) <= ahora]
        if len(cerradas) < ACUM_DUR_MIN + 5:
            continue

        base = cerradas.tail(ACUM_DUR_MIN)
        p_min, p_max = base["low"].min(), base["high"].max()
        if p_min <= 0 or p_max <= p_min:
            continue

        p_actual = cerradas.iloc[-1]["close"]
        dist_res = (p_max / p_actual - 1) * 100

        if dist_res > 3.0 or dist_res < -2.0:
            continue

        candidatas_count += 1

        rango_pct = (p_max / p_min - 1) * 100
        vol_medio = base["volume"].mean()
        vol_cv = base["volume"].std() / vol_medio if vol_medio and vol_medio > 0 else 1
        cambio_reciente = 0.0
        if len(cerradas) >= 5:
            cambio_reciente = (cerradas.iloc[-1]["close"] / cerradas.iloc[-5]["close"] - 1) * 100
        u6 = base.tail(6)
        squeeze = (u6["high"].max() - u6["low"].min()) / (p_max - p_min) if (p_max - p_min) > 0 else 1

        pasa_rango = ACUM_RANGO_MIN <= rango_pct <= ACUM_RANGO_MAX
        pasa_vol = vol_cv <= ACUM_VOL_CV_MAX
        pasa_cambio = cambio_reciente <= ACUM_CAMBIO_MAX
        pasa_squeeze = squeeze <= ACUM_SQUEEZE_MAX
        pasa_dist = -ACUM_RUPTURA_MAX <= dist_res <= ACUM_DIST_RESIST_MAX

        pasa_todo = pasa_rango and pasa_vol and pasa_cambio and pasa_squeeze and pasa_dist

        estado = "✅ SÍ" if pasa_todo else "❌ NO"
        falla = []
        if not pasa_rango: falla.append("rango")
        if not pasa_vol: falla.append("vol")
        if not pasa_cambio: falla.append("cambio")
        if not pasa_squeeze: falla.append("squeeze")
        if not pasa_dist: falla.append("dist")

        print(f"   {sym:<12} {dist_res:>+7.2f}% {cambio_reciente:>+7.2f}% {rango_pct:>6.1f}%  {squeeze:>6.2f}  {vol_cv:>6.2f}  {estado} {', '.join(falla)}")

    print(f"\n   Total candidatas: {candidatas_count}")
    print(f"   Pasan todos los filtros: {len(acumulaciones)}")
    # === FIN DEBUG CANDIDATAS ===

    # === DEBUG TEMPORAL ===
    print(f"\n🔍 DEBUG ACUM ({len(acumulaciones)} detectadas):")
    for a in acumulaciones[:MAX_POR_TIPO]:
        cd = not paso_cooldown(alertas_state, a["symbol"], "ACUM", COOLDOWN_ACUM)
        print(f"   {a_real(a['symbol'])} ({a['fase']}) - cooldown: {'SÍ' if cd else 'NO'}")
    # === FIN DEBUG ===


    print(f"\n   🎯 SUELOS:        {len(suelos)}")
    print(f"   🚨 FUERTES:       {len(fuertes)}")
    print(f"   🟡 TEMPRANAS:     {len(tempranas)}")
    print(f"   📐 REENTRADAS:    {len(reentradas)}")
    print(f"   🚀 ACUMULACIONES: {len(acumulaciones)}")
    print(f"   🚀 TCB DETECTADOS: {len(tcb_detectados)}")

    enviadas = 0
    btc_estado = btc.get("estado", "FLAT")

    bloquear_tempranas  = btc_estado in ("DOWN", "DOWN_SOFT")
    bloquear_fuertes    = btc_estado == "DOWN"
    bloquear_reentradas = btc_estado == "DOWN"

    print(f"\n📋 Política {a_real('xk1')} {btc_estado}:")
    print(f"   SUELOS:        ✅ siempre permitidos")
    print(f"   TEMPRANAS:     {'🚫 bloqueadas' if bloquear_tempranas else '✅ permitidas'}")
    print(f"   FUERTES:       {'🚫 bloqueadas' if bloquear_fuertes else '✅ permitidas'}")
    print(f"   REENTRADAS:    {'🚫 bloqueadas' if bloquear_reentradas else '✅ permitidas'}")
    print(f"   ACUMULACIONES: {'✅ permitidas' if btc_estado in ('FLAT','DOWN_SOFT','RECOVERY') else '🚫 bloqueadas'}")
    print(f"   TCB:           {'✅ permitidos' if btc_estado in ('FLAT','DOWN_SOFT','RECOVERY') else '🚫 bloqueados'}")

    # SUELOS
    suelos.sort(key=lambda x: x["dist_min"])
    for a in suelos[:MAX_POR_TIPO]:
        if not paso_cooldown(alertas_state, a["symbol"], "SUELO", COOLDOWN_SUELO):
            continue
        if enviar_alerta_suelo(a, btc):
            enviadas += 1
            registrar_alerta(alertas_state, a["symbol"], "SUELO")
            print(f"   🎯 SUELO: {a_real(a['symbol'])}")

    # ACUM
    acumulaciones.sort(key=lambda x: abs(x["dist_resist"]))
    for a in acumulaciones[:MAX_POR_TIPO]:
        if not paso_cooldown(alertas_state, a["symbol"], "ACUM", COOLDOWN_ACUM):
            continue
        if enviar_alerta_acumulacion(a, btc):
            enviadas += 1
            registrar_alerta(alertas_state, a["symbol"], "ACUM")
            print(f"   🚀 ACUM: {a_real(a['symbol'])} ({a['fase']})")

    # TCB
    registro_europa = []
    for a in tcb_detectados[:MAX_POR_TIPO]:
        ventana = a.get("ventana", "")

        if ventana == "REGISTRO_EUROPA":
            if paso_cooldown(alertas_state, a["symbol"], "EUROPA", COOLDOWN_EUROPA):
                registro_europa.append(a)
                registrar_alerta(alertas_state, a["symbol"], "EUROPA")
                print(f"   📝 REGISTRO EUROPA: {a_real(a['symbol'])}")
            continue

        if not paso_cooldown(alertas_state, a["symbol"], "TCB", COOLDOWN_TCB_CONFIRMADO):
            continue
        if enviar_alerta_tcb(a, btc):
            enviadas += 1
            registrar_alerta(alertas_state, a["symbol"], "TCB")
            print(f"   ✅ TCB: {a_real(a['symbol'])} ({ventana})")

    if registro_europa:
        df_europa = pd.DataFrame(registro_europa)
        archivo_europa = "registro_europa.csv"
        df_europa.to_csv(archivo_europa, mode="a",
                         header=not os.path.exists(archivo_europa), index=False)
        print(f"   📝 {len(registro_europa)} toques registrados en {archivo_europa}")

    # FUERTES
    if not bloquear_fuertes:
        fuertes.sort(key=lambda x: -x["cambio_1h"])
        for a in fuertes[:MAX_POR_TIPO]:
            if not paso_cooldown(alertas_state, a["symbol"], "ARRANQUE", COOLDOWN_ARRANQUE):
                continue
            if enviar_alerta_arranque(a, btc):
                enviadas += 1
                registrar_alerta(alertas_state, a["symbol"], "ARRANQUE")
                print(f"   🚨 FUERTE: {a_real(a['symbol'])}")

    # TEMPRANAS
    if not bloquear_tempranas:
        tempranas.sort(key=lambda x: -x["cambio_1h"])
        for a in tempranas[:MAX_POR_TIPO]:
            if not paso_cooldown(alertas_state, a["symbol"], "ARRANQUE", COOLDOWN_ARRANQUE):
                continue
            if enviar_alerta_arranque(a, btc):
                enviadas += 1
                registrar_alerta(alertas_state, a["symbol"], "ARRANQUE")
                print(f"   🟡 TEMPRANA: {a_real(a['symbol'])}")

    # REENTRADAS
    if not bloquear_reentradas:
        reentradas.sort(key=lambda x: (x["distancia_fibo"], -x["vol_ratio"]))
        for a in reentradas[:MAX_POR_TIPO]:
            if not paso_cooldown(alertas_state, a["symbol"], "REENTRADA", COOLDOWN_REENTRADA):
                continue
            if enviar_alerta_reentrada(a, btc):
                enviadas += 1
                registrar_alerta(alertas_state, a["symbol"], "REENTRADA")
                print(f"   📐 REENTRADA: {a_real(a['symbol'])}")

    guardar_alertas_state(alertas_state)

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
