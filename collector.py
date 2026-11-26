#!/usr/bin/env python3
"""Kei truck collector: Honda Acty HA7 and Subaru Sambar TT2 4WD listings, 25-year import rule.

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
# Newest model year fetched: 23 years back, so trucks turning 25 in the next year or two
# show up in "Coming up" (2003 in 2026). Rolls forward each January.
MAX_YEAR = dt.date.today().year - 23
# Models searched. Each source runs once per model. Truck keys stay "<source>|<id>",
# so saved and hidden trucks from before the Sambar was added keep working.
MODELS = {
    "acty": dict(
        name="Honda Acty", chassis="HA7", manual_only=False,
        min_year=1999, max_year=MAX_YEAR,  # HA7 launched 1999
        cfj="honda-acty-truck-for-sale-drive-type-4wd", sbt="honda/acty-truck",
        tcv="honda/acty%20truck", goo=("1020", "10204111", "HONDA/ACTY_TRUCK"),
        bf="make=2/model=287/model_code=HA7", bf_path="/honda/acty"),
    "sambar": dict(
        name="Subaru Sambar", chassis="TT2", manual_only=True,
        min_year=2000, max_year=MAX_YEAR,  # Josh: 2000 and newer, rolling like the Acty (2026-11-26)
        cfj="subaru-sambar-truck-for-sale-drive-type-4wd-transmission-manual", sbt="subaru/sambar-truck",
        tcv="subaru/sambar%20truck", goo=("1045", "10454111", "SUBARU/SAMBAR_TRUCK"),
        bf="make=94/model=13236/model_code=TT2", bf_path="/subaru/sambar"),
}
LOW_KM = 48280  # 30,000 miles
# Josh looks at the page every morning at 5 AM Mountain. A truck counts as new
# (NEW badge, shown at the top) when it was first seen after the last such check.
CHECK_TZ, CHECK_HOUR = "America/Denver", 5


def last_check(now_utc):
    """The most recent 5 AM Mountain at or before now_utc, as a UTC datetime."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(CHECK_TZ)
    except Exception:
        tz = dt.timezone(dt.timedelta(hours=-7))
    local = now_utc.astimezone(tz)
    c = local.replace(hour=CHECK_HOUR, minute=0, second=0, microsecond=0)
    if c > local:
        c = (local - dt.timedelta(days=1)).replace(hour=CHECK_HOUR, minute=0, second=0, microsecond=0)
    return c.astimezone(dt.timezone.utc)
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


def trans_of(t):
    m = re.search(r"\b(MT|AT|CVT|MANUAL|AUTOMATIC)\b", str(t or "").upper())
    return None if not m else ("MANUAL" if m.group(1) in ("MT", "MANUAL") else "AUTO")


def ac_from(t):
    return True if re.search(r"\bA/C\b|\bair ?con|air[- ]condition", t, re.I) else None


def chassis_of(*vals):
    for v in vals:
        if not v:
            continue
        m = re.search(r"\b(?:[A-Z]{1,3}-)?(H[AHM]\d|T[TV]\d|KS\d)", str(v).upper())
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------- sources

def src_carfromjapan(M):
    global JPY_PER_USD
    out, page, pages = [], 1, 1
    while page <= pages:
        url = (f"https://carfromjapan.com/cheap-used-{M['cfj']}"
               f"?minYear={M['min_year']}&maxYear={M['max_year']}&maxMonth=dec&country=USA&port=Long%20Beach%2FLos%20Angeles"
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
                vin=c.get("VIN"), trans=trans_of(c.get("transmissionKey")),
                image=(f"https://{img['cdnUri']}/{img['filename']}" if img.get("filename") else None),
                location="Japan", country="Japan", seller="CAR FROM JAPAN"))
        page += 1
    return out


