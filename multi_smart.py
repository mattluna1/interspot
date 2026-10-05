#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

# ============================================================
# MULTI SMART — Fase 2.2
#   Filtro compresión BTC (ATR% + NR7) + Squeeze Momentum + ADX
#   60 monedas dinámicas + LONG-only + SIN TPs
# ============================================================

SYMBOLS = [
    "ARB", "UNI", "LTC", "LINK", "BNB", "ENA",
    "RAY", "ETH", "HYPE", "ZEC", "DOGE", "STX", "DASH",
]

EXCLUIR = {
    "USDC", "USDT", "DAI", "TUSD", "FDUSD", "BUSD", "USDD",
    "USDE", "PYUSD", "USDS", "USD1", "RLUSD", "XAUT", "PAXG",
}

MAX_MONEDAS_DINAMICAS = 60

BTC_SYMBOL = "BTC"
TIMEFRAMES = ["15m", "1h"]

MAX_HISTORY_HOURS = 48

SMART_LINE_SCORE_MIN = 6.0
SMART_DIRECTION_SCORE_MIN_LONG = -20
SMART_TOTAL_SCORE_MIN = 80

# ============================================================
# FILTRO BTC — ATR percentil + NR7 + EXPANSIÓN
# ============================================================
ATR_PERIOD            = 14
ATR_VENTANA           = 100
ATR_UMBRAL_COMPRESION = 20.0
NR7_PERIOD            = 7

COMP_FACTOR_EXPANSION = 3.0
COMP_HORAS_RECIENTE   = 2
COMP_MIN_VELAS        = 12
COMP_MODO_FILTRO      = "hard"

COMP_THROTTLE_MIN     = 30

# ============================================================
# SQUEEZE MOMENTUM (LazyBear)
# ============================================================
SQZ_BB_LENGTH = 20
SQZ_BB_MULT   = 2.0
SQZ_KC_LENGTH = 20
SQZ_KC_MULT   = 1.5

# ============================================================
# ADX
# ============================================================
ADX_LENGTH = 14
ADX_UMBRAL = 23.0

# ============================================================
# CONTADOR DE DIAGNÓSTICO
# ============================================================
CONTADOR_FILTROS = {
    "EDAD": 0,
    "MOMENTUM": 0,
    "ADX": 0,
    "DI": 0,
    "SIN_EXPANSION": 0,
    "COMPRESION": 0,
    "PASA": 0,
}

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

STATE_FILE = DATA_DIR / "multi_smart_state.json"
CSV_FILE = DATA_DIR / "multi_smart.csv"
HISTORICO_CSV_FILE = DATA_DIR / "multi_smart_historial_lineas.csv"

THROTTLE_FILE = DATA_DIR / "multi_smart_throttle.json"
THROTTLE_REMOTE = (
    "https://raw.githubusercontent.com/mattluna1/"
    "interspot/main/data/multi_smart_throttle.json"
)

LIMA_OFFSET = timedelta(hours=-5)
HORA_INICIO = 0
HORA_FIN = 24

PD_VENTANA_MIN = 10
PD_MIN_PCT = 2.0

CACHE_REMOTE_BASE = (
    "https://raw.githubusercontent.com/mattluna1/"
    "interspot/main/data/cache"
)
CACHE_MAX_EDAD_MIN = 40


def hora_permite_envio():
    now_lima = datetime.now(timezone.utc) + LIMA_OFFSET
    return HORA_INICIO <= now_lima.hour < HORA_FIN


COINBEACON_TRENDLINES_URL = "https://api.coinbeacon.io/detectors/trendlines"
COINBEACON_VOLUME_URL = "https://api.coinbeacon.io/detectors/volume"
COINBEACON_PUMPING_URL = "https://api.coinbeacon.io/pumping/events"

COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"

COINGECKO_IDS = {
    "BTC": "bitcoin", "ARB": "arbitrum", "UNI": "uniswap",
    "LTC": "litecoin", "INJ": "injective-protocol", "LINK": "chainlink",
    "BNB": "binancecoin", "ENA": "ethena", "SUSHI": "sushi",
    "RAY": "raydium", "ETH": "ethereum", "SOL": "solana",
    "HYPE": "hyperliquid", "ZEC": "zcash", "DOGE": "dogecoin",
    "STX": "blockstack", "DASH": "dash",
}


def leer_cache_remoto(symbol):
    url = f"{CACHE_REMOTE_BASE}/{symbol}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"   ⚠️ cache {symbol}: HTTP {e.code}", flush=True)
        return None
    except Exception as e:
        print(f"   ⚠️ cache {symbol}: {str(e)[:60]}", flush=True)
        return None

    pulso = data.get("pulso", [])
    if not pulso:
        return None

    ultimo = pulso[-1]
    ts = ultimo.get("ts")
    if not ts:
        return None

    ahora = datetime.now(timezone.utc).timestamp()
    edad_min = (ahora - ts) / 60
    if edad_min > CACHE_MAX_EDAD_MIN:
        return None

    return {
        "symbol": symbol,
        "price": ultimo.get("price"),
        "rsi15": ultimo.get("rsi15"),
        "rsi1h": ultimo.get("rsi1h"),
        "atr_pct15": ultimo.get("atr_pct15"),
        "dir15": ultimo.get("dir15"),
        "dir1h": ultimo.get("dir1h"),
        "edad_min": edad_min,
        "n_muestras": len(pulso),
        "pulso": pulso,
        "velas_5m": data.get("velas_5m", []),
        "velas_15m": data.get("velas_15m", []),
        "velas_1h": data.get("velas_1h", []),
    }


def _media(xs):
    return sum(xs) / len(xs) if xs else 0.0


def construir_velas(cache, tf="5m"):
    if not cache:
        return []
    key = f"velas_{tf}"
    velas_raw = cache.get(key) or []
    if not velas_raw:
        return []
    velas = []
    for v in velas_raw:
        try:
            o = float(v["o"])
            c = float(v["c"])
            h = float(v.get("h", max(o, c)))
            l = float(v.get("l", min(o, c)))
        except (TypeError, ValueError, KeyError):
            continue
        velas.append({
            "timestamp": v["ts"] / 1000,
            "open": o,
            "close": c,
            "high": h,
            "low": l,
            "rango": abs(c - o),
        })
    return velas


# ============================================================
# TRADUCCIÓN DE COLORES
# ============================================================

def traducir_color_momentum(color_interno):
    mapa = {
        "lime":   ("SUBE FUERTE",   "🟢", "Alcista confirmado"),
        "green":  ("PIERDE FUERZA", "🟡", "Alcista agotándose"),
        "red":    ("CAE FUERTE",    "🔴", "Bajista confirmado"),
        "maroon": ("GIRA AL ALZA",  "🟠", "Reversión alcista temprana"),
    }
    return mapa.get(color_interno, ("DESCONOCIDO", "⚪", "Sin señal"))


# ============================================================
# ATR PERCENTIL + NR7
# ============================================================

def calcular_atr_percentile(velas, period=14, ventana=100):
    if len(velas) < period + ventana + 1:
        return None

    trs = []
    for i in range(1, len(velas)):
        high = velas[i]["high"]
        low  = velas[i]["low"]
        pc   = velas[i-1]["close"]
        tr = max(high - low, abs(high - pc), abs(low - pc))
        trs.append(tr)

    if len(trs) < period + ventana:
        return None

    atrs = []
    suma = sum(trs[:period])
    atrs.append(suma / period)
    for i in range(period, len(trs)):
        suma = suma - trs[i - period] + trs[i]
        atrs.append(suma / period)

    if len(atrs) < ventana:
        return None

    actual = atrs[-1]
    historico = atrs[-ventana:]
    menores = sum(1 for x in historico if x <= actual)
    return round((menores / len(historico)) * 100, 2)


