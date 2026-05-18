"""
DBL Schedule Scraper — directe AJAX POST naar baseball.de

Werkwijze:
  1. Haal de pagina op om de TYPO3 form-tokens te extraheren
  2. POST naar het listAjax endpoint met year + week als filter
  3. Parseer de HTML-response met BeautifulSoup

Installatie (eenmalig):
    pip install requests beautifulsoup4
"""

import json
import re
import datetime as dt
from datetime import timezone, timedelta
from urllib.parse import urlencode
import urllib.request
from html.parser import HTMLParser

BASE_URL   = "https://www.baseball.de/saison/spielplaene"
JSON_FILE  = "schedule.json"

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "de-DE,de;q=0.9",
}


def fetch(url, data=None, session_cookie=None):
    """Doet een GET of POST request en geeft de HTML terug."""
    headers = dict(HEADERS)
    if session_cookie:
        headers["Cookie"] = session_cookie
    if data:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        payload = urlencode(data).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    else:
        req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req, timeout=30) as resp:
        cookie = resp.headers.get("Set-Cookie", "")
        return resp.read().decode("utf-8"), cookie


def extract_form_tokens(html):
    """Haalt de TYPO3 form tokens en AJAX URL op uit de pagina HTML."""
    tokens = {}

    # Zoek de AJAX action URL
    ajax_match = re.search(
        r'action="(/saison/spielplaene\?[^"]+listAjax[^"]+)"',
        html
    )
    if ajax_match:
        tokens["action"] = "https://www.baseball.de" + ajax_match.group(1).replace("&amp;", "&")

    # Zoek alle hidden input velden in de game-list-filter form
    hidden_re = re.compile(
        r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"',
        re.IGNORECASE
    )
    for name, value in hidden_re.findall(html):
        if "tx_c3local_gamelist" in name and "filter" not in name:
            tokens[name] = value

    return tokens


def parse_games_from_html(html, week, year):
    """Parseer div.game elementen uit de AJAX HTML response."""
    games = []
    seen  = set()

    # Splits op div.game blokken
    # Elk blok begint met <div class="game
    blocks = re.split(r'(?=<div[^>]+class="game[^"]*"[^>]*>)', html)

    for block in blocks:
        if 'class="game' not in block:
            continue

        # data-state
        state_m = re.search(r'data-state="([^"]+)"', block)
        state   = state_m.group(1) if state_m else ""

        # divisie
        badge_m  = re.search(r'class="game-badge"[^>]*>([^<]+)<', block)
        division = None
        if badge_m:
            t = badge_m.group(1).strip()
            if "Nord" in t:        division = "Nord"
            elif "Süd" in t:       division = "Süd"
            elif "Zwischen" in t:  division = "Zwischenphase"
            elif "Playoff" in t:   division = "Playoff"

        # datum
        date_m   = re.search(r'class="game-header-date"[^>]*>([^<]+)<', block)
        date_str = date_m.group(1).strip() if date_m else None

        # tijd + locatie: "19:00 Uhr, Bonn"
        time_m   = re.search(r'class="game-header-time"[^>]*>([^<]+)<', block)
        time_str = None
        location = None
        if time_m:
            raw = time_m.group(1).strip()
            tm  = re.match(r"(\d{2}:\d{2})\s*Uhr,?\s*(.*)", raw)
            if tm:
                time_str = tm.group(1)
                location = tm.group(2).strip() or None

        # teamnamen via tooltip attribuut
        tooltips  = re.findall(r'tooltip="([^"]+)"', block)
        home_team = tooltips[0] if len(tooltips) > 0 else None
        away_team = tooltips[1] if len(tooltips) > 1 else None

        if not home_team or not away_team:
            continue

        # scores
        home_score_m = re.search(r'data-team-score="home"[^>]*>([^<]+)<', block)
        away_score_m = re.search(r'data-team-score="away"[^>]*>([^<]+)<', block)
        score_home   = parse_score(home_score_m.group(1) if home_score_m else None)
        score_away   = parse_score(away_score_m.group(1) if away_score_m else None)

        # datum parsen
        game_date = parse_date_str(date_str)

        # timestamp fallback
        if not game_date:
            ts_m = re.search(r'data-start="(\d+)"', block)
            if ts_m:
                d = dt.datetime.fromtimestamp(int(ts_m.group(1)), tz=timezone.utc) + timedelta(hours=2)
                game_date = d.date()
                if not time_str:
                    time_str = d.strftime("%H:%M")

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


