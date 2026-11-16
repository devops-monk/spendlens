# Running spendlens on a schedule (GitLab CI / GitHub Actions)

```bash
spendlens init --ci gitlab     # writes .gitlab-ci.yml
spendlens init --ci github     # writes .github/workflows/spendlens.yml
```

Each run does the following:
1. Assumes a read-only AWS role through **OIDC**, so no AWS keys are stored in CI.
2. Restores the history DB.
3. Runs `spendlens report`. This runs every check, writes `reports/*.json` and `reports/report.html`, and saves each run to history.
4. Saves the history DB.
5. Posts a summary to Slack and/or Teams.
6. Keeps `reports/` as a build artifact for 30 days.

## 1. AWS role (one time)

1. Create an **IAM OIDC identity provider**:
   - **GitLab:** URL `https://gitlab.com` (or your self-managed GitLab URL), audience `sts.amazonaws.com`.
   - **GitHub:** URL `https://token.actions.githubusercontent.com`, audience `sts.amazonaws.com`.
2. Create a role (e.g. `spendlens-readonly`):
   - Attach `ReadOnlyAccess` plus the cost statement in [permissions.md](permissions.md).
   - If you use S3 history, also add `s3:GetObject` / `s3:PutObject` on the history object.
3. Use a trust policy restricted to your project and default branch:

GitLab:
```json
{
  "Effect": "Allow",
  "Principal": {"Federated": "arn:aws:iam::<ACCOUNT>:oidc-provider/gitlab.com"},
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {"gitlab.com:aud": "sts.amazonaws.com"},
    "StringLike": {"gitlab.com:sub": "project_path:<group>/<project>:ref_type:branch:ref:main"}
  }
}
```

GitHub:
```json
{
  "Effect": "Allow",
  "Principal": {"Federated": "arn:aws:iam::<ACCOUNT>:oidc-provider/token.actions.githubusercontent.com"},
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
    "StringLike": {"token.actions.githubusercontent.com:sub": "repo:<org>/<repo>:ref:refs/heads/main"}
  }
}
```

For several accounts, configure a Steampipe aggregator connection in the CI image and set `steampipe.connection` in `finops.yaml`. Alternatively, run one job per account with a different role.

## 2. CI variables

| Variable | Where | Purpose |
|---|---|---|
| `AWS_ROLE_ARN` | GitLab variable / GitHub repo variable | Role to assume through OIDC |
| `AWS_REGION` | same | Default region (Cost Explorer is global; resource checks cover the regions in your Steampipe config) |
| `SPENDLENS_HISTORY_S3` | same, optional | `s3://<bucket>/spendlens/history.db`. Keeps trends and `history diff` across runs. Without it, the CI cache is used, which can be evicted |
| `SPENDLENS_SLACK_WEBHOOK` | masked variable / secret, optional | Slack incoming-webhook URL |
| `SPENDLENS_TEAMS_WEBHOOK` | masked variable / secret, optional | Teams Workflows webhook URL ("Post to a channel when a webhook request is received") |

Webhook URLs are read from environment variables only, never from `finops.yaml`, so they stay out of the repository. `spendlens notify` refuses non-https URLs and URLs with embedded credentials.

## 3. Schedule

- **GitLab:** Build → Pipeline schedules, e.g. `0 6 * * 1-5`.
- **GitHub:** the workflow already has a weekday 06:00 UTC cron and a manual trigger.

## Notes

- **Steampipe refuses to run as root.** The GitLab job therefore installs tools as root and runs the checks as a `finops` user. GitHub runners are already non-root.
- **Failed steps:** `spendlens report` continues past a failing step (for example missing Cost Optimization Hub permissions), writes everything it can, and exits 1 so the pipeline shows the failure. Alarms are findings, not failures.
- **Gating:** to fail a pipeline on policy violations, add a step such as `spendlens tags --source last -k mandatory --fail-on-alarm`. The GitLab template has a commented example.
- **Quiet runs:** `spendlens notify --only-if-changes` posts only when something needs attention: a flagged cost change, an anomaly, a forecast over budget, or new alarms since the previous run.
