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
# MULTI SMART — Filtro compresión BTC + 60 monedas + TPs
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
SMART_TOTAL_SCORE_MIN = 90

# ============================================================
# FILTRO 1 — COMPRESIÓN → EXPANSIÓN BTC
# ============================================================
COMP_VENTANA          = 4
COMP_MIN_VELAS        = 12
COMP_RATIO_COMPRESION = 0.70
COMP_FACTOR_EXPANSION = 3.0
COMP_HORAS_RECIENTE   = 2
COMP_MODO_FILTRO      = "hard"

COMP_THROTTLE_MIN     = 30

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

STATE_FILE = DATA_DIR / "multi_smart_state.json"
CSV_FILE = DATA_DIR / "multi_smart.csv"
HISTORICO_CSV_FILE = DATA_DIR / "multi_smart_historial_lineas.csv"
TP_CSV_FILE = DATA_DIR / "multi_smart_tps.csv"

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
            o, c = float(v.get("o")), float(v.get("c"))
        except (TypeError, ValueError):
            continue
        velas.append({
            "timestamp": v["ts"] / 1000,
            "open": o,
            "close": c,
            "rango": abs(c - o),
        })
    return velas


def analizar_patron_btc(btc_cache):
    if not btc_cache:
        return {"pasa": False, "estado": "sin_datos", "detalle": "sin cache BTC"}

    velas = construir_velas(btc_cache, "5m")
    n = len(velas)

    if n < COMP_MIN_VELAS:
        return {"pasa": False, "estado": "sin_datos", "detalle": f"solo {n} velas"}

    v = COMP_VENTANA
    r_ult = _media([x["rango"] for x in velas[-v:]])
    r_pre = _media([x["rango"] for x in velas[-2*v:-v]])

    if r_pre <= 0:
        return {"pasa": False, "estado": "neutral", "detalle": "sin rango previo"}

    ratio = r_ult / r_pre
    ahora = datetime.now(timezone.utc).timestamp()

    for k in range(max(0, n - 8), n):
        vela_actual = velas[k]["rango"]
        anteriores = [velas[i]["rango"] for i in range(max(0, k-6), k)]
        if not anteriores:
            continue
        prom_previo = _media(anteriores)
        if prom_previo > 0 and vela_actual > prom_previo * COMP_FACTOR_EXPANSION:
            edad_h = (ahora - velas[k]["timestamp"]) / 3600
            if edad_h <= COMP_HORAS_RECIENTE:
                d = "up" if velas[k]["close"] > velas[k]["open"] else "down"
                return {
                    "pasa": True,
                    "estado": "expandiendo",
                    "direccion": d,
                    "precio": velas[k]["close"],
                    "fuerza": vela_actual / prom_previo,
                    "edad_h": edad_h,
                    "detalle": f"expansión {d.upper()} hace {edad_h:.1f}h "
                               f"({vela_actual / prom_previo:.1f}x)"
                }

    if ratio < COMP_RATIO_COMPRESION:
        return {"pasa": True, "estado": "comprimiendo",
                "detalle": f"comprimiendo {ratio:.2f}x"}

    return {"pasa": False, "estado": "neutral",
            "detalle": f"rango normal ({ratio:.2f}x)"}


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


def obtener_resistencias_cercanas(lineas, precio_actual, max_items=2):
    if not lineas or precio_actual is None:
        return []
    resistencias = []
    for linea in lineas:
        if linea.get("type") != "resistance":
            continue
        nivel = linea.get("currentLevel")
        if nivel is None or nivel <= precio_actual:
            continue
        distancia_pct = ((nivel - precio_actual) / precio_actual) * 100
        resistencias.append({
            "nivel": nivel, "distancia_pct": distancia_pct,
            "timeframe": linea.get("timeframe"),
            "confianza": linea.get("confidence"),
        })
    resistencias.sort(key=lambda x: x["distancia_pct"])
    return resistencias[:max_items]


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
        "btc_dir", "btc_modo", "btc_estado",
        "pd_tipo", "pd_pct", "pd_rvol", "pd_conf",
        "smart_direction", "smart_line_score",
        "tp1_nivel", "tp1_dist", "tp2_nivel", "tp2_dist",
        "structure_quality", "total_score"
    ]
    if not CSV_FILE.exists():
        with CSV_FILE.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fieldnames).writeheader()
    with CSV_FILE.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fieldnames).writerow(alert_data)


def guardar_tp_csv(tp_data):
    fieldnames = [
        "hora_lima", "symbol", "tp_num", "tp_nivel", "entry_price",
        "precio_actual", "ganancia_pct", "detected_at_signal"
    ]
    if not TP_CSV_FILE.exists():
        with TP_CSV_FILE.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fieldnames).writeheader()
    with TP_CSV_FILE.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fieldnames).writerow(tp_data)


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


