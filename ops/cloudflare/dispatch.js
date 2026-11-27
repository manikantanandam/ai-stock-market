// Cloudflare Worker that starts the scan workflows on time. GitHub's own cron often runs hours late;
// a Cron Trigger here calls GitHub's workflow_dispatch API instead, and the run starts within seconds.
// `gated` makes the run skip like a scheduled one when main already has the session, so a late GitHub
// cron run after it (kept as a fallback) does nothing. Setup: README.md.

// Cron Trigger (wrangler.toml, UTC) -> workflow it starts
const WORKFLOWS = {
  "17 11 * * MON-FRI": "daily-scan.yml",     // 16:47 IST, after the NSE close
  "17 22 * * MON-FRI": "commodity-scan.yml", // after 17:00 New York all year
};

async function dispatch(workflow, env) {
  const res = await fetch(`https://api.github.com/repos/${env.REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "aistock-dispatch",
    },
    body: JSON.stringify({ ref: "main", inputs: { gated: "true" } }),
  });
  if (res.status !== 204) throw new Error(`${workflow}: HTTP ${res.status} ${await res.text()}`);
}

export default {
  async scheduled(event, env) {
    const workflow = WORKFLOWS[event.cron];
    if (!workflow) throw new Error(`no workflow for cron "${event.cron}"`);
    await dispatch(workflow, env);  // a throw shows the run as failed in the Worker's logs
  },
};
