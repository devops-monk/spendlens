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
for theme in light dark; do
  sed "s/<html lang=\"en\">/<html lang=\"en\" data-theme=\"$theme\">/" "$WORK/dash.html" > "$WORK/dash-$theme.html"
done
mkdir -p "$OUT"

while read -r name tab x y w h; do
  [ -z "$name" ] && continue
  for theme in light dark; do
    printf '<html><body style="margin:0;overflow:hidden"><iframe src="dash-%s.html#%s" style="position:absolute;left:-%spx;top:-%spx;width:1280px;height:3400px;border:0"></iframe></body></html>' \
      "$theme" "$tab" "$x" "$y" > "$WORK/crop.html"
    "$CHROME" --headless=new --disable-gpu --hide-scrollbars --allow-file-access-from-files \
      --user-data-dir="$WORK/profile" --force-device-scale-factor=2 --window-size="$w,$h" \
      --virtual-time-budget=3000 --screenshot="$WORK/$name-$theme.png" "file://$WORK/crop.html" >/dev/null 2>&1
    cwebp -quiet -q 84 -m 6 "$WORK/$name-$theme.png" -o "$OUT/$name-$theme.webp"
  done
  echo "  $name"
done <<'EOF'
hero overview 0 0 1280 860
kpis overview 50 138 1180 306
cost cost 50 138 1180 576
trend cost 50 698 1180 512
savings savings 50 138 1180 560
tags tags 50 138 1180 470
forecast forecast 50 138 1180 305
showback showback 50 138 1180 349
waste waste 50 138 1180 520
EOF
echo "Wrote $(find "$OUT" -name "*.webp" | wc -l | tr -d ' ') files to ${OUT#"$ROOT"/}"