def es_nr7(velas, period=7):
    if len(velas) < period:
        return False
    rangos = [v["rango"] for v in velas[-period:]]
    return rangos[-1] == min(rangos)


# ============================================================
# SQUEEZE MOMENTUM (LazyBear) — portado de multi_tf_coinbeaconB
# ============================================================

def _sma(serie, length):
    if len(serie) < length:
        return None
    return sum(serie[-length:]) / length


def _stdev(serie, length):
    if len(serie) < length:
        return None
    ventana = serie[-length:]
    m = sum(ventana) / length
    return (sum((x - m) ** 2 for x in ventana) / length) ** 0.5


def _linreg_value(y):
    n = len(y)
    if n < 2:
        return y[-1] if y else 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(y) / n
    num = sum((i - x_mean) * (y[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return y[-1]
    slope = num / den
    return y_mean + slope * ((n - 1) - x_mean)


def calcular_squeeze_momentum(velas, length=20, mult=2.0,
                              lengthKC=20, multKC=1.5):
    if len(velas) < 2 * lengthKC:
        return None

    highs  = [v["high"]  for v in velas]
    lows   = [v["low"]   for v in velas]
    closes = [v["close"] for v in velas]
    n = lengthKC

    basis = _sma(closes, length)
    dev   = _stdev(closes, length)
    if basis is None or dev is None:
        return None
    dev *= mult
    upperBB, lowerBB = basis + dev, basis - dev

    ma = _sma(closes, n)
    if ma is None:
        return None
    trs = []
    for i in range(len(closes)):
        if i == 0:
            trs.append(highs[i] - lows[i])
        else:
            pc = closes[i - 1]
            trs.append(max(highs[i] - lows[i],
                           abs(highs[i] - pc),
                           abs(lows[i] - pc)))
    rangema = _sma(trs, n)
    if rangema is None:
        return None
    upperKC = ma + rangema * multKC
    lowerKC = ma - rangema * multKC

    squeeze_on  = (lowerBB > lowerKC) and (upperBB < upperKC)
    squeeze_off = (lowerBB < lowerKC) and (upperBB > upperKC)

    serie_mom = []
    for i in range(n - 1, len(closes)):
        hh = max(highs[i - n + 1:i + 1])
        ll = min(lows[i - n + 1:i + 1])
        sma_c = sum(closes[i - n + 1:i + 1]) / n
        ref = 0.25 * (hh + ll) + 0.5 * sma_c
        serie_mom.append(closes[i] - ref)

    if len(serie_mom) < n + 1:
        return None

    m_actual = _linreg_value(serie_mom[-n:])
    m_prev   = _linreg_value(serie_mom[-n - 1:-1])

    if m_actual > 0:
        color = "lime" if m_actual > m_prev else "green"
    else:
        color = "red"  if m_actual < m_prev else "maroon"

    return {
        "squeeze_on":    squeeze_on,
        "squeeze_off":   squeeze_off,
        "momentum":      m_actual,
        "momentum_prev": m_prev,
        "color":         color,
    }


# ============================================================
# ADX — portado de multi_tf_coinbeaconB
# ============================================================

def calcular_adx(velas, length=14):
    n = len(velas)
    if n < length * 2:
        return None

    highs = [v["high"] for v in velas]
    lows = [v["low"] for v in velas]
    closes = [v["close"] for v in velas]

    tr_list, plus_dm_list, minus_dm_list = [], [], []
    for i in range(1, n):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i] - closes[i-1]))
        tr_list.append(tr)

        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    def smooth(data, period):
        smoothed = [sum(data[:period])]
        for i in range(period, len(data)):
            smoothed.append(smoothed[-1] - (smoothed[-1] / period) + data[i])
        return smoothed

    atr_smooth = smooth(tr_list, length)
    plus_dm_smooth = smooth(plus_dm_list, length)
    minus_dm_smooth = smooth(minus_dm_list, length)

    di_plus_list, di_minus_list, dx_list = [], [], []
    for i in range(len(atr_smooth)):
        if atr_smooth[i] == 0:
            continue
        di_plus = (plus_dm_smooth[i] / atr_smooth[i]) * 100
        di_minus = (minus_dm_smooth[i] / atr_smooth[i]) * 100
        di_plus_list.append(di_plus)
        di_minus_list.append(di_minus)

        di_sum = di_plus + di_minus
        if di_sum != 0:
            dx_list.append(abs(di_plus - di_minus) / di_sum * 100)

    if len(dx_list) < length:
        return None

    adx = sum(dx_list[-length:]) / length

    return {
        "adx": adx,
        "di_plus": di_plus_list[-1] if di_plus_list else None,
        "di_minus": di_minus_list[-1] if di_minus_list else None,
    }


# ============================================================
# ANÁLISIS DE PATRÓN BTC (ATR% + expansión + Squeeze + ADX)
# ============================================================

