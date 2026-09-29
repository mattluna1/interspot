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
# MULTI SMART — SOLO LONGs
# Filtro BTC v3 (fix delta=None): permite INDECISO en primera corrida
# Umbrales: conf 6.5 | score 75 | status activo | distancia ≤ 1.0%
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

SCORE_MIN_INDECISO = 90.0
SCORE_MIN_REBOTE   = 80.0
SALTO_15M          = 5.0
RSI15_ALTO         = 45.0
RSI15_BAJO         = 55.0
RSI1_ALTO          = 48.0
RSI1_BAJO          = 52.0
DELTA_CRUCE        = 1.50
RSI15_TECHO_ENTRADA = 65.0
RSI15_SUELO_ENTRADA = 35.0

PD_VENTANA_MIN = 10
PD_MIN_PCT = 2.0

SMART_LINE_SCORE_MIN = 6.5
SMART_DIRECTION_SCORE_MIN_LONG = -20
SMART_TOTAL_SCORE_MIN = 75

# [FILTRO BTC v3] Umbrales
BTC_REBOTE_RSI4H_MIN = 45    # RSI4h mínimo para considerar rebote temprano
BTC_REBOTE_DELTA_MIN = 0.0   # delta_2h debe ser >= 0 (RSI4h subiendo o plano)

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

STATE_FILE = DATA_DIR / "multi_smart_state.json"
CSV_FILE = DATA_DIR / "multi_smart.csv"
HISTORICO_CSV_FILE = DATA_DIR / "multi_smart_historial_lineas.csv"

LIMA_OFFSET = timedelta(hours=-5)
HORA_INICIO = 0
HORA_FIN = 24


def hora_permite_envio():
    now_lima = datetime.now(timezone.utc) + LIMA_OFFSET
    hora = now_lima.hour
    return HORA_INICIO <= hora < HORA_FIN


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

CACHE_REMOTE_BASE = (
    "https://raw.githubusercontent.com/Interpage188/"
    "interpage/main/data/cache"
)
CACHE_MAX_EDAD_MIN = 40


def leer_cache_remoto(symbol):
    url = f"{CACHE_REMOTE_BASE}/{symbol}.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"   ⚠️ cache remoto {symbol}: HTTP {e.code}", flush=True)
        return None
    except Exception as e:
        print(f"   ⚠️ cache remoto {symbol}: {str(e)[:60]}", flush=True)
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
        print(f"   ⚠️ cache {symbol} viejo ({edad_min:.0f} min)", flush=True)
        return None

    return {
        "symbol": symbol,
        "price": ultimo.get("price"),
        "rsi15": ultimo.get("rsi15"),
        "rsi1h": ultimo.get("rsi1h"),
        "rsi4h": ultimo.get("rsi4h"),
        "dir15": ultimo.get("dir15"),
        "dir1h": ultimo.get("dir1h"),
        "dir4h": ultimo.get("dir4h"),
        "edad_min": edad_min,
        "n_muestras": len(pulso),
        "pulso": pulso,
    }


def extraer_rsi_del_cache(cache, incluir_4h=False):
    if not cache:
        return {}
    datos = {}
    rsi15 = cache.get("rsi15")
    rsi1h = cache.get("rsi1h")
    if rsi15 is not None:
        datos["15m"] = {
            "price": cache.get("price"),
            "rsi14": rsi15,
            "tendencia": cache.get("dir15", "?"),
        }
    if rsi1h is not None:
        datos["1h"] = {
            "price": cache.get("price"),
            "rsi14": rsi1h,
            "tendencia": cache.get("dir1h", "?"),
        }
    if incluir_4h and cache.get("rsi4h") is not None:
        datos["4h"] = {
            "price": cache.get("price"),
            "rsi14": cache.get("rsi4h"),
            "tendencia": cache.get("dir4h", "?"),
        }
    return datos


def consultar_pumping_events():
    token = os.environ.get("COINBEACON_TOKEN")
    if not token:
        return []

    types = "pump_5m"
    url = f"{COINBEACON_PUMPING_URL}?exchange=binance&types={types}&pair=USDT&limit=500"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Cookie": f"access_token={token}",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("items", [])
    except Exception as e:
        print(f"   ⚠️ pump events: {str(e)[:80]}", flush=True)
        return []


def indexar_pumping_events(eventos):
    index = {}
    for ev in eventos:
        symbol_full = ev.get("symbol", "")
        symbol_base = symbol_full.replace("USDT", "")
        tipo = ev.get("type", "")
        spotted_at = ev.get("spottedAt", 0)

        if symbol_base not in index:
            index[symbol_base] = {}

        actual = index[symbol_base].get(tipo)
        if actual is None or spotted_at > actual.get("spottedAt", 0):
            index[symbol_base][tipo] = ev

    return index


def pd_para_symbol(symbol, pd_index):
    resultado = {
        "activo": False,
        "tipo": None,
        "direccion": None,
        "pct": 0.0,
        "rvol": 0.0,
        "vol_conf": False,
        "edad_min": 0.0,
        "reversal": False,
        "continuation": False,
    }

    if symbol not in pd_index:
        return resultado

    ahora_ts = datetime.now(timezone.utc).timestamp()
    mejor = None
    mejor_ts = 0

    for tipo, ev in pd_index[symbol].items():
        spotted_at = ev.get("spottedAt", 0)
        if spotted_at > mejor_ts:
            mejor = ev
            mejor_ts = spotted_at

    if not mejor:
        return resultado

    edad_min = (ahora_ts * 1000 - mejor_ts) / 60000

    resultado["tipo"] = mejor.get("type")
    resultado["direccion"] = "up"
    resultado["pct"] = mejor.get("pct", 0.0)
    resultado["rvol"] = mejor.get("rvol", 0.0)
    resultado["vol_conf"] = mejor.get("volConfirmed", False)
    resultado["edad_min"] = edad_min
    resultado["reversal"] = str(mejor.get("classification", "")).lower() == "reversal"
    resultado["continuation"] = str(mejor.get("classification", "")).lower() == "continuation"

    if edad_min <= PD_VENTANA_MIN and abs(resultado["pct"]) >= PD_MIN_PCT:
        resultado["activo"] = True

    return resultado


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
        print(f"⚠️ Error obteniendo monedas recomendadas: {str(e)[:80]}", flush=True)
        print("   → Fallback a lista estática SYMBOLS", flush=True)
        return list(SYMBOLS)

    items = result.get("items", [])
    if not items:
        print("⚠️ CoinBeacon no devolvió items. Usando lista estática.", flush=True)
        return list(SYMBOLS)

    monedas = []
    vistos = set()
    descartadas = 0
    for item in items:
        symbol = str(item.get("symbol", "")).upper()
        if not symbol.endswith("USDT"):
            continue
        base = symbol.replace("USDT", "")
        if not base.isascii():
            descartadas += 1
            continue
        if base in EXCLUIR or base in vistos:
            continue
        vistos.add(base)
        monedas.append(base)

    if descartadas > 0:
        print(f"   (descartados {descartadas} símbolos no-ASCII)", flush=True)

    if not monedas:
        print("⚠️ Ninguna moneda válida tras filtrar. Usando lista estática.", flush=True)
        return list(SYMBOLS)

    print(f"📡 CoinBeacon recomienda {len(monedas)} monedas (TF={timeframe})", flush=True)
    print(f"   Top 10: {monedas[:10]}", flush=True)

    return monedas[:limit]


