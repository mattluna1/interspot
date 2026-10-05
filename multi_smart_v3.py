#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import json
import os
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# MULTI SMART V3 — Fase 3.0
#   BTC portero + LONG/SHORT por ALT (momentum propio) + salidas
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

SMART_TOTAL_SCORE_MIN = 80

# ============================================================
# BTC PORTERO
# ============================================================
ATR_PERIOD            = 14
ATR_VENTANA           = 100
ATR_UMBRAL_COMPRESION = 20.0
NR7_PERIOD            = 7

COMP_MIN_VELAS        = 12

# ============================================================
# SQUEEZE MOMENTUM
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
# REPOS
# ============================================================
CACHE_BTC_BASE = (
    "https://raw.githubusercontent.com/mattluna1/"
    "interspot/main/data/cache"
)
CACHE_ALTS_BASE = (
    "https://raw.githubusercontent.com/mattlunaluna2/"
    "coins/main/data/cache"
)
CACHE_MAX_EDAD_MIN = 40

# ============================================================
# CARPETA DE DATA SEPARADA (no mezclar con multi_smart viejo)
# ============================================================
DATA_DIR = Path("data_v3")
DATA_DIR.mkdir(exist_ok=True)

STATE_FILE = DATA_DIR / "multi_smart_v3_state.json"
CSV_FILE = DATA_DIR / "multi_smart_v3.csv"

LIMA_OFFSET = timedelta(hours=-5)
HORA_INICIO = 0
HORA_FIN = 24

PD_VENTANA_MIN = 10

CONTADOR_FILTROS = {
    "BTC_COMPRIMIDO": 0,
    "BTC_NEUTRAL": 0,
    "BTC_PASA": 0,
    "SIN_LINEAS": 0,
    "MOMENTUM_ALT": 0,
    "ADX_ALT": 0,
    "PASA_LONG": 0,
    "PASA_SHORT": 0,
    "SIN_CACHE": 0,
}


def hora_permite_envio():
    now_lima = datetime.now(timezone.utc) + LIMA_OFFSET
    return HORA_INICIO <= now_lima.hour < HORA_FIN


COINBEACON_TRENDLINES_URL = "https://api.coinbeacon.io/detectors/trendlines"
COINBEACON_VOLUME_URL = "https://api.coinbeacon.io/detectors/volume"
COINBEACON_PUMPING_URL = "https://api.coinbeacon.io/pumping/events"


# ============================================================
# CACHE
# ============================================================

def leer_cache_remoto(symbol):
    if symbol == "BTC":
        base = CACHE_BTC_BASE
    else:
        base = CACHE_ALTS_BASE
    url = f"{base}/{symbol}.json"

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
        "pulso": pulso,
        "velas_5m": data.get("velas_5m", []),
        "velas_15m": data.get("velas_15m", []),
        "velas_1h": data.get("velas_1h", []),
    }


def construir_velas(cache, tf="15m"):
    if not cache:
        return []
    velas_raw = cache.get(f"velas_{tf}") or []
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
            "open": o, "close": c,
            "high": h, "low": l,
            "rango": abs(c - o),
        })
    return velas


def traducir_color_momentum(color_interno):
    mapa = {
        "lime":   ("SUBE FUERTE",   "🟢", "Alcista confirmado"),
        "green":  ("PIERDE FUERZA", "🟡", "Alcista agotándose"),
        "red":    ("CAE FUERTE",    "🔴", "Bajista confirmado"),
        "maroon": ("GIRA AL ALZA",  "🟠", "Reversión alcista temprana"),
    }
    return mapa.get(color_interno, ("DESCONOCIDO", "⚪", "Sin señal"))


# ============================================================
# INDICADORES
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


