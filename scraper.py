"""
DBL Scraper — uitslagen + programma in één run, één JSON.
De site toont altijd alle wedstrijden ongeacht week-URL.
Filter op datum: verleden = uitslagen, toekomst = programma.
"""

import json, os, re, datetime as dt
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

def main():
    today = (dt.datetime.now(timezone.utc) + timedelta(hours=2)).date()
    print(f"Vandaag (CEST): {today}")

    uitslagen = []
    programma = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",
            locale="de-DE", viewport={"width": 1280, "height": 800},
        ).new_page()

        # Één pagina ophalen — site toont toch altijd alle wedstrijden
        print(f"Laden: {BASE_URL}")
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("div.game", timeout=12000)
        raw = page.evaluate(EXTRACT_JS)
        print(f"→ {len(raw)} wedstrijden gevonden")

        browser.close()

    seen = set()
    for r in raw:
        h, a = r.get("homeTeam"), r.get("awayTeam")
        if not h or not a: continue

        # Datum bepalen
        game_date = parse_date_str(r.get("dateStr"))
        time_str  = r.get("time")
        if not game_date and r.get("timestamp"):
            d = dt.datetime.fromtimestamp(r["timestamp"] / 1000, tz=timezone.utc) + timedelta(hours=2)
            game_date = d.date()
            if not time_str: time_str = d.strftime("%H:%M")

        if not game_date: continue

        key = (h, a, str(game_date))
        if key in seen: continue
        seen.add(key)

        if game_date < today:
            # Uitslag: alleen als er scores zijn
            score_t = parse_score(r.get("homeScoreRaw"))
            score_u = parse_score(r.get("awayScoreRaw"))
            if score_t is None and score_u is None: continue
            uitslagen.append({
                "datum":       str(game_date),
                "datum_str":   r.get("dateStr"),
                "tijdstip":    time_str,
                "thuis":       h, "uit": a,
                "score_thuis": score_t,
                "score_uit":   score_u,
                "locatie":     r.get("location"),
                "divisie":     r.get("division"),
                "gespeeld":    True,
                "live":        r.get("state") == "live",
            })
        else:
            # Programma: toekomstige wedstrijden
            programma.append({
                "datum":     str(game_date),
                "datum_str": r.get("dateStr"),
                "tijdstip":  time_str,
                "thuis":     h, "uit": a,
                "locatie":   r.get("location"),
                "divisie":   r.get("division"),
            })

    uitslagen = sorted(uitslagen, key=lambda g: (g["datum"], g["tijdstip"] or ""))
    programma  = sorted(programma,  key=lambda g: (g["datum"], g["tijdstip"] or ""))

    print(f"Uitslagen: {len(uitslagen)}, Programma: {len(programma)}")
    for u in uitslagen[-3:]:
        print(f"  uitslag: {u['datum']} {u['uit']} {u['score_uit']}–{u['score_thuis']} {u['thuis']}")
    for p in programma[:3]:
        print(f"  programma: {p['datum']} {p['uit']} @ {p['thuis']}")

    now_str = dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "bijgewerkt": now_str,
        "uitslagen":  uitslagen,
        "programma":  programma,
    }

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ schedule.json geschreven: {len(uitslagen)} uitslagen, {len(programma)} programma")

if __name__ == "__main__":
    main()