def fetch_week(ajax_url, tokens, week, year, cookie):
    """Haalt één speelweek op via AJAX POST."""
    data = dict(tokens)
    data["tx_c3local_gamelist[filter][year]"] = str(year)
    data["tx_c3local_gamelist[filter][week]"] = str(week)
    data["tx_c3local_gamelist[filter][team]"] = ""

    html, _ = fetch(ajax_url, data=data, session_cookie=cookie)
    return parse_games_from_html(html, week, year)


def past_weeks():
    today = (dt.datetime.now(timezone.utc) + timedelta(hours=2)).date()
    return list(reversed([
        w for w in WEEKS
        if dt.date.fromisocalendar(w["year"], w["week"], 7) < today
    ]))


def future_weeks():
    today = (dt.datetime.now(timezone.utc) + timedelta(hours=2)).date()
    return [
        w for w in WEEKS
        if dt.date.fromisocalendar(w["year"], w["week"], 5) > today
    ]


def main():
    print(f"DBL scraper gestart — {dt.datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

    # Stap 1: haal de hoofdpagina op voor tokens en cookie
    print(f"Tokens ophalen: {BASE_URL}")
    html, cookie = fetch(BASE_URL)
    tokens = extract_form_tokens(html)

    print(f"  AJAX URL : {tokens.get('action', 'NIET GEVONDEN')}")
    print(f"  Tokens   : {len([k for k in tokens if k != 'action'])} form-velden")
    print(f"  Cookie   : {cookie[:60]}..." if cookie else "  Cookie   : geen")

    if "action" not in tokens:
        print("⚠️  AJAX URL niet gevonden — controleer de pagina")
        return

    ajax_url = tokens.pop("action")

    uitslagen      = []
    programma      = []
    uitslagen_week = None
    programma_week = None

    # Stap 2: uitslagen — meest recente afgelopen week met played wedstrijden
    print("\n=== UITSLAGEN ===")
    for w in past_weeks()[:4]:
        print(f"  Week {w['week']} ({w['label']})...")
        games  = fetch_week(ajax_url, tokens, w["week"], w["year"], cookie)
        played = [g for g in games if g["gespeeld"]]
        print(f"  → {len(games)} wedstrijden, {len(played)} gespeeld")
        if played:
            uitslagen      = sorted(played, key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))
            uitslagen_week = w
            break

    # Stap 3: programma — eerstvolgende toekomstige week met planned wedstrijden
    print("\n=== PROGRAMMA ===")
    for w in future_weeks()[:4]:
        print(f"  Week {w['week']} ({w['label']})...")
        games   = fetch_week(ajax_url, tokens, w["week"], w["year"], cookie)
        planned = [g for g in games if not g["gespeeld"]]
        print(f"  → {len(games)} wedstrijden, {len(planned)} gepland")
        if planned:
            programma      = sorted(planned, key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))
            programma_week = w
            break

    # Debug output
    print(f"\n--- Uitslagen ({len(uitslagen)}) ---")
    for u in uitslagen:
        print(f"  {u['datum']} {u['tijdstip']}  "
              f"{u['uit']} {u['score_uit']}–{u['score_thuis']} {u['thuis']}  [{u['divisie']}]")

    print(f"\n--- Programma ({len(programma)}) ---")
    for p in programma:
        print(f"  {p['datum']} {p['tijdstip']}  {p['uit']} @ {p['thuis']}  [{p['divisie']}]")

    output = {
        "bijgewerkt":     dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bron":           BASE_URL,
        "uitslagen_week": uitslagen_week,
        "programma_week": programma_week,
        "uitslagen":      uitslagen,
        "programma":      programma,
    }

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ {JSON_FILE} opgeslagen — {len(uitslagen)} uitslagen, {len(programma)} programma")


if __name__ == "__main__":
    main()