def src_sbt(M):
    out, page = [], 1
    name = M["name"].upper() + " TRUCK"
    while page <= 5:
        url = (f"https://www.sbtjapan.com/used-cars/{M['sbt']}?year__from={M['min_year']}"
               f"&year__to={M['max_year']}&country=85&port=212&page={page}")  # 85/212 = USA, Long Beach
        s = BeautifulSoup(get(url), "lxml")
        infos = [c for c in s.select("div.card-product__infos") if "Model Code" in c.get_text()]
        if not infos:
            break
        for inf in infos:
            card = inf
            while card.parent is not None and "card-products" not in (card.get("class") or []):
                card = card.parent
            t = txt(card)
            a = inf.find("a", href=re.compile(r"/used-cars/" + M["sbt"] + r"/[A-Z0-9]+"))
            if not a:
                continue
            reg = field(t, "Registration:") or ""
            ry, rm = (re.findall(r"\d+", reg) + [None, None])[:2]
            img = card.find("img", src=re.compile("img.sbtjapan.com"))
            out.append(dict(
                source="SBT Japan", sid=a["href"].split("?")[0].rstrip("/").split("/")[-1],
                url="https://www.sbtjapan.com" + a["href"],
                title=(re.search(r"\d{4}(?:/\d+)? " + name + r"[^|]*", t).group(0).strip().title() if re.search(r"\d{4}(?:/\d+)? " + name + r"[^|]*", t) else M["name"] + " Truck"), reg_year=num(ry), reg_month=num(rm),
                mfg_year=num(field(t, "Manufacture:")), km=num(field(t, "Mileage") and re.search(r"([\d,]+)km \| Mileage", t) and re.search(r"([\d,]+)km \| Mileage", t).group(1)),
                price_usd=num(re.search(r"Vehicle Price \| USD \| ([\d,]+)", t) and re.search(r"Vehicle Price \| USD \| ([\d,]+)", t).group(1)),
                delivered_usd=num(re.search(r"Total Price \| USD \| ([\d,]+)", t) and re.search(r"Total Price \| USD \| ([\d,]+)", t).group(1)),
                delivered_to="Long Beach",
                ac=ac_from(t), drive=re.search(r"\| (4WD|2WD) \| Drive", t) and re.search(r"\| (4WD|2WD) \| Drive", t).group(1),
                chassis=chassis_of(re.search(r"\| (\S+) \| Model Code", t) and re.search(r"\| (\S+) \| Model Code", t).group(1)),
                trans=trans_of(re.search(r"\| (\w+) \| Transmission", t) and re.search(r"\| (\w+) \| Transmission", t).group(1)),
                reserved="RESERVED" in t, image=img["src"] if img else None,
                location=field(t, "Inventory location:"), country="Japan", seller="SBT Japan"))
        page += 1
    return out


def src_tcv(M):
    url = f"https://www.tc-v.com/used_car/{M['tcv']}/?fid={M['min_year']}&jid={M['max_year']}&dr=10"
    s = BeautifulSoup(get(url), "lxml")
    out = []
    for c in s.select("div.car-item__detail"):
        t = txt(c)
        a = c.find("a", href=re.compile(r"/used_car/" + re.escape(M["tcv"]) + r"/\d+/"))
        if not a:
            continue
        reg = field(t, "Registration Year") or ""
        ry, rm = (re.findall(r"\d+", reg) + [None, None])[:2]
        sub = c.find(class_="title-wrap__sub")
        img = c.find("img")
        seller = c.find(class_="dealer-name")
        out.append(dict(
            source="TCV", sid=a["href"].strip("/").split("/")[-1],
            url="https://www.tc-v.com" + a["href"], title=f"{ry} {M['name']} Truck",
            reg_year=num(ry), reg_month=num(rm), mfg_year=num(field(t, "Manufacture Year")),
            km=num(field(t, "Mileage")), price_usd=num(field(t, "FOB Price")), ac=ac_from(t),
            drive="4WD" if "4WD" in t else None, chassis=chassis_of(sub and sub.get_text()),
            trans=trans_of(re.search(r"\| (MT|AT|CVT) \|", t) and re.search(r"\| (MT|AT|CVT) \|", t).group(1)),
            vin=sub and sub.get_text(strip=True), image=img and (img.get("src") or img.get("data-src")),
            location="Japan", country="Japan", seller=seller.get_text(strip=True) if seller else "TCV"))
    return out