def analizar_patron_btc(btc_cache):
    global CONTADOR_FILTROS

    if not btc_cache:
        CONTADOR_FILTROS["SIN_EXPANSION"] += 1
        return {"pasa": False, "estado": "sin_datos", "detalle": "sin cache BTC"}

    velas = construir_velas(btc_cache, "5m")
    n = len(velas)

    if n < COMP_MIN_VELAS:
        CONTADOR_FILTROS["SIN_EXPANSION"] += 1
        return {"pasa": False, "estado": "sin_datos", "detalle": f"solo {n} velas"}

    ahora = datetime.now(timezone.utc).timestamp()

    # --- ATR% + NR7 ---
    atr_pct = calcular_atr_percentile(velas, ATR_PERIOD, ATR_VENTANA)
    nr7 = es_nr7(velas, NR7_PERIOD)

    # --- Squeeze Momentum ---
    sqz = calcular_squeeze_momentum(velas, SQZ_BB_LENGTH, SQZ_BB_MULT,
                                    SQZ_KC_LENGTH, SQZ_KC_MULT)
    if sqz is None:
        CONTADOR_FILTROS["SIN_EXPANSION"] += 1
        return {"pasa": False, "estado": "neutral",
                "detalle": "faltan velas para momentum"}

    mom_color = sqz["color"]
    mom_val   = sqz["momentum"]
    mom_nombre, mom_emoji, _ = traducir_color_momentum(mom_color)

    # --- ADX ---
    adx_data = calcular_adx(velas, ADX_LENGTH)
    if adx_data is None:
        CONTADOR_FILTROS["SIN_EXPANSION"] += 1
        return {"pasa": False, "estado": "neutral",
                "detalle": "faltan velas para ADX"}

    adx_val = adx_data["adx"]
    di_plus = adx_data["di_plus"]
    di_minus = adx_data["di_minus"]

    # ═══════════════════════════════════════════════════════════
    # BÚSQUEDA DE EXPANSIÓN
    # ═══════════════════════════════════════════════════════════
    hay_expansion = False

    for k in range(max(0, n - 8), n):
        vela_actual = velas[k]["rango"]
        anteriores = [velas[i]["rango"] for i in range(max(0, k-6), k)]
        if not anteriores:
            continue
        prom_previo = _media(anteriores)
        if not (prom_previo > 0 and vela_actual > prom_previo * COMP_FACTOR_EXPANSION):
            continue

        hay_expansion = True
        fuerza_x = vela_actual / prom_previo
        edad_h = (ahora - velas[k]["timestamp"]) / 3600

        # Filtro EDAD
        if edad_h > COMP_HORAS_RECIENTE:
            CONTADOR_FILTROS["EDAD"] += 1
            print(f"   ⏭️ Expansión rechazada por EDAD ({edad_h:.1f}h)", flush=True)
            continue

        d = "up" if velas[k]["close"] > velas[k]["open"] else "down"
        etiqueta = ""

        # Filtro MOMENTUM
        if d == "up":
            if mom_color == "maroon":
                etiqueta = "TEMPRANO"
            elif mom_color == "lime":
                etiqueta = "CONFIRMADO"
            else:
                CONTADOR_FILTROS["MOMENTUM"] += 1
                print(f"   ⏭️ Expansión UP rechazada por MOMENTUM "
                      f"({mom_nombre}, {mom_val:+.4f})", flush=True)
                continue
        else:
            if mom_color == "green":
                etiqueta = "TEMPRANO"
            elif mom_color == "red":
                etiqueta = "CONFIRMADO"
            else:
                CONTADOR_FILTROS["MOMENTUM"] += 1
                print(f"   ⏭️ Expansión DOWN rechazada por MOMENTUM "
                      f"({mom_nombre}, {mom_val:+.4f})", flush=True)
                continue

        # Filtro ADX
        if adx_val < ADX_UMBRAL:
            CONTADOR_FILTROS["ADX"] += 1
            print(f"   ⏭️ Expansión {d.upper()} rechazada por ADX "
                  f"({adx_val:.1f} < {ADX_UMBRAL})", flush=True)
            continue

        # Filtro DI
        if d == "up" and (di_plus is None or di_minus is None or di_plus <= di_minus):
            CONTADOR_FILTROS["DI"] += 1
            print(f"   ⏭️ Expansión UP rechazada por DI", flush=True)
            continue
        if d == "down" and (di_plus is None or di_minus is None or di_minus <= di_plus):
            CONTADOR_FILTROS["DI"] += 1
            print(f"   ⏭️ Expansión DOWN rechazada por DI", flush=True)
            continue

        # PASA
        CONTADOR_FILTROS["PASA"] += 1
        print(f"   ✅ EXPANSIÓN {d.upper()} CONFIRMADA — "
              f"{mom_nombre} [{etiqueta}] | ADX {adx_val:.1f} | "
              f"ATR% {atr_pct if atr_pct is not None else 'N/A'}", flush=True)

        return {
            "pasa": True,
            "estado": "expandiendo",
            "direccion": d,
            "precio": velas[k]["close"],
            "fuerza": fuerza_x,
            "edad_h": edad_h,
            "momentum":        mom_val,
            "momentum_prev":   sqz["momentum_prev"],
            "momentum_color":  mom_color,
            "momentum_nombre": mom_nombre,
            "momentum_emoji":  mom_emoji,
            "momentum_etiqueta": etiqueta,
            "squeeze_on":      sqz["squeeze_on"],
            "adx": adx_val,
            "di_plus": di_plus,
            "di_minus": di_minus,
            "atr_pct": atr_pct,
            "nr7": nr7,
            "detalle": (f"expansión {d.upper()} hace {edad_h:.1f}h "
                        f"({fuerza_x:.1f}x) | "
                        f"mom {mom_nombre} [{etiqueta}] {mom_val:+.4f} | "
                        f"ADX {adx_val:.1f} | "
                        f"ATR% {atr_pct if atr_pct is not None else 'N/A'}")
        }

    if not hay_expansion:
        CONTADOR_FILTROS["SIN_EXPANSION"] += 1

    # Compresión
    if atr_pct is not None and atr_pct < ATR_UMBRAL_COMPRESION:
        CONTADOR_FILTROS["COMPRESION"] += 1
        nr7_txt = " | NR7 ✅" if nr7 else ""
        detalle = f"compresión ATR%={atr_pct:.1f} (<{ATR_UMBRAL_COMPRESION}){nr7_txt}"
        print(f"   🌀 COMPRESIÓN — {detalle}", flush=True)
        return {
            "pasa": False,
            "estado": "comprimiendo",
            "atr_pct": atr_pct,
            "nr7": nr7,
            "detalle": detalle,
        }

    return {"pasa": False, "estado": "neutral",
            "detalle": f"rango normal (ATR% {atr_pct if atr_pct is not None else 'N/A'})"}


# ============================================================
# THROTTLE
# ============================================================

