"""Write the body of a GitHub issue listing trucks first seen in this run.

The workflow runs every 2 hours, so only trucks that are legal to import now or
within 2 months (legal on arrival) are alerted; "coming up" trucks still show in
the page's New section. It also tracks how many runs in a row each source
failed, and writes a one-time warning (--warn-out) once a source has failed
WARN_AFTER runs in a row, so a blocked site doesn't go quiet unnoticed.

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
ALERT_STATUSES = ("legal_now", "on_arrival")
WARN_AFTER = 6  # about 12 hours of failed 2-hourly checks


def track_failures(d, state_path, out):
    """Count consecutive failed runs per source in state.json; warn once per outage."""
    try:
        st = json.load(open(state_path))
    except (OSError, ValueError):
        return
    fails = st.get("source_fail_streak") or {}
    warned = set(st.get("source_fail_warned") or [])
    errors = d.get("errors") or {}
    for src in set(fails) | set(errors):
        if src in errors:
            fails[src] = fails.get(src, 0) + 1
        else:
            fails.pop(src, None)
            warned.discard(src)
    newly = sorted(s for s, n in fails.items() if n >= WARN_AFTER and s not in warned)
    st["source_fail_streak"], st["source_fail_warned"] = fails, sorted(warned | set(newly))
    json.dump(st, open(state_path, "w"), indent=1)
    if newly and out:
        lines = ["@jgriffin187 The truck watcher couldn't read "
                 + ", ".join(f"{s} ({fails[s]} checks in a row)" for s in newly)
                 + ". New trucks there won't be alerted until it recovers; the page keeps its last known trucks.", ""]
        for s in newly:
            lines.append(f"- {s}: {str(errors.get(s))[:300]}")
        lines += ["", f"Dashboard: {SITE}", "",
                  "This warning is sent once per outage. It clears by itself when the site answers again."]
        open(out, "w").write("\n".join(lines) + "\n")
        open(out + ".title", "w").write("Truck watcher can't read " + ", ".join(newly))


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
    lines = [f"@jgriffin187 Daily summary: no new trucks in this check. The page lists {len(trucks)} trucks"
             + (f" ({models})" if models else "") + f", {legal} legal to import now.", ""]
    errors = d.get("errors") or {}
    if errors:
        lines += ["Couldn't fully read: " + ", ".join(sorted(errors)) + ". Their trucks from the last run are kept.", ""]
    lines += [f"Dashboard: {SITE}", "",
              "Close this issue once you've looked. The watcher still checks every 2 hours and opens an issue as soon as a new truck appears."]
    open(out, "w").write("\n".join(lines) + "\n")
    print("0 new (summary)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    ap.add_argument("--out", default="alert.md")
    ap.add_argument("--summary", action="store_true",
                    help="write a no-new-trucks summary instead of nothing")
    ap.add_argument("--warn-out", default="", help="where to write a source-failure warning")
    a = ap.parse_args()
    d = json.load(open(os.path.join(a.dir, "listings.json")))
    track_failures(d, os.path.join(a.dir, "state.json"), a.warn_out)
    keys = set() if d.get("first_run") else set(d.get("new_keys") or [])
    keys = {t["key"] for t in d["trucks"] if t["key"] in keys and t.get("status") in ALERT_STATUSES}
    order = {"legal_now": 0, "on_arrival": 1, "watch": 2}
    new = sorted((t for t in d["trucks"] if t["key"] in keys), key=lambda t: order.get(t["status"], 3))
    if not new:
        if a.summary:
            write_summary(d, a.out)
        return
    lines = [f"@jgriffin187 {len(new)} new truck{'s' if len(new) > 1 else ''} just listed.", ""]
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
              "Close this issue once you've looked. The watcher checks every 2 hours and opens a new one whenever it finds more."]
    open(a.out, "w").write("\n".join(lines) + "\n")
    print(f"{len(new)} new")


if __name__ == "__main__":
    main()
