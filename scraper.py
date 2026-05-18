"""
DBL Schedule Scraper — Playwright (headless Chrome)

Strategie:
  - Programma : standaard URL (geen week) → data-state="planned"
  - Uitslagen : week-URL's teruglopend vanaf vorige week totdat
                er gespeelde wedstrijden gevonden zijn (max 4 weken terug)

DOM-structuur (uit broncode baseball.de):
  div.game[data-state]
    p.game-badge                          → divisie
    p.game-header-date                    → "Freitag, 15. Mai 2026"
    p.game-header-time                    → "19:00 Uhr, Regensburg"
    div.game-scores-team (eerste)
      dbl-tooltip[tooltip]                → thuis teamnaam
      span[data-team-score="home"]        → score thuis
    div.game-scores-team (tweede)
      span[data-team-score="away"]        → score uit
      dbl-tooltip[tooltip]                → uit teamnaam

Installatie (eenmalig):
    pip install playwright
    playwright install chromium
"""

import json
import re
import datetime as dt
from datetime import timezone, timedelta
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.baseball.de/saison/spielplaene"

# Speelweken van het seizoen — voor het terugzoeken van uitslagen
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


# JavaScript dat in de browser draait en alle div.game elementen uitleest
EXTRACT_JS = """
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

        // Tijd + Locatie: "19:00 Uhr, Regensburg"
        const timeEl = card.querySelector('p.game-header-time');
        let time = null, location = null;
        if (timeEl) {
            const m = timeEl.textContent.trim().match(/(\\d{2}:\\d{2})\\s*Uhr,?\\s*(.*)/);
            if (m) { time = m[1]; location = m[2].trim() || null; }
        }

        // Scores en teamnamen
        // DOM-volgorde per game-scores-team:
        //   Eerste div.game-scores-team:  dbl-tooltip = thuis,  data-team-score="home"
        //   Tweede div.game-scores-team:  data-team-score="away", dbl-tooltip = uit
        const homeScoreEl = card.querySelector('span[data-team-score="home"]');
        const awayScoreEl = card.querySelector('span[data-team-score="away"]');
        const homeScoreRaw = homeScoreEl ? homeScoreEl.textContent.trim() : null;
        const awayScoreRaw = awayScoreEl ? awayScoreEl.textContent.trim() : null;

        // Teamnamen via dbl-tooltip[tooltip] attributen
        const tooltips = Array.from(card.querySelectorAll('dbl-tooltip[tooltip]'))
            .map(el => el.getAttribute('tooltip').trim());
        const homeTeam = tooltips[0] || null;
        const awayTeam = tooltips[1] || null;

        results.push({
            state, timestamp, division, dateStr,
            time, location, homeTeam, awayTeam,
            homeScoreRaw, awayScoreRaw,
        });
    });
    return results;
}
"""


def scrape_url(page, url, label):
    """Laadt één URL en geeft ruwe game-data terug."""
    print(f"  [{label}] {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("div.game", timeout=12000)
    except Exception as e:
        print(f"  ⚠️  Fout: {e}")
        return []
    return page.evaluate(EXTRACT_JS)


def process(raw_games):
    """Verwerkt ruwe browser-data naar schone game-dicts."""
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

        # Fallback op Unix timestamp als datum niet parseert
        if not game_date and timestamp:
            d = dt.datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc) + timedelta(hours=2)
            game_date = d.date()
            if not time_str:
                time_str = d.strftime("%H:%M")

        score_home = parse_score(r.get("homeScoreRaw"))
        score_away = parse_score(r.get("awayScoreRaw"))

        # played én live tellen als gespeeld voor de uitslagen
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


def past_weeks():
    """
    Geeft de WEEKS-entries terug die al voorbij zijn, meest recent eerst.
    'Voorbij' = de zondag van die week is al geweest.
    """
    today = (dt.datetime.now(timezone.utc) + timedelta(hours=2)).date()
    result = []
    for w in WEEKS:
        friday = dt.date.fromisocalendar(w["year"], w["week"], 5)
        sunday = friday + timedelta(days=2)
        if sunday < today:
            result.append(w)
    result.reverse()  # meest recent eerst
    return result


def main():
    now_str = dt.datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"DBL scraper gestart — {now_str}\n")

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

        # --- Programma: standaard URL → planned wedstrijden ---
        raw = scrape_url(page, BASE_URL, "programma")
        alle = process(raw)
        programma = sorted(
            [g for g in alle if not g["gespeeld"]],
            key=lambda g: (g["datum"] or "", g["tijdstip"] or "")
        )
        print(f"  → {len(programma)} geplande wedstrijden")

        # --- Uitslagen: zoek teruglopend door afgelopen weken ---
        # tot we een week vinden met gespeelde wedstrijden (max 4 weken terug)
        uitslagen = []
        uitslagen_week = None

        for w in past_weeks()[:4]:
            url = f"{BASE_URL}?year={w['year']}&week={w['week']}"
            raw = scrape_url(page, url, f"uitslagen week {w['week']}")
            games = process(raw)
            played = [g for g in games if g["gespeeld"]]
            print(f"  → {len(played)} gespeelde wedstrijden gevonden")

            if played:
                uitslagen = sorted(played, key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))
                uitslagen_week = w
                break

        browser.close()

    # Debug output
    print(f"\nUitslagen week {uitslagen_week['week'] if uitslagen_week else '?'} "
          f"({uitslagen_week['label'] if uitslagen_week else '-'}) — {len(uitslagen)} wedstrijden:")
    if uitslagen:
        for u in uitslagen:
            print(f"  {u['datum']} {u['tijdstip']}  "
                  f"{u['uit']} {u['score_uit']}–{u['score_thuis']} {u['thuis']}  [{u['divisie']}]")
    else:
        print("  (geen gespeelde wedstrijden gevonden)")

    print(f"\nProgramma — {len(programma)} wedstrijden:")
    for p in programma:
        print(f"  {p['datum']} {p['tijdstip']}  {p['uit']} @ {p['thuis']}  [{p['divisie']}]")

    output = {
        "bijgewerkt":     dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron":           BASE_URL,
        "uitslagen_week": uitslagen_week,
        "uitslagen":      uitslagen,
        "programma":      programma,
    }

    with open("schedule.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ schedule.json opgeslagen — "
          f"{len(uitslagen)} uitslagen, {len(programma)} programma")


if __name__ == "__main__":
    main()