def consultar_volume_coinbeacon():
    result = consultar_coinbeacon(COINBEACON_VOLUME_URL, "4h", 500)
    items = result.get("items", [])
    volume_data = []
    for item in items:
        volume_data.append({
            "symbol": item.get("symbol"),
            "price": numero(item.get("price")),
            "volumeTrend": numero(item.get("volumeTrend")) or 0.0,
            "volume24h": numero(item.get("volume24h")) or 0.0,
            "rvoll": numero(item.get("rvoll")) or 0.0,
            "direction": item.get("direction") or "unknown",
            "status": item.get("status") or "",
            "spottedAt": item.get("spottedAt"),
        })
    return volume_data


def clasificar_estructura(touches):
    if touches >= 11:
        return "VERY_STRONG"
    elif touches >= 8:
        return "STRONG"
    elif touches >= 6:
        return "VALID"
    elif touches >= 4:
        return "WEAK"
    else:
        return "IGNORE"


def obtener_touch_score(touches):
    if touches < 4:
        return 0
    if touches == 4:
        return 8
    if touches == 5:
        return 12
    if touches == 6:
        return 16
    if touches == 7:
        return 20
    if touches == 8:
        return 25
    if touches == 9:
        return 28
    if touches == 10:
        return 31
    if touches >= 11:
        return 35 + min((touches - 11) * 2, 15)
    return 0


def obtener_proximidad_score(distance_pct, timeframe):
    if distance_pct is None or distance_pct <= 0:
        return 0
    max_dist = 2.0 if timeframe == "1h" else 1.5
    if distance_pct > max_dist:
        return 0
    ratio = distance_pct / max_dist
    return 35 - (ratio * 30)


def obtener_timeframe_score(timeframe):
    return {"1h": 19, "15m": 15}.get(timeframe, 0)


def obtener_status_score(status):
    status_lower = str(status).lower()
    score = 0
    if "near breakout" in status_lower:
        score += 20
    if "near breakdown" in status_lower:
        score += 20
    if "retest" in status_lower:
        score += 15
    if "broken" in status_lower:
        score -= 30
    if "confirmed" in status_lower:
        score += 10
    return score


def obtener_confidence_score(confidence):
    if confidence is None:
        return 0
    if confidence <= 1:
        normalized = confidence * 100
    elif confidence <= 10:
        normalized = confidence * 10
    else:
        normalized = confidence
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
        "structure_quality": estructura,
        "structure_score": structure_score,
        "proximity_score": proximity_score,
        "timeframe_score": timeframe_score,
        "status_score": status_score,
        "confidence_score": confidence_score,
        "smart_score": smart_score,
        "bonus_score": bonus,
        "total_score": total_score,
    }


def analizar_coinbeacon(symbol):
    print(f"\n📈 COINBEACON — {symbol}", flush=True)
    print("=" * 70, flush=True)
    todas = []
    for timeframe in TIMEFRAMES:
        try:
            lineas = consultar_coinbeacon_trendlines(symbol, timeframe)
            print(f"   {timeframe}: {len(lineas)} líneas", flush=True)
            todas.extend(lineas)
        except Exception as e:
            print(f"   ❌ {timeframe}: {e}", flush=True)

    if not todas:
        print("❌ No se encontraron líneas.", flush=True)
        return {"price": None, "lines": []}

    precio = None
    for linea in todas:
        if linea.get("price") is not None:
            precio = linea["price"]
            break

    if precio is not None:
        print(f"💰 Precio detectado: ${precio:.6f}", flush=True)
    else:
        print("💰 Precio: N/A", flush=True)

    for linea in todas:
        linea["distance_pct"] = distancia_porcentual(precio, linea.get("currentLevel"))
        if linea["distance_pct"] is not None:
            score_data = calcular_score_linea(linea)
            linea.update(score_data)
        else:
            linea["structure_quality"] = "UNKNOWN"
            linea["total_score"] = 0

    validas = [l for l in todas if l["structure_quality"] in ("VALID", "STRONG", "VERY_STRONG")]
    if validas:
        print(f"\n⭐ {len(validas)} líneas válidas:", flush=True)
        for linea in validas[:5]:
            print(f"   {linea['timeframe']} | {linea['type']} | ${linea['currentLevel']:.6f} | dist {linea['distance_pct']:+.2f}% | toques {linea['touchCount']} | conf {linea.get('confidence',0):.1f} | dir {linea.get('direction_score',0)} | bias {linea.get('bias')} | score {linea.get('total_score',0):.1f}", flush=True)

    return {"price": precio, "lines": todas}


def obtener_precios_coingecko(symbol, days=7):
    coin_id = COINGECKO_IDS.get(symbol)
    if not coin_id:
        raise ValueError(f"Símbolo {symbol} no mapeado a CoinGecko")
    headers = {"Accept": "application/json"}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
    params = {"vs_currency": "usd", "days": days}
    url = COINGECKO_BASE_URL.format(coin_id=coin_id)
    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    prices = data.get("prices", [])
    if not prices:
        raise ValueError("No se obtuvieron precios de CoinGecko")
    return prices


def agrupar_precios(prices, intervalo_segundos):
    if not prices:
        return []
    buckets = {}
    for ts_ms, price in prices:
        ts = int(ts_ms / 1000)
        bucket = ts - (ts % intervalo_segundos)
        buckets[bucket] = price
    return [buckets[key] for key in sorted(buckets.keys())]


