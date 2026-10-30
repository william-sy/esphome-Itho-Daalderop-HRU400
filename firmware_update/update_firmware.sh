#!/bin/sh
# Updates firmware_metadata.json and downloads any new firmware, in one go.
#
#   ./update_firmware.sh            asks for your Itho api-key and application-id, then:
#                                   1. fetches the firmware catalog for the product
#                                   2. adds new versions to firmware_metadata.json
#                                   3. downloads missing files into base/ and wifi/
#                                   4. records each file's SHA-256
#                                   5. offers to commit and push (default: no)
#
# The key is typed hidden, never written to disk, and never shown in the process list.
# Needs: python3, curl. Optional first argument: article number (default 03-00632).
set -eu
cd "$(dirname "$0")"

ARTICLE="${1:-03-00632}"
URL="https://itho-tussenlaag.bettywebblocks.com/api/product/${ARTICLE}/display_set"
CATALOG="$(mktemp)"
restore() { [ -t 0 ] && stty echo 2>/dev/null || true; rm -f "$CATALOG"; }
trap restore EXIT INT TERM

printf 'Itho api-key (hidden): '
[ -t 0 ] && stty -echo
read -r KEY
[ -t 0 ] && stty echo
echo
printf 'Itho application-id: '
read -r APPID
[ -n "$KEY" ] && [ -n "$APPID" ] || { echo "Both the api-key and the application-id are required."; exit 1; }

echo "Fetching catalog for ${ARTICLE}..."
# Headers go in through stdin so the key does not appear in `ps`.
CODE=$(printf 'header = "api-key: %s"\nheader = "application-id: %s"\nheader = "Accept: application/json"\nheader = "Accept-Language: en-US"\nheader = "X-Requested-With: XMLHttpRequest"\n' "$KEY" "$APPID" \
  | curl -sS -m 40 -K - -o "$CATALOG" -w '%{http_code}' "$URL") || { echo "Could not reach the Itho server."; exit 1; }
unset KEY APPID

if [ "$CODE" != "200" ]; then
  echo "The Itho server answered HTTP $CODE. Check the key and application-id."
  exit 1
fi

python3 -I update_metadata.py --catalog "$CATALOG" --fetch

echo
git status --short . 2>/dev/null || true
echo
printf 'Commit and push these changes now? [y/N] '
read -r ANSWER
case "$ANSWER" in
  y|Y|yes|YES)
    git add -A .
    git commit -m "Update firmware metadata and files"
    git push
    echo "Pushed. In the app, tap 'Check GitHub for firmware'."
    ;;
  *)
    echo "Not pushed. When ready: git add -A firmware_update && git commit && git push"
    ;;
esac
