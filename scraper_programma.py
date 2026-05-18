"""
DBL Programma Scraper — Playwright (headless Chrome)

Laadt de standaard spielplaene-pagina (geen week-parameter).
De site toont zelf de eerstvolgende speelweek.
Schrijft alleen de "programma" sleutel in schedule.json,
laat "uitslagen" onaangeroerd.

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


def process(raw_games):
    games = []
    seen  = set()
    for r in raw_games:
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


def main():
    print(f"DBL programma scraper — {dt.datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Laden: {BASE_URL}")

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

        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("div.game", timeout=12000)
            raw = page.evaluate(EXTRACT_JS)
        except Exception as e:
            print(f"  ⚠️  Fout: {e}")
            raw = []

        browser.close()

    all_games = process(raw)
    programma = sorted(
        [g for g in all_games if not g["gespeeld"]],
        key=lambda g: (g["datum"] or "", g["tijdstip"] or "")
    )

    # Debug
    print(f"\nProgramma ({len(programma)}):")
    for p in programma:
        print(f"  {p['datum']} {p['tijdstip']}  {p['uit']} @ {p['thuis']}  [{p['divisie']}]")

    # Lees bestaande JSON, update alleen "programma"
    data = {}
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                pass

    data["bijgewerkt_programma"] = dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["programma"]            = programma

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ {JSON_FILE} bijgewerkt — {len(programma)} wedstrijden")


if __name__ == "__main__":
    main()
