import requests
from urllib.parse import quote_plus

for q in ["Roam Plus", "Roam+", "Roam"]:
    url = f"https://www.stuffcool.com/search/suggest.json?q={quote_plus(q)}&resources[type]=product"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    print(f"Query '{q}':")
    for p in r.json().get("resources", {}).get("results", {}).get("products", []):
        print("  ", p.get("title"), "->", p.get("url"))
