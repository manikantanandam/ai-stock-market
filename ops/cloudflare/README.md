# On-time scan trigger (Cloudflare Worker)

GitHub's scheduled workflows often start hours late. This Worker's Cron Triggers call GitHub's
`workflow_dispatch` API at the scheduled minute, so the scans start within seconds (decision 0019):

| Cron (UTC)          | IST   | Workflow             |
|---------------------|-------|----------------------|
| `17 11 * * MON-FRI` | 16:47 | `daily-scan.yml`     |
| `17 22 * * MON-FRI` | 03:47 | `commodity-scan.yml` |

Runs are dispatched with `gated: true`, so they skip when `main` already has the session. The GitHub
schedules stay as a fallback and skip in turn once the Worker's run has landed.

## Setup (once, free plan)

1. **GitHub token.** GitHub > Settings > Developer settings > Fine-grained tokens > Generate:
   repository access *Only select repositories* > `ai-stock-market`; permissions *Actions: Read and
   write* (nothing else). Pick an expiry and note it: the trigger stops when the token expires.
2. **Deploy.** From this folder:
   ```sh
   npx wrangler login
   npx wrangler deploy
   npx wrangler secret put GITHUB_TOKEN   # paste the token
   ```
3. **Check.** Cloudflare dashboard > Workers > `aistock-dispatch` > Settings > Triggers shows both crons.
   To test without waiting, run `npx wrangler dev --test-scheduled` and open
   `http://localhost:8787/__scheduled?cron=17+22+*+*+MON-FRI`: a gated Commodity scan run appears in
   GitHub Actions (it skips if `main` already has the session). Logs: `npx wrangler tail`.

## Changing a time
Edit the cron in both `wrangler.toml` and `WORKFLOWS` in `dispatch.js`, then `npx wrangler deploy`.
Keep the GitHub `schedule:` in the workflow in step as the fallback.

## Renewing the token
Generate a new token as in step 1 and run `npx wrangler secret put GITHUB_TOKEN` again.
