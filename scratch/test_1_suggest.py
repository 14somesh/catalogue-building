import requests

url = "https://www.stuffcool.com/search/suggest.json?q=1%23&resources[type]=product"
r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
data = r.json()
print("Suggest results for '1#':")
for p in data.get("resources", {}).get("results", {}).get("products", []):
    print(" ", p.get("title"), "->", p.get("url"))

url2 = "https://www.stuffcool.com/search/suggest.json?q=1&resources[type]=product"
r2 = requests.get(url2, headers={"User-Agent": "Mozilla/5.0"})
data2 = r2.json()
print("\nSuggest results for '1':")
for p in data2.get("resources", {}).get("results", {}).get("products", []):
    print(" ", p.get("title"), "->", p.get("url"))