def calcular_squeeze_momentum(velas, length=20, mult=2.0, lengthKC=20, multKC=1.5):
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
            trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    rangema = _sma(trs, n)
    if rangema is None:
        return None
    upperKC = ma + rangema * multKC
    lowerKC = ma - rangema * multKC

    squeeze_on = (lowerBB > lowerKC) and (upperBB < upperKC)

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
        color = "red" if m_actual < m_prev else "maroon"

    return {
        "squeeze_on": squeeze_on,
        "momentum": m_actual,
        "momentum_prev": m_prev,
        "color": color,
    }


def calcular_adx(velas, length=14):
    n = len(velas)
    if n < length * 2:
        return None

    highs = [v["high"] for v in velas]
    lows = [v["low"] for v in velas]
    closes = [v["close"] for v in velas]

    tr_list, plus_dm_list, minus_dm_list = [], [], []
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
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


def calcular_atr_percentile(velas, period=14, ventana=100):
    if len(velas) < period + ventana + 1:
        return None

    trs = []
    for i in range(1, len(velas)):
        high = velas[i]["high"]
        low = velas[i]["low"]
        pc = velas[i-1]["close"]
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


# ============================================================
# BTC PORTERO
# ============================================================

def analizar_patron_btc(btc_cache):
    global CONTADOR_FILTROS

    if not btc_cache:
        CONTADOR_FILTROS["BTC_NEUTRAL"] += 1
        return {"pasa": False, "estado": "sin_datos", "direccion": None,
                "detalle": "sin cache BTC"}

    velas = construir_velas(btc_cache, "15m")
    n = len(velas)

    if n < COMP_MIN_VELAS:
        CONTADOR_FILTROS["BTC_NEUTRAL"] += 1
        return {"pasa": False, "estado": "sin_datos", "direccion": None,
                "detalle": f"solo {n} velas"}

    atr_pct = calcular_atr_percentile(velas, ATR_PERIOD, ATR_VENTANA)

    sqz = calcular_squeeze_momentum(velas, SQZ_BB_LENGTH, SQZ_BB_MULT,
                                    SQZ_KC_LENGTH, SQZ_KC_MULT)
    if sqz is None:
        CONTADOR_FILTROS["BTC_NEUTRAL"] += 1
        return {"pasa": False, "estado": "neutral", "direccion": None,
                "detalle": "faltan velas momentum"}

    mom_color = sqz["color"]
    mom_val = sqz["momentum"]
    mom_nombre, mom_emoji, _ = traducir_color_momentum(mom_color)

    adx_data = calcular_adx(velas, ADX_LENGTH)
    if adx_data is None:
        CONTADOR_FILTROS["BTC_NEUTRAL"] += 1
        return {"pasa": False, "estado": "neutral", "direccion": None,
                "detalle": "faltan velas ADX"}

    adx_val = adx_data["adx"]
    di_plus = adx_data["di_plus"]
    di_minus = adx_data["di_minus"]

    if atr_pct is not None and atr_pct < ATR_UMBRAL_COMPRESION:
        CONTADOR_FILTROS["BTC_COMPRIMIDO"] += 1
        return {
            "pasa": False,
            "estado": "comprimiendo",
            "direccion": None,
            "atr_pct": atr_pct,
            "detalle": f"compresión ATR%={atr_pct:.1f}",
        }

    if adx_val < ADX_UMBRAL:
        CONTADOR_FILTROS["BTC_NEUTRAL"] += 1
        return {"pasa": False, "estado": "neutral", "direccion": None,
                "detalle": f"ADX {adx_val:.1f} < {ADX_UMBRAL}"}

    direccion = None
    if mom_color in ("lime", "maroon") and di_plus and di_minus and di_plus > di_minus:
        direccion = "up"
    elif mom_color in ("red", "green") and di_plus and di_minus and di_minus > di_plus:
        direccion = "down"

    if direccion is None:
        CONTADOR_FILTROS["BTC_NEUTRAL"] += 1
        return {"pasa": False, "estado": "neutral", "direccion": None,
                "detalle": f"momento {mom_nombre} + DI desalineado"}

    CONTADOR_FILTROS["BTC_PASA"] += 1
    print(f"   ✅ BTC {direccion.upper()} — Mom {mom_nombre} | ADX {adx_val:.1f} | ATR% {atr_pct:.1f}",
          flush=True)

    return {
        "pasa": True,
        "estado": "activo",
        "direccion": direccion,
        "atr_pct": atr_pct,
        "momentum_color": mom_color,
        "momentum_nombre": mom_nombre,
        "adx": adx_val,
        "detalle": f"BTC {direccion.upper()} | Mom {mom_nombre} | ADX {adx_val:.1f}",
    }


