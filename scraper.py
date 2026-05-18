import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta

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

DIVISION_MAP = {
    "Reguläre Saison Nord": "Nord",
    "Reguläre Saison Süd":  "Süd",
    "Zwischenphase":        "Zwischenphase",
    "Playoff":              "Playoff",
}

MAANDEN_DE = {
    "Januar": 1, "Februar": 2, "März": 3, "April": 4,
    "Mai": 5, "Juni": 6, "Juli": 7, "August": 8,
    "September": 9, "Oktober": 10, "November": 11, "Dezember": 12,
}


def fetch_html(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "de-DE,de;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def parse_date(date_str):
    """Parset 'Freitag, 29. Mai 2026' naar een date object."""
    if not date_str:
        return None
    m = re.search(r"(\d+)\.\s+(\w+)\s+(\d{4})", date_str)
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
    """Geeft None terug voor streepjes, anders het getal."""
    text = str(text).strip()
    if text in ("--", "-", "", "?"):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def speelweek_bounds():
    """
    Geeft de vrijdag en zondag van de meest recente DBL-speelweek.
    DBL speelt op vrijdag, zaterdag en zondag.

    Logica:
    - Ma t/m do → vorige week vr t/m zo
    - Vr t/m zo → deze week vr t/m zo
    """
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    today = now.date()
    weekday = today.weekday()  # 0=ma … 6=zo

    days_since_friday = (weekday - 4) % 7
    friday = today - timedelta(days=days_since_friday)
    sunday = friday + timedelta(days=2)
    return friday, sunday


def scrape_week(week, year):
    """
    Scrapt één speelweek van baseball.de.
    Deduplicatie per week via seen-set op thuis+uit+datum+tijd.
    """
    url = f"{BASE_URL}?year={year}&week={week}"
    print(f"  Ophalen: {url}")
    try:
        html = fetch_html(url)
    except Exception as e:
        print(f"  ⚠️  Fout: {e}")
        return []

    games = []
    seen  = set()

    # Splits HTML in secties per divisie-label
    sections = re.split(
        r'(Reguläre Saison Nord|Reguläre Saison Süd|Zwischenphase|Playoff)',
        html
    )

    current_division = None

    for section in sections:
        # Divisie-header?
        stripped = section.strip()
        matched_div = False
        for key, val in DIVISION_MAP.items():
            if stripped == key:
                current_division = val
                matched_div = True
                break

        if not matched_div:
            # Inhoudssectie — zoek wedstrijden
            found = parse_games_from_section(section, current_division, week, year, seen)
            games.extend(found)

    # Fallback als niets gevonden
    if not games:
        print(f"  ⚠️  Primaire parser leeg, probeer fallback...")
        games = scrape_week_fallback(html, week, year)

    return games


def parse_games_from_section(html, division, week, year, seen):
    """Zoekt wedstrijden in een HTML-sectie met één divisie."""
    games = []

    logo_re  = re.compile(r'alt="([^"]+) Logo"')
    time_re  = re.compile(r'(\d{2}:\d{2})\s*Uhr,\s*([^\n<]{2,60}?)(?:\s*<|\s*\n)')
    score_re = re.compile(r'(?<!\d)(\d{1,2}|--)\s*:\s*(\d{1,2}|--)(?!\d)')
    date_re  = re.compile(
        r'(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),'
        r'\s+\d+\.\s+\w+\s+\d{4}'
    )

    logos  = list(logo_re.finditer(html))
    times  = time_re.findall(html)
    scores = score_re.findall(html)
    dates  = date_re.findall(html)

    pairs = len(logos) // 2

    for i in range(pairs):
        away_match = logos[i * 2]
        home_match = logos[i * 2 + 1]

        away_team = away_match.group(1).strip()
        home_team = home_match.group(1).strip()

        time_str = times[i][0].strip() if i < len(times) else None
        location = times[i][1].strip()  if i < len(times) else None
        date_str = dates[i]             if i < len(dates) else None

        # Zoek score in het blok rondom de twee logo-posities
        block = html[max(0, away_match.start() - 300):home_match.end() + 300]
        score_m  = score_re.search(block)
        raw_away = score_m.group(1) if score_m else None
        raw_home = score_m.group(2) if score_m else None

        score_away = parse_score(raw_away) if raw_away else None
        score_home = parse_score(raw_home) if raw_home else None

        is_live = "LIVE" in block

        # Gespeeld = er zijn numerieke scores EN het is niet een live-wedstrijd
        # waarbij de scores 0-0 zijn (dat zijn streepjes die nog geladen worden)
        gespeeld = (
            score_away is not None
            and score_home is not None
            and not (is_live and score_away == 0 and score_home == 0)
        )

        game_date = parse_date(date_str)

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


def scrape_week_fallback(html, week, year):
    """Vereenvoudigde fallback parser zonder divisie-splitsing."""
    games  = []
    seen   = set()

    logo_re  = re.compile(r'alt="([^"]+) Logo"')
    time_re  = re.compile(r'(\d{2}:\d{2})\s*Uhr,\s*([^\n<]{2,60}?)(?:\s*<|\s*\n)')
    score_re = re.compile(r'(?<!\d)(\d{1,2}|--)\s*:\s*(\d{1,2}|--)(?!\d)')
    date_re  = re.compile(
        r'(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),'
        r'\s+\d+\.\s+\w+\s+\d{4}'
    )

    logos  = logo_re.findall(html)
    times  = time_re.findall(html)
    scores = score_re.findall(html)
    dates  = date_re.findall(html)

    pairs = len(logos) // 2

    for i in range(pairs):
        away = logos[i * 2]
        home = logos[i * 2 + 1]

        time_str = times[i][0].strip() if i < len(times) else None
        location = times[i][1].strip()  if i < len(times) else None
        date_str = dates[i]             if i < len(dates) else None

        raw_away = scores[i][0] if i < len(scores) else None
        raw_home = scores[i][1] if i < len(scores) else None

        score_away = parse_score(raw_away) if raw_away else None
        score_home = parse_score(raw_home) if raw_home else None
        gespeeld   = score_away is not None and score_home is not None
        game_date  = parse_date(date_str)

        key = (away, home, str(game_date), time_str)
        if key in seen:
            continue
        seen.add(key)

        games.append({
            "week":        week,
            "year":        year,
            "datum":       str(game_date) if game_date else None,
            "datum_str":   date_str,
            "tijdstip":    time_str,
            "thuis":       home,
            "uit":         away,
            "score_thuis": score_home if gespeeld else None,
            "score_uit":   score_away if gespeeld else None,
            "locatie":     location,
            "divisie":     None,
            "gespeeld":    gespeeld,
            "live":        False,
        })

    return games


def main():
    print(f"DBL scraper gestart — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    friday, sunday = speelweek_bounds()
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).date()
    print(f"Meest recente speelweek: {friday} (vr) t/m {sunday} (zo)")

    all_games = []

    for w in WEEKS:
        print(f"\nWeek {w['week']}/{w['year']} ({w['label']})...")
        games = scrape_week(w["week"], w["year"])
        all_games.extend(games)
        played   = sum(1 for g in games if g["gespeeld"])
        upcoming = sum(1 for g in games if not g["gespeeld"])
        print(f"  → {len(games)} wedstrijden ({played} gespeeld, {upcoming} gepland)")

    # Globale deduplicatie — zelfde wedstrijd kan op meerdere week-URLs staan
    seen_global  = set()
    unique_games = []
    for g in all_games:
        key = (g["thuis"], g["uit"], g["datum"], g["tijdstip"])
        if key not in seen_global:
            seen_global.add(key)
            unique_games.append(g)

    print(f"\nNa deduplicatie: {len(unique_games)} unieke wedstrijden (was {len(all_games)})")

    # Uitslagen: gespeeld in de meest recente speelweek (vr–zo)
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
        print("  ⚠️  Geen uitslagen gevonden voor deze periode")
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
        "bijgewerkt":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron":           BASE_URL,
        "speelweek": {
            "van": str(friday),
            "tot": str(sunday),
        },
        "uitslagen":      uitslagen,
        "programma":      programma,
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