def calcular_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [max(c, 0) for c in changes]
    losses = [max(-c, 0) for c in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def analizar_rsi_coingecko(symbol, incluir_4h=False):
    print(f"\n📊 RSI — {symbol}", flush=True)
    print("=" * 70, flush=True)

    cache = leer_cache_remoto(symbol)
    if cache:
        print(f"   📦 Cache recolector: RSI15={cache['rsi15']} | "
              f"RSI1h={cache['rsi1h']} | RSI4h={cache['rsi4h']} | "
              f"hace {cache['edad_min']:.1f} min ({cache['n_muestras']} muestras)",
              flush=True)
        return extraer_rsi_del_cache(cache, incluir_4h)

    print("   ⚠️ Sin cache → CoinGecko", flush=True)
    try:
        prices = obtener_precios_coingecko(symbol, days=7)
    except Exception as e:
        print(f"   ⚠️ Error obteniendo precios de {symbol}: {e}", flush=True)
        return {}
    if not prices:
        return {}

    intervalos = {"15m": 15 * 60, "1h": 60 * 60}
    if incluir_4h:
        intervalos["4h"] = 4 * 60 * 60

    datos = {}
    for tf, segs in intervalos.items():
        agrupados = agrupar_precios(prices, segs)
        if len(agrupados) < 15:
            continue
        rsi = calcular_rsi(agrupados)
        if rsi is None:
            continue
        precio_tf = agrupados[-1]
        precio_ant = agrupados[-2]
        tendencia = "up" if precio_tf > precio_ant else "down"
        datos[tf] = {"price": precio_tf, "rsi14": rsi, "tendencia": tendencia}
    return datos


def evaluar_rsi(datos, mostrar=True):
    if not datos:
        if mostrar:
            print("   ⚠️ Sin datos RSI", flush=True)
        return False, "N/A"
    datos_15m = datos.get("15m")
    datos_1h = datos.get("1h")
    if not datos_15m or not datos_1h:
        if mostrar:
            print("   ⚠️ Faltan RSI 15m o 1h", flush=True)
        return False, "Incompleto"
    rsi15 = datos_15m.get("rsi14")
    rsi1 = datos_1h.get("rsi14")
    if rsi15 is None or rsi1 is None:
        return False, "Incompleto"
    cumple_1h = rsi1 > 30
    cumple_15m = rsi15 > 30
    if mostrar:
        print(f"   RSI14: 1h={rsi1:.2f} {'✅' if cumple_1h else '❌'} | 15m={rsi15:.2f} {'✅' if cumple_15m else '❌'}", flush=True)
    return (cumple_1h and cumple_15m), f"1h={rsi1:.2f} | 15m={rsi15:.2f}"


def evaluar_btc_rsi(btc_rsi_data, rsi4_anterior=None, mostrar=True):
    datos_4h = btc_rsi_data.get("4h")
    datos_1h = btc_rsi_data.get("1h")
    datos_15m = btc_rsi_data.get("15m")
    if not datos_4h or not datos_1h or not datos_15m:
        if mostrar:
            print("   ⚠️ Faltan timeframes para filtro BTC", flush=True)
        return False, "incompleto"
    rsi4 = datos_4h.get("rsi14")
    rsi1 = datos_1h.get("rsi14")
    rsi15 = datos_15m.get("rsi14")
    if rsi4 is None or rsi1 is None or rsi15 is None:
        return False, "incompleto"

    cumple_4h = rsi4 > 50
    cumple_1h = rsi1 > 30
    cumple_15m = rsi15 > 30

    tendencia_alcista = False
    if rsi4_anterior is not None and rsi4 > rsi4_anterior:
        tendencia_alcista = True

    btc_ok = (cumple_4h and cumple_1h and cumple_15m) or \
             (tendencia_alcista and cumple_1h and cumple_15m)

    if mostrar:
        print(f"   BTC: 4h={rsi4:.2f} {'✅' if cumple_4h else '❌'} | 1h={rsi1:.2f} {'✅' if cumple_1h else '❌'} | 15m={rsi15:.2f} {'✅' if cumple_15m else '❌'}", flush=True)
    return (True, "favorable") if btc_ok else (False, "desfavorable")


def analizar_contexto_btc(btc_coin_data, btc_rsi_data, rsi4_anterior=None,
                          btc_cache=None, prev_btc=None):
    print("\n🌐 CONTEXTO BTC", flush=True)
    print("=" * 70, flush=True)

    precio_btc = None
    for timeframe in ["4h", "1h", "15m"]:
        datos = btc_rsi_data.get(timeframe)
        if datos and datos.get("price") is not None:
            precio_btc = datos["price"]
            break
    if precio_btc is None:
        precio_btc = btc_coin_data.get("price")
    if precio_btc is not None:
        print(f"💰 BTC: ${precio_btc:,.2f}", flush=True)

    datos_15m = btc_rsi_data.get("15m")
    datos_1h  = btc_rsi_data.get("1h")
    datos_4h  = btc_rsi_data.get("4h")

    rsi15 = datos_15m.get("rsi14") if datos_15m else None
    rsi1  = datos_1h.get("rsi14")  if datos_1h  else None
    rsi4  = datos_4h.get("rsi14")  if datos_4h  else None

    btc_rsi_ok, estado = evaluar_btc_rsi(btc_rsi_data, rsi4_anterior, mostrar=True)

    delta_2h = None
    if rsi4 is not None and rsi4_anterior is not None:
        delta_2h = rsi4 - rsi4_anterior

    prev_btc = prev_btc or {}
    rsi15_prev = prev_btc.get("rsi15")
    impulso_up_corto   = False
    impulso_down_corto = False

    if rsi15 is not None and rsi1 is not None:
        subida_15m = (rsi15_prev is not None and (rsi15 - rsi15_prev) >= SALTO_15M)
        bajada_15m = (rsi15_prev is not None and (rsi15_prev - rsi15) >= SALTO_15M)

        if (rsi15 >= RSI15_ALTO and rsi1 >= RSI1_ALTO) or subida_15m:
            impulso_up_corto = True
        if (rsi15 <= RSI15_BAJO and rsi1 <= RSI1_BAJO) or bajada_15m:
            impulso_down_corto = True

        if impulso_up_corto and rsi15 > RSI15_TECHO_ENTRADA:
            impulso_up_corto = False
        if impulso_down_corto and rsi15 < RSI15_SUELO_ENTRADA:
            impulso_down_corto = False

    RSI_SOBREVENTA     = 35.0
    RSI_BAJA_SALUDABLE = 48.0
    RSI_CENTRAL        = 52.0
    RSI_SOBRECOMPRA    = 65.0

    D_UP_FUERTE     =  0.34
    D_UP_INDECISO   =  0.10
    D_DOWN_INDECISO = -0.10
    D_DOWN_FUERTE   = -0.34

    btc_dir, btc_modo, razon = "flat", "neutro", "neutro"

    if rsi4 is None:
        razon = "RSI4h N/A"
    elif rsi4 >= RSI_SOBRECOMPRA:
        if impulso_down_corto:
            btc_dir, btc_modo = "down", "indeciso"
            razon = "sobrecompra + impulso 15m DOWN"
        elif delta_2h is None:
            razon = "sobrecompra sin delta"
        elif delta_2h > D_UP_FUERTE:
            btc_dir, btc_modo = "up", "fuerte"; razon = f"sobrecompra Δ{delta_2h:+.2f}"
        elif delta_2h > D_UP_INDECISO:
            btc_dir, btc_modo = "up", "indeciso"; razon = f"sobrecompra Δ{delta_2h:+.2f}"
        elif delta_2h < D_DOWN_FUERTE:
            btc_dir, btc_modo = "down", "fuerte"; razon = f"sobrecompra Δ{delta_2h:+.2f} gira"
        elif delta_2h < D_DOWN_INDECISO:
            btc_dir, btc_modo = "down", "indeciso"; razon = f"sobrecompra Δ{delta_2h:+.2f}"
        else:
            razon = f"sobrecompra Δ{delta_2h:+.2f} neutro"
    elif rsi4 >= RSI_CENTRAL:
        if impulso_down_corto:
            btc_dir, btc_modo = "down", "indeciso"
            razon = "alta saludable + impulso 15m DOWN"
        elif delta_2h is not None and delta_2h < -DELTA_CRUCE:
            btc_dir, btc_modo = "down", "indeciso"
            razon = f"alta saludable Δ{delta_2h:+.2f} gira DOWN"
        else:
            btc_dir, btc_modo = "up", "fuerte"
            razon = f"RSI4h {rsi4:.2f} alta saludable"
    elif rsi4 >= RSI_BAJA_SALUDABLE:
        if impulso_up_corto:
            btc_dir, btc_modo = "up", "indeciso"
            razon = "central + impulso 15m UP"
        elif impulso_down_corto:
            btc_dir, btc_modo = "down", "indeciso"
            razon = "central + impulso 15m DOWN"
        elif delta_2h is None:
            razon = "central sin delta"
        elif delta_2h > D_UP_FUERTE:
            btc_dir, btc_modo = "up", "fuerte"; razon = f"central Δ{delta_2h:+.2f}"
        elif delta_2h > D_UP_INDECISO:
            btc_dir, btc_modo = "up", "indeciso"; razon = f"central Δ{delta_2h:+.2f}"
        elif delta_2h < D_DOWN_FUERTE:
            btc_dir, btc_modo = "down", "fuerte"; razon = f"central Δ{delta_2h:+.2f}"
        elif delta_2h < D_DOWN_INDECISO:
            btc_dir, btc_modo = "down", "indeciso"; razon = f"central Δ{delta_2h:+.2f}"
        else:
            razon = f"central Δ{delta_2h:+.2f} neutro"
    elif rsi4 >= RSI_SOBREVENTA:
        if impulso_up_corto:
            btc_dir, btc_modo = "up", "indeciso"
            razon = "baja saludable + impulso 15m UP"
        elif delta_2h is not None and delta_2h > DELTA_CRUCE:
            btc_dir, btc_modo = "up", "indeciso"
            razon = f"baja saludable Δ{delta_2h:+.2f} gira UP"
        else:
            btc_dir, btc_modo = "down", "fuerte"
            razon = f"RSI4h {rsi4:.2f} baja saludable"
    else:
        if impulso_up_corto:
            btc_dir, btc_modo = "up", "indeciso"
            razon = "sobreventa + impulso 15m UP"
        elif delta_2h is None:
            razon = "sobreventa sin delta"
        elif delta_2h < D_DOWN_FUERTE:
            btc_dir, btc_modo = "down", "fuerte"; razon = f"sobreventa Δ{delta_2h:+.2f} sigue"
        elif delta_2h < D_DOWN_INDECISO:
            btc_dir, btc_modo = "down", "indeciso"; razon = f"sobreventa Δ{delta_2h:+.2f}"
        elif delta_2h > D_UP_FUERTE:
            btc_dir, btc_modo = "up", "fuerte"; razon = f"sobreventa Δ{delta_2h:+.2f} gira"
        elif delta_2h > D_UP_INDECISO:
            btc_dir, btc_modo = "up", "indeciso"; razon = f"sobreventa Δ{delta_2h:+.2f}"
        else:
            razon = f"sobreventa Δ{delta_2h:+.2f} neutro"

    print(f"\n🧭 BTC {btc_dir.upper()} {btc_modo.upper()} ({razon})", flush=True)
    if delta_2h is not None:
        print(f"   Δ2h = {delta_2h:+.2f}", flush=True)
    else:
        print(f"   Δ2h = N/A (sin rsi4_anterior en state)", flush=True)

    estado_btc = "FAVORABLE" if btc_rsi_ok else "DESFAVORABLE"
    print(f"📊 ESTADO BTC: {estado_btc}", flush=True)

    return {
        "price": precio_btc,
        "rsi_ok": btc_rsi_ok,
        "estado": estado_btc,
        "rsi15": rsi15,
        "rsi1":  rsi1,
        "rsi4":  rsi4,
        "btc_dir": btc_dir,
        "btc_modo": btc_modo,
        "razon_ventana": razon,
        "delta_2h": delta_2h,
        "impulso_up_corto": impulso_up_corto,
        "impulso_down_corto": impulso_down_corto,
    }


def send_telegram_message(message):
    if not hora_permite_envio():
        print("Fuera de horario.", flush=True)
        return False
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram no configurado.", flush=True)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = f"chat_id={urllib.parse.quote(str(chat_id))}&text={urllib.parse.quote(message)}".encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("ok"):
            return True
        return False
    except Exception as e:
        print(f"Error Telegram: {e}", flush=True)
        return False


def guardar_en_csv(alert_data):
    fieldnames = [
        "hora_lima", "symbol", "bias", "type", "timeframe",
        "currentLevel", "touchCount", "confidence", "rvoll", "volumeTrend",
        "score", "status", "fallingOrRising", "price",
        "tipo_efectivo",
        "rsi1h", "rsi15m", "tendencia",
        "btc_rsi4h", "btc_rsi1h", "btc_rsi15m", "btc_estado",
        "btc_dir", "btc_modo", "btc_razon", "btc_delta",
        "pd_tipo", "pd_pct", "pd_rvol", "pd_conf",
        "smart_direction", "smart_line_score",
        "structure_quality", "total_score"
    ]
    if not CSV_FILE.exists():
        with CSV_FILE.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
    with CSV_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerow(alert_data)


def guardar_historico_linea(symbol, linea, rsi_data, btc_rsi_data, hora_lima, tipo_efectivo=None):
    fieldnames = [
        "hora_lima", "symbol", "timeframe", "type", "tipo_efectivo",
        "currentLevel", "touchCount",
        "confidence", "status", "fallingOrRising", "distance_pct",
        "rsi_moneda_1h", "rsi_moneda_15m",
        "btc_rsi_4h", "btc_rsi_1h", "btc_rsi_15m",
        "price", "bias", "direction_score", "line_score",
        "structure_quality", "total_score"
    ]
    if not HISTORICO_CSV_FILE.exists():
        with HISTORICO_CSV_FILE.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
    rsi1 = rsi_data.get("1h", {}).get("rsi14") if rsi_data else None
    rsi15 = rsi_data.get("15m", {}).get("rsi14") if rsi_data else None
    btc_rsi4 = btc_rsi_data.get("4h", {}).get("rsi14") if btc_rsi_data else None
    btc_rsi1 = btc_rsi_data.get("1h", {}).get("rsi14") if btc_rsi_data else None
    btc_rsi15 = btc_rsi_data.get("15m", {}).get("rsi14") if btc_rsi_data else None
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
        "btc_rsi_4h": btc_rsi4, "btc_rsi_1h": btc_rsi1, "btc_rsi_15m": btc_rsi15,
        "price": linea.get("price"), "bias": linea.get("bias"),
        "direction_score": linea.get("direction_score"),
        "line_score": linea.get("line_score"),
        "structure_quality": linea.get("structure_quality"),
        "total_score": linea.get("total_score"),
    }
    with HISTORICO_CSV_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerow(row)


