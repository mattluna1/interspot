name: Recolector BTC

on:
  schedule:
    - cron: '*/5 * * * *'
  workflow_dispatch:

permissions:
  contents: write

# ═══════════════════════════════════════════════════════════════
# GRUPO ÚNICO — no compartir con Multi Smart
# ═══════════════════════════════════════════════════════════════
concurrency:
  group: recolector-btc      # ← ÚNICO, no "multi-smart"
  cancel-in-progress: true

jobs:
  run-recolector:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: 📥 Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: 🐍 Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: 🚀 Run Recolector
        run: |
          echo "========================================"
          echo "📦 INICIO RECOLECTOR"
          date -u
          echo "========================================"

          python -u recolector_btc.py

          echo "========================================"
          echo "🏁 FIN RECOLECTOR"
          date -u
          echo "========================================"

      - name: 💾 Save cache
        if: always()
        timeout-minutes: 3
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

          git add data/ 2>/dev/null || true

          if git diff --cached --quiet; then
            echo "📭 Sin cambios"
            exit 0
          fi

          git commit -m "data: recolector $(date -u +%Y-%m-%dT%H:%M) [skip ci]"

          for i in 1 2 3; do
            echo "🔄 Push intento $i..."
            if git pull --rebase origin main && git push origin HEAD:main; then
              echo "✅ Push OK"
              exit 0
            fi
            echo "⚠️ Push falló, esperando..."
            sleep 10
          done

          echo "❌ Push fallido"
          exit 1