def cargar_throttle():
    try:
        req = urllib.request.Request(
            THROTTLE_REMOTE, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        return {"ok": True, "estado": data.get("estado"), "ts": data.get("ts", 0)}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": True, "estado": None, "ts": 0}
        return {"ok": False, "estado": None, "ts": 0}
    except Exception:
        return {"ok": False, "estado": None, "ts": 0}


def guardar_throttle(estado):
    try:
        with THROTTLE_FILE.open("w", encoding="utf-8") as f:
            json.dump({
                "estado": estado,
                "ts": datetime.now(timezone.utc).timestamp(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)
    except Exception as e:
        print(f"⚠️ No se pudo guardar throttle: {e}", flush=True)


# ============================================================
# PUMP EVENTS
# ============================================================

def consultar_pumping_events():
    token = os.environ.get("COINBEACON_TOKEN")
    if not token:
        return []
    types = "pump_5m"
    url = f"{COINBEACON_PUMPING_URL}?exchange=binance&types={types}&pair=USDT&limit=500"
    headers = {"User-Agent": "Mozilla/5.0", "Cookie": f"access_token={token}",
               "Accept": "application/json"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("items", [])
    except Exception as e:
        print(f"   ⚠️ pump: {str(e)[:80]}", flush=True)
        return []


def indexar_pumping_events(eventos):
    index = {}
    for ev in eventos:
        symbol_base = ev.get("symbol", "").replace("USDT", "")
        tipo = ev.get("type", "")
        spotted_at = ev.get("spottedAt", 0)
        if symbol_base not in index:
            index[symbol_base] = {}
        actual = index[symbol_base].get(tipo)
        if actual is None or spotted_at > actual.get("spottedAt", 0):
            index[symbol_base][tipo] = ev
    return index


def pd_para_symbol(symbol, pd_index):
    resultado = {"activo": False, "tipo": None, "direccion": None,
                 "pct": 0.0, "rvol": 0.0, "vol_conf": False, "edad_min": 0.0}
    if symbol not in pd_index:
        return resultado
    ahora_ts = datetime.now(timezone.utc).timestamp()
    mejor, mejor_ts = None, 0
    for tipo, ev in pd_index[symbol].items():
        spotted_at = ev.get("spottedAt", 0)
        if spotted_at > mejor_ts:
            mejor, mejor_ts = ev, spotted_at
    if not mejor:
        return resultado
    edad_min = (ahora_ts * 1000 - mejor_ts) / 60000
    resultado["tipo"] = mejor.get("type")
    resultado["direccion"] = "up"
    resultado["pct"] = mejor.get("pct", 0.0)
    resultado["rvol"] = mejor.get("rvol", 0.0)
    resultado["vol_conf"] = mejor.get("volConfirmed", False)
    resultado["edad_min"] = edad_min
    if edad_min <= PD_VENTANA_MIN and abs(resultado["pct"]) >= PD_MIN_PCT:
        resultado["activo"] = True
    return resultado


# ============================================================
# HELPERS
# ============================================================

def numero(valor):
    if valor is None:
        return None
    try:
        return float(valor)
    except (ValueError, TypeError):
        return None


def distancia_porcentual(precio, nivel):
    if precio is None or nivel is None or precio == 0:
        return None
    return ((nivel - precio) / precio) * 100


def consultar_coinbeacon(endpoint, timeframe, limit, symbol=None):
    token = os.environ.get("COINBEACON_TOKEN")
    if not token:
        raise RuntimeError("Falta COINBEACON_TOKEN")
    url = f"{endpoint}?exchange=binance&timeframe={timeframe}&quote=USDT&limit={limit}"
    if symbol:
        url += f"&symbol={symbol}"
    headers = {"User-Agent": "Mozilla/5.0", "Cookie": f"access_token={token}"}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def consultar_coinbeacon_trendlines(symbol, timeframe):
    pair = symbol + "USDT"
    result = consultar_coinbeacon(COINBEACON_TRENDLINES_URL, timeframe, 200, symbol=pair)
    items = result.get("items", [])
    lineas = []
    for item in items:
        item_symbol = str(item.get("symbol", "")).upper()
        if item_symbol != pair:
            continue
        item_data = item.get("item", {}) or {}
        touch_count = int(numero(item_data.get("touchCount", 0)) or 0)
        lineas.append({
            "symbol": item_symbol,
            "price": numero(item.get("price")),
            "bias": item.get("bias"),
            "confidence": numero(item.get("confidence", 0)),
            "direction_score": numero(item.get("directionScore", 0)),
            "line_score": numero(item_data.get("score", 0)),
            "type": item_data.get("type"),
            "currentLevel": numero(item_data.get("currentLevel")),
            "status": item_data.get("status"),
            "grade": item_data.get("grade"),
            "touchCount": touch_count,
            "fallingOrRising": item_data.get("fallingOrRising", "flat"),
            "computedAt": item.get("computedAt"),
            "timeframe": timeframe,
        })
    return lineas


def obtener_monedas_recomendadas(timeframe="15m", limit=MAX_MONEDAS_DINAMICAS):
    try:
        result = consultar_coinbeacon(COINBEACON_TRENDLINES_URL, timeframe, limit)
    except Exception as e:
        print(f"⚠️ Error monedas dinámicas: {str(e)[:80]}", flush=True)
        return list(SYMBOLS)

    items = result.get("items", [])
    if not items:
        return list(SYMBOLS)

    monedas = []
    vistos = set()
    for item in items:
        symbol = str(item.get("symbol", "")).upper()
        if not symbol.endswith("USDT"):
            continue
        base = symbol.replace("USDT", "")
        if not base.isascii():
            continue
        if base in EXCLUIR or base in vistos:
            continue
        vistos.add(base)
        monedas.append(base)

    if not monedas:
        return list(SYMBOLS)

    print(f"📡 CoinBeacon recomienda {len(monedas)} monedas", flush=True)
    return monedas[:limit]


def consultar_volume_coinbeacon():
    result = consultar_coinbeacon(COINBEACON_VOLUME_URL, "4h", 500)
    items = result.get("items", [])
    return [{
        "symbol": item.get("symbol"),
        "price": numero(item.get("price")),
        "volumeTrend": numero(item.get("volumeTrend")) or 0.0,
        "volume24h": numero(item.get("volume24h")) or 0.0,
        "rvoll": numero(item.get("rvoll")) or 0.0,
        "direction": item.get("direction") or "unknown",
        "status": item.get("status") or "",
        "spottedAt": item.get("spottedAt"),
    } for item in items]


def clasificar_estructura(touches):
    if touches >= 11: return "VERY_STRONG"
    if touches >= 8: return "STRONG"
    if touches >= 6: return "VALID"
    if touches >= 4: return "WEAK"
    return "IGNORE"


def obtener_touch_score(touches):
    if touches < 4: return 0
    tabla = {4:8, 5:12, 6:16, 7:20, 8:25, 9:28, 10:31}
    if touches >= 11: return 35 + min((touches - 11) * 2, 15)
    return tabla.get(touches, 0)


def obtener_proximidad_score(distance_pct, timeframe):
    if distance_pct is None or distance_pct <= 0:
        return 0
    max_dist = 2.0 if timeframe == "1h" else 1.5
    if distance_pct > max_dist:
        return 0
    return 35 - ((distance_pct / max_dist) * 30)


def obtener_timeframe_score(timeframe):
    return {"1h": 19, "15m": 15}.get(timeframe, 0)


def obtener_status_score(status):
    s = str(status).lower()
    score = 0
    if "near breakout" in s: score += 20
    if "near breakdown" in s: score += 20
    if "retest" in s: score += 15
    if "broken" in s: score -= 30
    if "confirmed" in s: score += 10
    return score


def obtener_confidence_score(confidence):
    if confidence is None:
        return 0
    if confidence <= 1: normalized = confidence * 100
    elif confidence <= 10: normalized = confidence * 10
    else: normalized = confidence
    normalized = max(0, min(100, normalized))
    return (normalized / 100) * 20


def obtener_smart_score(line):
    smart = line.get("line_score") or 0
    if smart:
        return min((smart / 200) * 15, 15)
    return 0


def calcular_score_linea(linea):
    touches = linea.get("touchCount", 0)
    estructura = clasificar_estructura(touches)
    structure_score = obtener_touch_score(touches)
    proximity_score = obtener_proximidad_score(linea.get("distance_pct"), linea.get("timeframe"))
    timeframe_score = obtener_timeframe_score(linea.get("timeframe"))
    status_score = obtener_status_score(linea.get("status"))
    confidence_score = obtener_confidence_score(linea.get("confidence"))
    smart_score = obtener_smart_score(linea)
    bonus = 0
    if linea.get("type") == "support" and "rising" in str(linea.get("fallingOrRising", "")).lower():
        bonus += 10
    if linea.get("type") == "resistance" and "falling" in str(linea.get("fallingOrRising", "")).lower():
        bonus += 10
    total_score = (structure_score + proximity_score + timeframe_score
                   + status_score + confidence_score + smart_score + bonus)
    return {
        "structure_quality": estructura, "structure_score": structure_score,
        "proximity_score": proximity_score, "timeframe_score": timeframe_score,
        "status_score": status_score, "confidence_score": confidence_score,
        "smart_score": smart_score, "bonus_score": bonus,
        "total_score": total_score,
    }


def analizar_coinbeacon(symbol):
    print(f"\n📈 COINBEACON — {symbol}", flush=True)
    todas = []
    for timeframe in TIMEFRAMES:
        try:
            lineas = consultar_coinbeacon_trendlines(symbol, timeframe)
            print(f"   {timeframe}: {len(lineas)} líneas", flush=True)
            todas.extend(lineas)
        except Exception as e:
            print(f"   ❌ {timeframe}: {e}", flush=True)

    if not todas:
        return {"price": None, "lines": []}

    precio = None
    for linea in todas:
        if linea.get("price") is not None:
            precio = linea["price"]
            break

    if precio is not None:
        print(f"💰 Precio: ${precio:.6f}", flush=True)

    for linea in todas:
        linea["distance_pct"] = distancia_porcentual(precio, linea.get("currentLevel"))
        if linea["distance_pct"] is not None:
            linea.update(calcular_score_linea(linea))
        else:
            linea["structure_quality"] = "UNKNOWN"
            linea["total_score"] = 0

    return {"price": precio, "lines": todas}


def send_telegram_message(message):
    if not hora_permite_envio():
        return False
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = f"chat_id={urllib.parse.quote(str(chat_id))}&text={urllib.parse.quote(message)}".encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result.get("ok", False)
    except Exception as e:
        print(f"Error Telegram: {e}", flush=True)
        return False


def guardar_en_csv(alert_data):
    fieldnames = [
        "hora_lima", "symbol", "bias", "type", "timeframe",
        "currentLevel", "touchCount", "confidence", "rvoll", "volumeTrend",
        "score", "status", "fallingOrRising", "price",
        "tipo_efectivo", "rsi1h", "rsi15m", "tendencia",
        "btc_dir", "btc_modo", "btc_estado", "atr_pct_btc",
        "btc_momentum", "btc_adx",
        "pd_tipo", "pd_pct", "pd_rvol", "pd_conf",
        "smart_direction", "smart_line_score",
        "structure_quality", "total_score"
    ]
    if not CSV_FILE.exists():
        with CSV_FILE.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fieldnames).writeheader()
    with CSV_FILE.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fieldnames).writerow(alert_data)


def guardar_historico_linea(symbol, linea, rsi_data, hora_lima, tipo_efectivo=None):
    fieldnames = [
        "hora_lima", "symbol", "timeframe", "type", "tipo_efectivo",
        "currentLevel", "touchCount", "confidence", "status",
        "fallingOrRising", "distance_pct",
        "rsi_moneda_1h", "rsi_moneda_15m",
        "price", "bias", "direction_score", "line_score",
        "structure_quality", "total_score"
    ]
    if not HISTORICO_CSV_FILE.exists():
        with HISTORICO_CSV_FILE.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fieldnames).writeheader()
    rsi1 = rsi_data.get("1h", {}).get("rsi14") if rsi_data else None
    rsi15 = rsi_data.get("15m", {}).get("rsi14") if rsi_data else None
    row = {
        "hora_lima": hora_lima, "symbol": symbol,
        "timeframe": linea.get("timeframe"), "type": linea.get("type"),
        "tipo_efectivo": tipo_efectivo or linea.get("type"),
        "currentLevel": linea.get("currentLevel"),
        "touchCount": linea.get("touchCount", 0),
        "confidence": linea.get("confidence"), "status": linea.get("status"),
        "fallingOrRising": linea.get("fallingOrRising"),
        "distance_pct": linea.get("distance_pct"),
        "rsi_moneda_1h": rsi1, "rsi_moneda_15m": rsi15,
        "price": linea.get("price"), "bias": linea.get("bias"),
        "direction_score": linea.get("direction_score"),
        "line_score": linea.get("line_score"),
        "structure_quality": linea.get("structure_quality"),
        "total_score": linea.get("total_score"),
    }
    with HISTORICO_CSV_FILE.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fieldnames).writerow(row)