def cargar_estado():
    if not STATE_FILE.exists():
        return []
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            estado = json.load(f)
        if isinstance(estado, list):
            return estado
    except Exception as e:
        print(f"⚠️ No se pudo leer {STATE_FILE.name}: {e}", flush=True)
    return []


def limpiar_estado(previous_state, now_ts):
    resultado = []
    for item in previous_state:
        detected_at = item.get("detected_at")
        if detected_at is None:
            resultado.append(item)
            continue
        try:
            age_hours = (now_ts - float(detected_at)) / 3600
        except (ValueError, TypeError):
            continue
        if age_hours < MAX_HISTORY_HOURS:
            resultado.append(item)
    return resultado


def guardar_estado(estado):
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2)


def _prioridad_patron(linea):
    s = str(linea.get("status", "")).lower()
    if "near" in s:    return 2
    if "retest" in s:  return 1
    return 0


def analizar_confluencia(symbol, coin_data, rsi_data, volume_by_symbol,
                         btc_context, pd_index=None):
    print(f"\n🎯 CONFLUENCIA — {symbol}", flush=True)
    print("=" * 70, flush=True)

    precio = coin_data.get("price")
    lineas = coin_data.get("lines", [])
    if precio is None:
        print("❌ Sin precio CoinBeacon.", flush=True)
        return []

    rsi_ok, rsi_texto = evaluar_rsi(rsi_data, mostrar=True)

    btc_dir = btc_context.get("btc_dir", "flat")
    btc_modo = btc_context.get("btc_modo", "neutro")
    razon_ventana = btc_context.get("razon_ventana", "neutro")
    btc_rsi4 = btc_context.get("rsi4")
    btc_rsi1 = btc_context.get("rsi1")
    btc_rsi15 = btc_context.get("rsi15")
    btc_estado = btc_context.get("estado", "DESFAVORABLE")
    btc_delta = btc_context.get("delta_2h")

    tendencia_15m = rsi_data.get("15m", {}).get("tendencia") if rsi_data else None
    tendencia_1h = rsi_data.get("1h", {}).get("tendencia") if rsi_data else None

    print(f"\n🧭 BTC {btc_dir.upper()} {btc_modo.upper()}", flush=True)

    # ═══ BOT SOLO LONGs (filtro ya aplicado en main) ═══
    if btc_dir != "up":
        print(f"   ⏸️ BTC no está UP → solo LONGs, esperar.", flush=True)
        return []

    operacion_permitida = "LONG"

    pd = pd_para_symbol(symbol, pd_index or {})
    if pd["activo"]:
        print(f"   📊 PD activo: {pd['tipo']} {pd['pct']:+.2f}% rvol={pd['rvol']:.2f}", flush=True)

    volume = volume_by_symbol.get(symbol + "USDT") or volume_by_symbol.get(symbol)
    if volume:
        rvoll = volume.get("rvoll", 0)
        volume_trend = volume.get("volumeTrend", 0)
        vol_status = str(volume.get("status", "")).lower()
        is_spike = "spike" in vol_status
        is_dry_up = "dry" in vol_status or "squeeze" in vol_status
        is_exhaustion = "exhaustion" in vol_status
    else:
        rvoll = volume_trend = 0
        is_spike = is_dry_up = is_exhaustion = False

    candidates_by_tf = {}
    for linea in lineas:
        tipo = linea.get("type")
        distancia = linea.get("distance_pct")
        if distancia is None:
            continue

        status = str(linea.get("status", "")).lower()
        inclinacion = str(linea.get("fallingOrRising", "")).lower()

        # ═══ 1) Descartar status malos para LONG ═══
        if "broke down" in status:
            continue
        if "failed break" in status:
            continue
        if "near breakdown" in status:
            continue

        # ═══ 2) Clasificar operación apta para LONG ═══
        tipo_operacion = None
        if tipo == "resistance" and ("near breakout" in status or "broke up" in status):
            tipo_operacion = "breakout"
        elif tipo == "support" and "retest" in status and "holding" in status:
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
            print(f"   ⏭️ {symbol} {linea.get('timeframe')} confidence {confidence:.1f} < {SMART_LINE_SCORE_MIN} → sin alerta", flush=True)
            continue

        bias_linea = str(linea.get("bias", "")).lower()
        dir_score = linea.get("direction_score") or 0

        # Filtros Smart para LONG
        if bias_linea == "bearish":
            print(f"   ⏭️ {symbol} {linea.get('timeframe')} LONG bloqueado: bias=BEARISH", flush=True)
            continue
        if dir_score < SMART_DIRECTION_SCORE_MIN_LONG:
            print(f"   ⏭️ {symbol} {linea.get('timeframe')} LONG bloqueado: dirScore {dir_score:.0f} < {SMART_DIRECTION_SCORE_MIN_LONG}", flush=True)
            continue

        # ═══ 3) Distancia máxima unificada en 1.0% ═══
        if abs(distancia) > 1.0:
            continue

        # ═══ 4) Score interno mínimo ═══
        total_score = linea.get("total_score", 0)
        if total_score < SMART_TOTAL_SCORE_MIN:
            print(f"   ⏭️ {symbol} {linea.get('timeframe')} score {total_score:.1f} < {SMART_TOTAL_SCORE_MIN} → sin alerta", flush=True)
            continue

        tf = linea.get("timeframe")
        tendencia = tendencia_1h if tf == "1h" else tendencia_15m

        operacion = "LONG"

        # PD: si hay dump activo, bloquear LONG (a menos que sea reversal)
        if pd["activo"]:
            if pd["direccion"] == "down" and not pd["reversal"]:
                print(f"   ⏭️ {symbol} {tf} LONG bloqueado: PD dump {pd['pct']:+.2f}%", flush=True)
                continue

        pd_confirmado = False
        if pd["activo"] and pd["direccion"] == "up":
            pd_confirmado = True

        print(f"   ✅ {symbol} {tf} | LONG {tipo_operacion} | status '{status}' | dist {distancia:+.2f}% | score {total_score:.1f} | conf {confidence:.1f} | dir {dir_score:.0f} | bias {bias_linea}", flush=True)

        candidates_by_tf.setdefault(tf, []).append({
            "line": linea,
            "operacion": operacion,
            "tipo_operacion": tipo_operacion,
            "score": total_score,
            "distancia": abs(distancia),
            "tendencia": tendencia,
            "tipo_efectivo": tipo,
            "prioridad_patron": _prioridad_patron(linea),
            "pd_confirmado": pd_confirmado,
        })

    final_candidates = []
    for tf, items in candidates_by_tf.items():
        if items:
            best = max(items, key=lambda x: x["score"])
            final_candidates.append(best)

    final_candidates.sort(key=lambda x: x["score"], reverse=True)

    seen_symbols = set()
    filtered = []
    for cand in final_candidates:
        sym = cand["line"].get("symbol")
        if sym in seen_symbols:
            continue
        seen_symbols.add(sym)
        filtered.append(cand)

    alerts = []
    for cand in filtered:
        linea = cand["line"]
        total_score = cand["score"]

        alert = {
            "symbol": symbol,
            "line": linea,
            "volume": volume if volume else {},
            "score": total_score,
            "tipo_operacion": cand.get("tipo_operacion", "rebote"),
            "is_spike": is_spike,
            "is_dry_up": is_dry_up,
            "is_exhaustion": is_exhaustion,
            "is_near_breakout": "near breakout" in str(linea.get("status", "")).lower(),
            "is_near_breakdown": "near breakdown" in str(linea.get("status", "")).lower(),
            "is_retest": "retest" in str(linea.get("status", "")).lower(),
            "bias": "LONG",
            "tipo_efectivo": cand.get("tipo_efectivo"),
            "rsi1h": rsi_data.get("1h", {}).get("rsi14") if rsi_data else None,
            "rsi15m": rsi_data.get("15m", {}).get("rsi14") if rsi_data else None,
            "tendencia": cand.get("tendencia"),
            "btc_rsi4h": btc_rsi4,
            "btc_rsi1h": btc_rsi1,
            "btc_rsi15m": btc_rsi15,
            "btc_estado": btc_estado,
            "btc_dir": btc_dir,
            "btc_modo": btc_modo,
            "btc_razon": razon_ventana,
            "btc_delta": btc_delta,
            "structure_quality": linea.get("structure_quality"),
            "total_score": total_score,
            "pd_confirmado": cand.get("pd_confirmado", False),
            "pd_activo": pd["activo"],
            "pd_tipo": pd["tipo"],
            "pd_pct": pd["pct"],
            "pd_rvol": pd["rvol"],
            "pd_vol_conf": pd["vol_conf"],
            "pd_reversal": pd["reversal"],
            "smart_confidence": linea.get("confidence"),
            "smart_direction": linea.get("direction_score"),
            "smart_bias": linea.get("bias"),
            "smart_line_score": linea.get("line_score"),
        }
        alerts.append(alert)

    if alerts:
        print(f"\n   ⭐ {len(alerts)} LONG(s)", flush=True)
        for a in alerts:
            print(f"      LONG | {a['line']['timeframe']} | conf {a['smart_confidence']:.1f} | dir {a['smart_direction']:.0f} | bias {a['smart_bias']} | score {a['score']:.1f}", flush=True)
    else:
        print("\n   ⚪ Sin alertas LONG.", flush=True)

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
        operacion = alert["bias"]

        key = f"{symbol}_{line['type']}_{line['timeframe']}_{line['currentLevel']}"
        exists = any(
            f"{item.get('symbol')}_{item.get('type')}_{item.get('timeframe')}_{item.get('currentLevel')}" == key
            for item in new_state
        )
        if exists:
            continue

        emoji = "🟢"
        long_count += 1

        tipo_linea = (line.get("type") or "desconocido").upper()
        tipo_efectivo = (alert.get("tipo_efectivo") or line.get("type") or "").upper()
        inclinacion = (line.get("fallingOrRising", "flat")).upper()
        precio_actual = line.get("price") or 0.0
        nivel_linea = line.get("currentLevel") or 0.0
        tendencia = alert.get("tendencia") or "?"
        flecha = "↑" if tendencia == "up" else "↓" if tendencia == "down" else "?"
        flip_text = " [FLIP]" if tipo_efectivo != tipo_linea else ""
        tipo_op_txt = (alert.get("tipo_operacion") or "rebote").upper()

        tags = []
        if alert.get("is_near_breakout"):
            tags.append("🔥 NEAR BREAKOUT")
        if alert.get("is_near_breakdown"):
            tags.append("💀 NEAR BREAKDOWN")
        if alert.get("is_retest"):
            tags.append("🔄 RETEST")
        if alert.get("is_spike"):
            tags.append("🚀 VOLUME SPIKE")
        if alert.get("pd_confirmado"):
            tags.append("🔥 PD CONFIRMADO")
        tag_text = " ".join(tags) if tags else ""

        rsi1h = alert.get('rsi1h')
        rsi15m = alert.get('rsi15m')
        rsi1h_str = f"{rsi1h:.2f}" if rsi1h is not None else "N/A"
        rsi15m_str = f"{rsi15m:.2f}" if rsi15m is not None else "N/A"

        btc_rsi4h = alert.get('btc_rsi4h')
        btc_rsi1h = alert.get('btc_rsi1h')
        btc_rsi15m = alert.get('btc_rsi15m')
        btc_rsi4h_str = f"{btc_rsi4h:.2f}" if btc_rsi4h is not None else "N/A"
        btc_rsi1h_str = f"{btc_rsi1h:.2f}" if btc_rsi1h is not None else "N/A"
        btc_rsi15m_str = f"{btc_rsi15m:.2f}" if btc_rsi15m is not None else "N/A"
        btc_delta = alert.get('btc_delta')
        btc_delta_str = f"{btc_delta:+.2f}" if btc_delta is not None else "N/A"

        estado_btc = btc_context.get("estado", "DESFAVORABLE")
        btc_dir_str = alert.get("btc_dir", "flat").upper()
        btc_modo_str = alert.get("btc_modo", "neutro").upper()

        pd_tipo = alert.get("pd_tipo")
        pd_pct = alert.get("pd_pct", 0.0)
        pd_rvol = alert.get("pd_rvol", 0.0)
        pd_vol_conf = alert.get("pd_vol_conf", False)
        pd_confirmado = alert.get("pd_confirmado", False)

        if pd_tipo:
            flecha_pd = "🚀"
            tipo_pd_str = "PUMP 5m"
            vol_mark = " ⚡" if pd_vol_conf else ""
            confirm = " ✅ CONFIRMA" if pd_confirmado else ""
            pd_linea = f"📡 PD: {flecha_pd} {tipo_pd_str} {pd_pct:+.2f}% (rvol {pd_rvol:.2f}){vol_mark}{confirm}"
        else:
            pd_linea = "📡 PD: sin movimiento activo"

        smart_conf = alert.get("smart_confidence")
        smart_dir = alert.get("smart_direction")
        smart_bias = alert.get("smart_bias")
        smart_line_score = alert.get("smart_line_score")
        smart_conf_str = f"{smart_conf:.1f}/10" if smart_conf is not None else "N/A"
        smart_dir_str = f"{smart_dir:.0f}" if smart_dir is not None else "N/A"
        smart_line_score_str = f"{smart_line_score:.1f}" if smart_line_score is not None else "N/A"
        smart_linea = f"🧠 Smart: {smart_conf_str} | Dir {smart_dir_str} | Bias {str(smart_bias).upper()} | Score {smart_line_score_str}"

        msg = (
            f"📊 MULTI SMART\n"
            f"{emoji} {operacion} {symbol} [{tipo_op_txt}]\n"
            f"📈 Precio actual: ${precio_actual:.6f}\n"
            f"📉 {tipo_linea}{flip_text} ({inclinacion})\n"
            f"   • TF: {line.get('timeframe', '')}\n"
            f"   • Nivel: ${nivel_linea:.6f}\n"
            f"   • Toques: {line.get('touchCount', 0)} ({alert['structure_quality']})\n"
            f"🎯 Score: {alert['score']:.1f}\n"
            f"📈 Momentum: {flecha} {tendencia}\n"
            f"🌐 BTC: {btc_dir_str} {btc_modo_str} | RSI4h {btc_rsi4h_str} RSI1h {btc_rsi1h_str} RSI15m {btc_rsi15m_str}\n"
            f"📊 Estado BTC: {estado_btc} | Δ2h {btc_delta_str}\n"
            f"{smart_linea}\n"
            f"🧠 RSI moneda: 1h={rsi1h_str} | 15m={rsi15m_str}\n"
            f"{pd_linea}\n"
            f"🕐 {now_lima}\n"
        )
        if tag_text:
            msg += f"{tag_text}\n"
        msg += "━━━━━━━━━━━━━━━━━━━"

        if send_telegram_message(msg):
            sent_count += 1
            csv_data = {
                "hora_lima": now_lima, "symbol": symbol, "bias": operacion,
                "type": line.get("type", ""), "timeframe": line.get("timeframe", ""),
                "currentLevel": str(nivel_linea),
                "touchCount": str(line.get("touchCount", 0)),
                "confidence": str(line.get("confidence", 0)),
                "rvoll": f"{vol.get('rvoll', 0):.2f}",
                "volumeTrend": f"{vol.get('volumeTrend', 0):.1f}",
                "score": f"{alert['score']:.1f}", "status": line.get("status", ""),
                "fallingOrRising": line.get("fallingOrRising", "flat"),
                "price": str(precio_actual),
                "tipo_efectivo": tipo_efectivo,
                "rsi1h": rsi1h_str, "rsi15m": rsi15m_str, "tendencia": tendencia,
                "btc_rsi4h": btc_rsi4h_str, "btc_rsi1h": btc_rsi1h_str, "btc_rsi15m": btc_rsi15m_str,
                "btc_estado": estado_btc,
                "btc_dir": btc_dir_str, "btc_modo": btc_modo_str,
                "btc_razon": btc_context.get("razon_ventana", ""),
                "btc_delta": btc_delta_str,
                "pd_tipo": pd_tipo or "",
                "pd_pct": f"{pd_pct:+.2f}" if pd_tipo else "",
                "pd_rvol": f"{pd_rvol:.2f}" if pd_tipo else "",
                "pd_conf": "1" if pd_vol_conf else "0",
                "smart_direction": smart_dir_str,
                "smart_line_score": smart_line_score_str,
                "structure_quality": alert['structure_quality'],
                "total_score": f"{alert['score']:.1f}",
            }
            guardar_en_csv(csv_data)
            new_state.append({
                "symbol": symbol, "type": line.get("type"),
                "timeframe": line.get("timeframe"),
                "currentLevel": nivel_linea, "detected_at": now_ts,
            })

    return sent_count, long_count, new_state


