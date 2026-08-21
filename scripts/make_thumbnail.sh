#!/usr/bin/env bash
# Render the Devpost gallery thumbnail. 3:2, which the gallery card expects.
#
# Rendered at 1200x1000 and cropped rather than shot at 1200x800 directly:
# headless Chrome clips content sitting near the bottom edge when the window
# height exactly matches the body height, which silently drops the footer.
set -euo pipefail
cd "$(dirname "$0")/.."

google-chrome --headless --disable-gpu --no-sandbox --hide-scrollbars \
  --window-size=1200,1000 --screenshot=/tmp/praetor-thumb-tall.png \
  --virtual-time-budget=6000 "file://$PWD/docs/thumbnail.html" >/dev/null 2>&1

convert /tmp/praetor-thumb-tall.png -crop 1200x800+0+0 +repage docs/thumbnail.png
rm -f /tmp/praetor-thumb-tall.png
echo "docs/thumbnail.png written ($(stat -c%s docs/thumbnail.png) bytes)"
