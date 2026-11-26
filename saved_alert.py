"""Write the body of a GitHub issue when a saved truck is reserved, sold or comes back.

Saved trucks live in Josh's secret sync gist (the page's "Sync" section). The
workflow passes its id in SAVED_GIST_ID (a repository secret). Run after
collector.py, which records each truck's availability in state.json.

Prints nothing (and the workflow opens no issue) when nothing changed.
Also writes "saved_watch" into listings.json so the page knows alerts are on.
"""
import argparse, json, os, sys

import requests

SITE = "https://jgriffin187.github.io/kei-truck-finder/"
NAMES = {"acty": "Honda Acty", "sambar": "Subaru Sambar"}
SAY = {
    "reserved": "is now **reserved** by another buyer",
    "sold": "is now **sold**",
    "gone": "is **no longer listed** (most likely sold)",
    "available": "is **available again**",
}


def money(n):
    return None if n is None else f"${round(n):,}"


def load_saved(gist_id):
    h = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GH_TOKEN"):
        h["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=h, timeout=30)
    if r.status_code == 401 and "Authorization" in h:  # a secret gist is readable without a token too
        del h["Authorization"]
        r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=h, timeout=30)
    r.raise_for_status()
    f = r.json()["files"].get("kei-prefs.json") or {}
    content = f.get("content")
    if f.get("truncated") and f.get("raw_url"):
        content = requests.get(f["raw_url"], timeout=30).text
    return (json.loads(content or "{}").get("trucks") or {}).get("stars") or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    ap.add_argument("--out", default="saved_alert.md")
    a = ap.parse_args()
    lp, sp = os.path.join(a.dir, "listings.json"), os.path.join(a.dir, "state.json")
    d, state = json.load(open(lp)), json.load(open(sp))
    gist_id = os.environ.get("SAVED_GIST_ID", "").strip()
    d["saved_watch"] = False
    stars = None
    if gist_id:
        try:
            stars = load_saved(gist_id)
            d["saved_watch"] = True
        except Exception as e:
            print("could not read the saved-trucks gist:", str(e)[:200], file=sys.stderr)
    json.dump(d, open(lp, "w"), indent=1, default=str)
    if stars is None:
        return

    by_key = {}
    for t in d["trucks"]:
        by_key[t["key"]] = t
        for x in t.get("also_on") or []:
            if x.get("key"):
                by_key.setdefault(x["key"], t)
    seen = state["seen"]
    told = state.setdefault("saved_alerts", {})
    changes = []
    for k, v in stars.items():
        v = v if isinstance(v, dict) else {}
        keys = [k] + [x for x in (v.get("ks") or []) if x != k]
        t = next((by_key[x] for x in keys if x in by_key), None)
        if t is not None:
            now = "reserved" if t.get("reserved") else "available"
        else:
            avails = [seen[x].get("avail") for x in keys if x in seen]
            now = "sold" if "sold" in avails else "gone" if avails and all(x == "gone" for x in avails) else None
        if now is None:  # its site couldn't be read today, or we never tracked it
            continue
        before = told.get(k, "available")
        told[k] = now
        if now == before or (now == "gone" and before == "sold"):
            continue
        if now == "available" and before not in ("reserved", "sold", "gone"):
            continue
        changes.append((k, v, t, now))
    for k in list(told):  # forget trucks Josh unsaved
        if k not in stars:
            del told[k]
    json.dump(state, open(sp, "w"), indent=1)
    if not changes:
        return

    lines = ["@jgriffin187 " + ("A saved truck changed." if len(changes) == 1 else f"{len(changes)} saved trucks changed."), ""]
    for k, v, t, now in changes:
        title = " ".join(((t or {}).get("title") or v.get("t") or NAMES.get((t or {}).get("model"), "Kei") + " truck").split())
        url = (t or {}).get("url") or v.get("u") or ""
        km = (t or {}).get("km", v.get("k"))
        price = money((t or {}).get("delivered_usd")) or money((t or {}).get("usd", v.get("p")))
        bits = [b for b in [f"{km:,} km ({round(km * 0.621371):,} mi)" if km is not None else None,
                            f"{'was ' if t is None else ''}{price}{' delivered' if (t or {}).get('delivered_usd') else ''}"
                            if price else None] if b]
        lines.append(f"- **[{title}]({url})** {SAY[now]}." + (f" {', '.join(bits)}." if bits else ""))
    lines += ["", f"Dashboard: {SITE}", "",
              "Close this issue once you've looked. A new one opens if another saved truck changes."]
    open(a.out, "w").write("\n".join(lines) + "\n")
    if len(changes) == 1:
        title = {"reserved": "A saved kei truck was reserved", "sold": "A saved kei truck sold",
                 "gone": "A saved kei truck is no longer listed",
                 "available": "A saved kei truck is available again"}[changes[0][3]]
    else:
        title = f"{len(changes)} saved kei trucks changed"
    open(a.out + ".title", "w").write(title)
    print(f"{len(changes)} saved trucks changed")


if __name__ == "__main__":
    main()