def analizar_moneda(symbol, volume_by_symbol, btc_context, btc_rsi_data, hora_lima, pd_index=None):
    print(f"\n🔍 ANALIZANDO {symbol}", flush=True)
    print("#" * 70, flush=True)
    coin_data = analizar_coinbeacon(symbol)
    rsi_data = analizar_rsi_coingecko(symbol, incluir_4h=False)

    for linea in coin_data.get("lines", []):
        tipo = linea.get("type")
        dist = linea.get("distance_pct")
        tipo_efectivo = tipo
        if tipo == "resistance" and dist is not None and dist < 0:
            tipo_efectivo = "support"
        elif tipo == "support" and dist is not None and dist > 0:
            tipo_efectivo = "resistance"
        guardar_historico_linea(symbol, linea, rsi_data, btc_rsi_data, hora_lima, tipo_efectivo)

    alerts = analizar_confluencia(symbol, coin_data, rsi_data, volume_by_symbol, btc_context, pd_index)
    return alerts, coin_data, rsi_data


def main():
    print("\n" + "=" * 70, flush=True)
    print("🚀 MULTI SMART — SOLO LONGs", flush=True)
    print(f"   Lista dinámica desde CoinBeacon (hasta {MAX_MONEDAS_DINAMICAS})", flush=True)
    print(f"   Filtro BTC v3: UP FUERTE+FAVORABLE o INDECISO con delta>=0 (o N/A) y RSI4h>={BTC_REBOTE_RSI4H_MIN}", flush=True)
    print(f"   Smart línea: conf≥{SMART_LINE_SCORE_MIN} | dir≥{SMART_DIRECTION_SCORE_MIN_LONG} | score≥{SMART_TOTAL_SCORE_MIN}", flush=True)
    print(f"   Status: near breakout / broke up / retest holding / rising cerca (≤1.0%)", flush=True)
    print("=" * 70, flush=True)

    print(f"\nHora UTC: {datetime.now(timezone.utc).isoformat()}", flush=True)

    previous_state = cargar_estado()
    rsi4_anterior = None
    prev_btc = {}
    for item in previous_state:
        if item.get("type") == "btc_rsi":
            rsi4_anterior = item.get("rsi4")
            prev_btc = {
                "rsi4":  item.get("rsi4"),
                "rsi1":  item.get("rsi1"),
                "rsi15": item.get("rsi15"),
            }
            break

    print("\n🌐 ANALIZANDO CONTEXTO BTC", flush=True)
    btc_coin_data = analizar_coinbeacon(BTC_SYMBOL)
    btc_cache_full = leer_cache_remoto(BTC_SYMBOL)
    btc_rsi_data = extraer_rsi_del_cache(btc_cache_full, incluir_4h=True) if btc_cache_full else analizar_rsi_coingecko(BTC_SYMBOL, incluir_4h=True)
    btc_context = analizar_contexto_btc(
        btc_coin_data, btc_rsi_data, rsi4_anterior, btc_cache_full,
        prev_btc=prev_btc
    )

    # ═══ FILTRO BTC v3 (con fix delta=None) ═══
    btc_dir = btc_context.get("btc_dir")
    btc_modo = btc_context.get("btc_modo")
    btc_estado = btc_context.get("estado")
    btc_rsi4 = btc_context.get("rsi4")
    btc_delta = btc_context.get("delta_2h")

    permitir_longs = False
    razon_filtro = ""

    if btc_dir == "down":
        razon_filtro = "BTC DOWN"
    elif btc_dir == "flat":
        razon_filtro = "BTC FLAT"
    elif btc_modo == "fuerte" and btc_estado == "FAVORABLE":
        permitir_longs = True
        razon_filtro = "BTC UP FUERTE + FAVORABLE"
    elif btc_modo == "indeciso" and btc_rsi4 is not None and btc_rsi4 >= BTC_REBOTE_RSI4H_MIN:
        # [FIX A] delta=None (primera corrida) → permitir (beneficio de la duda)
        # delta>=0 → permitir (rebote en curso)
        # delta<0 → bloquear (girando a la baja)
        if btc_delta is None or btc_delta >= BTC_REBOTE_DELTA_MIN:
            permitir_longs = True
            delta_txt = f"{btc_delta:+.2f}" if btc_delta is not None else "N/A (primera corrida)"
            razon_filtro = f"BTC UP INDECISO (RSI4h {btc_rsi4:.2f}, delta {delta_txt})"
        else:
            razon_filtro = f"BTC UP INDECISO con delta negativo {btc_delta:+.2f}"
    else:
        razon_filtro = f"BTC {btc_dir} {btc_modo} sin condiciones (delta={btc_delta}, RSI4h={btc_rsi4})"

    if not permitir_longs:
        print(f"\n⏸️ {razon_filtro} → saliendo sin analizar monedas.", flush=True)
        now_ts = datetime.now(timezone.utc).timestamp()
        new_state = list(previous_state)
        new_state.append({
            "type": "btc_rsi",
            "rsi4":  btc_context.get("rsi4"),
            "rsi1":  btc_context.get("rsi1"),
            "rsi15": btc_context.get("rsi15"),
            "detected_at": now_ts,
        })
        guardar_estado(new_state)
        print("\n🏁 PROGRAMA TERMINADO (sin alertas)", flush=True)
        return

    print(f"\n✅ {razon_filtro} → analizando monedas.", flush=True)

    print("\n📡 CARGANDO PUMP EVENTS", flush=True)
    pd_eventos = consultar_pumping_events()
    print(f"   Eventos: {len(pd_eventos)}", flush=True)
    pd_index = indexar_pumping_events(pd_eventos)
    print(f"   Símbolos con evento: {len(pd_index)}", flush=True)

    print("\n📊 OBTENIENDO VOLUMEN COINBEACON", flush=True)
    try:
        volume_data = consultar_volume_coinbeacon()
        print(f"Datos de volumen (4h): {len(volume_data)}", flush=True)
    except Exception as e:
        print(f"❌ Error volumen: {e}", flush=True)
        volume_data = []
    volume_by_symbol = {}
    for item in volume_data:
        symbol = str(item.get("symbol", "")).upper()
        volume_by_symbol[symbol] = item

    print("\n📡 OBTENIENDO MONEDAS RECOMENDADAS DE COINBEACON", flush=True)
    monedas_a_analizar = obtener_monedas_recomendadas(
        timeframe="15m", limit=MAX_MONEDAS_DINAMICAS
    )
    print(f"   → Se analizarán {len(monedas_a_analizar)} monedas", flush=True)

    now_ts = datetime.now(timezone.utc).timestamp()
    filtered_previous = limpiar_estado(previous_state, now_ts)
    alertas_previas = [it for it in filtered_previous if it.get("type") != "btc_rsi"]
    hora_lima = (datetime.now(timezone.utc) + LIMA_OFFSET).strftime("%Y-%m-%d %H:%M:%S")

    all_alerts = []
    for symbol in monedas_a_analizar:
        alerts, _, _ = analizar_moneda(symbol, volume_by_symbol, btc_context, btc_rsi_data, hora_lima, pd_index)
        all_alerts.extend(alerts)

    all_alerts.sort(key=lambda x: -x["score"])

    sent_count, long_count, new_state = procesar_alertas(
        all_alerts, alertas_previas, btc_context, pd_index
    )

    new_state.append({
        "type": "btc_rsi",
        "rsi4":  btc_context.get("rsi4"),
        "rsi1":  btc_context.get("rsi1"),
        "rsi15": btc_context.get("rsi15"),
        "detected_at": now_ts,
    })
    guardar_estado(new_state)

    print("\n" + "=" * 70, flush=True)
    print("📢 RESULTADO FINAL", flush=True)
    print("=" * 70, flush=True)
    print(f"Alertas nuevas: {sent_count}", flush=True)
    print(f"🟢 LONG: {long_count}", flush=True)
    print(f"🧭 BTC: {btc_context.get('btc_dir', 'flat').upper()} {btc_context.get('btc_modo', 'neutro').upper()} | Δ2h {btc_context.get('delta_2h')}", flush=True)
    print(f"📡 Monedas analizadas: {len(monedas_a_analizar)}", flush=True)
    print("\n🏁 PROGRAMA TERMINADO", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\n❌ ERROR GENERAL", flush=True)
        print(str(e), flush=True)
        import traceback
        traceback.print_exc()
        raise