def cargar_estado():
    if not STATE_FILE.exists():
        return []
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            estado = json.load(f)
        if isinstance(estado, list):
            return estado
    except Exception:
        pass
    return []


def limpiar_estado(previous_state, now_ts):
    resultado = []
    for item in previous_state:
        da = item.get("detected_at")
        if da is None:
            resultado.append(item)
            continue
        try:
            if (now_ts - float(da)) / 3600 < MAX_HISTORY_HOURS:
                resultado.append(item)
        except (ValueError, TypeError):
            continue
    return resultado


def guardar_estado(estado):
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2)


def analizar_confluencia(symbol, coin_data, rsi_data, volume_by_symbol,
                         btc_context, pd_index=None):
    precio = coin_data.get("price")
    lineas = coin_data.get("lines", [])
    if precio is None:
        return []

    btc_dir = btc_context.get("btc_dir", "flat")
    btc_modo = btc_context.get("btc_modo", "neutro")

    if btc_dir != "up":
        return []

    pd = pd_para_symbol(symbol, pd_index or {})

    volume = volume_by_symbol.get(symbol + "USDT") or volume_by_symbol.get(symbol)
    if volume:
        vol_status = str(volume.get("status", "")).lower()
        is_spike = "spike" in vol_status
        is_dry_up = "dry" in vol_status or "squeeze" in vol_status
        is_exhaustion = "exhaustion" in vol_status
    else:
        is_spike = is_dry_up = is_exhaustion = False

    tendencia_15m = rsi_data.get("15m", {}).get("tendencia") if rsi_data else None
    tendencia_1h = rsi_data.get("1h", {}).get("tendencia") if rsi_data else None

    candidates_by_tf = {}
    for linea in lineas:
        tipo = linea.get("type")
        distancia = linea.get("distance_pct")
        if distancia is None:
            continue

        status = str(linea.get("status", "")).lower()
        inclinacion = str(linea.get("fallingOrRising", "")).lower()

        if "broke down" in status or "failed break" in status or "near breakdown" in status:
            continue

        tipo_operacion = None
        if tipo == "resistance" and ("near breakout" in status or "broke up" in status):
            tipo_operacion = "breakout"
        elif "retest" in status and "holding" in status:
            tipo_operacion = "rebote"
        elif tipo == "support" and inclinacion == "rising" and abs(distancia) <= 1.0:
            tipo_operacion = "rebote"

        if tipo_operacion is None:
            continue

        quality = linea.get("structure_quality", "IGNORE")
        if quality in ("IGNORE", "WEAK"):
            continue

        confidence = linea.get("confidence") or 0
        if confidence < SMART_LINE_SCORE_MIN:
            continue

        bias_linea = str(linea.get("bias", "")).lower()
        dir_score = linea.get("direction_score") or 0

        if bias_linea == "bearish":
            continue
        if dir_score < SMART_DIRECTION_SCORE_MIN_LONG:
            continue
        if abs(distancia) > 1.0:
            continue

        total_score = linea.get("total_score", 0)
        if total_score < SMART_TOTAL_SCORE_MIN:
            continue

        tf = linea.get("timeframe")
        tendencia = tendencia_1h if tf == "1h" else tendencia_15m

        if pd["activo"]:
            if pd["direccion"] == "down":
                continue

        pd_confirmado = pd["activo"] and pd["direccion"] == "up"

        candidates_by_tf.setdefault(tf, []).append({
            "line": linea,
            "operacion": "LONG",
            "tipo_operacion": tipo_operacion,
            "score": total_score,
            "tendencia": tendencia,
            "tipo_efectivo": tipo,
            "pd_confirmado": pd_confirmado,
        })

    final_candidates = []
    for tf, items in candidates_by_tf.items():
        if items:
            final_candidates.append(max(items, key=lambda x: x["score"]))

    final_candidates.sort(key=lambda x: x["score"], reverse=True)

    seen = {}
    for cand in final_candidates:
        sym = cand["line"].get("symbol", "").replace("USDT", "").upper()
        tf = cand["line"].get("timeframe", "15m")
        if sym not in seen:
            seen[sym] = cand
        else:
            tf_prev = seen[sym]["line"].get("timeframe", "15m")
            if tf == "1h" and tf_prev == "15m":
                seen[sym] = cand

    filtered = list(seen.values())
    filtered.sort(key=lambda x: x["score"], reverse=True)

    alerts = []
    for cand in filtered:
        linea = cand["line"]
        alerts.append({
            "symbol": symbol, "line": linea,
            "volume": volume if volume else {},
            "score": cand["score"],
            "tipo_operacion": cand.get("tipo_operacion", "rebote"),
            "is_spike": is_spike, "is_dry_up": is_dry_up,
            "is_exhaustion": is_exhaustion,
            "is_near_breakout": "near breakout" in str(linea.get("status", "")).lower(),
            "is_near_breakdown": "near breakdown" in str(linea.get("status", "")).lower(),
            "is_retest": "retest" in str(linea.get("status", "")).lower(),
            "bias": "LONG",
            "tipo_efectivo": cand.get("tipo_efectivo"),
            "rsi1h": rsi_data.get("1h", {}).get("rsi14") if rsi_data else None,
            "rsi15m": rsi_data.get("15m", {}).get("rsi14") if rsi_data else None,
            "tendencia": cand.get("tendencia"),
            "btc_dir": btc_dir, "btc_modo": btc_modo,
            "structure_quality": linea.get("structure_quality"),
            "total_score": cand["score"],
            "pd_confirmado": cand.get("pd_confirmado", False),
            "pd_activo": pd["activo"], "pd_tipo": pd["tipo"],
            "pd_pct": pd["pct"], "pd_rvol": pd["rvol"], "pd_vol_conf": pd["vol_conf"],
            "smart_confidence": linea.get("confidence"),
            "smart_direction": linea.get("direction_score"),
            "smart_bias": linea.get("bias"),
            "smart_line_score": linea.get("line_score"),
            "entry_price": linea.get("price"),
        })
    return alerts


