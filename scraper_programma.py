"""
Snelle DBL Schedule Scraper
Haalt aankomende geplande wedstrijden op van baseball.de
en bewaart ze in schedule.json zonder andere JSON-data te verwijderen.
"""

import json
import os
import re
import datetime as dt
from datetime import timezone
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://www.baseball.de/saison/spielplaene"
JSON_FILE = "schedule.json"

MAX_WEEKS_AHEAD = 8
MAX_GAMES = 10

MAANDEN_DE = {
    "Januar": 1,
    "Februar": 2,
    "März": 3,
    "Maerz": 3,
    "April": 4,
    "Mai": 5,
    "Juni": 6,
    "Juli": 7,
    "August": 8,
    "September": 9,
    "Oktober": 10,
    "November": 11,
    "Dezember": 12,
}

EXTRACT_JS = """
() => {
    const games = [];

    document.querySelectorAll("div.game").forEach(card => {
        const state = card.getAttribute("data-state") || "";
        const dataStart = card.getAttribute("data-start");

        const badge = card.querySelector("p.game-badge")?.textContent.trim() || null;
        const dateStr = card.querySelector("p.game-header-date")?.textContent.trim() || null;
        const timeText = card.querySelector("p.game-header-time")?.textContent.trim() || null;

        let time = null;
        let location = null;

        if (timeText) {
            const match = timeText.match(/(\\d{2}:\\d{2})\\s*Uhr,?\\s*(.*)/);
            if (match) {
                time = match[1];
                location = match[2]?.trim() || null;
            }
        }

        const teams = Array.from(card.querySelectorAll("dbl-tooltip[tooltip]"))
            .map(el => el.getAttribute("tooltip")?.trim())
            .filter(Boolean);

        games.push({
            state,
            dataStart,
            badge,
            dateStr,
            time,
            location,
            homeTeam: teams[0] || null,
            awayTeam: teams[1] || null,
            homeScoreRaw: card.querySelector('span[data-team-score="home"]')?.textContent.trim() || null,
            awayScoreRaw: card.querySelector('span[data-team-score="away"]')?.textContent.trim() || null
        });
    });

    return games;
}
"""


def load_json(path):
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_date(date_str, data_start=None):
    if date_str:
        match = re.search(r"(\d{1,2})\.\s+([A-Za-zÄÖÜäöüß]+)\s+(\d{4})", date_str)

        if match:
            day = int(match.group(1))
            month_name = match.group(2)
            year = int(match.group(3))
            month = MAANDEN_DE.get(month_name)

            if month:
                return dt.date(year, month, day)

    if data_start:
        try:
            return dt.datetime.fromtimestamp(int(data_start), tz=timezone.utc).date()
        except Exception:
            return None

    return None


def parse_division(badge):
    if not badge:
        return None

    if "Nord" in badge:
        return "Nord"
    if "Süd" in badge or "Sued" in badge:
        return "Süd"
    if "Zwischen" in badge:
        return "Zwischenphase"
    if "Playoff" in badge:
        return "Playoff"

    return badge


def is_played(game):
    state = (game.get("state") or "").lower()

    if state in ["played", "live", "finished", "final"]:
        return True

    home_score = game.get("homeScoreRaw")
    away_score = game.get("awayScoreRaw")

    if home_score and away_score:
        if home_score.strip() not in ["-", "--", ""] and away_score.strip() not in ["-", "--", ""]:
            return True

    return False


def normalize_game(game):
    game_date = parse_date(game.get("dateStr"), game.get("dataStart"))

    return {
        "datum": str(game_date) if game_date else None,
        "datum_str": game.get("dateStr"),
        "tijdstip": game.get("time"),
        "thuis": game.get("homeTeam"),
        "uit": game.get("awayTeam"),
        "score_thuis": None,
        "score_uit": None,
        "locatie": game.get("location"),
        "divisie": parse_division(game.get("badge")),
        "gespeeld": False,
        "live": False,
    }


def get_weeks_from_today(max_weeks):
    today = dt.datetime.now(timezone.utc).date()
    weeks = []
    seen = set()

    for i in range(max_weeks):
        date = today + dt.timedelta(weeks=i)
        iso = date.isocalendar()
        key = (iso.year, iso.week)

        if key not in seen:
            seen.add(key)
            weeks.append({
                "year": iso.year,
                "week": iso.week,
            })

    return weeks


def scrape_schedule():
    today = dt.datetime.now(timezone.utc).date()
    weeks = get_weeks_from_today(MAX_WEEKS_AHEAD)

    all_games = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 Chrome/122.0 Safari/537.36"
            ),
            locale="de-DE",
            viewport={"width": 1280, "height": 900},
        )

        page = context.new_page()

        for week in weeks:
            url = f"{BASE_URL}?year={week['year']}&week={week['week']}"
            print(f"Laden: {url}")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_selector("div.game", timeout=3000)
            except PlaywrightTimeoutError:
                print("Geen wedstrijden gevonden of pagina te traag")
                continue

            raw_games = page.evaluate(EXTRACT_JS)
            print(f"Ruwe wedstrijden gevonden: {len(raw_games)}")

            for raw_game in raw_games:
                if not raw_game.get("homeTeam") or not raw_game.get("awayTeam"):
                    continue

                if is_played(raw_game):
                    continue

                normalized = normalize_game(raw_game)

                if not normalized["datum"]:
                    continue

                game_date = dt.date.fromisoformat(normalized["datum"])

                if game_date < today:
                    continue

                key = (
                    normalized["datum"],
                    normalized["tijdstip"],
                    normalized["thuis"],
                    normalized["uit"],
                )

                if key in seen:
                    continue

                seen.add(key)
                all_games.append(normalized)

            if len(all_games) >= MAX_GAMES:
                break

        browser.close()

    all_games = sorted(
        all_games,
        key=lambda g: (
            g["datum"] or "9999-99-99",
            g["tijdstip"] or "99:99",
            g["thuis"] or "",
        )
    )

    return all_games[:MAX_GAMES]


def main():
    programma = scrape_schedule()

    data = load_json(JSON_FILE)

    data["bijgewerkt_programma"] = dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["programma"] = programma

    if programma:
        first_iso = dt.date.fromisoformat(programma[0]["datum"]).isocalendar()
        data["programma_week"] = {
            "year": first_iso.year,
            "week": first_iso.week,
        }
    else:
        data["programma_week"] = None

    save_json(JSON_FILE, data)

    print(f"✅ {len(programma)} aankomende wedstrijden opgeslagen in {JSON_FILE}")

    for game in programma:
        print(f"{game['datum']} {game['tijdstip']} - {game['uit']} @ {game['thuis']}")


if __name__ == "__main__":
    main()
