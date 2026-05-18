name: DBL Schedule Scraper
on:
  schedule:
    - cron: '0 * * * *'
    - cron: '0 22 * * 5'
    - cron: '0 17 * * 6'
    - cron: '0 22 * * 6'
    - cron: '0 16 * * 0'
  workflow_dispatch:
permissions:
  contents: write
jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Dependencies installeren
        run: |
          pip install playwright
          playwright install chromium --with-deps
      - name: Scraper uitvoeren
        run: python scraper.py
      - name: JSON committen
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add schedule.json
          git diff --staged --quiet || git commit -m "Schedule bijgewerkt: $(date -u '+%Y-%m-%d %H:%M UTC')"
          git push
