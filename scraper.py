"""
DBL Schedule Scraper — Playwright

Schedule  : standaard URL → werkte eerder
Uitslagen : week-URL      → werkte eerder
Beide samengevoegd in schedule.json

Installatie:
    pip install playwright
    playwright install chromium
"""

import json
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

        const badgeEl = card.querySelector('p.game-badge');
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

        const timeEl = card.querySelector('p.game-header-time');
        let time = null, location = null;
        if (timeEl) {
            const m = timeEl.textContent.trim().match(/(\\d{2}:\\d{2})\\s*Uhr,?\\s*(.*)/);
            if (m) { time = m[1]; location = m[2].trim() || null; }
        }

        const homeScoreRaw = card.querySelector('span[data-team-score="home"]')?.textContent.trim() || null;
        const awayScoreRaw = card.querySelector('span[data-team-score="away"]')?.textContent.trim() || null;

        const tooltips = Array.from(card.querySelectorAll('dbl-tooltip[tooltip]'))
            .map(el => el.getAttribute('tooltip').trim());
        const homeTeam = tooltips[0] || null;
        const awayTeam = tooltips[1] || null;

        results.push({ state, timestamp, division, dateStr, time, location,
                       homeTeam, awayTeam, homeScoreRaw, awayScoreRaw });
    });
    return results;
}
"""


def parse_date_str(date_str):
    if not date_str:
        return None
    m = re.search(r"(\d+)\.\s+(\w+)\s+(\d{4})", str(date_str))
    if not m:
        return None
    day   = int(m.group(1))
    month = MAANDEN_DE.get(m.group(2), 0)
    year  = int(m.group(3))
    if not month:
        return None
    try:
        return dt.datetime(year, month, day).date()
    except ValueError:
        return None


def parse_score(text):
    if not text or str(text).strip() in ("--", "-", "", "?"):
        return None
    try:
        return int(str(text).strip())
    except ValueError:
        return None


def process(raw):
    games = []
    seen  = set()
    for r in raw:
        home_team = r.get("homeTeam")
        away_team = r.get("awayTeam")
        if not home_team or not away_team:
            continue

        state     = r.get("state", "")
        date_str  = r.get("dateStr")
        time_str  = r.get("time")
        location  = r.get("location")
        division  = r.get("division")
        timestamp = r.get("timestamp")
        game_date = parse_date_str(date_str)

        if not game_date and timestamp:
            d = dt.datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc) + timedelta(hours=2)
            game_date = d.date()
            if not time_str:
                time_str = d.strftime("%H:%M")

        score_home = parse_score(r.get("homeScoreRaw"))
        score_away = parse_score(r.get("awayScoreRaw"))
        gespeeld   = state in ("played", "live")
        is_live    = state == "live"

        key = (home_team, away_team, str(game_date), time_str)
        if key in seen:
            continue
        seen.add(key)

        games.append({
            "datum":       str(game_date) if game_date else None,
            "datum_str":   date_str,
            "tijdstip":    time_str,
            "thuis":       home_team,
            "uit":         away_team,
            "score_thuis": score_home if gespeeld else None,
            "score_uit":   score_away if gespeeld else None,
            "locatie":     location,
            "divisie":     division,
            "gespeeld":    gespeeld,
            "live":        is_live,
        })
    return games


def load(page, url):
    """Laad een URL en geef verwerkte wedstrijden terug."""
    print(f"  {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("div.game", timeout=12000)
    games = process(page.evaluate(EXTRACT_JS))
    print(f"  → {len(games)} wedstrijden, states: { {g['gespeeld'] and 'played' or 'planned' for g in games} }")
    return games


def scrape_programma(page):
    """
    Scrape programma via de standaard URL.
    Dit werkte eerder — de standaard URL toont de komende week.
    Filter: niet gespeeld.
    """
    print("PROGRAMMA — standaard URL:")
    games = load(page, BASE_URL)
    return sorted(
        [g for g in games if not g["gespeeld"]],
        key=lambda g: (g["datum"] or "", g["tijdstip"] or "")
    )


def scrape_uitslagen(page):
    """
    Scrape uitslagen via expliciete week-URLs, teruglopend.
    Dit werkte eerder — week-URL toont gespeelde wedstrijden.
    Filter: gespeeld.
    """
    today = (dt.datetime.now(timezone.utc) + timedelta(hours=2)).date()
    afgelopen = list(reversed([
        w for w in WEEKS
        if dt.date.fromisocalendar(w["year"], w["week"], 7) < today
    ]))

    print("UITSLAGEN — week-URLs:")
    for w in afgelopen[:4]:
        url   = f"{BASE_URL}?year={w['year']}&week={w['week']}"
        games = load(page, url)
        played = [g for g in games if g["gespeeld"]]
        if played:
            return sorted(played, key=lambda g: (g["datum"] or "", g["tijdstip"] or "")), w

    return [], None


def main():
    print(f"DBL scraper — {dt.datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="de-DE",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        programma            = scrape_programma(page)
        uitslagen, uitslag_w = scrape_uitslagen(page)

        browser.close()

    print(f"\nProgramma  : {len(programma)} wedstrijden")
    for g in programma:
        print(f"  {g['datum']} {g['tijdstip']}  {g['uit']} @ {g['thuis']}  [{g['divisie']}]")

    print(f"\nUitslagen  : {len(uitslagen)} wedstrijden")
    for g in uitslagen:
        print(f"  {g['datum']} {g['tijdstip']}  {g['uit']} {g['score_uit']}–{g['score_thuis']} {g['thuis']}  [{g['divisie']}]")

    output = {
        "bijgewerkt":     dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron":           BASE_URL,
        "uitslagen_week": uitslag_w,
        "uitslagen":      uitslagen,
        "programma":      programma,
    }

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Opgeslagen — {len(uitslagen)} uitslagen, {len(programma)} programma")


if __name__ == "__main__":
    main()