def _prioridad_patron(linea):
    s = str(linea.get("status", "")).lower()
    if "near" in s: return 2
    if "retest" in s: return 1
    return 0


def revisar_tps_pendientes(previous_state):
    ahora_lima = (datetime.now(timezone.utc) + LIMA_OFFSET).strftime("%Y-%m-%d %H:%M:%S")
    nuevo_state = []
    avisos = 0

    print("\n🎯 REVISANDO TPs PENDIENTES", flush=True)

    for item in previous_state:
        if item.get("type") == "btc_rsi":
            nuevo_state.append(item)
            continue

        symbol = item.get("symbol")
        tp1 = item.get("tp1_nivel")
        tp2 = item.get("tp2_nivel")
        entry = item.get("entry_price")
        tp1_hit = item.get("tp1_hit", False)
        tp2_hit = item.get("tp2_hit", False)

        if tp1 is None and tp2 is None:
            nuevo_state.append(item)
            continue

        cache = leer_cache_remoto(symbol)
        precio_actual = cache.get("price") if cache else None
        if precio_actual is None:
            nuevo_state.append(item)
            continue

        if tp2 is not None and precio_actual >= tp2 and not tp2_hit:
            msg = (
                f"🧠 MULTI SMART\n"
                f"🎉 TP2 ALCANZADO — {symbol}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 Precio actual: ${precio_actual:.6f}\n"
                f"🎯 TP2: ${tp2:.6f}\n"
                f"💰 Entrada: ${entry:.6f}\n"
                f"📈 Ganancia: {((precio_actual - entry) / entry) * 100:+.2f}%\n"
                f"🔓 Se libera para re-alerta en pullback\n"
                f"🕐 {ahora_lima}\n"
                f"━━━━━━━━━━━━━━━━━━━"
            )
            if send_telegram_message(msg):
                avisos += 1
                guardar_tp_csv({
                    "hora_lima": ahora_lima, "symbol": symbol,
                    "tp_num": 2, "tp_nivel": f"{tp2:.6f}",
                    "entry_price": f"{entry:.6f}",
                    "precio_actual": f"{precio_actual:.6f}",
                    "ganancia_pct": f"{((precio_actual - entry) / entry) * 100:+.2f}",
                    "detected_at_signal": item.get("detected_at", ""),
                })
            continue

        if tp1 is not None and precio_actual >= tp1 and not tp1_hit:
            linea_tp2 = f"🎯 Próximo objetivo TP2: ${tp2:.6f}\n" if tp2 else ""
            msg = (
                f"🧠 MULTI SMART\n"
                f"✅ TP1 ALCANZADO — {symbol}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📈 Precio actual: ${precio_actual:.6f}\n"
                f"🎯 TP1: ${tp1:.6f}\n"
                f"💰 Entrada: ${entry:.6f}\n"
                f"📈 Ganancia: {((precio_actual - entry) / entry) * 100:+.2f}%\n"
                f"{linea_tp2}"
                f"🕐 {ahora_lima}\n"
                f"━━━━━━━━━━━━━━━━━━━"
            )
            if send_telegram_message(msg):
                avisos += 1
                guardar_tp_csv({
                    "hora_lima": ahora_lima, "symbol": symbol,
                    "tp_num": 1, "tp_nivel": f"{tp1:.6f}",
                    "entry_price": f"{entry:.6f}",
                    "precio_actual": f"{precio_actual:.6f}",
                    "ganancia_pct": f"{((precio_actual - entry) / entry) * 100:+.2f}",
                    "detected_at_signal": item.get("detected_at", ""),
                })
            item["tp1_hit"] = True
            nuevo_state.append(item)
            continue

        nuevo_state.append(item)

    print(f"   Avisos TP: {avisos}", flush=True)
    return nuevo_state, avisos


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

    operacion_permitida = "LONG"
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

    # ═══════════════════════════════════════════════════════════
    # CAMBIO 3: Dedup por símbolo — preferir 1h sobre 15m
    # ═══════════════════════════════════════════════════════════
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

    resistencias_cercanas = obtener_resistencias_cercanas(lineas, precio, max_items=2)
    tp1 = resistencias_cercanas[0] if len(resistencias_cercanas) > 0 else None
    tp2 = resistencias_cercanas[1] if len(resistencias_cercanas) > 1 else None

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
            "tp1": tp1, "tp2": tp2,
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

        tp1 = alert.get("tp1")
        tp2 = alert.get("tp2")
        entry_price = alert.get("entry_price") or precio_actual

        msg = (
            f"🧠 LONG {symbol} [{tipo_op_txt}]\n"
            f"📈 ${precio_actual:.6f}\n"
            f"📉 {tipo_linea}{flip_text} ({inclinacion})\n"
            f"   • TF: {line.get('timeframe', '')}\n"
            f"   • Nivel: ${nivel_linea:.6f}\n"
            f"   • Toques: {line.get('touchCount', 0)} ({alert['structure_quality']})\n"
            f"🎯 Score: {alert['score']:.1f}\n"
            f"🕐 {now_lima}"
        )

        if send_telegram_message(msg):
            sent_count += 1
            print(f"   🟢 LONG {symbol} [{tipo_op_txt}] → enviado", flush=True)

            tp1_nivel = tp1["nivel"] if tp1 else None
            tp2_nivel = tp2["nivel"] if tp2 else None
            tp1_dist = tp1["distancia_pct"] if tp1 else None
            tp2_dist = tp2["distancia_pct"] if tp2 else None

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
                "pd_tipo": pd_tipo or "",
                "pd_pct": f"{pd_pct:+.2f}" if pd_tipo else "",
                "pd_rvol": f"{pd_rvol:.2f}" if pd_tipo else "",
                "pd_conf": "1" if pd_vol_conf else "0",
                "smart_direction": smart_dir_str,
                "smart_line_score": smart_line_score_str,
                "tp1_nivel": f"{tp1_nivel:.6f}" if tp1_nivel else "",
                "tp1_dist": f"{tp1_dist:+.2f}" if tp1_dist else "",
                "tp2_nivel": f"{tp2_nivel:.6f}" if tp2_nivel else "",
                "tp2_dist": f"{tp2_dist:+.2f}" if tp2_dist else "",
                "structure_quality": alert['structure_quality'],
                "total_score": f"{alert['score']:.1f}",
            })

            new_state.append({
                "symbol": symbol, "type": line.get("type"),
                "timeframe": line.get("timeframe"),
                "currentLevel": nivel_linea, "detected_at": now_ts,
                "entry_price": precio_actual,
                "tp1_nivel": tp1_nivel, "tp2_nivel": tp2_nivel,
                "tp1_hit": False, "tp2_hit": False,
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


def main():
    print("\n" + "=" * 70, flush=True)
    print("🚀 MULTI SMART — Filtro compresión BTC + 60 monedas + TPs", flush=True)
    print("=" * 70, flush=True)
    print(f"\nHora UTC: {datetime.now(timezone.utc).isoformat()}", flush=True)

    previous_state = cargar_estado()
    previous_state, tp_avisos = revisar_tps_pendientes(previous_state)

    print("\n🔍 FILTRO BTC (compresión → expansión)...", flush=True)
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
        print(f"   📝 Throttle actualizado a: {estado_actual}", flush=True)
    else:
        print(f"   🔇 Throttle activo (estado no cambió)", flush=True)

    if not patron_btc["pasa"]:
        print(f"\n⏸️ Filtro BTC no pasó ({estado_actual.upper()}) → sin análisis", flush=True)
        now_ts = datetime.now(timezone.utc).timestamp()
        new_state = list(previous_state)
        guardar_estado(new_state)
        return

    if estado_actual == "expandiendo" and patron_btc.get("direccion") == "down":
        print(f"\n⚠️ BTC DOWN → enviando aviso informativo", flush=True)
        ahora_lima_str = (datetime.now(timezone.utc) + LIMA_OFFSET).strftime("%Y-%m-%d %H:%M")
        # CAMBIO 2: título 🧠 MULTI SMART en BTC DOWN
        send_telegram_message(
            f"🧠 MULTI SMART\n"
            f"📉 BTC DOWN DETECTADO\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"   {patron_btc['detalle']}\n"
            f"⏸️ Solo LONGs → no se analizan monedas\n"
            f"🕐 {ahora_lima_str} (Lima)"
        )
        now_ts = datetime.now(timezone.utc).timestamp()
        new_state = list(previous_state)
        guardar_estado(new_state)
        return

    print(f"\n✅ Filtro pasa → analizando monedas", flush=True)

    btc_context = {
        "btc_dir": "up",
        "btc_modo": "fuerte",
        "estado": "FAVORABLE",
        "price": btc_cache_full.get("price") if btc_cache_full else None,
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
    alertas_previas = [it for it in filtered_previous if it.get("type") != "btc_rsi"]
    hora_lima = (datetime.now(timezone.utc) + LIMA_OFFSET).strftime("%Y-%m-%d %H:%M:%S")

    all_alerts = []
    for symbol in monedas_a_analizar:
        alerts, _, _ = analizar_moneda(symbol, volume_by_symbol, btc_context, hora_lima, pd_index)
        all_alerts.extend(alerts)

    all_alerts.sort(key=lambda x: -x["score"])

    sent_count, long_count, new_state = procesar_alertas(
        all_alerts, alertas_previas, btc_context, pd_index
    )

    guardar_estado(new_state)

    print("\n" + "=" * 70, flush=True)
    print("📢 RESULTADO FINAL", flush=True)
    print("=" * 70, flush=True)
    print(f"Alertas LONG: {sent_count}", flush=True)
    print(f"Avisos TP: {tp_avisos}", flush=True)
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
