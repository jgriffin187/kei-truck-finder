# Kei Truck Finder

A daily-updated list of Honda Acty HA7 4WD and Subaru Sambar TT2 4WD manual (2000-2001) kei trucks that pass the US 25-year import rule, from Japanese exporters (CAR FROM JAPAN, SBT Japan, TCV, Goo-net Exchange, BE FORWARD) plus US dealer leads.

- `collector.py` reads the listing sites, applies the rules per model in `MODELS` (chassis, 4WD, years, manual only for the Sambar; build month + 25 years) and keeps history in `state.json`.
- `build.py` writes `site/data.json` for the page.
- `us_leads.json` holds trucks already in the US, added by hand or by Claude. Set `"model": "sambar"` on Sambar leads (Acty is the default).
- `.github/workflows/refresh.yml` runs both every morning and publishes `site/` to GitHub Pages.

Run locally: `pip install requests beautifulsoup4 lxml pillow && python collector.py --dir . && python build.py --dir . --remote-images`, then serve `site/`.