# ============================================================
# COINBEACON
# ============================================================

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
        return json.loads(response.read().decode("utf-8"))


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
            "type": item_data.get("type"),
            "currentLevel": numero(item_data.get("currentLevel")),
            "status": item_data.get("status"),
            "touchCount": touch_count,
            "fallingOrRising": item_data.get("fallingOrRising", "flat"),
            "timeframe": timeframe,
        })
    return lineas


def obtener_monedas_recomendadas(timeframe="15m", limit=MAX_MONEDAS_DINAMICAS):
    try:
        result = consultar_coinbeacon(COINBEACON_TRENDLINES_URL, timeframe, limit)
    except Exception:
        return []

    items = result.get("items", [])
    if not items:
        return []

    monedas = []
    vistos = set()
    for item in items:
        symbol = str(item.get("symbol", "")).upper()
        if not symbol.endswith("USDT"):
            continue
        base = symbol.replace("USDT", "")
        if not base.isascii() or base in EXCLUIR or base in vistos:
            continue
        vistos.add(base)
        monedas.append(base)

    return monedas[:limit]


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


def calcular_score_linea(linea):
    touches = linea.get("touchCount", 0)
    estructura = clasificar_estructura(touches)
    structure_score = obtener_touch_score(touches)
    proximity_score = obtener_proximidad_score(linea.get("distance_pct"), linea.get("timeframe"))
    timeframe_score = obtener_timeframe_score(linea.get("timeframe"))
    status_score = obtener_status_score(linea.get("status"))
    bonus = 0
    if linea.get("type") == "support" and "rising" in str(linea.get("fallingOrRising", "")).lower():
        bonus += 10
    if linea.get("type") == "resistance" and "falling" in str(linea.get("fallingOrRising", "")).lower():
        bonus += 10
    total_score = (structure_score + proximity_score + timeframe_score
                   + status_score + bonus)
    return {"structure_quality": estructura, "total_score": total_score}


def analizar_coinbeacon(symbol):
    todas = []
    for timeframe in TIMEFRAMES:
        try:
            lineas = consultar_coinbeacon_trendlines(symbol, timeframe)
            todas.extend(lineas)
        except Exception:
            pass

    if not todas:
        return {"price": None, "lines": []}

    precio = None
    for linea in todas:
        if linea.get("price") is not None:
            precio = linea["price"]
            break

    for linea in todas:
        linea["distance_pct"] = distancia_porcentual(precio, linea.get("currentLevel"))
        if linea["distance_pct"] is not None:
            linea.update(calcular_score_linea(linea))
        else:
            linea["structure_quality"] = "UNKNOWN"
            linea["total_score"] = 0

    return {"price": precio, "lines": todas}


# ============================================================
# ANÁLISIS DE ALT
# ============================================================