def src_goonet(M):
    brand, car, path = M["goo"]
    base = (f"https://www.goo-net-exchange.com/php/search/summary.php?brand_cd={brand}&car_cd={car}"
            f"&syear_min={M['min_year']}&syear_max={M['max_year']}")
    ids, page = [], 1
    while page <= 6:
        h = get(base + f"&page={page}")
        new = [i for i in dict.fromkeys(re.findall(r"/usedcars/" + path + r"/(\d{15,})/", h)) if i not in ids]
        if not new:
            break
        ids += new
        page += 1
    out = []
    for gid in ids:
        url = f"https://www.goo-net-exchange.com/usedcars/{path}/{gid}/"
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
            title=title.get_text(" ", strip=True)[:80] if title else M["name"] + " Truck",
            reg_year=ry, reg_month=rm, km=num(field(t, "Mileage")),
            price_jpy=num(price.group(1)) if price else None, ac=ac, drive=drive,
            trans=trans_of(field(t, "Transmission")),
            chassis=chassis_of(field(t, "Chassis No")), vin=field(t, "Chassis No"),
            image=img["src"] if img else None,
            location=(loc.group(1) + ", Japan") if loc else "Japan", country="Japan",
            seller="Goo-net Exchange dealer"))
        time.sleep(0.6)
    return out


