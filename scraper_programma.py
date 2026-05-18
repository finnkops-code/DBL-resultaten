"""
DBL Programma Scraper
Loopt vooruit door toekomstige week-URLs totdat planned wedstrijden gevonden worden.
Schrijft alleen "programma" in schedule.json.
"""

import json
import os
import re
import datetime as dt
from datetime import timezone, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://www.baseball.de/saison/spielplaene"
JSON_FILE = "schedule.json"

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
    "Januar": 1,
    "Februar": 2,
    "März": 3,
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
    const results = [];

    document.querySelectorAll('div.game').forEach(card => {
        const state = card.getAttribute('data-state') || '';
        const dataStart = card.getAttribute('data-start');
        const timestamp = dataStart ? parseInt(dataStart) * 1000 : null;

        const badgeEl = card.querySelector('p.game-badge');
        let division = null;

        if (badgeEl) {
            const t = badgeEl.textContent.trim();

            if (t.includes('Nord')) division = 'Nord';
            else if (t.includes('Süd')) division = 'Süd';
            else if (t.includes('Zwischen')) division = 'Zwischenphase';
            else if (t.includes('Playoff')) division = 'Playoff';
        }

        const dateEl = card.querySelector('p.game-header-date');
        const dateStr = dateEl ? dateEl.textContent.trim() : null;

        const timeEl = card.querySelector('p.game-header-time');
        let time = null;
        let location = null;

        if (timeEl) {
            const m = timeEl.textContent.trim().match(/(\\d{2}:\\d{2})\\s*Uhr,?\\s*(.*)/);

            if (m) {
                time = m[1];
                location = m[2].trim() || null;
            }
        }

        const homeScoreRaw =
            card.querySelector('span[data-team-score="home"]')?.textContent.trim() || null;

        const awayScoreRaw =
            card.querySelector('span[data-team-score="away"]')?.textContent.trim() || null;

        const tooltips = Array.from(card.querySelectorAll('dbl-tooltip[tooltip]'))
            .map(el => el.getAttribute('tooltip').trim());

        results.push({
            state,
            timestamp,
            division,
            dateStr,
            time,
            location,
            homeTeam: tooltips[0] || null,
            awayTeam: tooltips[1] || null,
            homeScoreRaw,
            awayScoreRaw
        });
    });

    return results;
}
"""


def parse_date_str(s):
    if not s:
        return None

    m = re.search(r"(\d+)\.\s+(\w+)\s+(\d{4})", str(s))

    if not m:
        return None

    month = MAANDEN_DE.get(m.group(2), 0)

    if not month:
        return None

    try:
        return dt.datetime(
            int(m.group(3)),
            month,
            int(m.group(1))
        ).date()
    except Exception:
        return None


def parse_score(t):
    if not t or str(t).strip() in ("--", "-", "", "?"):
        return None

    try:
        return int(str(t).strip())
    except Exception:
        return None


def process(raw):
    games = []
    seen = set()

    for r in raw:
        h = r.get("homeTeam")
        a = r.get("awayTeam")

        if not h or not a:
            continue

        state = r.get("state", "")
        date_str = r.get("dateStr")
        time_str = r.get("time")
        location = r.get("location")
        division = r.get("division")

        game_date = parse_date_str(date_str)

        if not game_date and r.get("timestamp"):
            d = (
                dt.datetime.fromtimestamp(
                    r["timestamp"] / 1000,
                    tz=timezone.utc
                )
                + timedelta(hours=2)
            )

            game_date = d.date()

            if not time_str:
                time_str = d.strftime("%H:%M")

        gespeeld = state in ("played", "live")

        key = (h, a, str(game_date), time_str)

        if key in seen:
            continue

        seen.add(key)

        games.append({
            "datum": str(game_date) if game_date else None,
            "datum_str": date_str,
            "tijdstip": time_str,
            "thuis": h,
            "uit": a,
            "score_thuis": parse_score(r.get("homeScoreRaw")) if gespeeld else None,
            "score_uit": parse_score(r.get("awayScoreRaw")) if gespeeld else None,
            "locatie": location,
            "divisie": division,
            "gespeeld": gespeeld,
            "live": state == "live",
        })

    return games


def load_existing_json():
    if not os.path.exists(JSON_FILE):
        return {}

    with open(JSON_FILE, encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}


def main():
    today = (dt.datetime.now(timezone.utc) + timedelta(hours=2)).date()

    # Toekomstige weken: zondag van de speelweek ligt vandaag of in de toekomst.
    # Hierdoor wordt een speelweek niet al op vrijdag weggefilterd.
    toekomstig = [
        w for w in WEEKS
        if dt.date.fromisocalendar(w["year"], w["week"], 7) >= today
    ]

    print("Vandaag:", today)
    print("Toekomstige weken:", toekomstig)

    programma = []
    programma_week = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 Chrome/122.0 Safari/537.36"
            ),
            locale="de-DE",
            viewport={"width": 1280, "height": 800},
        )

        page = context.new_page()

        for w in toekomstig:
            url = f"{BASE_URL}?year={w['year']}&week={w['week']}"
            print(f"Laden: {url}")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_selector("div.game", timeout=12000)
            except PlaywrightTimeoutError:
                print("→ Geen wedstrijden gevonden of pagina laadde te traag")
                continue

            raw = page.evaluate(EXTRACT_JS)
            games = process(raw)

            planned = [g for g in games if not g["gespeeld"]]

            print(f"→ {len(games)} wedstrijden, {len(planned)} gepland")

            if planned:
                programma = sorted(
                    planned,
                    key=lambda g: (g["datum"] or "", g["tijdstip"] or "")
                )
                programma_week = w
                break

        browser.close()

    print(f"\nProgramma ({len(programma)}):")

    for game in programma:
        print(
            f"  {game['datum']} {game['tijdstip']} "
            f"{game['uit']} @ {game['thuis']}"
        )

    data = load_existing_json()

    data["bijgewerkt_programma"] = dt.datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    data["programma_week"] = programma_week
    data["programma"] = programma

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ {len(programma)} programma-wedstrijden opgeslagen")


if __name__ == "__main__":
    main()