def analizar_alt_momentum(symbol):
    cache = leer_cache_remoto(symbol)
    if not cache:
        return None

    velas = construir_velas(cache, "15m")
    if len(velas) < 2 * SQZ_KC_LENGTH:
        return None

    sqz = calcular_squeeze_momentum(velas, SQZ_BB_LENGTH, SQZ_BB_MULT,
                                    SQZ_KC_LENGTH, SQZ_KC_MULT)
    if not sqz:
        return None

    adx_data = calcular_adx(velas, ADX_LENGTH)
    if not adx_data:
        return None

    atr_pct = calcular_atr_percentile(velas, ATR_PERIOD, ATR_VENTANA)

    return {
        "cache": cache,
        "mom_color": sqz["color"],
        "mom_val": sqz["momentum"],
        "adx": adx_data["adx"],
        "di_plus": adx_data["di_plus"],
        "di_minus": adx_data["di_minus"],
        "atr_pct": atr_pct,
        "price": cache.get("price"),
    }


def confluencia(symbol, coin_data, btc_dir, alt_mom_data):
    precio = coin_data.get("price")
    lineas = coin_data.get("lines", [])
    if precio is None:
        return []

    if alt_mom_data is None:
        return confluencia_simple(symbol, coin_data, btc_dir)
    return confluencia_premium(symbol, coin_data, btc_dir, alt_mom_data)


def confluencia_simple(symbol, coin_data, btc_dir):
    precio = coin_data.get("price")
    lineas = coin_data.get("lines", [])
    if precio is None:
        return []

    candidates = []
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

        total_score = linea.get("total_score", 0)
        if total_score < SMART_TOTAL_SCORE_MIN or abs(distancia) > 1.0:
            continue

        candidates.append({
            "line": linea,
            "operacion": "LONG" if btc_dir == "up" else "SHORT",
            "tipo_operacion": tipo_operacion,
            "score": total_score,
            "premium": False,
            "alt_mom": None,
        })

    if not candidates:
        return []

    return [max(candidates, key=lambda x: x["score"])]


def confluencia_premium(symbol, coin_data, btc_dir, alt_mom_data):
    precio = coin_data.get("price")
    lineas = coin_data.get("lines", [])
    if precio is None:
        return []

    mom_color = alt_mom_data["mom_color"]
    adx_val = alt_mom_data["adx"]
    di_plus = alt_mom_data["di_plus"]
    di_minus = alt_mom_data["di_minus"]

    alt_dir = None
    if mom_color in ("lime", "maroon"):
        alt_dir = "up"
    elif mom_color in ("red", "green"):
        alt_dir = "down"

    if alt_dir is None:
        CONTADOR_FILTROS["MOMENTUM_ALT"] += 1
        return []

    if adx_val < ADX_UMBRAL:
        CONTADOR_FILTROS["ADX_ALT"] += 1
        return []

    if alt_dir == "up" and (di_plus is None or di_minus is None or di_plus <= di_minus):
        return []
    if alt_dir == "down" and (di_plus is None or di_minus is None or di_minus <= di_plus):
        return []

    candidates = []
    for linea in lineas:
        tipo = linea.get("type")
        distancia = linea.get("distance_pct")
        if distancia is None:
            continue

        status = str(linea.get("status", "")).lower()
        inclinacion = str(linea.get("fallingOrRising", "")).lower()

        tipo_operacion = None
        if alt_dir == "up":
            if tipo == "resistance" and ("near breakout" in status or "broke up" in status):
                tipo_operacion = "breakout"
            elif "retest" in status and "holding" in status:
                tipo_operacion = "rebote"
            elif tipo == "support" and inclinacion == "rising" and abs(distancia) <= 1.0:
                tipo_operacion = "rebote"
        else:
            if tipo == "support" and ("near breakdown" in status or "broke down" in status):
                tipo_operacion = "breakdown"
            elif "retest" in status and "rejecting" in status:
                tipo_operacion = "rechazo"
            elif tipo == "resistance" and inclinacion == "falling" and abs(distancia) <= 1.0:
                tipo_operacion = "rechazo"

        if tipo_operacion is None:
            continue

        quality = linea.get("structure_quality", "IGNORE")
        if quality in ("IGNORE", "WEAK"):
            continue

        total_score = linea.get("total_score", 0)
        if total_score < SMART_TOTAL_SCORE_MIN or abs(distancia) > 1.0:
            continue

        candidates.append({
            "line": linea,
            "operacion": "LONG" if alt_dir == "up" else "SHORT",
            "tipo_operacion": tipo_operacion,
            "score": total_score,
            "premium": True,
            "alt_mom": alt_mom_data,
        })

    if not candidates:
        return []

    return [max(candidates, key=lambda x: x["score"])]


