"""
DBL Schedule Scraper — Playwright (headless Chrome)

Laadt twee gerichte week-URLs:
  - Vorige speelweek  → uitslagen (data-state="played")
  - Volgende speelweek → programma (data-state="planned")

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

# Alle speelweken van het seizoen op volgorde
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


def week_friday(week_entry):
    """Geeft de vrijdag van een week-entry als date object."""
    return dt.date.fromisocalendar(week_entry["year"], week_entry["week"], 5)


def determine_weeks():
    """
    Bepaalt welke week we voor uitslagen en programma moeten laden.
    Vergelijkt de vrijdag van elke week met vandaag.

    - Uitslagen  = de meest recente week waarvan de vrijdag al geweest is
    - Programma  = de eerstvolgende week waarvan de vrijdag nog in de toekomst ligt
    """
    now   = dt.datetime.now(timezone.utc) + timedelta(hours=2)
    today = now.date()

    afgelopen  = [w for w in WEEKS if week_friday(w) <= today]
    toekomstig = [w for w in WEEKS if week_friday(w) > today]

    uitslagen_week = afgelopen[-1]  if afgelopen  else None
    programma_week = toekomstig[0] if toekomstig else None

    return uitslagen_week, programma_week


def parse_date_str(date_str):
    """'Freitag, 29. Mai 2026' → date object"""
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


def extract_games(page):
    """Extraheert alle div.game elementen van de geladen pagina via JS in de browser."""
    return page.evaluate("""
    () => {
        const results = [];

        document.querySelectorAll('div.game').forEach(card => {

            const state    = card.getAttribute('data-state') || '';
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

            // Tijd + Locatie: "19:00 Uhr, Bonn"
            const timeEl = card.querySelector('p.game-header-time');
            let time = null;
            let location = null;
            if (timeEl) {
                const raw = timeEl.textContent.trim();
                const m = raw.match(/(\\d{2}:\\d{2})\\s*Uhr,?\\s*(.*)/);
                if (m) {
                    time     = m[1];
                    location = m[2].trim() || null;
                }
            }

            // Teamnamen: eerste dbl-tooltip = thuis, tweede = uit
            const tooltips = Array.from(
                card.querySelectorAll('dbl-tooltip[tooltip]')
            ).map(el => el.getAttribute('tooltip').trim());

            const homeTeam = tooltips[0] || null;
            const awayTeam = tooltips[1] || null;

            // Scores
            const homeScoreEl = card.querySelector('span[data-team-score="home"]');
            const awayScoreEl = card.querySelector('span[data-team-score="away"]');
            const homeScoreRaw = homeScoreEl ? homeScoreEl.textContent.trim() : null;
            const awayScoreRaw = awayScoreEl ? awayScoreEl.textContent.trim() : null;

            results.push({
                state, timestamp, division, dateStr,
                time, location, homeTeam, awayTeam,
                homeScoreRaw, awayScoreRaw,
            });
        });

        return results;
    }
    """)


def process_raw(raw_games):
    """Verwerkt ruwe browser-data naar schone game-dicts, dedupliceert."""
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

        # Fallback op Unix timestamp
        if not game_date and timestamp:
            d = dt.datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc) + timedelta(hours=2)
            game_date = d.date()
            if not time_str:
                time_str = d.strftime("%H:%M")

        score_home = parse_score(r.get("homeScoreRaw"))
        score_away = parse_score(r.get("awayScoreRaw"))

        gespeeld = (state == "played")
        is_live  = (state == "live")

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


def scrape_url(page, url, label):
    """Laadt één URL en geeft verwerkte games terug."""
    print(f"  [{label}] {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("div.game", timeout=10000)
    except Exception as e:
        print(f"  ⚠️  Fout bij laden: {e}")
        return []

    raw   = extract_games(page)
    games = process_raw(raw)

    played   = sum(1 for g in games if g["gespeeld"])
    upcoming = sum(1 for g in games if not g["gespeeld"] and not g["live"])
    live     = sum(1 for g in games if g["live"])
    print(f"  → {len(games)} wedstrijden — {played} gespeeld / {upcoming} gepland / {live} live")
    return games


def main():
    now_str = dt.datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"DBL scraper gestart — {now_str}\n")

    uitslagen_week, programma_week = determine_weeks()

    if uitslagen_week:
        print(f"Uitslagen week : {uitslagen_week['week']} ({uitslagen_week['label']})")
    if programma_week:
        print(f"Programma week : {programma_week['week']} ({programma_week['label']})")
    print()

    uitslagen = []
    programma = []

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

        # Laad uitslagen-week
        if uitslagen_week:
            url = f"{BASE_URL}?year={uitslagen_week['year']}&week={uitslagen_week['week']}"
            games = scrape_url(page, url, "uitslagen")
            uitslagen = [g for g in games if g["gespeeld"]]

        # Laad programma-week
        if programma_week:
            url = f"{BASE_URL}?year={programma_week['year']}&week={programma_week['week']}"
            games = scrape_url(page, url, "programma")
            programma = [g for g in games if not g["gespeeld"]]

        browser.close()

    uitslagen.sort(key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))
    programma.sort(key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))

    # Debug output
    print(f"\nUitslagen ({len(uitslagen)}):")
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
        "bron":       BASE_URL,
        "uitslagen_week": uitslagen_week,
        "programma_week": programma_week,
        "uitslagen":  uitslagen,
        "programma":  programma,
    }

    with open("schedule.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ schedule.json opgeslagen")
    print(f"   Uitslagen : {len(uitslagen)}")
    print(f"   Programma : {len(programma)}")


if __name__ == "__main__":
    main()
