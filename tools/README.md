# tools/ — repo-level helper scripts

## Pre-commit / pre-push secret audit

`check_no_secrets.sh` scans a git change for plausible secrets, sensitive file
extensions, and notebook outputs that might carry PII. Exits 1 (blocks the
operation) on a hit; exits 0 if the diff is clean.

It is invoked automatically by two git hooks once you install them on a clone:

  - `git-hooks/pre-commit` runs the audit on the staged diff.
  - `git-hooks/pre-push` runs the audit on the commits ahead of the upstream.

### Install (one time per clone)

```bash
bash tools/install-git-hooks.sh
```

The installer points this clone's `core.hooksPath` at `tools/git-hooks/` so
both hooks fire automatically on every `git commit` and `git push`.

### What gets scanned

  - **File extensions / paths**: `.pem`, `.key`, `.p12`, `.pfx`, `.crt`,
    `.cer`, `.pkcs12`, `.jks`, `.keystore`, `.env`, `.pdf`, `.db`, `.sqlite`,
    SSH key names (`id_rsa`, `id_dsa`, `id_ed25519`, `id_ecdsa`),
    `credentials.*`, `.aws/`, `.gcp/`, `.azure/`, `service-account*.json`.
  - **Secret-shape regex on added lines**: Anthropic `sk-ant-…`,
    OpenAI `sk-proj-…`, generic `sk-…`, AWS `AKIA…` + `aws_secret_access_key=…`,
    PEM `-----BEGIN PRIVATE KEY-----`, GitHub `github_pat_…` and `ghp_…`,
    Slack `xox?-…`, Stripe `sk_live_…`.
  - **Env-var assignments with non-placeholder values**:
    `ANTHROPIC_API_KEY=…`, `OPENAI_API_KEY=…`, `ADZUNA_APP_KEY=…`,
    `ADZUNA_APP_ID=…`. Lines whose RHS is an obvious placeholder (`<…>`,
    `YOUR_…`, `TODO`, `TBD`, `${…}`, empty, etc.) are excluded.
  - **Generic credential assignments**: `password=`, `secret=`, `token=`,
    `api_key=` with a quoted literal value longer than 6 chars. Lines
    containing `test|fake|mock|dummy|example|placeholder|fixture` are
    excluded.
  - **Notebook outputs**: any `.ipynb` whose cell outputs aren't empty.
    Clear notebook outputs before committing.

### Bypass (emergencies only)

```bash
GIT_CHECK_NO_SECRETS=skip git commit ...
GIT_CHECK_NO_SECRETS=skip git push ...
```

Document the reason in the commit message. The override is intentional
friction — easy to type, easy to grep for, hard to do by accident.

### Standalone runs

```bash
bash tools/check_no_secrets.sh staged    # scan currently-staged changes
bash tools/check_no_secrets.sh outgoing  # scan commits ahead of upstream
bash tools/check_no_secrets.sh diff HEAD~3..HEAD   # scan an explicit range
```