# ============================================================
# SALIDAS
# ============================================================

def revisar_salidas(previous_state, hora_lima):
    avisos = 0
    nuevo_state = []

    for item in previous_state:
        if item.get("cerrada"):
            nuevo_state.append(item)
            continue

        symbol = item.get("symbol")
        operacion = item.get("operacion")
        entry = item.get("entry_price") or 0
        mom_al_entrar = item.get("mom_al_entrar")

        if not mom_al_entrar or not operacion:
            nuevo_state.append(item)
            continue

        alt_data = analizar_alt_momentum(symbol)
        if not alt_data:
            nuevo_state.append(item)
            continue

        mom_actual = alt_data["mom_color"]
        precio_actual = alt_data["price"] or entry
        ganancia_pct = ((precio_actual - entry) / entry * 100) if entry > 0 else 0

        aviso_agot = item.get("aviso_agotamiento", False)
        aviso_cierre = item.get("aviso_cierre", False)

        if operacion == "LONG" and mom_al_entrar == "lime" and mom_actual == "green" and not aviso_agot:
            msg = (
                f"⚠️ AGOTAMIENTO — {symbol} LONG\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💰 ${precio_actual:.6f} ({ganancia_pct:+.2f}%)\n"
                f"🎯 Momentum: 🟡 PIERDE FUERZA\n"
                f"💡 Considera cerrar\n"
                f"🕐 {hora_lima}"
            )
            if send_telegram_message(msg):
                avisos += 1
                item["aviso_agotamiento"] = True

        if operacion == "LONG" and mom_actual == "red" and not aviso_cierre:
            msg = (
                f"🔴 CIERRE URGENTE — {symbol} LONG\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💰 ${precio_actual:.6f} ({ganancia_pct:+.2f}%)\n"
                f"🎯 Momentum: 🔴 CAE FUERTE\n"
                f"🚨 Cerrar posición\n"
                f"🕐 {hora_lima}"
            )
            if send_telegram_message(msg):
                avisos += 1
                item["aviso_cierre"] = True

        if operacion == "SHORT" and mom_al_entrar == "red" and mom_actual == "green" and not aviso_agot:
            msg = (
                f"⚠️ AGOTAMIENTO — {symbol} SHORT\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💰 ${precio_actual:.6f}\n"
                f"🎯 Momentum: 🟡 PIERDE FUERZA\n"
                f"💡 Considera cerrar\n"
                f"🕐 {hora_lima}"
            )
            if send_telegram_message(msg):
                avisos += 1
                item["aviso_agotamiento"] = True

        if operacion == "SHORT" and mom_actual == "lime" and not aviso_cierre:
            msg = (
                f"🔴 CIERRE URGENTE — {symbol} SHORT\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"💰 ${precio_actual:.6f}\n"
                f"🎯 Momentum: 🟢 SUBE FUERTE\n"
                f"🚨 Cerrar posición\n"
                f"🕐 {hora_lima}"
            )
            if send_telegram_message(msg):
                avisos += 1
                item["aviso_cierre"] = True

        nuevo_state.append(item)

    return nuevo_state, avisos


# ============================================================
# TELEGRAM
# ============================================================

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


# ============================================================
# ESTADO
# ============================================================

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


def guardar_estado(estado):
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2)


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


