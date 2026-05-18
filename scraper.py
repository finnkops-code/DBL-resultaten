"""
DBL Scraper — uitslagen + programma in één run, één JSON.

Logica:
  Uitslagen : BASE_URL zonder parameters → data-state "played" / "live"
  Programma : BASE_URL?year=…&week=… per komende week → data-state "planned"
"""

import json, os, re, datetime as dt
from datetime import timezone, timedelta
from playwright.sync_api import sync_playwright

BASE_URL  = "https://www.baseball.de/saison/spielplaene"
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

def scrape_page(page, url):
    print(f"  Laden: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("div.game", timeout=12000)
    return page.evaluate(EXTRACT_JS)

def to_uitslag(r):
    date_str, time_str = r.get("dateStr"), r.get("time")
    game_date = parse_date_str(date_str)
    if not game_date and r.get("timestamp"):
        d = dt.datetime.fromtimestamp(r["timestamp"] / 1000, tz=timezone.utc) + timedelta(hours=2)
        game_date = d.date()
        if not time_str: time_str = d.strftime("%H:%M")
    return {
        "datum":       str(game_date) if game_date else None,
        "datum_str":   date_str,
        "tijdstip":    time_str,
        "thuis":       r["homeTeam"],
        "uit":         r["awayTeam"],
        "score_thuis": parse_score(r.get("homeScoreRaw")),
        "score_uit":   parse_score(r.get("awayScoreRaw")),
        "locatie":     r.get("location"),
        "divisie":     r.get("division"),
        "gespeeld":    True,
        "live":        r.get("state") == "live",
    }

def to_programma(r):
    date_str, time_str = r.get("dateStr"), r.get("time")
    game_date = parse_date_str(date_str)
    if not game_date and r.get("timestamp"):
        d = dt.datetime.fromtimestamp(r["timestamp"] / 1000, tz=timezone.utc) + timedelta(hours=2)
        game_date = d.date()
        if not time_str: time_str = d.strftime("%H:%M")
    return {
        "datum":     str(game_date) if game_date else None,
        "datum_str": date_str,
        "tijdstip":  time_str,
        "thuis":     r["homeTeam"],
        "uit":       r["awayTeam"],
        "locatie":   r.get("location"),
        "divisie":   r.get("division"),
    }

def main():
    today = (dt.datetime.now(timezone.utc) + timedelta(hours=2)).date()

    # Komende weken: zondag van de week >= vandaag
    komende_weken = [
        w for w in WEEKS
        if dt.date.fromisocalendar(w["year"], w["week"], 7) >= today
    ]

    uitslagen       = []
    uitslagen_week  = None
    programma       = []
    programma_weken = []
    seen_u          = set()
    seen_p          = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
            locale="de-DE", viewport={"width": 1280, "height": 800},
        ).new_page()

        # ── 1. UITSLAGEN: geen parameters → site toont meest recente gespeelde week
        print("\n=== UITSLAGEN ===")
        try:
            raw = scrape_page(page, BASE_URL)
            for r in raw:
                if r.get("state") not in ("played", "live"): continue
                if not r.get("homeTeam") or not r.get("awayTeam"): continue
                key = (r["homeTeam"], r["awayTeam"], r.get("dateStr"), r.get("time"))
                if key in seen_u: continue
                seen_u.add(key)
                uitslagen.append(to_uitslag(r))
            uitslagen = sorted(uitslagen, key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))
            if uitslagen:
                uitslagen_week = {"label": "meest recent", "url": BASE_URL}
            print(f"  → {len(uitslagen)} uitslagen")
        except Exception as e:
            print(f"  !! Fout bij uitslagen: {e}")

        # ── 2. PROGRAMMA: week-URLs → data-state="planned"
        print("\n=== PROGRAMMA ===")
        for w in komende_weken[:4]:
            url = f"{BASE_URL}?year={w['year']}&week={w['week']}"
            try:
                raw = scrape_page(page, url)
                week_games = []
                for r in raw:
                    if r.get("state") != "planned": continue
                    if not r.get("homeTeam") or not r.get("awayTeam"): continue
                    key = (r["homeTeam"], r["awayTeam"], r.get("dateStr"), r.get("time"))
                    if key in seen_p: continue
                    seen_p.add(key)
                    week_games.append(to_programma(r))
                print(f"  → {len(week_games)} geplande wedstrijden ({w['label']})")
                if week_games:
                    programma.extend(week_games)
                    programma_weken.append(w)
            except Exception as e:
                print(f"  !! Fout bij {w['label']}: {e}")

        browser.close()

    programma = sorted(programma, key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))

    # ── 3. Schrijf alles in één keer naar schedule.json
    now_str = dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "bijgewerkt":       now_str,
        "uitslagen_week":   uitslagen_week,
        "uitslagen":        uitslagen,
        "programma_weken":  programma_weken,
        "programma":        programma,
    }

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ schedule.json geschreven: {len(uitslagen)} uitslagen, {len(programma)} programma")

if __name__ == "__main__":
    main()
