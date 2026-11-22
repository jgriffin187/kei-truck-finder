#!/usr/bin/env python3
"""Kei truck collector: Honda Acty HA7 4WD listings, 25-year import rule.

Reads listing sites, normalizes each truck, decides import eligibility,
merges with history in state.json (first_seen / last_seen), and writes
listings.json for the dashboard builder.

Usage: python3 -I collector.py [--dir /mnt/project-files/kei-truck]
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import warnings

import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = sys.argv[sys.argv.index("--dir") + 1] if "--dir" in sys.argv else HERE
MIN_YEAR, MAX_YEAR = 1999, 2003  # HA7 launched 1999; fetch a bit past the cutoff for the watch list
LOW_KM = 48280  # 30,000 miles
JPY_PER_USD = 155.0  # replaced at runtime from carfromjapan when available

S = requests.Session()
S.headers["User-Agent"] = UA
ERRORS = {}


def get(url, **kw):
    for attempt in range(3):
        try:
            r = S.get(url, timeout=40, **kw)
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
        except Exception as e:  # network hiccup
            last = str(e)[:120]
        time.sleep(2 + attempt * 3)
    raise RuntimeError(f"{url}: {last}")


def num(s):
    if s is None:
        return None
    m = re.search(r"\d[\d,]*", str(s))
    return int(m.group(0).replace(",", "")) if m else None


def txt(el):
    return re.sub(r"\s+", " ", el.get_text(" | ", strip=True))


def field(t, label):
    m = re.search(re.escape(label) + r"\s*\|\s*([^|]+)", t)
    return m.group(1).strip() if m else None


def ac_from(t):
    return True if re.search(r"\bA/C\b|\bair ?con|air[- ]condition", t, re.I) else None


def chassis_of(*vals):
    for v in vals:
        if not v:
            continue
        m = re.search(r"\b(?:[A-Z]{1,3}-)?(H[AHM]\d)\b", str(v).upper())
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------- sources

def src_carfromjapan():
    global JPY_PER_USD
    out, page, pages = [], 1, 1
    while page <= pages:
        url = ("https://carfromjapan.com/cheap-used-honda-acty-truck-for-sale-drive-type-4wd"
               f"?minYear={MIN_YEAR}&maxYear={MAX_YEAR}&maxMonth=dec&country=USA&port=Long%20Beach%2FLos%20Angeles"
               + (f"&page={page}" if page > 1 else ""))
        h = get(url)
        chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', h, re.S)
        t = "".join(json.loads('"' + c + '"') for c in chunks)
        dec, seen = json.JSONDecoder(), {}
        for m in re.finditer(r'\{"id":"[0-9a-f]{24}","year"', t):
            o, _ = dec.raw_decode(t, m.start())
            seen.setdefault(o["id"], o)  # first copy is the full one
        tc = re.search(r'"totalCount":(\d+),"totalPage":(\d+)', t)
        if tc:
            pages = int(tc.group(2))
        for c in seen.values():
            if c.get("rateUSD"):
                JPY_PER_USD = float(c["rateUSD"])
            spec = c.get("specialities") if isinstance(c.get("specialities"), list) else []
            offer = c.get("offer") if isinstance(c.get("offer"), dict) else {}
            img = c.get("imagePreview") if isinstance(c.get("imagePreview"), dict) else {}
            out.append(dict(
                source="CAR FROM JAPAN", sid=c["id"],
                url="https://carfromjapan.com" + c["carUrl"],
                title=c.get("title"), reg_year=c.get("registrationYear") or c.get("year"),
                reg_month=c.get("registrationMonth"), mfg_year=c.get("manufacturedYear"),
                km=c.get("mileage"), price_usd=c.get("priceUSD"),
                delivered_usd=offer.get("priceUSD"),
                delivered_to=offer.get("port"),
                ac=True if "A/C" in spec else False,
                drive=c.get("driveTypeKey"), chassis=chassis_of(c.get("modelCode"), c.get("VIN")),
                vin=c.get("VIN"), trans=c.get("transmissionKey"),
                image=(f"https://{img['cdnUri']}/{img['filename']}" if img.get("filename") else None),
                location="Japan", country="Japan", seller="CAR FROM JAPAN"))
        page += 1
    return out


def src_sbt():
    out, page = [], 1
    while page <= 5:
        url = (f"https://www.sbtjapan.com/used-cars/honda/acty-truck?year__from={MIN_YEAR}"
               f"&year__to={MAX_YEAR}&country=85&port=212&page={page}")  # 85/212 = USA, Long Beach
        s = BeautifulSoup(get(url), "lxml")
        infos = [c for c in s.select("div.card-product__infos") if "Model Code" in c.get_text()]
        if not infos:
            break
        for inf in infos:
            card = inf
            while card.parent is not None and "card-products" not in (card.get("class") or []):
                card = card.parent
            t = txt(card)
            a = inf.find("a", href=re.compile(r"/used-cars/honda/acty-truck/[A-Z0-9]+"))
            if not a:
                continue
            reg = field(t, "Registration:") or ""
            ry, rm = (re.findall(r"\d+", reg) + [None, None])[:2]
            img = card.find("img", src=re.compile("img.sbtjapan.com"))
            out.append(dict(
                source="SBT Japan", sid=a["href"].split("?")[0].rstrip("/").split("/")[-1],
                url="https://www.sbtjapan.com" + a["href"],
                title=(re.search(r"\d{4}(?:/\d+)? HONDA ACTY TRUCK[^|]*", t).group(0).strip().title() if re.search(r"\d{4}(?:/\d+)? HONDA ACTY TRUCK[^|]*", t) else "Honda Acty Truck"), reg_year=num(ry), reg_month=num(rm),
                mfg_year=num(field(t, "Manufacture:")), km=num(field(t, "Mileage") and re.search(r"([\d,]+)km \| Mileage", t) and re.search(r"([\d,]+)km \| Mileage", t).group(1)),
                price_usd=num(re.search(r"Vehicle Price \| USD \| ([\d,]+)", t) and re.search(r"Vehicle Price \| USD \| ([\d,]+)", t).group(1)),
                delivered_usd=num(re.search(r"Total Price \| USD \| ([\d,]+)", t) and re.search(r"Total Price \| USD \| ([\d,]+)", t).group(1)),
                delivered_to="Long Beach",
                ac=ac_from(t), drive=re.search(r"\| (4WD|2WD) \| Drive", t) and re.search(r"\| (4WD|2WD) \| Drive", t).group(1),
                chassis=chassis_of(re.search(r"\| (\S+) \| Model Code", t) and re.search(r"\| (\S+) \| Model Code", t).group(1)),
                reserved="RESERVED" in t, image=img["src"] if img else None,
                location=field(t, "Inventory location:"), country="Japan", seller="SBT Japan"))
        page += 1
    return out


def src_tcv():
    url = f"https://www.tc-v.com/used_car/honda/acty%20truck/?fid={MIN_YEAR}&jid={MAX_YEAR}&dr=10"
    s = BeautifulSoup(get(url), "lxml")
    out = []
    for c in s.select("div.car-item__detail"):
        t = txt(c)
        a = c.find("a", href=re.compile(r"/used_car/honda/acty%20truck/\d+/"))
        if not a:
            continue
        reg = field(t, "Registration Year") or ""
        ry, rm = (re.findall(r"\d+", reg) + [None, None])[:2]
        sub = c.find(class_="title-wrap__sub")
        img = c.find("img")
        seller = c.find(class_="dealer-name")
        out.append(dict(
            source="TCV", sid=a["href"].strip("/").split("/")[-1],
            url="https://www.tc-v.com" + a["href"], title=f"{ry} Honda Acty Truck",
            reg_year=num(ry), reg_month=num(rm), mfg_year=num(field(t, "Manufacture Year")),
            km=num(field(t, "Mileage")), price_usd=num(field(t, "FOB Price")), ac=ac_from(t),
            drive="4WD" if "4WD" in t else None, chassis=chassis_of(sub and sub.get_text()),
            vin=sub and sub.get_text(strip=True), image=img and (img.get("src") or img.get("data-src")),
            location="Japan", country="Japan", seller=seller.get_text(strip=True) if seller else "TCV"))
    return out


def src_goonet():
    base = ("https://www.goo-net-exchange.com/php/search/summary.php?brand_cd=1020&car_cd=10204111"
            f"&syear_min={MIN_YEAR}&syear_max={MAX_YEAR}")
    ids, page = [], 1
    while page <= 6:
        h = get(base + f"&page={page}")
        new = [i for i in dict.fromkeys(re.findall(r"/usedcars/HONDA/ACTY_TRUCK/(\d{15,})/", h)) if i not in ids]
        if not new:
            break
        ids += new
        page += 1
    out = []
    for gid in ids:
        url = f"https://www.goo-net-exchange.com/usedcars/HONDA/ACTY_TRUCK/{gid}/"
        try:
            h = get(url)
        except Exception as e:
            ERRORS.setdefault("Goo-net Exchange (some details)", str(e)[:120])
            continue
        s = BeautifulSoup(h, "lxml")
        t = txt(s)
        drive = field(t, "Drive System")
        if drive and "4WD" not in drive:
            continue
        my = field(t, "Month/Year") or ""
        parts = re.findall(r"\d+", my)
        ry = int([p for p in parts if len(p) == 4][0]) if any(len(p) == 4 for p in parts) else None
        rm = next((int(p) for p in parts if len(p) <= 2), None)
        ac = None
        for li in s.find_all("li"):
            if li.get_text(strip=True) == "Air Conditioner":
                ac = "active" in (li.get("class") or [])
                break
        price = re.search(r"Car Price \(FOB\) \| ¥([\d,]+)", t) or re.search(r"¥([\d,]+)", t)
        img = s.find("img", src=re.compile(r"picture\d\.goo-net\.com"))
        loc = re.search(r"\| ([A-Za-z]+) Japan \|", t)
        title = s.find("h1")
        out.append(dict(
            source="Goo-net Exchange", sid=gid, url=url,
            title=title.get_text(" ", strip=True)[:80] if title else "Honda Acty Truck",
            reg_year=ry, reg_month=rm, km=num(field(t, "Mileage")),
            price_jpy=num(price.group(1)) if price else None, ac=ac, drive=drive,
            chassis=chassis_of(field(t, "Chassis No")), vin=field(t, "Chassis No"),
            image=img["src"] if img else None,
            location=(loc.group(1) + ", Japan") if loc else "Japan", country="Japan",
            seller="Goo-net Exchange dealer"))
        time.sleep(0.6)
    return out


def src_beforward():
    out = []
    url = (f"https://www.beforward.jp/stocklist/make=2/model=287/model_code=HA7/mfg_year_to={MAX_YEAR}/"
           "tp_country_id=90/tp_port_id=338/alt_port_id=338/certificate=1/insurance=1/protection=1/"
           "shipping_method=1/currency=USD/")  # 90/338 = USA, Los Angeles
    for page in range(1, 6):
        u = url if page == 1 else url + f"page={page}/"
        try:
            s = BeautifulSoup(get(u), "lxml")
        except Exception:
            if page == 1:
                raise
            break
        rows = s.select("tr.stocklist-row")
        if not rows:
            break
        for r in rows:
            t = txt(r)
            a = r.find("a", href=re.compile(r"/honda/acty/"))
            if not a:
                continue
            yr = field(t, "Year") or ""
            ry, rm = (re.findall(r"\d+", yr) + [None, None])[:2]
            img = r.find("img", src=re.compile("image-cdn"))
            ref = re.search(r"Ref No\. (\w+)", t)
            out.append(dict(
                source="BE FORWARD", sid=ref.group(1) if ref else a["href"],
                url="https://www.beforward.jp" + a["href"],
                title=re.search(r"\d{4} HONDA ACTY[^|]*", t).group(0).strip() if re.search(r"\d{4} HONDA ACTY[^|]*", t) else "Honda Acty",
                reg_year=num(ry), reg_month=num(rm), km=num(field(t, "Mileage")),
                price_usd=num(field(t, "Price")), delivered_usd=num(field(t, "Total Price")),
                delivered_to=field(t, "CIF + Cert to"),
                ac=ac_from(t), drive="4WD" if "4WD" in t else None, chassis=chassis_of(field(t, "Model code")),
                image=("https:" + img["src"]) if img else None,
                location=(field(t, "Location") or "") + ", Japan", country="Japan", seller="BE FORWARD"))
        if len(rows) < 20:
            break
    return out


def field_block(t, label, n=1500):
    i = t.find(label)
    return t[i:i + n] if i >= 0 else ""


SOURCES = [("CAR FROM JAPAN", src_carfromjapan), ("SBT Japan", src_sbt), ("TCV", src_tcv),
           ("Goo-net Exchange", src_goonet), ("BE FORWARD", src_beforward),
           ("US dealer leads", lambda: src_us_leads())]


def src_us_leads():
    """Trucks already in the US, found by the daily web search and saved to us_leads.json.

    Each lead: {url, title, reg_year, km, price_usd, ac, chassis, drive, location, seller,
    image, sold}. The daily run updates this file before running the collector.
    """
    p = os.path.join(OUT_DIR, "us_leads.json")
    if not os.path.exists(p):
        return []
    out = []
    for v in json.load(open(p)):
        v = dict(v)
        v.setdefault("source", "US dealer")
        v.setdefault("sid", v["url"].rstrip("/").split("/")[-1])
        v.setdefault("country", "United States")
        out.append(v)
    return out


# ---------------------------------------------------------------- rules

def month_add(y, m, k):
    i = y * 12 + (m - 1) + k
    return i // 12, i % 12 + 1


def classify(t, today):
    """Return (is_match, ha7_note) and set eligibility fields."""
    ch = (t.get("chassis") or "").upper()
    drive = (t.get("drive") or "").upper()
    if ch and ch != "HA7":
        return False
    if drive and "4WD" not in drive and "4X4" not in drive:
        return False
    ry = t.get("reg_year")
    if not ry or ry < MIN_YEAR or ry > MAX_YEAR:
        return False
    if ch == "HA7":
        t["ha7"] = "confirmed"
    elif ry >= 2000 and ("4WD" in drive):
        t["ha7"] = "likely"  # every 2000+ 4WD Acty truck is an HA7
    else:
        t["ha7"] = "check"

    # Latest possible build month: registration happens on/after manufacture.
    if t.get("mfg_year") and t.get("reg_month") and t["mfg_year"] == ry:
        by, bm = ry, t["reg_month"]
    elif t.get("mfg_year") and t["mfg_year"] < ry:
        by, bm = t["mfg_year"], 12
    elif t.get("reg_month"):
        by, bm = ry, t["reg_month"]
    else:
        by, bm = ry, 12
    t["build_latest"] = f"{by}-{bm:02d}"
    t["build_month_known"] = bool(t.get("reg_month"))
    ly, lm = by + 25, bm
    t["legal_from"] = f"{ly}-{lm:02d}"
    now_i = today.year * 12 + today.month - 1
    leg_i = ly * 12 + lm - 1
    in_na = t.get("country") in ("United States", "USA")
    if in_na:
        t["status"] = "in_us"
    elif leg_i <= now_i:
        t["status"] = "legal_now"
    elif leg_i <= now_i + 2:
        t["status"] = "on_arrival"
    elif leg_i <= now_i + 12:
        t["status"] = "watch"
    else:
        return False
    return True


def usd(t):
    if t.get("price_usd"):
        return t["price_usd"]
    if t.get("price_jpy"):
        return round(t["price_jpy"] / JPY_PER_USD)
    return None


def main():
    today = dt.date.today()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    raw, counts = [], {}
    for name, fn in SOURCES:
        try:
            items = fn()
            counts[name] = len(items)
            raw += items
        except Exception as e:
            ERRORS[name] = str(e)[:200]
            counts[name] = None
        print(f"{name}: {counts[name]}", file=sys.stderr)

    trucks = []
    for t in raw:
        if t.get("sold"):
            continue
        if not classify(t, today):
            continue
        t["usd"] = usd(t)
        t["low_km"] = bool(t.get("km") and t["km"] <= LOW_KM)
        t["key"] = f"{t['source']}|{t['sid']}"
        trucks.append(t)

    # Cross-site duplicates: same year and exact same km in Japan.
    groups = {}
    for t in trucks:
        k = (t["reg_year"], t.get("km"), t["country"]) if t.get("km") else t["key"]
        groups.setdefault(k, []).append(t)
    merged = []
    for g in groups.values():
        g.sort(key=lambda x: (x.get("usd") is None, x.get("usd") or 0))
        best = dict(g[0])
        best["also_on"] = [{"source": x["source"], "url": x["url"], "usd": x.get("usd")} for x in g[1:]]
        if best.get("ac") is None and any(x.get("ac") for x in g):
            best["ac"] = True
        merged.append(best)

    state_p = os.path.join(OUT_DIR, "state.json")
    state = json.load(open(state_p)) if os.path.exists(state_p) else {"seen": {}}
    first_run = not state["seen"]
    new = []
    for t in merged:
        rec = state["seen"].get(t["key"])
        if rec is None:
            rec = {"first_seen": now, "first_usd": t.get("usd")}
            state["seen"][t["key"]] = rec
            if not first_run:
                new.append(t["key"])
        rec["last_seen"] = now
        t["first_seen"] = rec["first_seen"]
        t["price_drop"] = bool(rec.get("first_usd") and t.get("usd") and t["usd"] < rec["first_usd"])
        t["is_new"] = t["key"] in new

    order = {"legal_now": 0, "in_us": 0, "on_arrival": 1, "watch": 2}
    merged.sort(key=lambda t: (order[t["status"]], -t["reg_year"],
                               t.get("km") or 10 ** 7, not t.get("ac")))
    data = {
        "generated_at": now, "today": today.isoformat(),
        "cutoff_build": f"{today.year - 25}-{today.month:02d}",
        "jpy_per_usd": JPY_PER_USD, "source_counts": counts, "errors": ERRORS,
        "new_keys": new, "first_run": first_run, "trucks": merged,
    }
    json.dump(data, open(os.path.join(OUT_DIR, "listings.json"), "w"), indent=1, default=str)
    json.dump(state, open(state_p, "w"), indent=1)
    print(json.dumps({"trucks": len(merged), "new": len(new), "errors": ERRORS, "counts": counts}))


if __name__ == "__main__":
    main()
