# diagnostico_coinbeacon.py
import os, json, urllib.request

TOKEN = os.environ.get("COINBEACON_TOKEN")
print(f"Token presente: {bool(TOKEN)}")

# Llamada SIN symbol, como debería hacer obtener_monedas_recomendadas
url = "https://api.coinbeacon.io/detectors/trendlines?exchange=binance&timeframe=15m&quote=USDT&limit=60"

headers = {
    "User-Agent": "Mozilla/5.0",
    "Cookie": f"access_token={TOKEN}",
    "Accept": "application/json",
}

try:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8")
    data = json.loads(raw)
    print(f"Código de respuesta OK")
    print(f"Tipo de dato: {type(data)}")
    
    items = data.get("items", [])
    print(f"Total items recibidos: {len(items)}")
    
    if items:
        print("\nPrimeros 10 items (symbol + bias):")
        for item in items[:10]:
            print(f"  {item.get('symbol')} | {item.get('bias')}")
        
        # Ver estructura completa del primer item
        print("\nEstructura completa del primer item:")
        print(json.dumps(items[0], indent=2, ensure_ascii=False)[:1000])
    else:
        print("La respuesta NO contiene 'items' o está vacía")
        print(f"Claves disponibles en la respuesta: {list(data.keys())}")
        
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
