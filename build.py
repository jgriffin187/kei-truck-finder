#!/usr/bin/env python3
"""Build the dashboard site from listings.json.

Writes site/data.json and site/img/<id>.jpg thumbnails (480px wide), and
publish_plan.json listing which files changed since the last publish:
  {"new_images": [...], "removed_images": [...], "data": "site/data.json"}
New images must be looked at before they are published.

Usage: python3 -I build.py [--dir /mnt/project-files/kei-truck]
"""
import hashlib
import io
import json
import os
import sys

import requests
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
D = sys.argv[sys.argv.index("--dir") + 1] if "--dir" in sys.argv else HERE
REMOTE = "--remote-images" in sys.argv
SITE = os.path.join(D, "site")
IMG = os.path.join(SITE, "img")
os.makedirs(IMG, exist_ok=True)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

data = json.load(open(os.path.join(D, "listings.json")))
pub_p = os.path.join(D, "published_images.json")
published = set(json.load(open(pub_p))) if os.path.exists(pub_p) else set()

used, new = set(), []
for t in data["trucks"]:
    t["img"] = None
    src = t.get("image")
    if not src or not src.startswith("http"):
        continue
    if "--remote-images" in sys.argv:  # GitHub Pages: link the seller's photo directly
        t["img"] = src
        continue
    name = hashlib.sha1(t["key"].encode()).hexdigest()[:12] + ".jpg"
    path = os.path.join(IMG, name)
    if not os.path.exists(path):
        try:
            r = requests.get(src, headers={"User-Agent": UA, "Referer": t["url"]}, timeout=30)
            r.raise_for_status()
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
            im.thumbnail((480, 360))
            im.save(path, "JPEG", quality=78, optimize=True)
        except Exception as e:
            print("image failed:", t["key"], str(e)[:80], file=sys.stderr)
            continue
    t["img"] = "img/" + name
    used.add(name)
    if name not in published:
        new.append("img/" + name)

slim_keys = ["key", "source", "url", "title", "reg_year", "reg_month", "mfg_year", "km", "usd",
             "delivered_usd", "delivered_to", "ac", "ha7", "vin", "status", "build_latest",
             "build_month_known", "legal_from", "location", "country", "seller", "also_on",
             "first_seen", "is_new", "price_drop", "low_km", "img", "reserved"]
out = {k: v for k, v in data.items() if k != "trucks"}
out["trucks"] = [{k: t.get(k) for k in slim_keys} for t in data["trucks"]]
json.dump(out, open(os.path.join(SITE, "data.json"), "w"), separators=(",", ":"))

removed = sorted("img/" + n for n in published - used)
for r in removed:
    try:
        os.remove(os.path.join(SITE, r))
    except OSError:
        pass
plan = {"new_images": sorted(new), "removed_images": removed, "data": "site/data.json",
        "all_images": sorted("img/" + n for n in used)}
json.dump(plan, open(os.path.join(D, "publish_plan.json"), "w"), indent=1)
print(json.dumps({"trucks": len(out["trucks"]), "new_images": len(new), "removed": len(removed)}))