def procesar_alertas(alerts, filtered_previous, btc_context, pd_index):
    sent_count = 0
    long_count = 0
    new_state = list(filtered_previous)
    now_ts = datetime.now(timezone.utc).timestamp()
    now_lima = (datetime.now(timezone.utc) + LIMA_OFFSET).strftime("%Y-%m-%d %H:%M:%S")

    for alert in alerts:
        symbol = alert["symbol"]
        line = alert["line"]
        vol = alert["volume"]

        nivel_nuevo = line.get("currentLevel") or 0
        duplicado = False
        for item in new_state:
            if item.get("symbol") != symbol: continue
            if item.get("type") != line.get("type"): continue
            if item.get("timeframe") != line.get("timeframe"): continue
            nivel_prev = item.get("currentLevel") or 0
            if nivel_prev > 0 and nivel_nuevo > 0:
                diff_pct = abs(nivel_nuevo - nivel_prev) / nivel_prev * 100
                if diff_pct < 0.3:
                    duplicado = True
                    break
            else:
                duplicado = True
                break

        if duplicado:
            continue

        long_count += 1

        tipo_linea = (line.get("type") or "?").upper()
        tipo_efectivo = (alert.get("tipo_efectivo") or line.get("type") or "").upper()
        inclinacion = (line.get("fallingOrRising", "flat")).upper()
        precio_actual = line.get("price") or 0.0
        nivel_linea = line.get("currentLevel") or 0.0
        tendencia = alert.get("tendencia") or "?"
        flecha = "↑" if tendencia == "up" else "↓" if tendencia == "down" else "?"
        flip_text = " [FLIP]" if tipo_efectivo != tipo_linea else ""
        tipo_op_txt = (alert.get("tipo_operacion") or "rebote").upper()

        tags = []
        if alert.get("is_near_breakout"): tags.append("🔥 NEAR BREAKOUT")
        if alert.get("is_retest"): tags.append("🔄 RETEST")
        if alert.get("is_spike"): tags.append("🚀 VOL SPIKE")
        if alert.get("pd_confirmado"): tags.append("🔥 PD CONFIRMADO")
        tag_text = " ".join(tags) if tags else ""

        rsi1h_str = f"{alert.get('rsi1h'):.2f}" if alert.get('rsi1h') is not None else "N/A"
        rsi15m_str = f"{alert.get('rsi15m'):.2f}" if alert.get('rsi15m') is not None else "N/A"

        btc_dir_str = alert.get("btc_dir", "flat").upper()
        btc_modo_str = alert.get("btc_modo", "neutro").upper()

        # --- Momentum + ADX BTC ---
        pat = btc_context.get("patron_btc") or {}
        mom_color_interno = (pat.get("momentum_color") or "").lower()
        mom_nombre, mom_emoji, mom_signif = traducir_color_momentum(mom_color_interno)
        mom_val = pat.get("momentum")
        mom_etq = pat.get("momentum_etiqueta", "")

        if mom_etq == "TEMPRANO":
            badge = "🟠 TEMPRANO"
        elif mom_etq == "CONFIRMADO":
            badge = "🟢 CONFIRMADO"
        else:
            badge = ""

        mom_val_txt = f"{mom_val:+.4f}" if mom_val is not None else "N/A"
        mom_linea = f"📈 BTC Mom: {mom_emoji} {mom_nombre} {mom_val_txt} {badge}\n"

        adx_val = pat.get("adx")
        adx_emoji = "✅" if adx_val and adx_val >= ADX_UMBRAL else "⚠️"
        adx_str = f"{adx_val:.1f}" if adx_val is not None else "N/A"
        mom_linea += f"📊 BTC ADX: {adx_emoji} {adx_str}\n"

        btc_atr = pat.get("atr_pct")
        btc_atr_str = f"{btc_atr:.1f}" if isinstance(btc_atr, (int, float)) else "N/A"
        mom_linea += f"📉 BTC ATR%: {btc_atr_str}\n"

        pd_tipo = alert.get("pd_tipo")
        pd_pct = alert.get("pd_pct", 0.0)
        pd_rvol = alert.get("pd_rvol", 0.0)
        pd_vol_conf = alert.get("pd_vol_conf", False)
        pd_confirmado = alert.get("pd_confirmado", False)

        if pd_tipo:
            vol_mark = " ⚡" if pd_vol_conf else ""
            confirm = " ✅ CONFIRMA" if pd_confirmado else ""
            pd_linea = f"📡 PD: 🚀 PUMP 5m {pd_pct:+.2f}% (rvol {pd_rvol:.2f}){vol_mark}{confirm}"
        else:
            pd_linea = "📡 PD: sin movimiento"

        smart_conf = alert.get("smart_confidence")
        smart_dir = alert.get("smart_direction")
        smart_bias = alert.get("smart_bias")
        smart_line_score = alert.get("smart_line_score")
        smart_conf_str = f"{smart_conf:.1f}/10" if smart_conf is not None else "N/A"
        smart_dir_str = f"{smart_dir:.0f}" if smart_dir is not None else "N/A"
        smart_line_score_str = f"{smart_line_score:.1f}" if smart_line_score is not None else "N/A"
        smart_linea = f"🧠 Smart: {smart_conf_str} | Dir {smart_dir_str} | Bias {str(smart_bias).upper()} | Score {smart_line_score_str}"

        msg = (
            f"🧠 MULTI SMART\n"
            f"🟢 LONG {symbol} [{tipo_op_txt}]\n"
            f"📈 ${precio_actual:.6f}\n"
            f"📉 {tipo_linea}{flip_text} ({inclinacion})\n"
            f"   • TF: {line.get('timeframe', '')}\n"
            f"   • Nivel: ${nivel_linea:.6f}\n"
            f"   • Toques: {line.get('touchCount', 0)} ({alert['structure_quality']})\n"
            f"🎯 Score: {alert['score']:.1f}\n"
            f"{mom_linea}"
            f"📈 RSI 1h={rsi1h_str} | 15m={rsi15m_str}\n"
            f"{pd_linea}\n"
            f"{smart_linea}\n"
            f"🕐 {now_lima}"
        )
        if tag_text:
            msg += f"\n{tag_text}"

        if send_telegram_message(msg):
            sent_count += 1
            print(f"   🟢 LONG {symbol} [{tipo_op_txt}] → enviado", flush=True)

            guardar_en_csv({
                "hora_lima": now_lima, "symbol": symbol, "bias": "LONG",
                "type": line.get("type", ""), "timeframe": line.get("timeframe", ""),
                "currentLevel": str(nivel_linea),
                "touchCount": str(line.get("touchCount", 0)),
                "confidence": str(line.get("confidence", 0)),
                "rvoll": f"{vol.get('rvoll', 0):.2f}",
                "volumeTrend": f"{vol.get('volumeTrend', 0):.1f}",
                "score": f"{alert['score']:.1f}", "status": line.get("status", ""),
                "fallingOrRising": line.get("fallingOrRising", "flat"),
                "price": str(precio_actual), "tipo_efectivo": tipo_efectivo,
                "rsi1h": rsi1h_str, "rsi15m": rsi15m_str, "tendencia": tendencia,
                "btc_dir": btc_dir_str, "btc_modo": btc_modo_str,
                "btc_estado": "FAVORABLE",
                "atr_pct_btc": btc_atr_str,
                "btc_momentum": mom_nombre,
                "btc_adx": adx_str,
                "pd_tipo": pd_tipo or "",
                "pd_pct": f"{pd_pct:+.2f}" if pd_tipo else "",
                "pd_rvol": f"{pd_rvol:.2f}" if pd_tipo else "",
                "pd_conf": "1" if pd_vol_conf else "0",
                "smart_direction": smart_dir_str,
                "smart_line_score": smart_line_score_str,
                "structure_quality": alert['structure_quality'],
                "total_score": f"{alert['score']:.1f}",
            })

            new_state.append({
                "symbol": symbol, "type": line.get("type"),
                "timeframe": line.get("timeframe"),
                "currentLevel": nivel_linea, "detected_at": now_ts,
                "entry_price": precio_actual,
            })

    return sent_count, long_count, new_state


