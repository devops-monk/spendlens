#!/usr/bin/env bash
# Regenerate the website's dashboard close-ups (light + dark, 2x WebP) from the bundled demo data.
#
#   scripts/capture_screenshots.sh            # writes website/static/shots/<name>-{light,dark}.webp
#
# Needs: Google Chrome (headless), cwebp. Uses --source demo only: no AWS calls.
# Crops are CSS-pixel rectangles of the dashboard at a 1280px-wide viewport (x y width height);
# update them if the dashboard layout changes, then check the result on the home page.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
OUT="$ROOT/website/static/shots"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

command -v cwebp >/dev/null || { echo "cwebp is required (brew install webp)" >&2; exit 1; }
[ -x "$CHROME" ] || { echo "Chrome not found; set CHROME=/path/to/chrome" >&2; exit 1; }

"$ROOT/.venv/bin/spendlens" dashboard --source demo -o "$WORK/dash.html" >/dev/null
# the executive brief with its rule-based summary (--no-ai: nothing invented, nothing sent to an AI provider)
SPENDLENS_NO_PROGRESS=1 "$ROOT/.venv/bin/spendlens" brief --source demo --no-ai -o "$WORK/brief.html" >/dev/null 2>&1
for theme in light dark; do
  sed "s/<html lang=\"en\">/<html lang=\"en\" data-theme=\"$theme\">/" "$WORK/dash.html" > "$WORK/dash-$theme.html"
  sed "s/<html lang=\"en\">/<html lang=\"en\" data-theme=\"$theme\">/" "$WORK/brief.html" > "$WORK/brief-$theme.html"
done
mkdir -p "$OUT"

while read -r name tab x y w h; do
  [ -z "$name" ] && continue
  page=dash; [ "$tab" = "brief" ] && page=brief
  for theme in light dark; do
    printf '<html><body style="margin:0;overflow:hidden"><iframe src="%s-%s.html#%s" style="position:absolute;left:-%spx;top:-%spx;width:1280px;height:3400px;border:0"></iframe></body></html>' \
      "$page" "$theme" "$tab" "$x" "$y" > "$WORK/crop.html"
    "$CHROME" --headless=new --disable-gpu --hide-scrollbars --allow-file-access-from-files \
      --user-data-dir="$WORK/profile" --force-device-scale-factor=2 --window-size="$w,$h" \
      --virtual-time-budget=3000 --screenshot="$WORK/$name-$theme.png" "file://$WORK/crop.html" >/dev/null 2>&1
    cwebp -quiet -q 84 -m 6 "$WORK/$name-$theme.png" -o "$OUT/$name-$theme.webp"
  done
  echo "  $name"
done <<'EOF'
hero overview 0 0 1280 1000
kpis overview 54 288 1172 377
cost cost 54 231 1172 703
trend cost 54 914 1172 597
savings savings 54 231 1172 640
tags tags 54 231 1172 560
forecast forecast 54 231 1172 391
showback showback 54 231 1172 429
waste waste 54 231 1172 600
brief brief 54 170 1172 840
EOF
echo "Wrote $(find "$OUT" -name "*.webp" | wc -l | tr -d ' ') files to ${OUT#"$ROOT"/}"
