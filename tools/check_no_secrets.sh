#!/usr/bin/env bash
# Sensitive-data audit for git changes.
#
# Usage:
#   tools/check_no_secrets.sh staged    -> scans `git diff --cached` (pre-commit)
#   tools/check_no_secrets.sh outgoing  -> scans commits ahead of upstream (pre-push)
#   tools/check_no_secrets.sh diff <args...>  -> scans an explicit git diff
#
# Exits 0 if nothing suspicious is found, 1 (BLOCKS the operation) if a secret
# pattern, sensitive file extension, or banned content shows up.
#
# Override (use sparingly): set GIT_CHECK_NO_SECRETS=skip in the environment
# to bypass. Document why in the commit message.
#
# This script has no external dependencies beyond git and POSIX grep.

set -u

if [[ "${GIT_CHECK_NO_SECRETS:-}" == "skip" ]]; then
    echo "[check_no_secrets] SKIP requested via GIT_CHECK_NO_SECRETS=skip" >&2
    exit 0
fi

MODE="${1:-staged}"
shift || true

# ── 1. Get the set of changed files + the changed lines ─────────────────────

case "$MODE" in
    staged)
        FILES=$(git diff --cached --name-only --diff-filter=ACMRT)
        DIFF=$(git diff --cached -U0)
        ;;
    outgoing)
        # Compare HEAD against its upstream (or origin/main if none configured)
        UPSTREAM=$(git rev-parse --abbrev-ref @{upstream} 2>/dev/null || echo "origin/main")
        FILES=$(git diff --name-only --diff-filter=ACMRT "$UPSTREAM"..HEAD)
        DIFF=$(git diff -U0 "$UPSTREAM"..HEAD)
        ;;
    diff)
        FILES=$(git diff --name-only --diff-filter=ACMRT "$@")
        DIFF=$(git diff -U0 "$@")
        ;;
    *)
        echo "[check_no_secrets] unknown mode: $MODE" >&2
        echo "Usage: $0 {staged|outgoing|diff [git diff args]}" >&2
        exit 2
        ;;
esac

if [[ -z "$FILES" && -z "$DIFF" ]]; then
    echo "[check_no_secrets] no changes to scan ($MODE mode)" >&2
    exit 0
fi

HITS=()

add_hit() {
    HITS+=("$1")
}

# ── 2. Banned file extensions / names ────────────────────────────────────────

BANNED_RE='\.(pem|key|p12|pfx|crt|cer|pkcs12|jks|keystore|env|env\.[a-z]+|pdf|db|sqlite|sqlite3)$|(^|/)(id_rsa|id_dsa|id_ed25519|id_ecdsa)(\.|$)|(^|/)credentials(\.json|\.toml|\.ini|\.yml|\.yaml)?$|(^|/)\.aws/|(^|/)\.gcp/|(^|/)\.azure/|(^|/)service-account.*\.json$'

while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if printf '%s\n' "$f" | grep -qE "$BANNED_RE"; then
        add_hit "Banned file: $f"
    fi
done <<< "$FILES"

# ── 3. Secret-shape patterns inside the diff ─────────────────────────────────
# Only added lines (start with `+` but not `+++` file headers). We strip
# changes that come from the audit script itself or its README — those files
# document every pattern they detect, so they'd always self-trigger.

EXCLUDED_PATHS_RE='tools/check_no_secrets\.sh|tools/README\.md|tools/git-hooks/'

