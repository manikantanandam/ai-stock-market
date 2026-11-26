"""Dashboard screenshots for PR descriptions (`aistock pr-screenshots`, and the PR screenshots workflow).

`shoot` serves a built site on a free local port and captures each page with headless Chrome. `publish`
commits the images to the orphan `pr-screenshots` branch under pr-<N>/, then rewrites the PR description's
screenshots section (between SECTION_START and SECTION_END) with links pinned to that commit, so GitHub's
image cache never shows an old version.

Pages are URL hashes as the dashboard uses them (`today`, `journal/stocks/INFY.csv?range=1M`). A PR picks
its own with a `<!-- screenshots: <page> <page> ... -->` line anywhere in its description; otherwise
`default_pages` takes Today, Paper accounts, the latest session and a stock a paper account holds.
"""
import csv
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BRANCH = "pr-screenshots"
ORPHAN = BRANCH + "-new"  # local name while creating the branch; deleted afterwards
SECTION_START, SECTION_END = "<!-- screenshots:start -->", "<!-- screenshots:end -->"
# not inside backticks: descriptions (and the bot's own section) quote the syntax as an example
PAGES_LINE = re.compile(r"(?<!`)<!--\s*screenshots:\s*(.*?)\s*-->(?!`)")
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WIDTH, HEIGHT = 1400, 2000
BUDGET_MS = 40_000  # virtual time for data loads and charts; Today needs the most


def chrome() -> str:
    """The Chrome binary: $CHROME, the macOS app, or one on PATH."""
    for candidate in (os.environ.get("CHROME"), MAC_CHROME):
        if candidate and Path(candidate).exists():
            return candidate
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if found := shutil.which(name):
            return found
    raise RuntimeError("Chrome not found; set CHROME to its binary")


def pages_from_body(body: str) -> list[str]:
    """Pages listed in the PR description's `<!-- screenshots: ... -->` line, if any. The bot's own section is
    skipped, and so are names without a letter or digit (e.g. a "..." placeholder)."""
    m = PAGES_LINE.search(replace_section(body, "") if SECTION_START in (body or "") else body or "")
    return [p for p in m.group(1).split() if re.search(r"[A-Za-z0-9]", p)] if m else []


def default_pages(site: Path) -> list[str]:
    """Today, Paper accounts, the latest session and the first stock a paper account holds (1M range)."""
    files = json.loads((site / "data" / "manifest.json").read_text())["files"]
    pages = ["today", "accounts"]
    days = [f["path"] for f in files if f["kind"] == "day"]
    if days:
        pages.append(days[-1])
    for positions in sorted((site / "data" / "journal" / "paper").glob("*/positions.csv")):
        with positions.open(newline="") as f:
            held = next(csv.DictReader(f), None)
        if held and (site / "data" / "journal" / "stocks" / f"{held['sym']}.csv").exists():
            pages.append(f"journal/stocks/{held['sym']}.csv?range=1M")
            break
    return pages


def file_name(page: str) -> str:
    """A stable PNG name per page: 'journal/stocks/INFY.csv?range=1M' -> 'journal-stocks-INFY-csv-range-1M.png'."""
    return re.sub(r"[^A-Za-z0-9]+", "-", page).strip("-")[:80] + ".png"


def shoot(site: Path, pages: list[str], out: Path) -> list[Path]:
    """Serve `site` on a free port and save one screenshot per page into `out`."""
    out.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    binary, shots = chrome(), []
    try:
        with tempfile.TemporaryDirectory() as profile:
            for page in pages:
                png = out / file_name(page)
                cmd = [binary, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                       f"--window-size={WIDTH},{HEIGHT}", f"--virtual-time-budget={BUDGET_MS}",
                       f"--user-data-dir={profile}", f"--screenshot={png}",
                       f"http://127.0.0.1:{server.server_address[1]}/#{page}"]
                if platform.system() == "Linux":
                    cmd.insert(1, "--no-sandbox")  # CI runners run Chrome as root
                subprocess.run(cmd, capture_output=True, timeout=180, check=False)
                if png.exists():
                    shots.append(png)
                else:
                    print(f"screenshot failed: {page}")
    finally:
        server.shutdown()
    return shots


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def section(images: list[tuple[str, str]], head: str, pages: list[str]) -> str:
    """The PR description's screenshots block: (page, image URL) pairs, newest run only."""
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [SECTION_START, "## Screenshots",
             f"_Generated by `aistock pr-screenshots` from {head[:7]} on {when}. Pages: "
             + ", ".join(f"`{p}`" for p in pages)
             + ". To choose others, add `<!-- screenshots: today journal/stocks/INFY.csv?range=1M -->` here._", ""]
    for page, url in images:
        lines += [f"**{page}**", f"![{page}]({url})", ""]
    return "\n".join(lines + [SECTION_END])