def guardar_en_csv(alert):
    fieldnames = [
        "hora_lima", "symbol", "operacion", "tipo", "timeframe",
        "currentLevel", "touchCount", "score", "status", "price",
        "premium", "mom_alt", "adx_alt",
    ]
    if not CSV_FILE.exists():
        with CSV_FILE.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fieldnames).writeheader()
    with CSV_FILE.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fieldnames).writerow(alert)


# ============================================================
# PROCESAR
# ============================================================

def procesar_alertas(alerts, filtered_previous, hora_lima):
    sent = 0
    long_count = 0
    short_count = 0
    new_state = list(filtered_previous)
    now_ts = datetime.now(timezone.utc).timestamp()

    for alert in alerts:
        symbol = alert["symbol"]
        line = alert["line"]
        operacion = alert["operacion"]
        premium = alert.get("premium", False)
        alt_mom = alert.get("alt_mom")

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

        if operacion == "LONG":
            long_count += 1
        else:
            short_count += 1

        tipo_linea = (line.get("type") or "?").upper()
        inclinacion = (line.get("fallingOrRising", "flat")).upper()
        precio_actual = line.get("price") or 0
        nivel_linea = line.get("currentLevel") or 0
        tipo_op_txt = (alert.get("tipo_operacion") or "rebote").upper()

        emoji_op = "🟢" if operacion == "LONG" else "🔴"
        aviso_premium = "" if premium else "⚠️"
        filtros_txt = "✅ Filtros OK (Mom + ADX + ATR)" if premium else "⚠️ Sin filtros avanzados"

        msg = (
            f"🧠 MULTI SMART V3\n"
            f"{aviso_premium}{emoji_op} {operacion} {symbol} [{tipo_op_txt}]\n"
            f"📈 ${precio_actual:.6f}\n"
            f"📉 {tipo_linea} ({inclinacion})\n"
            f"   • TF: {line.get('timeframe', '')}\n"
            f"   • Nivel: ${nivel_linea:.6f}\n"
            f"   • Toques: {line.get('touchCount', 0)} ({alert.get('structure_quality', '?')})\n"
            f"🎯 Score: {alert['score']:.1f}\n"
            f"{filtros_txt}\n"
            f"🕐 {hora_lima}"
        )

        if send_telegram_message(msg):
            sent += 1
            print(f"   {emoji_op} {operacion} {symbol} [{tipo_op_txt}]"
                  f"{' [PREMIUM]' if premium else ''} → enviado", flush=True)

            guardar_en_csv({
                "hora_lima": hora_lima, "symbol": symbol, "operacion": operacion,
                "tipo": line.get("type", ""), "timeframe": line.get("timeframe", ""),
                "currentLevel": str(nivel_linea),
                "touchCount": str(line.get("touchCount", 0)),
                "score": f"{alert['score']:.1f}",
                "status": line.get("status", ""),
                "price": str(precio_actual),
                "premium": "1" if premium else "0",
                "mom_alt": alt_mom["mom_color"] if alt_mom else "",
                "adx_alt": f"{alt_mom['adx']:.1f}" if alt_mom else "",
            })

            new_state.append({
                "symbol": symbol,
                "type": line.get("type"),
                "timeframe": line.get("timeframe"),
                "currentLevel": nivel_linea,
                "detected_at": now_ts,
                "entry_price": precio_actual,
                "operacion": operacion,
                "mom_al_entrar": alt_mom["mom_color"] if alt_mom else None,
                "premium": premium,
                "aviso_agotamiento": False,
                "aviso_cierre": False,
            })

    return sent, long_count, short_count, new_state


# ============================================================
# MAIN
# ============================================================

def imprimir_diagnostico():
    total = sum(CONTADOR_FILTROS.values())
    print("\n" + "=" * 70, flush=True)
    print("🔬 DIAGNÓSTICO", flush=True)
    print("=" * 70, flush=True)

    if total == 0:
        print("   (Sin evaluaciones)", flush=True)
        return

    orden = sorted(CONTADOR_FILTROS.items(), key=lambda x: -x[1])
    for nombre, count in orden:
        if count == 0:
            continue
        pct = (count / total) * 100
        barra = "█" * int(pct / 3)
        print(f"   {nombre:20s} {count:3d}  ({pct:5.1f}%)  {barra}", flush=True)