ADDED=$(printf '%s\n' "$DIFF" | awk -v re="$EXCLUDED_PATHS_RE" '
    /^diff --git/ { skip = ($0 ~ re); next }
    skip          { next }
    /^\+\+\+ /    { next }
    /^\+/         { print }
')

# Anthropic-style keys (the live shape Anthropic issues)
if printf '%s\n' "$ADDED" | grep -qE 'sk-ant-[A-Za-z0-9_-]{20,}'; then
    add_hit "Anthropic-style API key pattern (sk-ant-...)"
fi

# OpenAI project key
if printf '%s\n' "$ADDED" | grep -qE 'sk-proj-[A-Za-z0-9_-]{20,}'; then
    add_hit "OpenAI project key pattern (sk-proj-...)"
fi

# Generic sk- bearer key (must come AFTER the more specific sk-ant / sk-proj
# checks, so the message attribution is right)
if printf '%s\n' "$ADDED" | grep -qE 'sk-[A-Za-z0-9]{20,}'; then
    add_hit "Generic bearer key pattern (sk-...)"
fi

# AWS access key id
if printf '%s\n' "$ADDED" | grep -qE 'AKIA[0-9A-Z]{16}'; then
    add_hit "AWS access key id (AKIA...)"
fi

# AWS secret access key (40-char base64-ish)
if printf '%s\n' "$ADDED" | grep -qE 'aws_secret_access_key[[:space:]]*=[[:space:]]*[A-Za-z0-9/+=]{40}'; then
    add_hit "AWS secret access key assignment"
fi

# PEM private key block. The regex starts with dashes so grep would read it
# as a CLI option; -e protects it.
if printf '%s\n' "$ADDED" | grep -qE -e '-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----'; then
    add_hit "PEM private key block"
fi

# GitHub fine-grained PAT
if printf '%s\n' "$ADDED" | grep -qE 'github_pat_[A-Za-z0-9_]{20,}'; then
    add_hit "GitHub fine-grained personal access token"
fi

# GitHub classic PAT
if printf '%s\n' "$ADDED" | grep -qE 'ghp_[A-Za-z0-9]{30,}'; then
    add_hit "GitHub classic personal access token (ghp_...)"
fi

# Slack token
if printf '%s\n' "$ADDED" | grep -qE 'xox[abprs]-[A-Za-z0-9-]{20,}'; then
    add_hit "Slack token (xox?-...)"
fi

# Stripe live secret key
if printf '%s\n' "$ADDED" | grep -qE 'sk_live_[A-Za-z0-9]{20,}'; then
    add_hit "Stripe live secret key (sk_live_...)"
fi

# ── 4. ANTHROPIC_API_KEY=<non-placeholder> assignments ───────────────────────
# Hardcoded env-var assignments where the right-hand side isn't an obvious
# placeholder. Pattern: `KEY=` followed by something other than empty, sk-...,
# or a quoted "sk-...".
ENV_ASSIGN_LINE=$(printf '%s\n' "$ADDED" | \
    grep -oE '(ANTHROPIC_API_KEY|OPENAI_API_KEY|ADZUNA_APP_KEY|ADZUNA_APP_ID)[[:space:]]*=[[:space:]]*[^[:space:]]+' || true)

if [[ -n "$ENV_ASSIGN_LINE" ]]; then
    # Filter out placeholder / commented values. The RHS is treated as a
    # placeholder if it's empty, a single non-alphanumeric "doc dot"
    # (`…`, `…`, `...`, `xxx`, `***`), wrapped in `<...>` / `${...}`, or starts
    # with a known placeholder word.
    SUSPECT=$(printf '%s\n' "$ENV_ASSIGN_LINE" | grep -vE \
        '=("?\$?\{|"?<|=…|=\.+|=\*+|=x{3,}|=X{3,}|placeholder|YOUR_|your_|TODO|TBD|REPLACE|""$|"\$"$|=$)' || true)
    if [[ -n "$SUSPECT" ]]; then
        add_hit "API-key env assignment with non-placeholder value:"
        while IFS= read -r line; do
            add_hit "  $line"
        done <<< "$SUSPECT"
    fi
fi

# ── 5. Generic password / token / secret assignments with literal values ────
if printf '%s\n' "$ADDED" | grep -qE '\b(password|passwd|pwd|secret|token|access_token|auth_token|api_key|apikey)[[:space:]]*[:=][[:space:]]*["\x27][^"\x27]{6,}["\x27]'; then
    SAMPLE=$(printf '%s\n' "$ADDED" | grep -E '\b(password|passwd|pwd|secret|token|access_token|auth_token|api_key|apikey)[[:space:]]*[:=][[:space:]]*["\x27][^"\x27]{6,}["\x27]' | head -3)
    # Suppress hits where the value is an obvious placeholder / test fixture
    SAMPLE=$(printf '%s\n' "$SAMPLE" | grep -viE '(test|fake|mock|dummy|example|placeholder|fixture|YOUR_|TODO|TBD)' || true)
    if [[ -n "$SAMPLE" ]]; then
        add_hit "Possible literal credential assignment:"
        while IFS= read -r line; do
            add_hit "  $line"
        done <<< "$SAMPLE"
    fi
fi

# ── 6. Notebook outputs that look like PII ───────────────────────────────────
# `.ipynb` files store cell outputs in JSON. Detect newly-added or modified
# notebooks AND check if the file currently contains an "outputs" array with
# non-trivial content. We treat ANY non-empty outputs array as a flag because
# committed notebooks should be cleared.
while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if [[ "$f" == *.ipynb && -f "$f" ]]; then
        if grep -qE '"outputs"[[:space:]]*:[[:space:]]*\[[[:space:]]*\{' "$f" 2>/dev/null; then
            add_hit "Notebook with non-empty cell outputs: $f (clear outputs before committing)"
        fi
    fi
done <<< "$FILES"

# ── 7. Report ────────────────────────────────────────────────────────────────

if (( ${#HITS[@]} == 0 )); then
    echo "[check_no_secrets] OK ($MODE mode, $(printf '%s\n' "$FILES" | grep -c .) files scanned)"
    exit 0
fi

echo "" >&2
echo "==================== BLOCKED: possible sensitive content ====================" >&2
echo "" >&2
for hit in "${HITS[@]}"; do
    echo "  - $hit" >&2
done
echo "" >&2
echo "Audit mode: $MODE" >&2
echo "" >&2
echo "If this is a false positive:" >&2
echo "  1. Inspect the staged change. If you are 100% sure there is no leak," >&2
echo "     re-run the operation with GIT_CHECK_NO_SECRETS=skip prefixed:" >&2
echo "        GIT_CHECK_NO_SECRETS=skip git commit ..." >&2
echo "        GIT_CHECK_NO_SECRETS=skip git push ..." >&2
echo "  2. Document why the override was used in the commit message." >&2
echo "" >&2
echo "If this is a real hit:" >&2
echo "  1. \`git restore --staged <file>\` to un-stage the file, OR" >&2
echo "  2. Edit the file to remove the secret + add the path to .gitignore," >&2
echo "  3. Re-stage and re-run the operation." >&2
echo "============================================================================" >&2
exit 1
