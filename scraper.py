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
    "Reguläre Saison Süd": "Süd",
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
    """
    Parset een Duitse datum zoals 'Freitag, 29. Mai 2026'
    naar een datetime.date object.
    """
    m = re.search(
        r"(\d+)\.\s+(\w+)\s+(\d{4})",
        date_str
    )
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
    text = text.strip()
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
    """Scrapt één speelweek van baseball.de en geeft een lijst van games terug."""
    url = f"{BASE_URL}?year={year}&week={week}"
    print(f"  Ophalen: {url}")
    try:
        html = fetch_html(url)
    except Exception as e:
        print(f"  ⚠️  Fout: {e}")
        return []

    games = []
    current_division = None
    current_date_str = None

    # Zoek alle logo-links — elke wedstrijd heeft er twee (away, home)
    logo_pattern = re.compile(
        r'href="/saison/vereine/detail/[^"]*"[^>]*>.*?<img[^>]+alt="([^"]+) Logo"',
        re.DOTALL
    )
    logos = logo_pattern.findall(html)

    # Splits de HTML in blokken per wedstrijd via tijdstip-patroon
    # Elk wedstrijdblok bevat: divisie-label, datum, tijd, locatie, teams, score
    block_pattern = re.compile(
        r'(Reguläre Saison \w+|Zwischenphase|Playoff).*?'
        r'((?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),\s+\d+\.\s+\w+\s+\d{4}).*?'
        r'(\d{2}:\d{2})\s*Uhr,\s*([^\n<]+?)\s*(?:<|\n).*?'
        r'alt="([^"]+) Logo".*?'
        r'(\d+|--)\s*:\s*(\d+|--).*?'
        r'alt="([^"]+) Logo"',
        re.DOTALL
    )

    seen = set()

    for m in block_pattern.finditer(html):
        division_raw = m.group(1).strip()
        date_str     = m.group(2).strip()
        time_str     = m.group(3).strip()
        location     = m.group(4).strip()
        away_team    = m.group(5).strip()
        score_away   = parse_score(m.group(6))
        score_home   = parse_score(m.group(7))
        home_team    = m.group(8).strip()

        division = None
        for key, val in DIVISION_MAP.items():
            if key in division_raw:
                division = val
                break

        game_date = parse_date(date_str)
        is_played = score_home is not None and score_away is not None
        is_live   = False

        # Live check: score aanwezig maar "LIVE" in de buurt van dit blok
        if is_played and "LIVE" in m.group(0):
            is_live = True

        key = (away_team, home_team, date_str, time_str)
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
            "score_thuis": score_home if is_played else None,
            "score_uit":   score_away if is_played else None,
            "locatie":     location,
            "divisie":     division,
            "gespeeld":    is_played,
            "live":        is_live,
        })

    # Fallback als de regex-blokken niets opleveren
    if not games:
        games = scrape_week_fallback(html, week, year)

    return games


def scrape_week_fallback(html, week, year):
    """
    Vereenvoudigde fallback: koppelt logo-paren aan tijd/score via positie in de HTML.
    """
    games = []

    logo_re  = re.compile(r'alt="([^"]+) Logo"')
    time_re  = re.compile(r'(\d{2}:\d{2})\s*Uhr,\s*([^\n<]{2,50})')
    score_re = re.compile(r'(\d+|--)\s*:\s*(\d+|--)')
    date_re  = re.compile(
        r'(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),'
        r'\s+\d+\.\s+\w+\s+\d{4}'
    )
    div_re   = re.compile(r'Reguläre Saison (Nord|Süd)|Zwischenphase|Playoff')

    logos   = logo_re.findall(html)
    times   = time_re.findall(html)
    scores  = score_re.findall(html)
    dates   = date_re.findall(html)
    divs    = div_re.findall(html)

    pairs = len(logos) // 2
    seen  = set()

    for i in range(pairs):
        away = logos[i * 2]
        home = logos[i * 2 + 1]

        time_str  = times[i][0]   if i < len(times)  else None
        location  = times[i][1]   if i < len(times)  else None
        date_str  = dates[i]      if i < len(dates)   else None
        div       = divs[i]       if i < len(divs)    else None

        s_away = parse_score(scores[i][0]) if i < len(scores) else None
        s_home = parse_score(scores[i][1]) if i < len(scores) else None
        is_played = s_home is not None and s_away is not None

        game_date = parse_date(date_str) if date_str else None

        key = (away, home, date_str, time_str)
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
            "score_thuis": s_home if is_played else None,
            "score_uit":   s_away if is_played else None,
            "locatie":     location,
            "divisie":     div,
            "gespeeld":    is_played,
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
        print(f"  → {len(games)} wedstrijden gevonden")

    # Uitslagen: gespeeld in de meest recente speelweek
    uitslagen = [
        g for g in all_games
        if g["gespeeld"] and g["datum"]
        and friday <= datetime.strptime(g["datum"], "%Y-%m-%d").date() <= sunday
    ]

    # Programma: toekomstige wedstrijden, max 10
    programma = sorted(
        [g for g in all_games if not g["gespeeld"] and g["datum"]
         and datetime.strptime(g["datum"], "%Y-%m-%d").date() > today],
        key=lambda g: (g["datum"], g["tijdstip"] or "")
    )[:10]

    uitslagen.sort(key=lambda g: (g["datum"], g["tijdstip"] or ""))

    # Debug output
    print(f"\nGespeelde wedstrijden in speelweek ({friday} – {sunday}):")
    if uitslagen:
        for u in uitslagen:
            print(f"  {u['datum']} {u['tijdstip']}  {u['uit']} {u['score_uit']}–{u['score_thuis']} {u['thuis']}  [{u['divisie']}]")
    else:
        print("  ⚠️  Geen uitslagen gevonden voor deze speelweek")

    output = {
        "bijgewerkt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron": BASE_URL,
        "speelweek": {
            "van": str(friday),
            "tot": str(sunday),
        },
        "uitslagen": uitslagen,
        "programma": programma,
        "alle_wedstrijden": all_games,
    }

    with open("schedule.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ schedule.json opgeslagen")
    print(f"   Uitslagen deze speelweek  : {len(uitslagen)}")
    print(f"   Aankomende wedstrijden    : {len(programma)}")
    print(f"   Totaal alle wedstrijden   : {len(all_games)}")


if __name__ == "__main__":
    main()
