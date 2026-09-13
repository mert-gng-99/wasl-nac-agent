#!/usr/bin/env bash
# Create the GitHub repository for this submission and push it.
#
#   bash scripts/init-repo.sh            uses the default name below
#   bash scripts/init-repo.sh my-name    uses your own
#
# Needs the GitHub CLI, authenticated:  gh auth login
set -euo pipefail

REPO="${1:-wasl-nac-agent}"
cd "$(dirname "$0")/.."

if [ ! -d .git ]; then
  git init -b main
fi

git add -A
if git diff --cached --quiet; then
  echo "Nothing to commit."
else
  git commit -m "Wasl: MENA Ignite prototype phase submission

AI agent layer orchestrating 8 CAMARA API families on the Nokia
Network-as-Code platform, with a costed tool registry, a consent gate in the
transport path, and an evidence ledger behind every decision."
fi

if command -v gh >/dev/null 2>&1; then
  gh repo create "$REPO" --public --source=. --remote=origin --push
  echo
  echo "Repository URL:  $(gh repo view "$REPO" --json url -q .url)"
  echo "Paste that into the Repository URL field."
else
  echo "GitHub CLI not found. Create the repo in the browser, then:"
  echo "  git remote add origin https://github.com/<you>/$REPO.git"
  echo "  git push -u origin main"
fi
