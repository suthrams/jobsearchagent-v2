#!/usr/bin/env bash
# One-time installer: point this clone at the repo's tracked git hooks.
#
# Run this once per clone. Idempotent.
#
# Uses `core.hooksPath` so the actual hook scripts live in `tools/git-hooks/`
# (tracked, reviewable, identical across clones) rather than `.git/hooks/`
# (untracked, per-clone).

set -e
REPO_ROOT=$(git rev-parse --show-toplevel)

echo "Installing pre-commit and pre-push hooks for this clone..."
git -C "$REPO_ROOT" config core.hooksPath tools/git-hooks

# Make sure the hook scripts are executable (matters on Linux/Mac;
# git for Windows ignores the bit but it's harmless to chmod).
chmod +x "$REPO_ROOT/tools/git-hooks/pre-commit" 2>/dev/null || true
chmod +x "$REPO_ROOT/tools/git-hooks/pre-push" 2>/dev/null || true
chmod +x "$REPO_ROOT/tools/check_no_secrets.sh" 2>/dev/null || true

echo "Done."
echo ""
echo "Verify with:"
echo "  git config --get core.hooksPath        # should print: tools/git-hooks"
echo ""
echo "To bypass (emergencies only, and only after eyeballing the diff):"
echo "  GIT_CHECK_NO_SECRETS=skip git commit ..."
echo "  GIT_CHECK_NO_SECRETS=skip git push ..."
