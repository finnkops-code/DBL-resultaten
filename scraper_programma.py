"""
DBL Programma Scraper
Haalt geplande wedstrijden op via de standaard URL (geen weekparameter).
De site toont automatisch de eerstvolgende speelweek.
Schrijft alleen "programma" in schedule.json.
"""

import json
import os
import re
import datetime as dt
from datetime import timezone, timedelta
from playwright.sync_api import sync_playwright

BASE_URL  = "https://www.baseball.de/saison/spielplaene"
JSON_FILE = "schedule.json"

MAANDEN_DE = {
    "Januar": 1, "Februar": 2, "März": 3, "April": 4,
    "Mai": 5, "Juni": 6, "Juli": 7, "August": 8,
    "September": 9, "Oktober": 10, "November": 11, "Dezember": 12,
}

EXTRACT_JS = """
() => {
    const results = [];
    document.querySelectorAll('div.game').forEach(card => {
        const state     = card.getAttribute('data-state') || '';
        const dataStart = card.getAttribute('data-start');
        const timestamp = dataStart ? parseInt(dataStart) * 1000 : null;
        const badgeEl   = card.querySelector('p.game-badge');
        let division = null;
        if (badgeEl) {
            const t = badgeEl.textContent.trim();
            if (t.includes('Nord'))          division = 'Nord';
            else if (t.includes('Süd'))      division = 'Süd';
            else if (t.includes('Zwischen')) division = 'Zwischenphase';
            else if (t.includes('Playoff'))  division = 'Playoff';
        }
        const dateEl  = card.querySelector('p.game-header-date');
        const dateStr = dateEl ? dateEl.textContent.trim() : null;
        const timeEl  = card.querySelector('p.game-header-time');
        let time = null, location = null;
        if (timeEl) {
            const m = timeEl.textContent.trim().match(/(\\d{2}:\\d{2})\\s*Uhr,?\\s*(.*)/);
            if (m) { time = m[1]; location = m[2].trim() || null; }
        }
        const homeScoreRaw = card.querySelector('span[data-team-score="home"]')?.textContent.trim() || null;
        const awayScoreRaw = card.querySelector('span[data-team-score="away"]')?.textContent.trim() || null;
        const tooltips = Array.from(card.querySelectorAll('dbl-tooltip[tooltip]'))
            .map(el => el.getAttribute('tooltip').trim());
        results.push({ state, timestamp, division, dateStr, time, location,
                       homeTeam: tooltips[0] || null, awayTeam: tooltips[1] || null,
                       homeScoreRaw, awayScoreRaw });
    });
    return results;
}
"""

def parse_date_str(s):
    if not s: return None
    m = re.search(r"(\d+)\.\s+(\w+)\s+(\d{4})", str(s))
    if not m: return None
    month = MAANDEN_DE.get(m.group(2), 0)
    if not month: return None
    try: return dt.datetime(int(m.group(3)), month, int(m.group(1))).date()
    except: return None

def parse_score(t):
    if not t or str(t).strip() in ("--", "-", "", "?"): return None
    try: return int(str(t).strip())
    except: return None

def process(raw):
    games, seen = [], set()
    for r in raw:
        h, a = r.get("homeTeam"), r.get("awayTeam")
        if not h or not a: continue
        state = r.get("state", "")
        date_str, time_str, location, division = r.get("dateStr"), r.get("time"), r.get("location"), r.get("division")
        game_date = parse_date_str(date_str)
        if not game_date and r.get("timestamp"):
            d = dt.datetime.fromtimestamp(r["timestamp"] / 1000, tz=timezone.utc) + timedelta(hours=2)
            game_date = d.date()
            if not time_str: time_str = d.strftime("%H:%M")
        gespeeld = state in ("played", "live")
        key = (h, a, str(game_date), time_str)
        if key in seen: continue
        seen.add(key)
        games.append({
            "datum": str(game_date) if game_date else None,
            "datum_str": date_str, "tijdstip": time_str,
            "thuis": h, "uit": a,
            "score_thuis": parse_score(r.get("homeScoreRaw")) if gespeeld else None,
            "score_uit":   parse_score(r.get("awayScoreRaw")) if gespeeld else None,
            "locatie": location, "divisie": division,
            "gespeeld": gespeeld, "live": state == "live",
        })
    return games

def main():
    print(f"Laden: {BASE_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
            locale="de-DE", viewport={"width": 1280, "height": 800},
        ).new_page()

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("div.game", timeout=12000)
        games = process(page.evaluate(EXTRACT_JS))
        browser.close()

    programma = sorted(
        [g for g in games if not g["gespeeld"]],
        key=lambda g: (g["datum"] or "", g["tijdstip"] or "")
    )

    print(f"→ {len(games)} wedstrijden gevonden, {len(programma)} gepland")
    print(f"\nProgramma ({len(programma)}):")
    for p in programma:
        print(f"  {p['datum']} {p['tijdstip']}  {p['uit']} @ {p['thuis']}")

    data = {}
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, encoding="utf-8") as f:
            try: data = json.load(f)
            except: pass

    data["bijgewerkt_programma"] = dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["programma"]            = programma

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ {len(programma)} programma-wedstrijden opgeslagen")

if __name__ == "__main__":
    main()
