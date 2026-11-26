"""Dashboard screenshots for PR descriptions (`aistock pr-screenshots`, and the PR screenshots workflow).

`shoot` serves a built site on a free local port and captures each page with headless Chrome, driven over
the DevTools protocol on a pipe (`--remote-debugging-pipe`, no extra packages): it waits until the page sets
`body[data-shown]` (app.js `show`) and two animation frames have drawn the charts. A fixed timer
(`--virtual-time-budget`) left ~1 in 4 Paper accounts captures stuck on "Loading…". `publish`
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
import base64
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
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
READY_TIMEOUT = 90  # seconds for a page to draw; Today loads the most files
THEMES = ("dark", "light")  # emulated prefers-color-scheme, which the page follows


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


def shoot(site: Path, pages: list[str], out: Path, theme: str = "dark") -> list[Path]:
    """Serve `site` on a free port and save one screenshot per page into `out`, in `theme` (dark or light)."""
    out.mkdir(parents=True, exist_ok=True)
    server = _Server(("127.0.0.1", 0), partial(_QuietHandler, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    shots = []
    try:
        with tempfile.TemporaryDirectory() as profile, _Chrome(chrome(), profile) as browser:
            for page in pages:
                png = out / file_name(page)
                try:
                    png.write_bytes(browser.screenshot(f"http://127.0.0.1:{server.server_address[1]}/#{page}", theme))
                    shots.append(png)
                except TimeoutError:
                    print(f"screenshot failed (page never finished drawing): {page}")
    finally:
        server.shutdown()
    return shots


class _Chrome:
    """Headless Chrome over the DevTools protocol on a pipe: Chrome reads commands on fd 3 and writes replies
    on fd 4, each a JSON message ending in a NUL byte."""

    def __init__(self, binary: str, profile: str):
        to_chrome, self._send = os.pipe()
        self._recv, from_chrome = os.pipe()

        def fds():  # runs in the child before exec: put the pipe ends where Chrome expects them
            os.dup2(to_chrome, 3)
            os.dup2(from_chrome, 4)

        cmd = [binary, "--headless=new", "--remote-debugging-pipe", "--disable-gpu", "--hide-scrollbars",
               f"--user-data-dir={profile}", "about:blank"]
        if platform.system() == "Linux":
            cmd.insert(1, "--no-sandbox")  # CI runners run Chrome as root
        self.proc = subprocess.Popen(cmd, preexec_fn=fds, close_fds=False,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(to_chrome)
        os.close(from_chrome)
        self._buf, self._id = b"", 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.proc.kill()
        self.proc.wait()
        os.close(self._send)
        os.close(self._recv)

    def call(self, method: str, session: str | None = None, **params) -> dict:
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params, **({"sessionId": session} if session else {})}
        os.write(self._send, json.dumps(msg).encode() + b"\0")
        while True:  # skip events and other replies until ours
            while b"\0" not in self._buf:
                chunk = os.read(self._recv, 1 << 20)
                if not chunk:
                    raise RuntimeError("Chrome exited")
                self._buf += chunk
            raw, self._buf = self._buf.split(b"\0", 1)
            reply = json.loads(raw)
            if reply.get("id") == self._id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})

    def screenshot(self, url: str, theme: str) -> bytes:
        """PNG of `url` once the dashboard has drawn it; a fresh tab per page."""
        target = self.call("Target.createTarget", url="about:blank")["targetId"]
        s = self.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        try:
            self.call("Emulation.setDeviceMetricsOverride", s, width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)
            self.call("Emulation.setEmulatedMedia", s, features=[{"name": "prefers-color-scheme", "value": theme}])
            self.call("Page.navigate", s, url=url)
            deadline = time.monotonic() + READY_TIMEOUT
            while not self._eval(s, "document.body && document.body.dataset.shown !== undefined"):
                if time.monotonic() > deadline:
                    raise TimeoutError(url)
                time.sleep(0.25)
            # charts are created in requestAnimationFrame callbacks after the page is drawn
            self._eval(s, "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(true))))")
            return base64.b64decode(self.call("Page.captureScreenshot", s, format="png")["data"])
        finally:
            self.call("Target.closeTarget", targetId=target)

    def _eval(self, session: str, expression: str):
        return self.call("Runtime.evaluate", session, expression=expression, awaitPromise=True,
                         returnByValue=True).get("result", {}).get("value")


class _Server(ThreadingHTTPServer):
    # a page fetches ~20 CSVs at once; the default backlog of 5 made macOS refuse some ("Failed to fetch")
    request_queue_size = 128


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def section(images: list[tuple[str, str]], head: str, pages: list[str], theme: str = "dark") -> str:
    """The PR description's screenshots block: (page, image URL) pairs, newest run only."""
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [SECTION_START, "## Screenshots",
             f"_Generated by `aistock pr-screenshots` from {head[:7]} on {when}, {theme} theme. Pages: "
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


def publish(pr: int, site: Path, repo: Path, pages: list[str] | None = None, theme: str = "dark") -> str:
    """Screenshot the PR's pages, push them and update its description; returns the PR URL."""
    body = _gh("pr", "view", str(pr), "--json", "body", "-q", ".body")
    pages = pages or pages_from_body(body) or default_pages(site)
    with tempfile.TemporaryDirectory() as tmp:
        shots = shoot(site, pages, Path(tmp), theme)
        if not shots:
            raise RuntimeError("no screenshots were taken")
        commit = push_images(pr, shots, repo)
    slug = _gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
    head = _gh("pr", "view", str(pr), "--json", "headRefOid", "-q", ".headRefOid")
    taken = {s.name for s in shots}
    images = [(p, f"https://raw.githubusercontent.com/{slug}/{commit}/pr-{pr}/{file_name(p)}")
              for p in pages if file_name(p) in taken]
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(replace_section(body, section(images, head, pages, theme)))
    try:
        _gh("pr", "edit", str(pr), "--body-file", f.name)
    finally:
        os.unlink(f.name)
    return _gh("pr", "view", str(pr), "--json", "url", "-q", ".url")
