#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RECOLECTOR — interspot
- 21 monedas
- Fetches: 15m, 1h, 4h, 1D, 1W
- Sin 5m, sin velas crudas (solo pulso)
- Retención: 12h de pulso
- Guarda máximo reciente + horas desde el máximo + tendencia bajista
"""

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = [
    "BTC",
    "SUI", "ARB", "RAY", "NEAR", "UNI", "ENA", "APT",
    "AVAX", "INJ", "ZEC", "SEI", "DASH", "WLD",
    "ETH", "LINK", "SOL", "BNB", "LTC", "STX", "HYPE",
]

RETENCION_PULSO_H = 12
OKX_LIMIT_VELAS = 100

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

OKX_INTERVALOS = {"15m": "15m", "1h": "1H", "4h": "4H", "1D": "1D", "1W": "1W"}

def fetch_okx_klines(symbol, intervalo, limite=OKX_LIMIT_VELAS):
    bar = OKX_INTERVALOS.get(intervalo)
    if not bar:
        return []
    inst_id = f"{symbol}-USDT"
    url = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limite}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"   ⚠️ OKX {symbol} {intervalo}: {str(e)[:60]}", flush=True)
        return []
    if raw.get("code") != "0":
        print(f"   ⚠️ OKX {symbol} {intervalo}: code={raw.get('code')}", flush=True)
        return []
    data = raw.get("data", [])
    data.reverse()
    return [{"ts": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])} for k in data]

def calcular_rsi(prices, period=14):
    if not prices or len(prices) < period + 1:
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

def cache_path(symbol):
    return CACHE_DIR / f"{symbol}.json"

def cargar_cache(symbol):
    p = cache_path(symbol)
    if not p.exists():
        return {"symbol": symbol, "updated_at": None, "pulso": []}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"symbol": symbol, "updated_at": None, "pulso": []}
    except Exception:
        return {"symbol": symbol, "updated_at": None, "pulso": []}

def guardar_cache(symbol, data):
    with cache_path(symbol).open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def actualizar_pulso(symbol, ahora):
    velas_15m = fetch_okx_klines(symbol, "15m")
    velas_1h  = fetch_okx_klines(symbol, "1h")
    velas_4h  = fetch_okx_klines(symbol, "4h")
    velas_1d  = fetch_okx_klines(symbol, "1D")
    velas_1w  = fetch_okx_klines(symbol, "1W")

    if not velas_15m:
        print(f"   ❌ {symbol}: sin velas 15m. Se omite.", flush=True)
        return False

    rsi15 = calcular_rsi([v["c"] for v in velas_15m])
    rsi1h = calcular_rsi([v["c"] for v in velas_1h]) if velas_1h else None
    rsi4h = calcular_rsi([v["c"] for v in velas_4h]) if velas_4h else None
    rsi1d = calcular_rsi([v["c"] for v in velas_1d]) if velas_1d else None
    rsi1w = calcular_rsi([v["c"] for v in velas_1w]) if velas_1w else None

    price = velas_15m[-1]["c"]
    ultima_vela = velas_15m[-1]

    # ═══════════════════════════════════════════════
    # Máximo de referencia (considerando rebotes)
    # Si un rebote NO supera el máximo anterior,
    # el máximo original sigue válido.
    # Si lo supera, se resetea al nuevo máximo.
    # ═══════════════════════════════════════════════
    horas_desde_max = 0
    max_reciente = None
    tendencia_bajista = False

    if len(velas_1h) >= 25:
        ventana = velas_1h[-72:] if len(velas_1h) >= 72 else velas_1h

        idx_inicio = 0
        max_idx = 0
        max_val = ventana[0]["c"]
        while idx_inicio < len(ventana) - 1:
            # Máximo desde idx_inicio
            max_val = ventana[idx_inicio]["c"]
            max_idx = idx_inicio
            for i in range(idx_inicio, len(ventana)):
                if ventana[i]["c"] > max_val:
                    max_val = ventana[i]["c"]
                    max_idx = i

            # Si el máximo es el último → no hay bajada
            if max_idx >= len(ventana) - 1:
                break

            # Mínimo después del máximo
            min_val = ventana[max_idx]["l"]
            min_idx = max_idx
            for i in range(max_idx, len(ventana)):
                if ventana[i]["l"] < min_val:
                    min_val = ventana[i]["l"]
                    min_idx = i

            # Máximo después del mínimo (pico del rebote)
            max_post = ventana[min_idx]["c"]
            max_post_idx = min_idx
            for i in range(min_idx, len(ventana)):
                if ventana[i]["c"] > max_post:
                    max_post = ventana[i]["c"]
                    max_post_idx = i

            # ¿El rebote supera el máximo anterior?
            if max_post > max_val:
                # Sí → reiniciar desde el pico del rebote
                idx_inicio = max_post_idx
                continue
            else:
                # No → el máximo original sigue válido
                break

        max_reciente = max_val
        horas_desde_max = len(ventana) - 1 - max_idx

        # ¿Sigue bajando? Precio pegado al mínimo desde ese máximo
        tramo = ventana[max_idx:]
        if len(tramo) >= 2:
            min_desde_max = min(v["l"] for v in tramo)
            precio_actual = tramo[-1]["c"]
            if min_desde_max > 0:
                dist_al_min = (precio_actual - min_desde_max) / min_desde_max * 100
                tendencia_bajista = (dist_al_min <= 3.0)

    def direccion(velas):
        if len(velas) < 2:
            return "?"
        return "up" if velas[-1]["c"] > velas[-2]["c"] else "down"

    vol_contratos = ultima_vela.get("v")
    vol_usdt = None
    if vol_contratos is not None and price:
        vol_usdt = vol_contratos * price

    rvol = 1.0
    if len(velas_15m) >= 21 and vol_contratos:
        vols_previos = [v["v"] for v in velas_15m[-21:-1] if v.get("v")]
        if vols_previos:
            vol_promedio = sum(vols_previos) / len(vols_previos)
            if vol_promedio > 0:
                rvol = vol_contratos / vol_promedio

    sample = {
        "ts": int(ahora.timestamp()),
        "price": round(price, 8),
        "high15": round(ultima_vela.get("h"), 8) if ultima_vela.get("h") is not None else None,
        "low15": round(ultima_vela.get("l"), 8) if ultima_vela.get("l") is not None else None,
        "vol15": round(vol_contratos, 4) if vol_contratos is not None else None,
        "vol_usdt15": round(vol_usdt, 2) if vol_usdt is not None else None,
        "rvol15": round(rvol, 2),
        "dir4h": direccion(velas_4h) if velas_4h else "?",
        "rsi15": round(rsi15, 2) if rsi15 is not None else None,
        "rsi1h": round(rsi1h, 2) if rsi1h is not None else None,
        "rsi4h": round(rsi4h, 2) if rsi4h is not None else None,
        "rsi1d": round(rsi1d, 2) if rsi1d is not None else None,
        "rsi1w": round(rsi1w, 2) if rsi1w is not None else None,
        "dir15": direccion(velas_15m),
        "dir1h": direccion(velas_1h) if velas_1h else "?",
        "horas_desde_max": horas_desde_max,
        "max_reciente": round(max_reciente, 8) if max_reciente is not None else None,
        "tendencia_bajista": tendencia_bajista,
    }

    cache = cargar_cache(symbol)
    pulso = cache.get("pulso", [])

    if pulso and pulso[-1].get("ts") == sample["ts"]:
        return False

    pulso.append(sample)
    limite_ts = ahora.timestamp() - RETENCION_PULSO_H * 3600
    pulso = [p for p in pulso if p.get("ts", 0) >= limite_ts]

    cache["symbol"] = symbol
    cache["updated_at"] = ahora.isoformat()
    cache["pulso"] = pulso
    guardar_cache(symbol, cache)

    rsi4h_str = f"{rsi4h:.1f}" if rsi4h is not None else "N/A"
    rsi1d_str = f"{rsi1d:.1f}" if rsi1d is not None else "N/A"
    rsi1w_str = f"{rsi1w:.1f}" if rsi1w is not None else "N/A"
    max_str = f"${max_reciente:.6f}" if max_reciente is not None else "N/A"
    tend_str = "bajando" if tendencia_bajista else "rebotó"

    print(
        f"   ✅ {symbol}: ${price:.6f} | RSI4h={rsi4h_str} | RSI1d={rsi1d_str} | RSI1w={rsi1w_str} | "
        f"RVOL={sample['rvol15']:.2f} | max {max_str} hace {horas_desde_max}h | {tend_str} | pulso={len(pulso)}",
        flush=True
    )
    return True

def main():
    ahora = datetime.now(timezone.utc)
    print("\n" + "=" * 70, flush=True)
    print("📦 RECOLECTOR — interspot", flush=True)
    print(f"   {len(SYMBOLS)} monedas | Fetches: 15m, 1h, 4h, 1D, 1W", flush=True)
    print("=" * 70, flush=True)
    print(f"\nHora UTC: {ahora.isoformat()}", flush=True)

    guardados = 0
    fallidos = 0
    for symbol in SYMBOLS:
        if actualizar_pulso(symbol, ahora):
            guardados += 1
        else:
            fallidos += 1

    print("\n" + "=" * 70, flush=True)
    print(f"Guardados: {guardados} | Fallidos: {fallidos}", flush=True)
    print(f"💾 Cache dir: {CACHE_DIR}", flush=True)
    print("🏁 PROGRAMA TERMINADO", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR GENERAL: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise
