"""Write the body of a GitHub issue listing trucks first seen in this run.

The workflow opens the issue and assigns it to Josh, so GitHub emails him.
Prints nothing (and the workflow skips the issue) when nothing is new, unless
--summary is given: then a short "no new trucks" summary is written instead, so
the daily run emails Josh every day.
"""
import argparse, json, os

MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
STATUS = {"legal_now": "Legal now", "on_arrival": "Legal on arrival", "watch": "Coming up"}
NAMES = {"acty": "Honda Acty", "sambar": "Subaru Sambar"}
SITE = "https://jgriffin187.github.io/kei-truck-finder/"


def ym(s):
    y, m = s.split("-")[:2]
    return f"{MON[int(m) - 1]} {y}"


def money(n):
    return "price on request" if n is None else f"${round(n):,}"


def write_summary(d, out):
    trucks = d.get("trucks") or []
    by_model = {}
    legal = 0
    for t in trucks:
        by_model[t.get("model")] = by_model.get(t.get("model"), 0) + 1
        legal += t.get("status") == "legal_now"
    models = ", ".join(f"{n} {NAMES.get(m, m)}" for m, n in sorted(by_model.items(), key=lambda x: -x[1]))
    lines = [f"@jgriffin187 No new trucks today. The page lists {len(trucks)} trucks"
             + (f" ({models})" if models else "") + f", {legal} legal to import now.", ""]
    errors = d.get("errors") or {}
    if errors:
        lines += ["Couldn't fully read: " + ", ".join(sorted(errors)) + ". Their trucks from the last run are kept.", ""]
    lines += [f"Dashboard: {SITE}", "",
              "Close this issue once you've looked. A new one opens after each daily run."]
    open(out, "w").write("\n".join(lines) + "\n")
    print("0 new (summary)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    ap.add_argument("--out", default="alert.md")
    ap.add_argument("--summary", action="store_true",
                    help="write a no-new-trucks summary instead of nothing")
    a = ap.parse_args()
    d = json.load(open(os.path.join(a.dir, "listings.json")))
    keys = set() if d.get("first_run") else set(d.get("new_keys") or [])
    order = {"legal_now": 0, "on_arrival": 1, "watch": 2}
    new = sorted((t for t in d["trucks"] if t["key"] in keys), key=lambda t: order.get(t["status"], 3))
    if not new:
        if a.summary:
            write_summary(d, a.out)
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
