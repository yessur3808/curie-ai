"""Bounded X website reader. No API client, stealth, or challenge bypass.

Authentication is deliberately separate from API OAuth and from the owner's
desktop browser. Only an explicitly enrolled, private server profile is used.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import quote_plus, urlparse

from services.credential_vault import get_credential, put_credential

PROVIDER = "x_browser"
HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
POST_PATH = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d+)$")
PROFILE_MODE = "persistent_firefox"


class BrowserAccessError(RuntimeError):
    """Only a safe reason code crosses the browser boundary."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def profile_path(owner):
    """A dedicated server profile, never the owner's personal browser directory."""
    if not isinstance(owner, str) or not owner.strip():
        raise BrowserAccessError("owner_required")
    default = (
        Path.home() / "snap/firefox/common/curie-x-profiles"
        if Path("/snap/bin/geckodriver").exists()
        else Path(".curie-browser/x")
    )
    root = Path(os.getenv("CURIE_X_BROWSER_PROFILE_ROOT", str(default))).expanduser()
    return (
        root.parent.resolve() / root.name / hashlib.sha256(owner.encode()).hexdigest()
    )


def _private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise BrowserAccessError("browser_profile_unsafe")
    path.chmod(0o700)


@contextmanager
def profile_lease(owner):
    """Serialize login and research across processes; never break browser locks."""
    import fcntl

    path = profile_path(owner)
    # Reject symlink ancestors instead of following them into another profile.
    if path.is_symlink() or path.parent.is_symlink():
        raise BrowserAccessError("browser_profile_unsafe")
    _private_directory(path.parent)
    _private_directory(path)
    fd = os.open(
        path.parent / (path.name + ".lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise BrowserAccessError("browser_profile_unsafe")
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BrowserAccessError("browser_profile_busy") from None
        yield path
    finally:
        os.close(fd)


def session_metadata(owner):
    try:
        item = get_credential(owner, PROVIDER) or {}
        path = profile_path(owner)
        ready = bool(
            item.get("mode") == PROFILE_MODE
            and HANDLE.fullmatch(str(item.get("handle", "")))
            and not path.is_symlink()
            and path.is_dir()
            and (path / "prefs.js").is_file()
        )
    except (RuntimeError, PermissionError, OSError):
        return {"ready": False, "reason": "vault_or_profile_unavailable"}
    return {
        "ready": ready,
        "handle": item.get("handle", ""),
        "saved_at": item.get("saved_at", ""),
        "reason": "" if ready else "server_browser_login_required",
    }


@asynccontextmanager
async def browser_context(owner, *, headless=True):
    """Reuse installed Firefox with ordinary networking and persistent storage."""
    from connectors.x_browser_runtime import firefox_context

    with profile_lease(owner) as path:
        async with firefox_context(path, headless=headless) as context:
            yield context


async def verify_account(page, expected):
    """Do not mistake someone else's feed, an auth wall, or a challenge for data."""
    if not HANDLE.fullmatch(expected):
        raise BrowserAccessError("account_not_configured")
    if hasattr(page, "refresh_url"):
        await page.refresh_url()
    path = urlparse(page.url).path
    if path.startswith(("/i/flow/", "/i/jf/onboarding/", "/login", "/account/access")):
        raise BrowserAccessError("login_or_verification_required")
    if await page.locator(
        'iframe[src*="captcha"], iframe[src*="arkoselabs"], iframe[title*="challenge" i]'
    ).count():
        raise BrowserAccessError("verification_required")
    if (
        await page.get_by_role("dialog")
        .get_by_text(
            re.compile(
                r"verify your identity|authenticate your account|account locked", re.I
            )
        )
        .count()
    ):
        raise BrowserAccessError("verification_required")
    profile = page.locator('a[data-testid="AppTabBar_Profile_Link"]')
    if not await profile.count():
        raise BrowserAccessError("login_or_verification_required")
    actual = (await profile.first.get_attribute("href") or "").strip("/")
    if actual.casefold() != expected.casefold():
        raise BrowserAccessError("wrong_account")


async def _navigate(page, url, expected):
    response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    if response and response.status in (403, 429):
        raise BrowserAccessError("restricted_or_rate_limited")
    try:
        await page.locator('a[data-testid="AppTabBar_Profile_Link"]').wait_for(
            timeout=15000
        )
    except Exception:
        if hasattr(page, "refresh_url"):
            await page.refresh_url()
        if urlparse(page.url).path.startswith(
            ("/i/flow/", "/i/jf/onboarding/", "/login", "/account/access")
        ):
            raise BrowserAccessError("login_or_verification_required") from None
        raise BrowserAccessError("browser_unavailable_or_timeout") from None
    await verify_account(page, expected)


async def collect_posts(owner, expected, queries):
    """At most four pages, two viewports each, 60 DOM-visible posts; 180s total."""
    item = get_credential(owner, PROVIDER) or {}
    if not session_metadata(owner).get("ready"):
        raise BrowserAccessError("login_required")
    if str(item.get("handle", "")).casefold() != expected.casefold():
        raise BrowserAccessError("wrong_account")
    try:
        return await asyncio.wait_for(_collect(owner, expected, queries), timeout=180)
    except BrowserAccessError:
        raise
    except Exception:
        # Browser exceptions can contain private request details or page text.
        raise BrowserAccessError("browser_unavailable_or_timeout") from None


async def _collect(owner, expected, queries):
    posts = {}
    async with browser_context(owner) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        restrictions = set()

        def observe_response(response):
            # Observe the browser's own response status, never replay its API or
            # read hidden response bodies/cookies. Data requests can fail while
            # the document itself is still HTTP 200.
            if response.status in (403, 429) and urlparse(response.url).hostname in {
                "x.com",
                "www.x.com",
                "api.x.com",
                "jf.x.com",
            }:
                restrictions.add(response.status)

        page.on("response", observe_response)

        def check_restrictions():
            if restrictions:
                raise BrowserAccessError("restricted_or_rate_limited")

        urls = ["https://x.com/home"] + [
            "https://x.com/search?q=" + quote_plus(q[:250]) + "&f=live"
            for q in queries[:3]
        ]
        for url in urls:
            await _navigate(page, url, expected)
            check_restrictions()
            articles = page.locator('article[data-testid="tweet"]')
            try:
                await articles.first.wait_for(timeout=10000)
            except Exception:
                check_restrictions()
                await verify_account(page, expected)
                continue
            for _ in range(2):
                check_restrictions()
                await verify_account(page, expected)
                for article in (await articles.all())[:20]:
                    if await article.get_by_text("Ad", exact=True).count():
                        continue
                    times = article.locator("time")
                    texts = article.locator('[data-testid="tweetText"]')
                    if not await times.count() or not await texts.count():
                        continue
                    stamp = await times.first.get_attribute("datetime")
                    link = await times.first.locator("..").get_attribute("href")
                    match = POST_PATH.fullmatch(urlparse(link or "").path)
                    if not match:
                        continue
                    handle, post_id = match.groups()
                    posts.setdefault(
                        post_id,
                        {
                            "id": post_id,
                            "author": handle,
                            "url": f"https://x.com/{handle}/status/{post_id}",
                            "published_at": stamp,
                            "text": (await texts.first.inner_text())[:1200],
                            "untrusted": True,
                        },
                    )
                    if len(posts) >= 60:
                        check_restrictions()
                        return list(posts.values())
                if await articles.count():
                    await articles.last.scroll_into_view_if_needed()
        check_restrictions()
        return list(posts.values())


async def _wait_for_login_confirmation():
    """Wait for terminal confirmation without leaving a blocked input thread."""
    print("Press Enter only when Curie's home feed is visible: ", end="", flush=True)
    loop = asyncio.get_running_loop()
    confirmed = loop.create_future()

    def read_line():
        if not confirmed.done():
            if sys.stdin.readline():
                confirmed.set_result(None)
            else:
                confirmed.set_exception(BrowserAccessError("login_cancelled"))

    loop.add_reader(sys.stdin.fileno(), read_line)
    try:
        await confirmed
    finally:
        loop.remove_reader(sys.stdin.fileno())


async def enroll_session(owner, expected):
    """Sign in using Curie's dedicated, persistent server Firefox profile."""
    if not HANDLE.fullmatch(expected):
        raise ValueError("Expected a valid X handle without @")
    async with browser_context(owner, headless=False) as context:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(
            (
                "https://x.com/home"
                if session_metadata(owner).get("ready")
                else "https://x.com/login"
            ),
            wait_until="domcontentloaded",
        )
        print("Sign in to Curie's account in the opened server browser.")
        print(
            "Complete any verification yourself. No passwords belong in this terminal."
        )
        await _wait_for_login_confirmation()
        await verify_account(page, expected)
        put_credential(
            owner,
            PROVIDER,
            {
                "handle": expected,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "mode": PROFILE_MODE,
            },
        )
        print("Curie's server browser profile is enrolled. Future sessions reuse it.")
