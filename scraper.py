"""
DBL Schedule Scraper — Playwright (headless Chrome)
baseball.de blokkeert gewone HTTP-requests met 403.

Installatie (eenmalig):
    pip install playwright
    playwright install chromium
"""

import json
import re
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.baseball.de/saison/spielplaene"

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
    """'--' of '' → None, '5' → 5"""
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


def scrape_week(page, week, year):
    """
    Scrapt één speelweek via Playwright.
    Gebruikt de exacte CSS-selectors uit de broncode van baseball.de:

      div.game                                 → container per wedstrijd
        [data-state]                           → "planned" | "live" | "final"
        [aria-label]                           → "Team A gegen Team B am Datum"
        p.game-badge                           → divisie ("Reguläre Saison Nord")
        p.game-header-date                     → "Freitag, 29. Mai 2026"
        p.game-header-time                     → "19:00 Uhr, Bonn"
        span[data-team-score="home"]           → score thuis (of "--")
        span[data-team-score="away"]           → score uit  (of "--")
        dbl-tooltip[tooltip="<teamnaam>"]      → teamnamen (eerste=thuis, tweede=uit)
    """
    url = f"{BASE_URL}?year={year}&week={week}"
    print(f"  Laden: {url}")

    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception as e:
        print(f"  ⚠️  Fout bij laden: {e}")
        return []

    # Wacht tot wedstrijdkaarten aanwezig zijn
    try:
        page.wait_for_selector("div.game", timeout=10000)
    except Exception:
        print(f"  ⚠️  Geen div.game elementen gevonden")
        return []

    games = page.evaluate("""
    () => {
        const results = [];

        document.querySelectorAll('div.game').forEach(card => {

            // --- Status ---
            const state = card.getAttribute('data-state') || '';
            // "planned" = gepland, "live" = bezig, "final" = gespeeld

            // --- Divisie ---
            const badgeEl = card.querySelector('p.game-badge');
            let division = null;
            if (badgeEl) {
                const t = badgeEl.textContent.trim();
                if (t.includes('Nord'))          division = 'Nord';
                else if (t.includes('Süd'))      division = 'Süd';
                else if (t.includes('Zwischen')) division = 'Zwischenphase';
                else if (t.includes('Playoff'))  division = 'Playoff';
            }

            // --- Datum ---
            const dateEl = card.querySelector('p.game-header-date');
            const dateStr = dateEl ? dateEl.textContent.trim() : null;

            // --- Tijd + Locatie ---
            // Formaat: "19:00 Uhr, Bonn"
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

            // --- Teamnamen ---
            // dbl-tooltip elementen met tooltip attribuut, eerste = thuis, tweede = uit
            const tooltips = Array.from(
                card.querySelectorAll('dbl-tooltip[tooltip]')
            ).map(el => el.getAttribute('tooltip').trim());

            const homeTeam = tooltips[0] || null;
            const awayTeam = tooltips[1] || null;

            // --- Scores ---
            const homeScoreEl = card.querySelector('span[data-team-score="home"]');
            const awayScoreEl = card.querySelector('span[data-team-score="away"]');
            const homeScoreRaw = homeScoreEl ? homeScoreEl.textContent.trim() : null;
            const awayScoreRaw = awayScoreEl ? awayScoreEl.textContent.trim() : null;

            results.push({
                state,
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

    parsed = []
    seen   = set()

    for r in games:
        home_team = r.get("homeTeam")
        away_team = r.get("awayTeam")
        if not home_team or not away_team:
            continue

        state      = r.get("state", "")         # "planned" | "live" | "final"
        date_str   = r.get("dateStr")
        time_str   = r.get("time")
        location   = r.get("location")
        division   = r.get("division")
        game_date  = parse_date_str(date_str)

        score_home = parse_score(r.get("homeScoreRaw"))
        score_away = parse_score(r.get("awayScoreRaw"))

        gespeeld   = (state == "final")
        is_live    = (state == "live")

        # Bij live: scores zijn tussenstand, niet weergeven als eindstand
        if is_live:
            gespeeld = False

        key = (home_team, away_team, str(game_date), time_str)
        if key in seen:
            continue
        seen.add(key)

        parsed.append({
            "week":        week,
            "year":        year,
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

    return parsed


def main():
    print(f"DBL scraper gestart — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    friday, sunday = speelweek_bounds()
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).date()
    print(f"Meest recente speelweek: {friday} (vr) t/m {sunday} (zo)\n")

    all_games = []

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

        for w in WEEKS:
            print(f"Week {w['week']}/{w['year']} ({w['label']})...")
            games = scrape_week(page, w["week"], w["year"])
            all_games.extend(games)
            played   = sum(1 for g in games if g["gespeeld"])
            upcoming = sum(1 for g in games if not g["gespeeld"] and not g["live"])
            live     = sum(1 for g in games if g["live"])
            print(f"  → {len(games)} wedstrijden — {played} gespeeld, {upcoming} gepland, {live} live\n")

        browser.close()

    # Globale deduplicatie — zelfde wedstrijd kan op meerdere week-URLs staan
    seen_global  = set()
    unique_games = []
    for g in all_games:
        key = (g["thuis"], g["uit"], g["datum"], g["tijdstip"])
        if key not in seen_global:
            seen_global.add(key)
            unique_games.append(g)

    print(f"Na deduplicatie: {len(unique_games)} unieke wedstrijden (was {len(all_games)})")

    # Uitslagen: data-state="final" én binnen de meest recente speelweek
    uitslagen = [
        g for g in unique_games
        if g["gespeeld"] and g["datum"]
        and friday <= datetime.strptime(g["datum"], "%Y-%m-%d").date() <= sunday
    ]

    # Programma: data-state="planned", datum in de toekomst, max 15
    programma = sorted(
        [
            g for g in unique_games
            if not g["gespeeld"] and not g["live"] and g["datum"]
            and datetime.strptime(g["datum"], "%Y-%m-%d").date() > today
        ],
        key=lambda g: (g["datum"], g["tijdstip"] or "")
    )[:15]

    uitslagen.sort(key=lambda g: (g["datum"], g["tijdstip"] or ""))

    # Debug output
    print(f"\nUitslagen ({friday} – {sunday}):")
    if uitslagen:
        for u in uitslagen:
            print(f"  {u['datum']} {u['tijdstip']}  "
                  f"{u['uit']} {u['score_uit']}–{u['score_thuis']} {u['thuis']}  [{u['divisie']}]")
    else:
        print("  (geen — wedstrijden nog niet gespeeld of buiten speelweek)")
        periode = [
            g for g in unique_games if g["datum"]
            and friday <= datetime.strptime(g["datum"], "%Y-%m-%d").date() <= sunday
        ]
        for g in periode:
            print(f"    state={g['gespeeld']}/{g['live']}  "
                  f"{g['score_uit']}-{g['score_thuis']}  "
                  f"{g['uit']} @ {g['thuis']}")

    print(f"\nProgramma (eerstvolgende {len(programma)}):")
    for p in programma:
        print(f"  {p['datum']} {p['tijdstip']}  {p['uit']} @ {p['thuis']}  [{p['divisie']}]")

    output = {
        "bijgewerkt":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron":             BASE_URL,
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

    print(f"\n✅ schedule.json opgeslagen")
    print(f"   Unieke wedstrijden totaal : {len(unique_games)}")
    print(f"   Uitslagen deze speelweek  : {len(uitslagen)}")
    print(f"   Aankomende wedstrijden    : {len(programma)}")


if __name__ == "__main__":
    main()