def replace_section(body: str, block: str) -> str:
    """Swap the previous screenshots block for `block`; else put it before '## Test plan', else at the end."""
    body = body or ""
    if SECTION_START in body and SECTION_END in body:
        start, end = body.index(SECTION_START), body.index(SECTION_END) + len(SECTION_END)
        return body[:start] + block + body[end:]
    if "## Test plan" in body:
        i = body.index("## Test plan")
        return body[:i] + block + "\n\n" + body[i:]
    return body.rstrip() + "\n\n" + block + "\n"


def _git(*args, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _gh(*args) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()


def push_images(pr: int, shots: list[Path], repo: Path) -> str:
    """Replace pr-<N>/ on the screenshots branch with `shots`; returns the commit holding them."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "shots"
        exists = subprocess.run(["git", "fetch", "-q", "origin", BRANCH], cwd=repo, capture_output=True).returncode == 0
        if exists:
            _git("worktree", "add", "-q", "--detach", str(work), "FETCH_HEAD", cwd=repo)
        else:
            _git("worktree", "add", "-q", "--detach", str(work), cwd=repo)
            _git("checkout", "-q", "--orphan", ORPHAN, cwd=work)  # first run: start the branch empty
            _git("rm", "-rfq", ".", cwd=work)
        try:
            folder = work / f"pr-{pr}"
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir()
            for png in shots:
                shutil.copy(png, folder / png.name)
            _git("add", "-A", f"pr-{pr}", cwd=work)
            if _git("status", "--porcelain", cwd=work):
                _git("commit", "-q", "-m", f"Screenshots for PR #{pr}", cwd=work)
                for attempt in range(3):  # another PR's run may have pushed meanwhile
                    if subprocess.run(["git", "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}"],
                                      cwd=work, capture_output=True).returncode == 0:
                        break
                    _git("pull", "-q", "--rebase", "origin", BRANCH, cwd=work)
                else:
                    raise RuntimeError(f"could not push to {BRANCH}")
            return _git("rev-parse", "HEAD", cwd=work)
        finally:
            _git("worktree", "remove", "--force", str(work), cwd=repo)
            subprocess.run(["git", "branch", "-q", "-D", ORPHAN], cwd=repo, capture_output=True)


def publish(pr: int, site: Path, repo: Path, pages: list[str] | None = None) -> str:
    """Screenshot the PR's pages, push them and update its description; returns the PR URL."""
    body = _gh("pr", "view", str(pr), "--json", "body", "-q", ".body")
    pages = pages or pages_from_body(body) or default_pages(site)
    with tempfile.TemporaryDirectory() as tmp:
        shots = shoot(site, pages, Path(tmp))
        if not shots:
            raise RuntimeError("no screenshots were taken")
        commit = push_images(pr, shots, repo)
    slug = _gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
    head = _gh("pr", "view", str(pr), "--json", "headRefOid", "-q", ".headRefOid")
    taken = {s.name for s in shots}
    images = [(p, f"https://raw.githubusercontent.com/{slug}/{commit}/pr-{pr}/{file_name(p)}")
              for p in pages if file_name(p) in taken]
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(replace_section(body, section(images, head, pages)))
    try:
        _gh("pr", "edit", str(pr), "--body-file", f.name)
    finally:
        os.unlink(f.name)
    return _gh("pr", "view", str(pr), "--json", "url", "-q", ".url")
