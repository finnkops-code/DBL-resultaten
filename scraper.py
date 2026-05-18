"""
DBL Schedule Scraper — Playwright (headless Chrome)
Laadt de pagina EENMALIG en leest alle div.game elementen in één keer uit.

Installatie (eenmalig):
    pip install playwright
    playwright install chromium
"""

import json
import re
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

# Één URL — de site laadt alle wedstrijden in de DOM, week-filtering is client-side
PAGE_URL = "https://www.baseball.de/saison/spielplaene"

MAANDEN_DE = {
    "Januar": 1, "Februar": 2, "März": 3, "April": 4,
    "Mai": 5, "Juni": 6, "Juli": 7, "August": 8,
    "September": 9, "Oktober": 10, "November": 11, "Dezember": 12,
}


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
        return datetime(year, month, day).date()
    except ValueError:
        return None


def parse_score(text):
    if not text or str(text).strip() in ("--", "-", "", "?"):
        return None
    try:
        return int(str(text).strip())
    except ValueError:
        return None


def speelweek_bounds():
    """Vrijdag en zondag van de meest recente DBL-speelweek."""
    now     = datetime.now(timezone.utc) + timedelta(hours=2)
    today   = now.date()
    weekday = today.weekday()
    days_since_friday = (weekday - 4) % 7
    friday  = today - timedelta(days=days_since_friday)
    sunday  = friday + timedelta(days=2)
    return friday, sunday


def scrape_all(page):
    """
    Laadt de pagina één keer en extraheert ALLE div.game elementen.
    De week-URL-parameter maakt niet uit — alle wedstrijden zitten in de DOM.
    """
    print(f"  Laden: {PAGE_URL}")
    page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=30000)

    # Wacht alleen tot de eerste wedstrijdkaart zichtbaar is
    page.wait_for_selector("div.game", timeout=15000)

    print("  Pagina geladen — wedstrijden uitlezen...")

    games = page.evaluate("""
    () => {
        const results = [];

        document.querySelectorAll('div.game').forEach(card => {

            // Status: "planned" | "live" | "final"
            const state = card.getAttribute('data-state') || '';

            // Unix timestamp → gebruiken voor datum/tijd (betrouwbaarder dan tekst)
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

            // Datum (tekst)
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

            // Teamnamen via dbl-tooltip: eerste = thuis, tweede = uit
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
                state,
                timestamp,
                division,
                dateStr,
                time,
                location,
                homeTeam,
                awayTeam,
                homeScoreRaw,
                awayScoreRaw,
            });
        });

        return results;
    }
    """)

    print(f"  {len(games)} wedstrijden gevonden in DOM")
    return games


def main():
    print(f"DBL scraper gestart — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    friday, sunday = speelweek_bounds()
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).date()
    print(f"Meest recente speelweek: {friday} (vr) t/m {sunday} (zo)\n")

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
        raw_games = scrape_all(page)
        browser.close()

    # Verwerk ruwe data
    unique_games = []
    seen = set()

    for r in raw_games:
        home_team = r.get("homeTeam")
        away_team = r.get("awayTeam")
        if not home_team or not away_team:
            continue

        state      = r.get("state", "")
        date_str   = r.get("dateStr")
        time_str   = r.get("time")
        location   = r.get("location")
        division   = r.get("division")
        timestamp  = r.get("timestamp")
        game_date  = parse_date_str(date_str)

        # Gebruik timestamp als fallback voor datum
        if not game_date and timestamp:
            dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc) + timedelta(hours=2)
            game_date = dt.date()
            if not time_str:
                time_str = dt.strftime("%H:%M")

        score_home = parse_score(r.get("homeScoreRaw"))
        score_away = parse_score(r.get("awayScoreRaw"))

        gespeeld = (state == "final")
        is_live  = (state == "live")

        key = (home_team, away_team, str(game_date), time_str)
        if key in seen:
            continue
        seen.add(key)

        unique_games.append({
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

    unique_games.sort(key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))

    played   = sum(1 for g in unique_games if g["gespeeld"])
    upcoming = sum(1 for g in unique_games if not g["gespeeld"] and not g["live"])
    live     = sum(1 for g in unique_games if g["live"])
    print(f"Totaal: {len(unique_games)} wedstrijden — {played} gespeeld, {upcoming} gepland, {live} live")

    # Uitslagen: gespeeld in meest recente speelweek
    uitslagen = [
        g for g in unique_games
        if g["gespeeld"] and g["datum"]
        and friday <= datetime.strptime(g["datum"], "%Y-%m-%d").date() <= sunday
    ]

    # Programma: toekomstige wedstrijden, max 15
    programma = [
        g for g in unique_games
        if not g["gespeeld"] and not g["live"] and g["datum"]
        and datetime.strptime(g["datum"], "%Y-%m-%d").date() > today
    ][:15]

    # Debug
    print(f"\nUitslagen ({friday} – {sunday}):")
    if uitslagen:
        for u in uitslagen:
            print(f"  {u['datum']} {u['tijdstip']}  "
                  f"{u['uit']} {u['score_uit']}–{u['score_thuis']} {u['thuis']}  [{u['divisie']}]")
    else:
        print("  (geen — wedstrijden nog niet gespeeld of buiten speelweek)")

    print(f"\nProgramma (eerstvolgende {len(programma)}):")
    for p in programma:
        print(f"  {p['datum']} {p['tijdstip']}  {p['uit']} @ {p['thuis']}  [{p['divisie']}]")

    output = {
        "bijgewerkt":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron":             PAGE_URL,
        "speelweek": {
            "van": str(friday),
            "tot": str(sunday),
        },
        "uitslagen":        uitslagen,
        "programma":        programma,
        "alle_wedstrijden": unique_games,
    }

    with open("schedule.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ schedule.json opgeslagen ({len(unique_games)} wedstrijden)")


if __name__ == "__main__":
    main()