def analizar_moneda(symbol, volume_by_symbol, btc_context, hora_lima, pd_index=None):
    coin_data = analizar_coinbeacon(symbol)
    rsi_data = analizar_rsi_de_cache(symbol)

    for linea in coin_data.get("lines", []):
        tipo = linea.get("type")
        dist = linea.get("distance_pct")
        tipo_efectivo = tipo
        if tipo == "resistance" and dist is not None and dist < 0:
            tipo_efectivo = "support"
        elif tipo == "support" and dist is not None and dist > 0:
            tipo_efectivo = "resistance"
        guardar_historico_linea(symbol, linea, rsi_data, hora_lima, tipo_efectivo)

    alerts = analizar_confluencia(symbol, coin_data, rsi_data, volume_by_symbol,
                                   btc_context, pd_index)
    return alerts, coin_data, rsi_data


def analizar_rsi_de_cache(symbol):
    cache = leer_cache_remoto(symbol)
    if not cache:
        return {}
    datos = {}
    if cache.get("rsi15") is not None:
        datos["15m"] = {"price": cache.get("price"), "rsi14": cache["rsi15"],
                        "tendencia": cache.get("dir15", "?")}
    if cache.get("rsi1h") is not None:
        datos["1h"] = {"price": cache.get("price"), "rsi14": cache["rsi1h"],
                       "tendencia": cache.get("dir1h", "?")}
    return datos


# ============================================================
# RESUMEN DIAGNÓSTICO
# ============================================================

def imprimir_resumen_diagnostico():
    total = sum(CONTADOR_FILTROS.values())
    print("\n" + "=" * 70, flush=True)
    print("🔬 DIAGNÓSTICO — ¿Qué detuvo cada señal en este run?", flush=True)
    print("=" * 70, flush=True)

    if total == 0:
        print("   (Sin evaluaciones registradas)", flush=True)
        return

    orden = sorted(CONTADOR_FILTROS.items(), key=lambda x: -x[1])
    for nombre, count in orden:
        if count == 0:
            continue
        pct = (count / total) * 100
        barra = "█" * int(pct / 3)
        print(f"   {nombre:15s} {count:3d}  ({pct:5.1f}%)  {barra}", flush=True)

    print("-" * 70, flush=True)
    print(f"   TOTAL evaluaciones: {total}", flush=True)


