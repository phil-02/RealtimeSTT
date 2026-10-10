#!/bin/bash
# Bring in upstream (KoljaB/RealtimeSTT) changes without risking local work.
#
# master is kept an exact copy of upstream, so it only ever fast-forwards.
# The dictation branch (type.py and friends) then merges master. Never
# commit to master: that is what would make syncing conflict.
set -euo pipefail
cd "$(dirname "$0")"

if [ "$(git branch --show-current)" != "dictation" ]; then
    echo "Switch to the dictation branch first: git switch dictation"
    exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "You have uncommitted changes. Commit them first, then re-run."
    exit 1
fi

# Fast-forward master to upstream without checking it out (checking it out
# would hide type.py from the working tree). Refuses if master has diverged.
git fetch upstream master:master
git push origin master

before=$(git rev-parse HEAD)
git merge --no-edit master
if [ "$(git rev-parse HEAD)" = "$before" ]; then
    echo "Already up to date."
    exit 0
fi

if ! .venv/bin/python -m unittest tests.unit.test_portal_keyboard \
        tests.unit.test_type_script_accuracy_contract; then
    echo
    echo "Dictation tests failed after the merge. To undo it:"
    echo "  git reset --hard $before"
    exit 1
fi
.venv/bin/python type.py --check

git push origin dictation
echo
echo "Synced. Restart type.sh to use the new code."
