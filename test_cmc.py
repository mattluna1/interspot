import os
import requests

API_KEY = os.environ["CMC_API_KEY"]

url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

params = {
    "start": 1,
    "limit": 10,
    "convert": "USD",
}

headers = {
    "Accepts": "application/json",
    "X-CMC_PRO_API_KEY": API_KEY,
}

response = requests.get(
    url,
    params=params,
    headers=headers,
    timeout=30,
)

print("HTTP:", response.status_code)

data = response.json()

print("\nSTATUS:")
print(data.get("status"))

print("\nCOINS:")

for coin in data.get("data", []):
    usd = coin["quote"]["USD"]

    print(
        f'{coin["cmc_rank"]:>3} '
        f'{coin["symbol"]:<8} '
        f'price={usd["price"]:.6f} '
        f'24h={usd["percent_change_24h"]:.2f}% '
        f'volume=${usd["volume_24h"]:,.0f}'
    )
