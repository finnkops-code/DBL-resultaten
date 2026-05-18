"""
DBL Schedule Scraper — Playwright met klikgedrag

Het probleem: de week-URL parameter werkt niet — de site filtert
wedstrijden via JavaScript na een klik op de weekfilter.
Oplossing: Playwright klikt zelf op de juiste week in de filter.

Strategie:
  1. Laad de pagina eenmalig
  2. Lees alle beschikbare weekfilter-opties uit de DOM
  3. Klik op de meest recente afgelopen week → scrape uitslagen
  4. Klik op de eerstvolgende toekomstige week → scrape programma

Installatie (eenmalig):
    pip install playwright
    playwright install chromium
"""

import json
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

        const badgeEl = card.querySelector('p.game-badge');
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

        const timeEl = card.querySelector('p.game-header-time');
        let time = null, location = null;
        if (timeEl) {
            const m = timeEl.textContent.trim().match(/(\\d{2}:\\d{2})\\s*Uhr,?\\s*(.*)/);
            if (m) { time = m[1]; location = m[2].trim() || null; }
        }

        const homeScoreRaw = card.querySelector('span[data-team-score="home"]')?.textContent.trim() || null;
        const awayScoreRaw = card.querySelector('span[data-team-score="away"]')?.textContent.trim() || null;

        const tooltips = Array.from(card.querySelectorAll('dbl-tooltip[tooltip]'))
            .map(el => el.getAttribute('tooltip').trim());
        const homeTeam = tooltips[0] || null;
        const awayTeam = tooltips[1] || null;

        results.push({ state, timestamp, division, dateStr, time, location,
                       homeTeam, awayTeam, homeScoreRaw, awayScoreRaw });
    });
    return results;
}
"""

# Leest alle beschikbare weekfilter-opties uit de DOM
WEEKS_JS = """
() => {
    const options = [];
    // Zoek de weekfilter — dit zijn klikbare elementen met datumtekst
    // Probeer verschillende selectors die de site kan gebruiken
    const selectors = [
        'li[data-week]',
        '[data-filter-week]',
        '.filter-week li',
        '.spielplan-filter li',
        'ul li a[href*="week"]',
        '[data-week]',
    ];

    for (const sel of selectors) {
        const els = document.querySelectorAll(sel);
        if (els.length > 0) {
            els.forEach(el => {
                options.push({
                    selector: sel,
                    text: el.textContent.trim(),
                    dataWeek: el.getAttribute('data-week'),
                    href: el.querySelector('a')?.href || el.getAttribute('href') || null,
                    outerHTML: el.outerHTML.substring(0, 200),
                });
            });
            break;
        }
    }

    // Fallback: dump alle li elementen in de buurt van "Spielwoche"
    if (options.length === 0) {
        document.querySelectorAll('li').forEach(el => {
            const t = el.textContent.trim();
            if (t.match(/\\d{2}\\.\\d{2}\\.\\d{4}/)) {
                options.push({
                    selector: 'li (fallback)',
                    text: t,
                    dataWeek: el.getAttribute('data-week'),
                    href: el.querySelector('a')?.href || null,
                    outerHTML: el.outerHTML.substring(0, 200),
                });
            }
        });
    }

    return options;
}
"""


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


def parse_date_range(text):
    """Parset '15.05.2026 - 17.05.2026' → (date, date)"""
    dates = re.findall(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if len(dates) >= 2:
        d1 = dt.date(int(dates[0][2]), int(dates[0][1]), int(dates[0][0]))
        d2 = dt.date(int(dates[1][2]), int(dates[1][1]), int(dates[1][0]))
        return d1, d2
    elif len(dates) == 1:
        d = dt.date(int(dates[0][2]), int(dates[0][1]), int(dates[0][0]))
        return d, d
    return None, None


def parse_score(text):
    if not text or str(text).strip() in ("--", "-", "", "?"):
        return None
    try:
        return int(str(text).strip())
    except ValueError:
        return None


def process(raw_games):
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

        if not game_date and timestamp:
            d = dt.datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc) + timedelta(hours=2)
            game_date = d.date()
            if not time_str:
                time_str = d.strftime("%H:%M")

        score_home = parse_score(r.get("homeScoreRaw"))
        score_away = parse_score(r.get("awayScoreRaw"))
        gespeeld   = state in ("played", "live")
        is_live    = state == "live"

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


def scrape_after_click(page, element, label):
    """Klik op een weekfilter element en wacht tot de wedstrijden herladen zijn."""
    print(f"  Klikken op: {label}")
    element.click()
    # Wacht tot de DOM bijgewerkt is na de klik
    page.wait_for_timeout(2000)
    raw   = page.evaluate(EXTRACT_JS)
    games = process(raw)
    played  = sum(1 for g in games if g["gespeeld"])
    planned = sum(1 for g in games if not g["gespeeld"])
    print(f"  → {len(games)} wedstrijden ({played} played, {planned} planned)")
    return games


def main():
    today = (dt.datetime.now(timezone.utc) + timedelta(hours=2)).date()
    print(f"DBL scraper gestart — {dt.datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Vandaag: {today}\n")

    uitslagen      = []
    programma      = []
    uitslagen_week = None
    programma_week = None

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

        # Laad pagina eenmalig
        print(f"Laden: {BASE_URL}")
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("div.game", timeout=12000)

        # Lees beschikbare weekfilters uit
        week_options = page.evaluate(WEEKS_JS)
        print(f"\n{len(week_options)} weekfilter-opties gevonden:")
        for opt in week_options:
            print(f"  [{opt['selector']}] {opt['text'][:60]}")

        # Categoriseer de weekfilters op datum
        afgelopen  = []  # (start_date, end_date, element_index)
        toekomstig = []

        for i, opt in enumerate(week_options):
            start, end = parse_date_range(opt["text"])
            if start and end:
                if end < today:
                    afgelopen.append((start, end, i, opt["text"]))
                elif start > today:
                    toekomstig.append((start, end, i, opt["text"]))

        afgelopen.sort(key=lambda x: x[0], reverse=True)   # meest recent eerst
        toekomstig.sort(key=lambda x: x[0])                 # eerstvolgende eerst

        print(f"\nAfgelopen weken  : {[x[3] for x in afgelopen[:3]]}")
        print(f"Toekomstige weken: {[x[3] for x in toekomstig[:3]]}")

        # Haal de weekfilter elementen op als Playwright-objecten
        # We gebruiken de tekst van het filter om ze te vinden en te klikken
        def get_week_elements():
            """Geeft alle klikbare weekfilter-elementen terug."""
            for sel in ['li[data-week]', '[data-filter-week]', '.filter-week li',
                        '.spielplan-filter li', '[data-week]']:
                els = page.query_selector_all(sel)
                if els:
                    return els
            # Fallback: li met datumpatroon
            all_li = page.query_selector_all('li')
            return [el for el in all_li
                    if re.search(r'\d{2}\.\d{2}\.\d{4}', el.text_content() or '')]

        week_elements = get_week_elements()
        print(f"\n{len(week_elements)} klikbare weekelementen gevonden")

        # --- Uitslagen: klik op meest recente afgelopen week ---
        print("\n=== UITSLAGEN ===")
        for start, end, idx, label in afgelopen[:4]:
            if idx < len(week_elements):
                games  = scrape_after_click(page, week_elements[idx], label)
                played = [g for g in games if g["gespeeld"]]
                if played:
                    uitslagen      = sorted(played, key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))
                    uitslagen_week = {"label": label, "van": str(start), "tot": str(end)}
                    break
            # Refresh elementen na elke klik (DOM kan veranderen)
            week_elements = get_week_elements()

        # --- Programma: klik op eerstvolgende toekomstige week ---
        print("\n=== PROGRAMMA ===")
        week_elements = get_week_elements()
        for start, end, idx, label in toekomstig[:4]:
            if idx < len(week_elements):
                games   = scrape_after_click(page, week_elements[idx], label)
                planned = [g for g in games if not g["gespeeld"]]
                if planned:
                    programma      = sorted(planned, key=lambda g: (g["datum"] or "", g["tijdstip"] or ""))
                    programma_week = {"label": label, "van": str(start), "tot": str(end)}
                    break
            week_elements = get_week_elements()

        browser.close()

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
