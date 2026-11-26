"""Write the body of a GitHub issue listing trucks first seen in this run.

The workflow opens the issue and assigns it to Josh, so GitHub emails him.
Prints nothing (and the workflow skips the issue) when nothing is new.
"""
import argparse, json, os

MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
STATUS = {"legal_now": "Legal now", "in_us": "In the US", "on_arrival": "Legal on arrival", "watch": "Coming up"}
NAMES = {"acty": "Honda Acty", "sambar": "Subaru Sambar"}
SITE = "https://jgriffin187.github.io/kei-truck-finder/"


def ym(s):
    y, m = s.split("-")[:2]
    return f"{MON[int(m) - 1]} {y}"


def money(n):
    return "price on request" if n is None else f"${round(n):,}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    ap.add_argument("--out", default="alert.md")
    a = ap.parse_args()
    d = json.load(open(os.path.join(a.dir, "listings.json")))
    if d.get("first_run") or not d.get("new_keys"):
        return
    keys = set(d["new_keys"])
    order = {"legal_now": 0, "in_us": 0, "on_arrival": 1, "watch": 2}
    new = sorted((t for t in d["trucks"] if t["key"] in keys), key=lambda t: order.get(t["status"], 3))
    if not new:
        return
    lines = [f"@jgriffin187 {len(new)} new truck{'s' if len(new) > 1 else ''} found today.", ""]
    for t in new:
        status = STATUS.get(t["status"], t["status"])
        if t["status"] in ("on_arrival", "watch") and t.get("legal_from"):
            status += f" (legal from {ym(t['legal_from'])})"
        build = ym(t["build_latest"]) if t.get("build_month_known") else f"{t.get('reg_year')} (month not stated)"
        km = f"{t['km']:,} km ({round(t['km'] * 0.621371):,} mi)" if t.get("km") is not None else "km unknown"
        price = money(t.get("usd"))
        if t.get("delivered_usd"):
            price += f", {money(t['delivered_usd'])} delivered to Long Beach/LA"
        ac = {True: "A/C", False: "no A/C listed"}.get(t.get("ac"), "A/C unknown")
        title = " ".join((t.get("title") or NAMES.get(t.get("model"), "") + " truck").split())
        lines.append(f"- **[{title}]({t['url']})**: {status}. Built {build}, {km}, {price}, {ac}. "
                     f"{t.get('seller') or t.get('source')}, {t.get('location') or t.get('country') or ''}.")
    lines += ["", f"Dashboard: {SITE}", "",
              "Close this issue once you've looked. A new one opens whenever the daily run finds more."]
    open(a.out, "w").write("\n".join(lines) + "\n")
    print(f"{len(new)} new")


if __name__ == "__main__":
    main()
