"""
DBL Schedule Scraper — Playwright (headless Chrome)
Laadt twee pagina's: huidige speelweek (programma) + vorige speelweek (uitslagen).

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
    if not text or str(text).strip() in ("--", "-", "", "?"):
        return None
    try:
        return int(str(text).strip())
    except ValueError:
        return None


def speelweek_bounds():
    """
    Geeft de vrijdag en zondag van de meest recente DBL-speelweek.
    - Ma t/m do → vorige week vr t/m zo  (uitslagen tonen)
    - Vr t/m zo → deze week vr t/m zo    (live/gepland)
    """
    now     = datetime.now(timezone.utc) + timedelta(hours=2)
    today   = now.date()
    weekday = today.weekday()
    days_since_friday = (weekday - 4) % 7
    friday  = today - timedelta(days=days_since_friday)
    sunday  = friday + timedelta(days=2)
    return friday, sunday


def vorige_speelweek(friday):
    """Geeft de week-entry uit WEEKS die het dichtst vóór de huidige friday ligt."""
    friday_iso_week = friday.isocalendar()[1]
    friday_year     = friday.year

    # Zoek de vorige week in onze WEEKS lijst
    candidates = [
        w for w in WEEKS
        if (w["year"] < friday_year)
        or (w["year"] == friday_year and w["week"] < friday_iso_week)
    ]
    return candidates[-1] if candidates else None


def extract_games(page):
    """Extraheert alle div.game elementen van de geladen pagina."""
    return page.evaluate("""
    () => {
        const results = [];

        document.querySelectorAll('div.game').forEach(card => {

            // data-state: "planned" | "live" | "played"
            const state = card.getAttribute('data-state') || '';

            // Unix timestamp voor datum/tijd fallback
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


def process_raw(raw_games):
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

        # Fallback op Unix timestamp als datum niet parsed
        if not game_date and timestamp:
            dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc) + timedelta(hours=2)
            game_date = dt.date()
            if not time_str:
                time_str = dt.strftime("%H:%M")

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
    print(f"  Laden: {url}  ({label})")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("div.game", timeout=10000)
    except Exception as e:
        print(f"  ⚠️  Fout: {e}")
        return []
    raw   = extract_games(page)
    games = process_raw(raw)
    played   = sum(1 for g in games if g["gespeeld"])
    upcoming = sum(1 for g in games if not g["gespeeld"] and not g["live"])
    live     = sum(1 for g in games if g["live"])
    print(f"  → {len(games)} wedstrijden — {played} gespeeld, {upcoming} gepland, {live} live")
    return games


def main():
    print(f"DBL scraper gestart — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    friday, sunday = speelweek_bounds()
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).date()
    print(f"Huidige speelweek : {friday} (vr) t/m {sunday} (zo)")

    vorige = vorige_speelweek(friday)
    if vorige:
        print(f"Vorige speelweek  : week {vorige['week']} ({vorige['label']})")
    print()

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

        # Pagina 1: huidige week (programma + eventueel live)
        huidige_url = BASE_URL
        alle_games = scrape_url(page, huidige_url, "huidige week")

        # Pagina 2: vorige speelweek (uitslagen)
        if vorige:
            vorige_url = f"{BASE_URL}?year={vorige['year']}&week={vorige['week']}"
            vorige_games = scrape_url(page, vorige_url, f"vorige week ({vorige['label']})")

            # Voeg toe, dedupliceer
            existing_keys = {(g["thuis"], g["uit"], g["datum"], g["tijdstip"]) for g in alle_games}
            for g in vorige_games:
                key = (g["thuis"], g["uit"], g["datum"], g["tijdstip"])
                if key not in existing_keys:
                    alle_games.append(g)
                    existing_keys.add(key)

        browser.close()

    alle_games.sort(key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))

    played   = sum(1 for g in alle_games if g["gespeeld"])
    upcoming = sum(1 for g in alle_games if not g["gespeeld"] and not g["live"])
    live     = sum(1 for g in alle_games if g["live"])
    print(f"\nTotaal: {len(alle_games)} wedstrijden — {played} gespeeld, {upcoming} gepland, {live} live")

    # Uitslagen: data-state="played" in de vorige speelweek
    # Bepaal de bounds van de vorige speelweek
    if vorige:
        # Bereken vrijdag van de vorige speelweek op basis van week-nummer
        import datetime as dt_module
        vorige_friday = dt_module.date.fromisocalendar(vorige["year"], vorige["week"], 5)
        vorige_sunday = vorige_friday + timedelta(days=2)
    else:
        vorige_friday = friday
        vorige_sunday = sunday

    uitslagen = [
        g for g in alle_games
        if g["gespeeld"] and g["datum"]
        and vorige_friday <= datetime.strptime(g["datum"], "%Y-%m-%d").date() <= vorige_sunday
    ]

    # Programma: toekomstige wedstrijden, max 15
    programma = [
        g for g in alle_games
        if not g["gespeeld"] and not g["live"] and g["datum"]
        and datetime.strptime(g["datum"], "%Y-%m-%d").date() > today
    ][:15]

    # Debug
    print(f"\nUitslagen ({vorige_friday} – {vorige_sunday}):")
    if uitslagen:
        for u in uitslagen:
            print(f"  {u['datum']} {u['tijdstip']}  "
                  f"{u['uit']} {u['score_uit']}–{u['score_thuis']} {u['thuis']}  [{u['divisie']}]")
    else:
        print("  (geen gespeelde wedstrijden gevonden)")

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
        "alle_wedstrijden": alle_games,
    }

    with open("schedule.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ schedule.json opgeslagen")
    print(f"   Uitslagen vorige speelweek : {len(uitslagen)}")
    print(f"   Aankomende wedstrijden     : {len(programma)}")
    print(f"   Totaal alle wedstrijden    : {len(alle_games)}")


if __name__ == "__main__":
    main()