def src_beforward(M):
    out = []
    url = (f"https://www.beforward.jp/stocklist/{M['bf']}/mfg_year_to={M['max_year']}/"
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
            a = r.find("a", href=re.compile(M["bf_path"]))
            if not a:
                continue
            yr = field(t, "Year") or ""
            ry, rm = (re.findall(r"\d+", yr) + [None, None])[:2]
            img = r.find("img", src=re.compile("image-cdn"))
            ref = re.search(r"Ref No\. (\w+)", t)
            out.append(dict(
                source="BE FORWARD", sid=ref.group(1) if ref else a["href"],
                url="https://www.beforward.jp" + a["href"],
                title=re.search(r"\d{4} " + M["name"].upper() + r"[^|]*", t).group(0).strip() if re.search(r"\d{4} " + M["name"].upper() + r"[^|]*", t) else M["name"],
                reg_year=num(ry), reg_month=num(rm), km=num(field(t, "Mileage")),
                price_usd=num(field(t, "Price")), delivered_usd=num(field(t, "Total Price")),
                delivered_to=field(t, "CIF + Cert to"),
                ac=ac_from(t), drive="4WD" if "4WD" in t else None, chassis=chassis_of(field(t, "Model code")),
                trans=trans_of(field(t, "Trans.")), sold=bool(re.search(r"\| SOLD \|", t)),
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
           ("US dealer leads", lambda M: src_us_leads(M))]


def src_us_leads(M):
    """Trucks already in the US, found by the daily web search and saved to us_leads.json.

    Each lead: {model ("acty" default, or "sambar"), url, title, reg_year, km, price_usd, ac,
    chassis, drive, trans, location, seller, image, sold}. The daily run updates this file
    before running the collector.
    """
    p = os.path.join(OUT_DIR, "us_leads.json")
    if not os.path.exists(p):
        return []
    out = []
    for v in json.load(open(p)):
        if MODELS.get(v.get("model") or "acty") is not M:
            continue
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
    """Return True if the truck matches its model's rules, and set eligibility fields.

    t["ha7"] keeps its old name for the dashboard: it is the chassis check for the truck's
    own model (HA7 for the Acty, TT2 for the Sambar): confirmed / likely / check.
    """
    M = MODELS[t["model"]]
    ch = (t.get("chassis") or "").upper()
    drive = (t.get("drive") or "").upper()
    if ch and ch != M["chassis"]:
        return False
    if drive and "4WD" not in drive and "4X4" not in drive:
        return False
    if M["manual_only"] and t.get("trans") and t["trans"] != "MANUAL":
        return False
    ry = t.get("reg_year")
    if not ry or ry < M["min_year"] or ry > M["max_year"]:
        return False
    t["chassis_code"] = M["chassis"]
    if ch == M["chassis"]:
        t["ha7"] = "confirmed"
    elif ry >= 2000 and ("4WD" in drive):
        t["ha7"] = "likely"  # every 2000+ 4WD Acty truck is an HA7; every 4WD Sambar truck of 1999-2012 is a TT2
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
    global JPY_PER_USD
    today = dt.date.today()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    # Last run's results: if a site can't be read today (bot check, outage),
    # keep showing its trucks from last time instead of dropping them.
    prev_p = os.path.join(OUT_DIR, "listings.json")
    try:
        prev = json.load(open(prev_p))
    except Exception:
        prev = {}
    raw, counts, model_counts = [], {}, {}
    for name, fn in SOURCES:
        for mk, M in MODELS.items():
            try:
                items = fn(M)
                for it in items:
                    it["model"] = mk
                n = len(items)
                raw += items
            except Exception as e:
                err = str(e)[:200]
                ERRORS[name] = (ERRORS[name] + " / " if name in ERRORS else "") + f"{M['name']}: {err}"
                n = None
                kept = [dict(t, stale=True) for t in prev.get("trucks", [])
                        if t.get("source") == name and (t.get("model") or "acty") == mk]
                raw += kept
                if kept:
                    print(f"{name} {mk}: kept {len(kept)} trucks from last run", file=sys.stderr)
            model_counts.setdefault(name, {})[mk] = n
            if n is not None:
                counts[name] = (counts.get(name) or 0) + n
            else:
                counts.setdefault(name, None)
            print(f"{name} {mk}: {n}", file=sys.stderr)

    if "CAR FROM JAPAN" in ERRORS and prev.get("jpy_per_usd"):
        JPY_PER_USD = prev["jpy_per_usd"]  # the live rate comes from that site; reuse the last good one

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
        k = (t["model"], t["reg_year"], t.get("km"), t["country"]) if t.get("km") else t["key"]
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
    # Records from before a model was added have no "model" (they are all Acty).
    known_models = {rec.get("model") or "acty" for rec in state["seen"].values()}
    since = last_check(dt.datetime.fromisoformat(now))
    # Each model's earliest run found trucks that were listed all along (a model's
    # first run, or the very first run), so those never count as new.
    model_start = {}
    for rec in state["seen"].values():
        m = rec.get("model") or "acty"
        model_start[m] = min(model_start.get(m, rec["first_seen"]), rec["first_seen"])
    new = []
    for t in merged:
        rec = state["seen"].get(t["key"])
        if rec is None:
            rec = {"first_seen": now, "first_usd": t.get("usd")}
            # The first run of a newly added model finds trucks that were listed
            # all along: record them, but don't call them new.
            if first_run or t["model"] not in known_models:
                rec["baseline"] = True
            else:
                new.append(t["key"])
            state["seen"][t["key"]] = rec
        if "model" not in rec and t["model"] != "acty":
            # Recorded before records kept a model: only a model's first runs did
            # that (the Sambar on 2026-11-26), so it was listed all along.
            rec["baseline"] = True
        rec["model"] = t["model"]
        rec["last_seen"] = now
        t["first_seen"] = rec["first_seen"]
        t["price_drop"] = bool(rec.get("first_usd") and t.get("usd") and t["usd"] < rec["first_usd"])
        # New since the last 5 AM check, so an extra run later in the day doesn't
        # clear the badge from trucks Josh hasn't seen yet.
        t["is_new"] = (not rec.get("baseline") and rec["first_seen"] != model_start.get(t["model"])
                       and dt.datetime.fromisoformat(rec["first_seen"]) > since)

    order = {"legal_now": 0, "in_us": 0, "on_arrival": 1, "watch": 2}
    merged.sort(key=lambda t: (order[t["status"]], -t["reg_year"],
                               t.get("km") or 10 ** 7, not t.get("ac")))
    data = {
        "generated_at": now, "today": today.isoformat(),
        "cutoff_build": f"{today.year - 25}-{today.month:02d}",
        "jpy_per_usd": JPY_PER_USD, "source_counts": counts, "model_counts": model_counts,
        "models": {k: {"name": M["name"], "chassis": M["chassis"], "years": [M["min_year"], M["max_year"]],
                       "manual_only": M["manual_only"]} for k, M in MODELS.items()},
        "errors": ERRORS,
        "new_keys": new, "new_since": since.isoformat(timespec="minutes"), "first_run": first_run, "trucks": merged,
    }
    json.dump(data, open(os.path.join(OUT_DIR, "listings.json"), "w"), indent=1, default=str)
    json.dump(state, open(state_p, "w"), indent=1)
    print(json.dumps({"trucks": len(merged), "new": len(new), "errors": ERRORS, "counts": counts}))


if __name__ == "__main__":
    main()
