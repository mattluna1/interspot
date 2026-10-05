name: RECOLECTOR BTC interspot

permissions:
  contents: write

on:
  schedule:
    - cron: '2,7,12,17,22,27,32,37,42,47,52,57 * * * *'
  workflow_dispatch:

concurrency:
  group: interspot-push
  cancel-in-progress: false

jobs:
  monitor:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Check syntax
        run: |
          python -m py_compile recolector_btc.py
          echo "✅ Sintaxis OK"

      - name: Run RECOLECTOR BTC
        run: python recolector_btc.py

      - name: Commit cache
        if: always()
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"

          if [ -d data/cache ]; then
            git add data/cache/
          fi

          if git diff --staged --quiet; then
            echo "📭 Sin cambios."
          else
            git commit -m "Recolector BTC [skip ci] $(date -u +'%Y-%m-%d %H:%M UTC')"
            for i in 1 2 3; do
              git pull --rebase origin main && break
              sleep 5
            done
            for i in 1 2 3; do
              git push && echo "✅ Push OK" && break
              git pull --rebase origin main || true
              sleep 10
            done
          fi
