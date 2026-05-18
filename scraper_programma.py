"""
DBL Programma Scraper
Loopt door de week-URLs vooruit totdat planned wedstrijden gevonden worden.
Elke week-URL toont alleen de wedstrijden van die specifieke week.
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
    """Filtert alleen planned wedstrijden uit de ruwe data."""
    games, seen = [], set()
    for r in raw:
        h, a = r.get("homeTeam"), r.get("awayTeam")
        if not h or not a: continue

        # Alleen planned — final en live horen niet in het programma
        if r.get("state") != "planned": continue

        date_str  = r.get("dateStr")
        time_str  = r.get("time")
        location  = r.get("location")
        division  = r.get("division")
        game_date = parse_date_str(date_str)

        # Fallback op Unix timestamp als datum niet geparsed kon worden
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
    today    = (dt.datetime.now(timezone.utc) + timedelta(hours=2)).date()
    iso_week = today.isocalendar()[1]
    iso_year = today.isocalendar()[0]
    print(f"DBL Programma Scraper — {dt.datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Vandaag: {today} (ISO week {iso_week})\n")

    # Sla afgelopen weken over — begin bij de huidige of eerstvolgende week
    # Een week is "voorbij" als de zondag (dag 7) vóór vandaag ligt
    weken = [
        w for w in WEEKS
        if dt.date.fromisocalendar(w["year"], w["week"], 7) >= today
    ]
    if not weken:
        print("Geen toekomstige weken meer in de lijst.")
        return

    programma      = []
    programma_week = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
            locale="de-DE", viewport={"width": 1280, "height": 800},
        ).new_page()

        # Loop alleen door weken vanaf nu — stop bij de eerste met planned wedstrijden
        for w in weken:
            url = f"{BASE_URL}?year={w['year']}&week={w['week']}"
            print(f"Laden: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_selector("div.game", timeout=12000)
            except Exception as e:
                print(f"  ⚠️  {e}")
                continue

            raw    = page.evaluate(EXTRACT_JS)
            games  = process(raw)
            total  = len(raw)
            print(f"  → {total} wedstrijden op pagina, {len(games)} planned")

            if games:
                programma      = sorted(games, key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))
                programma_week = w
                print(f"  ✓ Programma gevonden in week {w['week']}/{w['year']}")
                break
            else:
                print(f"  — Geen planned wedstrijden, volgende week...")

        browser.close()

    print(f"\nProgramma ({len(programma)}):")
    for g in programma:
        print(f"  {g['datum']} {g['tijdstip']}  {g['uit']} @ {g['thuis']}  [{g['divisie']}]")

    # Lees bestaande JSON en update alleen "programma"
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

    print(f"\n✅ {len(programma)} programma-wedstrijden opgeslagen in {JSON_FILE}")


if __name__ == "__main__":
    main()
