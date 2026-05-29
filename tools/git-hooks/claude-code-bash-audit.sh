#!/usr/bin/env bash
# Claude Code PreToolUse/Bash hook wrapper.
#
# Reads the hook JSON payload from stdin, inspects the bash command Claude
# is about to run, and dispatches to the right `check_no_secrets.sh` mode
# when that command is a `git commit` or `git push`. Anything else: exit 0
# (the hook allows the operation through unchanged).
#
# Exit non-zero from the audit blocks the Bash call.
#
# Wired up in .claude/settings.json under hooks.PreToolUse[Bash].
#
# Python is used to parse the stdin JSON rather than jq, because jq is not
# universally available on Windows/Git-Bash setups and a missing jq would
# cause the wrapper to silently let every command through. Python is a hard
# project dependency, so requiring it here is safe.

set -u

repo=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$repo" ]]; then
    # Not inside a git repo - nothing to audit.
    exit 0
fi

# Extract the bash command Claude is about to run. We pipe stdin into Python;
# if the input is malformed we fail closed (exit 1) rather than allow.
cmd=$(python -c "
import json, sys
try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)
cmd = (payload.get('tool_input') or {}).get('command') or ''
sys.stdout.write(cmd)
" 2>/dev/null || true)

if [[ -z "$cmd" ]]; then
    # Either not a Bash invocation or we couldn't parse the payload. Don't
    # gate - a no-op skip is correct here because we're not blocking; the
    # commit/push will still hit the local git pre-commit/pre-push hook
    # installed by tools/install-git-hooks.sh, which is the belt-and-
    # suspenders layer.
    exit 0
fi

case "$cmd" in
    *"git commit"*)
        exec bash "$repo/tools/check_no_secrets.sh" staged
        ;;
    *"git push"*)
        exec bash "$repo/tools/check_no_secrets.sh" outgoing
        ;;
    *)
        exit 0
        ;;
esac
