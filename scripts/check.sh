#!/bin/sh
# Run before EVERY push: sh scripts/check.sh   (green = safe to push)
set -e
git pull --rebase
python -m pytest
echo ""
echo "GREEN. Safe to push. Push your data/ and out/ files with: git add -A data out src && git commit && git push"
echo "SKIPPED lines above = parts not built yet. At 4:30 only should-ship/stretch items may still be skipped."
