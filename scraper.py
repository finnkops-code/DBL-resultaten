"""
DBL Schedule Scraper — Playwright (headless Chrome)

Laadt de spielplaene-pagina zonder week-parameter zodat de site
zelf de meest relevante week toont. Filtert dan op data-state:
  "played"  → uitslagen
  "planned" → programma
  "live"    → live (telt mee in uitslagen)

Installatie (eenmalig):
    pip install playwright
    playwright install chromium
"""

import json
import re
import datetime as dt
from datetime import timezone, timedelta
from playwright.sync_api import sync_playwright

# Geen week-parameter — de site toont zelf de juiste week
PAGE_URL = "https://www.baseball.de/saison/spielplaene"

MAANDEN_DE = {
    "Januar": 1, "Februar": 2, "März": 3, "April": 4,
    "Mai": 5, "Juni": 6, "Juli": 7, "August": 8,
    "September": 9, "Oktober": 10, "November": 11, "Dezember": 12,
}


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


def extract_and_process(page):
    """Extraheert alle div.game elementen en verwerkt ze direct."""
    raw = page.evaluate("""
    () => {
        const results = [];
        document.querySelectorAll('div.game').forEach(card => {
            const state     = card.getAttribute('data-state') || '';
            const dataStart = card.getAttribute('data-start');
            const timestamp = dataStart ? parseInt(dataStart) * 1000 : null;

            // Divisie
            const badgeEl = card.querySelector('p.game-badge');
            let division = null;
            if (badgeEl) {
                const t = badgeEl.textContent.trim();
                if (t.includes('Nord'))          division = 'Nord';
                else if (t.includes('Süd'))      division = 'Süd';
                else if (t.includes('Zwischen')) division = 'Zwischenphase';
                else if (t.includes('Playoff'))  division = 'Playoff';
            }

            // Datum
            const dateEl = card.querySelector('p.game-header-date');
            const dateStr = dateEl ? dateEl.textContent.trim() : null;

            // Tijd + Locatie
            const timeEl = card.querySelector('p.game-header-time');
            let time = null, location = null;
            if (timeEl) {
                const m = timeEl.textContent.trim().match(/(\\d{2}:\\d{2})\\s*Uhr,?\\s*(.*)/);
                if (m) { time = m[1]; location = m[2].trim() || null; }
            }

            // Teamnamen: eerste dbl-tooltip = thuis, tweede = uit
            const tooltips = Array.from(card.querySelectorAll('dbl-tooltip[tooltip]'))
                .map(el => el.getAttribute('tooltip').trim());
            const homeTeam = tooltips[0] || null;
            const awayTeam = tooltips[1] || null;

            // Scores
            const homeScoreRaw = card.querySelector('span[data-team-score="home"]')?.textContent.trim() || null;
            const awayScoreRaw = card.querySelector('span[data-team-score="away"]')?.textContent.trim() || null;

            results.push({ state, timestamp, division, dateStr, time, location,
                           homeTeam, awayTeam, homeScoreRaw, awayScoreRaw });
        });
        return results;
    }
    """)

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

        gespeeld = state in ("played", "live")
        is_live  = state == "live"

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
    now_str = dt.datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"DBL scraper gestart — {now_str}")
    print(f"Ophalen: {PAGE_URL}\n")

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
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("div.game", timeout=15000)
        all_games = extract_and_process(page)
        browser.close()

    # Splits op data-state — geen datumberekeningen nodig
    uitslagen = sorted(
        [g for g in all_games if g["gespeeld"]],
        key=lambda g: (g["datum"] or "", g["tijdstip"] or "")
    )
    programma = sorted(
        [g for g in all_games if not g["gespeeld"]],
        key=lambda g: (g["datum"] or "", g["tijdstip"] or "")
    )

    # Debug
    print(f"Gevonden: {len(all_games)} wedstrijden — "
          f"{len(uitslagen)} gespeeld / {len(programma)} gepland\n")

    print(f"Uitslagen ({len(uitslagen)}):")
    if uitslagen:
        for u in uitslagen:
            print(f"  {u['datum']} {u['tijdstip']}  "
                  f"{u['uit']} {u['score_uit']}–{u['score_thuis']} {u['thuis']}  [{u['divisie']}]")
    else:
        print("  (geen)")

    print(f"\nProgramma ({len(programma)}):")
    for p in programma:
        print(f"  {p['datum']} {p['tijdstip']}  {p['uit']} @ {p['thuis']}  [{p['divisie']}]")

    output = {
        "bijgewerkt": dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron":       PAGE_URL,
        "uitslagen":  uitslagen,
        "programma":  programma,
    }

    with open("schedule.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ schedule.json opgeslagen — {len(uitslagen)} uitslagen, {len(programma)} programma")


if __name__ == "__main__":
    main()
