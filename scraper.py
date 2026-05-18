"""
DBL Schedule Scraper — gebruikt Playwright (headless Chrome)
omdat baseball.de Python-requests met 403 blokkeert.

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

DIVISION_KEYWORDS = {
    "Nord": "Nord",
    "Süd": "Süd",
    "Zwischenphase": "Zwischenphase",
    "Playoff": "Playoff",
}


def parse_date(date_str):
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
    text = str(text).strip()
    if text in ("--", "-", "", "?", "None"):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def speelweek_bounds():
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    today = now.date()
    weekday = today.weekday()
    days_since_friday = (weekday - 4) % 7
    friday = today - timedelta(days=days_since_friday)
    sunday = friday + timedelta(days=2)
    return friday, sunday


def scrape_week_playwright(page, week, year):
    """Scrapt één speelweek via Playwright en extraheert wedstrijden uit de DOM."""
    url = f"{BASE_URL}?year={year}&week={week}"
    print(f"  Laden: {url}")

    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception as e:
        print(f"  ⚠️  Timeout/fout: {e}")
        return []

    # Wacht tot wedstrijdkaarten geladen zijn
    try:
        page.wait_for_selector("img[alt*='Logo']", timeout=10000)
    except Exception:
        print(f"  ⚠️  Geen logo's gevonden na wachten")

    games = []
    seen  = set()

    # Extraheer wedstrijddata via JavaScript in de browser
    # Dit loopt in de context van de geladen pagina
    raw = page.evaluate("""
    () => {
        const results = [];

        // Zoek alle wedstrijdcontainers
        // De site gebruikt waarschijnlijk cards/rows per wedstrijd
        // We zoeken naar elementen met twee team-logo's

        const allImgs = Array.from(document.querySelectorAll('img[alt]'))
            .filter(img => img.alt.includes('Logo'));

        // Groepeer logo's per 2 (away, home)
        for (let i = 0; i + 1 < allImgs.length; i += 2) {
            const awayImg = allImgs[i];
            const homeImg = allImgs[i + 1];

            const awayTeam = awayImg.alt.replace(' Logo', '').trim();
            const homeTeam = homeImg.alt.replace(' Logo', '').trim();

            // Zoek de gemeenschappelijke container
            let container = awayImg.parentElement;
            for (let depth = 0; depth < 8; depth++) {
                if (!container) break;
                const text = container.innerText || '';
                // Container moet beide teamnamen bevatten
                if (text.includes(awayTeam) && text.includes(homeTeam)) break;
                container = container.parentElement;
            }

            const text = container ? (container.innerText || '') : '';

            // Tijd
            const timeMatch = text.match(/(\\d{2}:\\d{2})\\s*Uhr/);
            const time = timeMatch ? timeMatch[1] : null;

            // Locatie (na "Uhr,")
            const locMatch = text.match(/Uhr,\\s*([^\\n]+)/);
            const location = locMatch ? locMatch[1].trim().split('\\n')[0].trim() : null;

            // Datum
            const dateMatch = text.match(
                /(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),\\s+\\d+\\.\\s+\\w+\\s+\\d{4}/
            );
            const dateStr = dateMatch ? dateMatch[0] : null;

            // Score — zoek op patroon "getal : getal" of "-- : --"
            const scoreMatch = text.match(/(?<![\\d])(\\d{1,2}|--)\\s*:\\s*(\\d{1,2}|--)(?![\\d])/);
            const scoreAway = scoreMatch ? scoreMatch[1] : null;
            const scoreHome = scoreMatch ? scoreMatch[2] : null;

            // Divisie — zoek in tekst of dichtstbijzijnde header
            let division = null;
            const divMatches = [
                ['Reguläre Saison Nord', 'Nord'],
                ['Reguläre Saison Süd', 'Süd'],
                ['Zwischenphase', 'Zwischenphase'],
                ['Playoff', 'Playoff'],
            ];
            for (const [keyword, val] of divMatches) {
                if (text.includes(keyword)) { division = val; break; }
            }

            // Zoek ook in voorgaande siblings/parents voor divisie-header
            if (!division && container) {
                let el = container.previousElementSibling;
                for (let d = 0; d < 5 && el; d++) {
                    const t = el.innerText || '';
                    for (const [keyword, val] of divMatches) {
                        if (t.includes(keyword)) { division = val; break; }
                    }
                    if (division) break;
                    el = el.previousElementSibling;
                }
            }

            const isLive = text.includes('LIVE');

            results.push({
                awayTeam, homeTeam, time, location, dateStr,
                scoreAway, scoreHome, division, isLive,
                rawText: text.substring(0, 200)
            });
        }

        return results;
    }
    """)

    for r in raw:
        away_team = r.get("awayTeam", "")
        home_team = r.get("homeTeam", "")
        if not away_team or not home_team:
            continue

        time_str   = r.get("time")
        location   = r.get("location")
        date_str   = r.get("dateStr")
        score_away = parse_score(r.get("scoreAway"))
        score_home = parse_score(r.get("scoreHome"))
        division   = r.get("division")
        is_live    = r.get("isLive", False)
        game_date  = parse_date(date_str)

        # Gespeeld als er echte scores zijn en het niet puur live 0-0 is
        gespeeld = (
            score_away is not None
            and score_home is not None
            and not (is_live and score_away == 0 and score_home == 0)
        )

        key = (away_team, home_team, str(game_date), time_str)
        if key in seen:
            continue
        seen.add(key)

        games.append({
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

    return games


def main():
    print(f"DBL scraper (Playwright) gestart — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

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
            games = scrape_week_playwright(page, w["week"], w["year"])
            all_games.extend(games)
            played   = sum(1 for g in games if g["gespeeld"])
            upcoming = sum(1 for g in games if not g["gespeeld"])
            print(f"  → {len(games)} wedstrijden ({played} gespeeld, {upcoming} gepland)\n")

        browser.close()

    # Globale deduplicatie
    seen_global  = set()
    unique_games = []
    for g in all_games:
        key = (g["thuis"], g["uit"], g["datum"], g["tijdstip"])
        if key not in seen_global:
            seen_global.add(key)
            unique_games.append(g)

    print(f"Na deduplicatie: {len(unique_games)} unieke wedstrijden (was {len(all_games)})")

    # Uitslagen: gespeeld in de meest recente speelweek
    uitslagen = [
        g for g in unique_games
        if g["gespeeld"] and g["datum"]
        and friday <= datetime.strptime(g["datum"], "%Y-%m-%d").date() <= sunday
    ]

    # Programma: toekomstige wedstrijden, max 15
    programma = sorted(
        [
            g for g in unique_games
            if not g["gespeeld"] and g["datum"]
            and datetime.strptime(g["datum"], "%Y-%m-%d").date() > today
        ],
        key=lambda g: (g["datum"], g["tijdstip"] or "")
    )[:15]

    uitslagen.sort(key=lambda g: (g["datum"], g["tijdstip"] or ""))

    # Debug
    print(f"\nUitslagen ({friday} – {sunday}):")
    if uitslagen:
        for u in uitslagen:
            print(f"  {u['datum']} {u['tijdstip']}  "
                  f"{u['uit']} {u['score_uit']}–{u['score_thuis']} {u['thuis']}  [{u['divisie']}]")
    else:
        print("  ⚠️  Geen uitslagen — wedstrijden nog niet gespeeld of buiten speelweek")
        periode = [
            g for g in unique_games if g["datum"]
            and friday <= datetime.strptime(g["datum"], "%Y-%m-%d").date() <= sunday
        ]
        for g in periode:
            print(f"    gespeeld={g['gespeeld']} live={g['live']} "
                  f"score={g['score_uit']}-{g['score_thuis']} "
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
