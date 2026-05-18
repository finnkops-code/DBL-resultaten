# DBL-resultaten

Widget voor wedstrijduitslagen en aankomende wedstrijden van de **Deutsche Baseball Liga**.

Haalt data op van [baseball.de](https://www.baseball.de/saison/spielplaene) en toont deze via een WordPress shortcode.

## Structuur

```
DBL-resultaten/
├── scraper/
│   └── fetch_games.py       # Scrapet baseball.de → data/games.json
├── data/
│   └── games.json           # Gegenereerde wedstrijddata (per week)
└── wordpress/
    ├── dbl-widget.php        # WordPress shortcode plugin
    └── dbl-widget.js         # Frontend widget (JS)
```

## Installatie

### 1. Scraper

Vereisten: Python 3.8+

```bash
pip install requests beautifulsoup4
python scraper/fetch_games.py
```

De scraper schrijft de wedstrijddata weg naar `data/games.json`.

### 2. WordPress plugin

1. Kopieer `wordpress/dbl-widget.php` naar `wp-content/plugins/dbl-widget/dbl-widget.php`
2. Kopieer `wordpress/dbl-widget.js` naar dezelfde map
3. Upload `data/games.json` naar een publiek toegankelijke locatie (bijv. je theme-map of een aparte upload-map)
4. Pas de `$json_url` in `dbl-widget.php` aan naar het juiste pad
5. Activeer de plugin in WordPress
6. Gebruik de shortcode `[dbl_resultaten]` op elke pagina of post

### 3. Automatisch updaten (optioneel)

Voeg een cronjob toe op je server om de scraper dagelijks uit te voeren:

```bash
0 6 * * * /usr/bin/python3 /pad/naar/scraper/fetch_games.py
```

## Shortcode opties

| Optie | Standaard | Omschrijving |
|-------|-----------|--------------|
| `week` | huidige week | ISO-weeknummer |
| `year` | huidig jaar | Seizoensjaar |
| `team` | (alle) | Filter op teamnaam |

Voorbeeld: `[dbl_resultaten week="22" year="2026"]`

## Licentie

MIT