def main():
    global CONTADOR_FILTROS
    CONTADOR_FILTROS = {k: 0 for k in CONTADOR_FILTROS}

    print("\n" + "=" * 70, flush=True)
    print("🚀 MULTI SMART — Fase 2.2 (ATR% + NR7 + Squeeze + ADX)", flush=True)
    print("=" * 70, flush=True)
    print(f"\nHora UTC: {datetime.now(timezone.utc).isoformat()}", flush=True)

    previous_state = cargar_estado()

    print("\n🔍 FILTRO BTC (compresión → expansión + momentum + ADX)...", flush=True)
    btc_cache_full = leer_cache_remoto(BTC_SYMBOL)
    patron_btc = analizar_patron_btc(btc_cache_full)
    print(f"   Estado:  {patron_btc['estado'].upper()}", flush=True)
    print(f"   Detalle: {patron_btc['detalle']}", flush=True)

    ahora_ts = datetime.now(timezone.utc).timestamp()
    lectura = cargar_throttle()

    if not lectura["ok"]:
        print("   ⚠️ Throttle remoto no disponible", flush=True)
        debe_avisar = False
    else:
        estado_anterior = lectura["estado"]
        ts_anterior = lectura["ts"]
        estado_actual = patron_btc["estado"]

        if estado_anterior is None:
            debe_avisar = True
        else:
            cambio_estado = (estado_actual != estado_anterior)
            reintentar = (ahora_ts - ts_anterior) > (COMP_THROTTLE_MIN * 60)
            debe_avisar = cambio_estado or reintentar

    estado_actual = patron_btc["estado"]

    if debe_avisar:
        guardar_throttle(estado_actual)
        ahora_lima_str = (datetime.now(timezone.utc) + LIMA_OFFSET).strftime("%Y-%m-%d %H:%M")

        if estado_actual == "comprimiendo":
            atr_pct_txt = patron_btc.get("atr_pct")
            nr7_val = patron_btc.get("nr7", False)
            atr_str = f"ATR%: {atr_pct_txt:.1f}" if atr_pct_txt is not None else "ATR%: N/A"
            nr7_str = " | NR7 ✅" if nr7_val else ""
            send_telegram_message(
                f"🧠 MULTI SMART\n"
                f"🌀 COMPRESIÓN BTC DETECTADA\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"   {atr_str}{nr7_str}\n"
                f"⏳ Esperando ruptura (UP o DOWN)\n"
                f"🕐 {ahora_lima_str} (Lima)"
            )
        elif estado_actual == "expandiendo":

        # ═══════════════════════════════════════════════════════════
        # SOLO se envía Telegram cuando hay expansión CONFIRMADA.
        # Estados "comprimiendo" y "neutral" quedan en SILENCIO.
        # ═══════════════════════════════════════════════════════════
        if estado_actual == "expandiendo":
            direccion = patron_btc.get("direccion", "?")
            emoji_op = "🟢" if direccion == "up" else "🔴"
            op_txt = "LONG" if direccion == "up" else "SHORT"
            precio_actual = patron_btc.get("precio", 0)
            fuerza = patron_btc.get("fuerza", 0)
            edad_h = patron_btc.get("edad_h", 0)

            mom_color_interno = (patron_btc.get("momentum_color") or "").lower()
            mom_nombre, mom_emoji, mom_signif = traducir_color_momentum(mom_color_interno)
            mom_val = patron_btc.get("momentum")
            mom_etq = patron_btc.get("momentum_etiqueta", "")

            if mom_etq == "TEMPRANO":
                badge = "🟠 TEMPRANO"
            elif mom_etq == "CONFIRMADO":
                badge = "🟢 CONFIRMADO"
            ahora_lima_str = (datetime.now(timezone.utc) + LIMA_OFFSET).strftime("%Y-%m-%d %H:%M")

            # Solo notificamos la expansión si es UP (los LONGs son los que operamos)
            if direccion == "up":
                emoji_op = "🟢"
                op_txt = "LONG"
                precio_actual = patron_btc.get("precio", 0)
                fuerza = patron_btc.get("fuerza", 0)
                edad_h = patron_btc.get("edad_h", 0)

                mom_color_interno = (patron_btc.get("momentum_color") or "").lower()
                mom_nombre, mom_emoji, mom_signif = traducir_color_momentum(mom_color_interno)
                mom_val = patron_btc.get("momentum")
                mom_etq = patron_btc.get("momentum_etiqueta", "")

                if mom_etq == "TEMPRANO":
                    badge = "🟠 TEMPRANO"
                elif mom_etq == "CONFIRMADO":
                    badge = "🟢 CONFIRMADO"
                else:
                    badge = ""

                mom_val_txt = f"{mom_val:+.4f}" if mom_val is not None else "N/A"
                adx_val = patron_btc.get("adx")
                adx_str = f"{adx_val:.1f}" if adx_val is not None else "N/A"
                atr_pct_txt = patron_btc.get("atr_pct")
                atr_str = f"{atr_pct_txt:.1f}" if atr_pct_txt is not None else "N/A"

                send_telegram_message(
                    f"🧠 MULTI SMART\n"
                    f"🔥 EXPANSIÓN UP — {emoji_op} {op_txt} BTC\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"📍 Precio: ${precio_actual:,.2f}\n"
                    f"📊 Fuerza: {fuerza:.1f}x hace {edad_h:.1f}h\n"
                    f"📈 Momentum: {mom_emoji} {mom_nombre} {mom_val_txt} — {badge}\n"
                    f"📊 ADX: {adx_str} | ATR%: {atr_str}\n"
                    f"✅ Analizando monedas...\n"
                    f"🕐 {ahora_lima_str} (Lima)"
                )
            else:
                badge = ""

            mom_val_txt = f"{mom_val:+.4f}" if mom_val is not None else "N/A"
            adx_val = patron_btc.get("adx")
            adx_str = f"{adx_val:.1f}" if adx_val is not None else "N/A"
            atr_pct_txt = patron_btc.get("atr_pct")
            atr_str = f"{atr_pct_txt:.1f}" if atr_pct_txt is not None else "N/A"

            send_telegram_message(
                f"🧠 MULTI SMART\n"
                f"🔥 EXPANSIÓN {direccion.upper()} — {emoji_op} {op_txt} BTC\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📍 Precio: ${precio_actual:,.2f}\n"
                f"📊 Fuerza: {fuerza:.1f}x hace {edad_h:.1f}h\n"
                f"📈 Momentum: {mom_emoji} {mom_nombre} {mom_val_txt} — {badge}\n"
                f"📊 ADX: {adx_str} | ATR%: {atr_str}\n"
                f"✅ Filtro pasa → analizando monedas...\n"
                f"🕐 {ahora_lima_str} (Lima)"
            )
                # Expansión DOWN → solo log interno, sin Telegram
                print(f"   🔇 Expansión DOWN confirmada — sin envío (solo LONGs)", flush=True)
        else:
            print(f"   🔇 Estado {estado_actual.upper()} — sin envío (solo expansión UP)", flush=True)
    else:
        print("   🔇 Throttle activo — sin envío", flush=True)

    if COMP_MODO_FILTRO == "hard" and not patron_btc["pasa"]:
        print(f"\n⏸️ Filtro no pasó ({estado_actual.upper()}) — abortando en silencio", flush=True)
        imprimir_resumen_diagnostico()
        now_ts = datetime.now(timezone.utc).timestamp()
        new_state = list(previous_state)
        guardar_estado(new_state)
        return

    if estado_actual == "expandiendo" and patron_btc.get("direccion") == "down":
        print(f"\n⚠️ BTC DOWN → aviso informativo", flush=True)
        ahora_lima_str = (datetime.now(timezone.utc) + LIMA_OFFSET).strftime("%Y-%m-%d %H:%M")
        send_telegram_message(
            f"🧠 MULTI SMART\n"
            f"📉 BTC DOWN DETECTADO\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"   {patron_btc['detalle']}\n"
            f"⏸️ Solo LONGs → no se analizan monedas\n"
            f"🕐 {ahora_lima_str} (Lima)"
        )
        print(f"\n🔇 BTC DOWN confirmado — sin envío (solo LONGs)", flush=True)
        now_ts = datetime.now(timezone.utc).timestamp()
        new_state = list(previous_state)
        guardar_estado(new_state)
        imprimir_resumen_diagnostico()
        return

    print(f"\n✅ Filtro pasa → analizando monedas", flush=True)

    btc_context = {
        "btc_dir": "up",
        "btc_modo": "fuerte",
        "estado": "FAVORABLE",
        "price": btc_cache_full.get("price") if btc_cache_full else None,
        "patron_btc": patron_btc,
    }

    print("\n📡 PUMP EVENTS", flush=True)
    pd_eventos = consultar_pumping_events()
    print(f"   Eventos: {len(pd_eventos)}", flush=True)
    pd_index = indexar_pumping_events(pd_eventos)

    try:
        volume_data = consultar_volume_coinbeacon()
        print(f"📊 Volumen: {len(volume_data)}", flush=True)
    except Exception as e:
        print(f"❌ Volumen: {e}", flush=True)
        volume_data = []
    volume_by_symbol = {str(i.get("symbol", "")).upper(): i for i in volume_data}

    monedas_a_analizar = obtener_monedas_recomendadas(timeframe="15m", limit=MAX_MONEDAS_DINAMICAS)
    print(f"   → Analizando {len(monedas_a_analizar)} monedas", flush=True)

    now_ts = datetime.now(timezone.utc).timestamp()
    filtered_previous = limpiar_estado(previous_state, now_ts)
    hora_lima = (datetime.now(timezone.utc) + LIMA_OFFSET).strftime("%Y-%m-%d %H:%M:%S")

    all_alerts = []
    for symbol in monedas_a_analizar:
        alerts, _, _ = analizar_moneda(symbol, volume_by_symbol, btc_context, hora_lima, pd_index)
        all_alerts.extend(alerts)

    all_alerts.sort(key=lambda x: -x["score"])

    sent_count, long_count, new_state = procesar_alertas(
        all_alerts, filtered_previous, btc_context, pd_index
    )

    guardar_estado(new_state)

    imprimir_resumen_diagnostico()

    print("\n" + "=" * 70, flush=True)
    print("📢 RESULTADO FINAL", flush=True)
    print("=" * 70, flush=True)
    print(f"Alertas LONG: {sent_count}", flush=True)
    print(f"Monedas analizadas: {len(monedas_a_analizar)}", flush=True)
    print("\n🏁 PROGRAMA TERMINADO", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR GENERAL: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise
