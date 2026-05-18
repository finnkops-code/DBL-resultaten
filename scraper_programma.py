"""
DBL Programma Scraper
Zelfde techniek als scraper_uitslagen.py — loopt week-URLs af,
pakt de eerste week met planned wedstrijden.
Schrijft alleen "programma" in schedule.json (behoudt "uitslagen").

Installatie (eenmalig):
    pip install playwright
    playwright install chromium
"""

import json
import os
import re
import datetime as dt
from datetime import timezone, timedelta
from playwright.sync_api import sync_playwright

BASE_URL  = "https://www.baseball.de/saison/spielplaene"
JSON_FILE = "schedule.json"

WEEKS = [
    {"label": "10–12 apr", "week": 14, "year": 2026},
    {"label": "17–19 apr", "week": 16, "year": 2026},
    {"label": "24–26 apr", "week": 17, "year": 2026},
    {"label": "01–03 mei", "week": 18, "year": 2026},
    {"label": "08–10 mei", "week": 19, "year": 2026},
    {"label": "15–17 mei", "week": 20, "year": 2026},
    {"label": "29–31 mei", "week": 22, "year": 2026},
    {"label": "05–07 jun", "week": 23, "year": 2026},
    {"label": "12–14 jun", "week": 24, "year": 2026},
    {"label": "19–21 jun", "week": 25, "year": 2026},
]

MAANDEN_DE = {
    "Januar": 1, "Februar": 2, "März": 3, "April": 4,
    "Mai": 5, "Juni": 6, "Juli": 7, "August": 8,
    "September": 9, "Oktober": 10, "November": 11, "Dezember": 12,
}

EXTRACT_JS = """
() => {
    const results = [];
    document.querySelectorAll('div.game').forEach(card => {
        const state     = card.getAttribute('data-state') || '';
        const dataStart = card.getAttribute('data-start');
        const timestamp = dataStart ? parseInt(dataStart) * 1000 : null;
        const badgeEl   = card.querySelector('p.game-badge');
        let division = null;
        if (badgeEl) {
            const t = badgeEl.textContent.trim();
            if (t.includes('Nord'))          division = 'Nord';
            else if (t.includes('Süd'))      division = 'Süd';
            else if (t.includes('Zwischen')) division = 'Zwischenphase';
            else if (t.includes('Playoff'))  division = 'Playoff';
        }
        const dateEl  = card.querySelector('p.game-header-date');
        const dateStr = dateEl ? dateEl.textContent.trim() : null;
        const timeEl  = card.querySelector('p.game-header-time');
        let time = null, location = null;
        if (timeEl) {
            const m = timeEl.textContent.trim().match(/(\\d{2}:\\d{2})\\s*Uhr,?\\s*(.*)/);
            if (m) { time = m[1]; location = m[2].trim() || null; }
        }
        const tooltips = Array.from(card.querySelectorAll('dbl-tooltip[tooltip]'))
            .map(el => el.getAttribute('tooltip').trim());
        results.push({ state, timestamp, division, dateStr, time, location,
                       homeTeam: tooltips[0] || null, awayTeam: tooltips[1] || null });
    });
    return results;
}
"""

def parse_date_str(s):
    if not s: return None
    m = re.search(r"(\d+)\.\s+(\w+)\s+(\d{4})", str(s))
    if not m: return None
    month = MAANDEN_DE.get(m.group(2), 0)
    if not month: return None
    try: return dt.datetime(int(m.group(3)), month, int(m.group(1))).date()
    except: return None

def process(raw):
    games, seen = [], set()
    for r in raw:
        h, a = r.get("homeTeam"), r.get("awayTeam")
        if not h or not a: continue
        if r.get("state") != "planned": continue
        date_str, time_str, location, division = r.get("dateStr"), r.get("time"), r.get("location"), r.get("division")
        game_date = parse_date_str(date_str)
        if not game_date and r.get("timestamp"):
            d = dt.datetime.fromtimestamp(r["timestamp"] / 1000, tz=timezone.utc) + timedelta(hours=2)
            game_date = d.date()
            if not time_str: time_str = d.strftime("%H:%M")
        key = (h, a, str(game_date), time_str)
        if key in seen: continue
        seen.add(key)
        games.append({
            "datum":     str(game_date) if game_date else None,
            "datum_str": date_str,
            "tijdstip":  time_str,
            "thuis":     h,
            "uit":       a,
            "locatie":   location,
            "divisie":   division,
        })
    return games

def main():
    today = (dt.datetime.now(timezone.utc) + timedelta(hours=2)).date()
    print(f"DBL Programma Scraper — {dt.datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    # Zelfde aanpak als uitslagen scraper: loop weken af in omgekeerde volgorde
    # maar dan vooruit — pak de meest recente week met planned wedstrijden
    afgelopen = list(reversed([
        w for w in WEEKS
        if dt.date.fromisocalendar(w["year"], w["week"], 7) < today
    ]))

    programma      = []
    programma_week = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
            locale="de-DE", viewport={"width": 1280, "height": 800},
        ).new_page()

        # Laad de huidige week-URL — die toont de eerstvolgende wedstrijden
        # op baseball.de is dit altijd de pagina zonder weekparameter,
        # of de week die het dichtst bij vandaag ligt
        # Strategie: probeer eerst de week ná de laatste afgelopen week
        if afgelopen:
            laatste_idx = WEEKS.index(afgelopen[0])
            volgende_weken = WEEKS[laatste_idx + 1:]
        else:
            volgende_weken = WEEKS

        for w in volgende_weken:
            url = f"{BASE_URL}?year={w['year']}&week={w['week']}"
            print(f"Laden: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_selector("div.game", timeout=12000)
            except Exception as e:
                print(f"  ⚠️  {e}")
                continue

            games = process(page.evaluate(EXTRACT_JS))
            print(f"→ {len(games)} planned wedstrijden")

            if games:
                programma      = sorted(games, key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))
                programma_week = w
                break
            else:
                print(f"  — Geen planned, volgende week...")

        browser.close()

    print(f"\nProgramma ({len(programma)}):")
    for g in programma:
        print(f"  {g['datum']} {g['tijdstip']}  {g['uit']} @ {g['thuis']}  [{g['divisie']}]")

    data = {}
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, encoding="utf-8") as f:
            try: data = json.load(f)
            except: pass

    data["bijgewerkt_programma"] = dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["programma_week"]       = programma_week
    data["programma"]            = programma

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ {len(programma)} programma-wedstrijden opgeslagen in {JSON_FILE}")

if __name__ == "__main__":
    main()
