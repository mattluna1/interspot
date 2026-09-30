import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# CONFIGURACIÓN
# ============================================================

CMC_API_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

TOP_N = 100

DATA_DIR = Path("data") / "cmc"
CSV_FILE = DATA_DIR / "market_history.csv"

TIMEOUT = 30


# ============================================================
# COLUMNAS DEL HISTÓRICO
# ============================================================

CSV_COLUMNS = [
    "snapshot_id",
    "timestamp",

    "cmc_id",
    "name",
    "symbol",
    "slug",

    "cmc_rank",

    "price",

    "percent_change_1h",
    "percent_change_24h",
    "percent_change_7d",
    "percent_change_30d",
    "percent_change_60d",
    "percent_change_90d",

    "volume_24h",
    "volume_change_24h",

    "market_cap",
    "market_cap_dominance",

    "volume_rank_top100",
    "loser_rank_top100",
    "gainer_rank_top100",

    "is_top100",
]


# ============================================================
# UTILIDADES
# ============================================================

def get_api_key():
    """
    Obtiene la API key desde la variable de entorno CMC_API_KEY.
    """

    api_key = os.getenv("CMC_API_KEY")

    if not api_key:
        print("ERROR: No existe la variable de entorno CMC_API_KEY.")
        sys.exit(1)

    return api_key


def safe_float(value):
    """
    Convierte valores a float de forma segura.
    """

    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value):
    """
    Convierte valores a int de forma segura.
    """

    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def create_snapshot_id(timestamp):
    """
    ID único de la fotografía de mercado.

    Ejemplo:
    20260930_022500
    """

    return timestamp.strftime("%Y%m%d_%H%M%S")


# ============================================================
# OBTENER TOP 100
# ============================================================

def fetch_top_100(api_key):
    """
    Obtiene las primeras 100 monedas por market cap.
    """

    headers = {
        "Accepts": "application/json",
        "X-CMC_PRO_API_KEY": api_key,
    }

    params = {
        "start": 1,
        "limit": TOP_N,
        "convert": "USD",
        "sort": "market_cap",
        "sort_dir": "desc",
        "cryptocurrency_type": "all",
    }

    print("Consultando CoinMarketCap...")

    response = requests.get(
        CMC_API_URL,
        headers=headers,
        params=params,
        timeout=TIMEOUT,
    )

    print(f"HTTP: {response.status_code}")

    if response.status_code != 200:
        print("Respuesta de CoinMarketCap:")
        print(response.text)
        response.raise_for_status()

    payload = response.json()

    status = payload.get("status", {})

    error_code = status.get("error_code", 0)

    if error_code != 0:
        print("ERROR DE CMC:")
        print(status)
        sys.exit(1)

    data = payload.get("data", [])

    if not data:
        print("ERROR: CMC no devolvió monedas.")
        sys.exit(1)

    print(f"Monedas recibidas: {len(data)}")
    print(f"Credits utilizados: {status.get('credit_count')}")

    return data


# ============================================================
# CALCULAR RANKINGS
# ============================================================

def calculate_rankings(coins):
    """
    Calcula rankings internos sobre las 100 monedas descargadas.

    Losers:
        mayor caída 24h -> posición 1

    Gainers:
        mayor subida 24h -> posición 1

    Volume:
        mayor volumen 24h -> posición 1
    """

    def change_24h(coin):
        quote = coin.get("quote", {}).get("USD", {})
        return safe_float(quote.get("percent_change_24h"))

    def volume_24h(coin):
        quote = coin.get("quote", {}).get("USD", {})
        return safe_float(quote.get("volume_24h"))

    # Mayores caídas primero.
    losers = sorted(
        coins,
        key=lambda coin: (
            change_24h(coin)
            if change_24h(coin) is not None
            else float("inf")
        )
    )

    # Mayores subidas primero.
    gainers = sorted(
        coins,
        key=lambda coin: (
            change_24h(coin)
            if change_24h(coin) is not None
            else float("-inf")
        ),
        reverse=True,
    )

    # Mayor volumen primero.
    volume = sorted(
        coins,
        key=lambda coin: (
            volume_24h(coin)
            if volume_24h(coin) is not None
            else float("-inf")
        ),
        reverse=True,
    )

    loser_rank = {
        coin["id"]: position
        for position, coin in enumerate(losers, start=1)
    }

    gainer_rank = {
        coin["id"]: position
        for position, coin in enumerate(gainers, start=1)
    }

    volume_rank = {
        coin["id"]: position
        for position, coin in enumerate(volume, start=1)
    }

    return loser_rank, gainer_rank, volume_rank


# ============================================================
# CONSTRUIR REGISTROS
# ============================================================

def build_records(coins, timestamp):
    """
    Convierte la respuesta de CMC en filas para el CSV.
    """

    snapshot_id = create_snapshot_id(timestamp)

    loser_rank, gainer_rank, volume_rank = calculate_rankings(coins)

    records = []

    for coin in coins:

        quote = coin.get("quote", {}).get("USD", {})

        record = {
            "snapshot_id": snapshot_id,
            "timestamp": timestamp.isoformat(),

            "cmc_id": coin.get("id"),
            "name": coin.get("name"),
            "symbol": coin.get("symbol"),
            "slug": coin.get("slug"),

            "cmc_rank": coin.get("cmc_rank"),

            "price": safe_float(
                quote.get("price")
            ),

            "percent_change_1h": safe_float(
                quote.get("percent_change_1h")
            ),

            "percent_change_24h": safe_float(
                quote.get("percent_change_24h")
            ),

            "percent_change_7d": safe_float(
                quote.get("percent_change_7d")
            ),

            "percent_change_30d": safe_float(
                quote.get("percent_change_30d")
            ),

            "percent_change_60d": safe_float(
                quote.get("percent_change_60d")
            ),

            "percent_change_90d": safe_float(
                quote.get("percent_change_90d")
            ),

            "volume_24h": safe_float(
                quote.get("volume_24h")
            ),

            "volume_change_24h": safe_float(
                quote.get("volume_change_24h")
            ),

            "market_cap": safe_float(
                quote.get("market_cap")
            ),

            "market_cap_dominance": safe_float(
                quote.get("market_cap_dominance")
            ),

            "volume_rank_top100": volume_rank.get(
                coin.get("id")
            ),

            "loser_rank_top100": loser_rank.get(
                coin.get("id")
            ),

            "gainer_rank_top100": gainer_rank.get(
                coin.get("id")
            ),

            "is_top100": 1,
        }

        records.append(record)

    return records


# ============================================================
# GUARDAR CSV
# ============================================================

def save_records(records):
    """
    Añade los registros al histórico CSV.

    No elimina registros anteriores.
    """

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    file_exists = CSV_FILE.exists()

    with CSV_FILE.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=CSV_COLUMNS,
        )

        if not file_exists:
            writer.writeheader()

        writer.writerows(records)

    print(f"Guardado: {len(records)} registros")
    print(f"Archivo: {CSV_FILE}")


# ============================================================
# RESUMEN
# ============================================================

def print_summary(records):
    """
    Muestra un resumen para comprobar visualmente
    que la captura funciona.
    """

    print()
    print("=" * 100)
    print("RESUMEN DE CAPTURA CMC")
    print("=" * 100)

    print(f"Registros: {len(records)}")

    if not records:
        return

    timestamp = records[0]["timestamp"]

    print(f"Timestamp: {timestamp}")
    print(f"Archivo:   {CSV_FILE}")

    print()
    print("TOP 10 MAYORES CAÍDAS 24H")
    print("-" * 100)

    losers = sorted(
        records,
        key=lambda x: (
            x["percent_change_24h"]
            if x["percent_change_24h"] is not None
            else float("inf")
        )
    )

    for position, coin in enumerate(losers[:10], start=1):

        change = coin["percent_change_24h"]

        print(
            f"{position:2d}. "
            f"{coin['symbol']:<10} "
            f"{change:8.2f}% "
            f"CMC#{coin['cmc_rank']}"
        )

    print()
    print("TOP 10 MAYORES SUBIDAS 24H")
    print("-" * 100)

    gainers = sorted(
        records,
        key=lambda x: (
            x["percent_change_24h"]
            if x["percent_change_24h"] is not None
            else float("-inf")
        ),
        reverse=True,
    )

    for position, coin in enumerate(gainers[:10], start=1):

        change = coin["percent_change_24h"]

        print(
            f"{position:2d}. "
            f"{coin['symbol']:<10} "
            f"{change:8.2f}% "
            f"CMC#{coin['cmc_rank']}"
        )

    print()
    print("TOP 10 MAYOR VOLUMEN 24H")
    print("-" * 100)

    volume = sorted(
        records,
        key=lambda x: (
            x["volume_24h"]
            if x["volume_24h"] is not None
            else float("-inf")
        ),
        reverse=True,
    )

    for position, coin in enumerate(volume[:10], start=1):

        volume_value = coin["volume_24h"] or 0

        print(
            f"{position:2d}. "
            f"{coin['symbol']:<10} "
            f"${volume_value:,.0f} "
            f"CMC#{coin['cmc_rank']}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 100)
    print("CMC COLLECTOR")
    print("=" * 100)

    api_key = get_api_key()

    timestamp = datetime.now(timezone.utc)

    coins = fetch_top_100(api_key)

    records = build_records(
        coins,
        timestamp,
    )

    save_records(records)

    print_summary(records)

    print()
    print("=" * 100)
    print("CAPTURA FINALIZADA")
    print("=" * 100)


if __name__ == "__main__":
    main()
