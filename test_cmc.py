import os
import json
import requests


API_KEY = os.environ["CMC_API_KEY"]

URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

PARAMS = {
    "start": 1,
    "limit": 100,
    "convert": "USD",
}

HEADERS = {
    "Accepts": "application/json",
    "X-CMC_PRO_API_KEY": API_KEY,
}


def get_data():
    response = requests.get(
        URL,
        params=PARAMS,
        headers=HEADERS,
        timeout=30,
    )

    print(f"HTTP: {response.status_code}")

    response.raise_for_status()

    return response.json()


def get_usd(coin):
    return coin["quote"]["USD"]


def print_coin(coin):
    usd = get_usd(coin)

    print(
        f'#{coin["cmc_rank"]:>3} '
        f'{coin["symbol"]:<10} '
        f'price=${usd["price"]:>14,.4f} '
        f'1h={usd["percent_change_1h"]:>8.2f}% '
        f'24h={usd["percent_change_24h"]:>8.2f}% '
        f'7d={usd["percent_change_7d"]:>8.2f}% '
        f'volume=${usd["volume_24h"]:>15,.0f} '
        f'mcap=${usd["market_cap"]:>15,.0f}'
    )


def main():

    data = get_data()

    status = data["status"]
    coins = data["data"]

    print("\n" + "=" * 100)
    print("CMC STATUS")
    print("=" * 100)

    print(f'Timestamp:   {status.get("timestamp")}')
    print(f'Error code:   {status.get("error_code")}')
    print(f'Credits:      {status.get("credit_count")}')
    print(f'Total coins:  {status.get("total_count")}')
    print(f'Returned:     {len(coins)}')

    # ---------------------------------------------------------
    # TOP 100 POR MARKET CAP
    # ---------------------------------------------------------

    print("\n" + "=" * 100)
    print("TOP 100 POR MARKET CAP")
    print("=" * 100)

    for coin in coins:
        print_coin(coin)

    # ---------------------------------------------------------
    # TOP LOSERS DENTRO DEL TOP 100
    # ---------------------------------------------------------

    losers = sorted(
        coins,
        key=lambda coin: get_usd(coin)["percent_change_24h"]
    )

    print("\n" + "=" * 100)
    print("TOP 10 MAYORES CAIDAS 24H - DENTRO DEL TOP 100")
    print("=" * 100)

    for coin in losers[:10]:
        print_coin(coin)

    # ---------------------------------------------------------
    # TOP GAINERS DENTRO DEL TOP 100
    # ---------------------------------------------------------

    gainers = sorted(
        coins,
        key=lambda coin: get_usd(coin)["percent_change_24h"],
        reverse=True,
    )

    print("\n" + "=" * 100)
    print("TOP 10 MAYORES SUBIDAS 24H - DENTRO DEL TOP 100")
    print("=" * 100)

    for coin in gainers[:10]:
        print_coin(coin)

    # ---------------------------------------------------------
    # TOP VOLUME
    # ---------------------------------------------------------

    volume = sorted(
        coins,
        key=lambda coin: get_usd(coin)["volume_24h"],
        reverse=True,
    )

    print("\n" + "=" * 100)
    print("TOP 10 MAYOR VOLUMEN 24H - DENTRO DEL TOP 100")
    print("=" * 100)

    for coin in volume[:10]:
        print_coin(coin)

    # ---------------------------------------------------------
    # PARTE BAJA DEL TOP 100
    # ---------------------------------------------------------

    bottom_10 = sorted(
        coins,
        key=lambda coin: coin["cmc_rank"],
        reverse=True,
    )

    print("\n" + "=" * 100)
    print("ULTIMAS 10 POSICIONES DEL TOP 100")
    print("=" * 100)

    for coin in bottom_10[:10]:
        print_coin(coin)

    # ---------------------------------------------------------
    # BTC
    # ---------------------------------------------------------

    btc = next(
        coin
        for coin in coins
        if coin["symbol"] == "BTC"
    )

    print("\n" + "=" * 100)
    print("BTC - DATOS COMPLETOS")
    print("=" * 100)

    print(
        json.dumps(
            btc,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
