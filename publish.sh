#!/bin/bash
# One-command commit + push for dashboard changes.
# Usage:  ./publish.sh "your commit message"
#         ./publish.sh           # uses a default message
set -e
cd "$(dirname "$0")"

MSG="${1:-Update dashboard}"

git add -A
if git diff --cached --quiet; then
  echo "nothing to commit — working tree clean"
  exit 0
fi

git commit -m "$MSG"
git push

echo ""
echo "✓ pushed to GitHub. Live in ~30-60s at:"
echo "  https://inesgamboasorensen.github.io/cs-partner-dashboard/"