def main():
    global CONTADOR_FILTROS
    CONTADOR_FILTROS = {k: 0 for k in CONTADOR_FILTROS}

    print("\n" + "=" * 70, flush=True)
    print("🚀 MULTI SMART V3 — Fase 3.0 (BTC portero + LONG/SHORT por ALT)", flush=True)
    print("=" * 70, flush=True)
    print(f"\nHora UTC: {datetime.now(timezone.utc).isoformat()}", flush=True)

    previous_state = cargar_estado()

    print("\n🛡️ Revisando posiciones abiertas...", flush=True)
    hora_lima_rev = (datetime.now(timezone.utc) + LIMA_OFFSET).strftime("%Y-%m-%d %H:%M:%S")
    previous_state, avisos_salida = revisar_salidas(previous_state, hora_lima_rev)
    if avisos_salida > 0:
        print(f"   ✅ {avisos_salida} aviso(s) enviado(s)", flush=True)
    else:
        print(f"   🔇 Sin avisos", flush=True)

    print("\n🔍 BTC PORTERO (ATR% + Squeeze + ADX)...", flush=True)
    btc_cache = leer_cache_remoto(BTC_SYMBOL)
    patron_btc = analizar_patron_btc(btc_cache)
    print(f"   Estado: {patron_btc['estado'].upper()}", flush=True)
    print(f"   Detalle: {patron_btc['detalle']}", flush=True)

    if not patron_btc["pasa"]:
        print(f"\n⏸️ BTC NO permite analizar → abortando", flush=True)
        imprimir_diagnostico()
        now_ts = datetime.now(timezone.utc).timestamp()
        new_state = limpiar_estado(previous_state, now_ts)
        guardar_estado(new_state)
        return

    btc_dir = patron_btc["direccion"]
    print(f"\n✅ BTC permite analizar → dirección {btc_dir.upper()}", flush=True)

    monedas = obtener_monedas_recomendadas(timeframe="15m", limit=MAX_MONEDAS_DINAMICAS)
    print(f"\n📡 CoinBeacon recomienda {len(monedas)} monedas", flush=True)

    now_ts = datetime.now(timezone.utc).timestamp()
    filtered_previous = limpiar_estado(previous_state, now_ts)
    hora_lima = (datetime.now(timezone.utc) + LIMA_OFFSET).strftime("%Y-%m-%d %H:%M:%S")

    all_alerts = []
    premium_count = 0
    simple_count = 0

    for symbol in monedas:
        coin_data = analizar_coinbeacon(symbol)
        alt_mom_data = analizar_alt_momentum(symbol)

        if alt_mom_data:
            premium_count += 1
        else:
            simple_count += 1

        alertas = confluencia(symbol, coin_data, btc_dir, alt_mom_data)
        all_alerts.extend(alertas)

    print(f"   Premium (con cache): {premium_count}", flush=True)
    print(f"   Simple (sin cache):  {simple_count}", flush=True)

    all_alerts.sort(key=lambda x: (0 if x["operacion"] == "LONG" else 1, -x["score"]))

    sent, longs, shorts, new_state = procesar_alertas(all_alerts, filtered_previous, hora_lima)

    guardar_estado(new_state)

    imprimir_diagnostico()

    print("\n" + "=" * 70, flush=True)
    print("📢 RESULTADO FINAL", flush=True)
    print("=" * 70, flush=True)
    print(f"Alertas: {sent} | LONG: {longs} | SHORT: {shorts}", flush=True)
    print(f"Avisos salida: {avisos_salida}", flush=True)
    print("\n🏁 PROGRAMA TERMINADO", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR GENERAL: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise
