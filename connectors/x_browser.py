"""Bounded X website reader. No API client, stealth, or challenge bypass.

Authentication is deliberately separate from API OAuth and from the owner's
desktop browser. Only an explicitly enrolled, encrypted Curie session is used.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import re
from urllib.parse import quote_plus, urlparse

from services.credential_vault import get_credential, put_credential

PROVIDER = "x_browser"
HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
POST_PATH = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d+)$")


class BrowserAccessError(RuntimeError):
    """Only a safe reason code crosses the browser boundary."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def session_metadata(owner):
    try:
        item = get_credential(owner, PROVIDER) or {}
    except (RuntimeError, PermissionError):
        return {"ready": False, "reason": "vault_unavailable"}
    return {
        "ready": bool(item.get("storage_state") and item.get("handle")),
        "handle": item.get("handle", ""),
        "saved_at": item.get("saved_at", ""),
    }


def allowed_resource(url):
    """Only browser resources for X; never paid API hosts or external pages."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
            and (
                host in {"x.com", "www.x.com"}
                or host == "twimg.com"
                or host.endswith(".twimg.com")
            )
            and not parsed.path.startswith(("/2/", "/1.1/"))
        )
    except ValueError:
        return False


@asynccontextmanager
async def browser_context(storage_state=None, *, headless=True):
    from playwright.async_api import async_playwright

    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(headless=headless)
        try:
            context = await browser.new_context(
                storage_state=storage_state,
                accept_downloads=False,
                service_workers="block",
            )

            async def guard(route):
                if allowed_resource(route.request.url):
                    await route.continue_()
                else:
                    await route.abort("blockedbyclient")

            await context.route("**/*", guard)
            context.set_default_timeout(10000)
            yield context
        finally:
            await browser.close()


async def verify_account(page, expected):
    """Do not mistake someone else's feed, an auth wall, or a challenge for data."""
    if not HANDLE.fullmatch(expected):
        raise BrowserAccessError("account_not_configured")
    path = urlparse(page.url).path
    if path.startswith(("/i/flow/", "/login", "/account/access")):
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
        if urlparse(page.url).path.startswith(
            ("/i/flow/", "/login", "/account/access")
        ):
            raise BrowserAccessError("login_or_verification_required") from None
        raise BrowserAccessError("browser_unavailable_or_timeout") from None
    await verify_account(page, expected)


async def collect_posts(owner, expected, queries):
    """At most four pages, two viewports each, 60 DOM-visible posts; 180s total."""
    item = get_credential(owner, PROVIDER) or {}
    if not item.get("storage_state"):
        raise BrowserAccessError("login_required")
    if str(item.get("handle", "")).casefold() != expected.casefold():
        raise BrowserAccessError("wrong_account")
    try:
        return await asyncio.wait_for(_collect(item, expected, queries), timeout=180)
    except BrowserAccessError:
        raise
    except Exception:
        # Playwright exceptions can contain private request details or page text.
        raise BrowserAccessError("browser_unavailable_or_timeout") from None


async def _collect(item, expected, queries):
    posts = {}
    async with browser_context(item["storage_state"]) as context:
        page = await context.new_page()
        restrictions = set()

        def observe_response(response):
            # Observe the browser's own response status, never replay its API or
            # read hidden response bodies/cookies. Data requests can fail while
            # the document itself is still HTTP 200.
            if response.status in (403, 429) and urlparse(response.url).hostname in {
                "x.com",
                "www.x.com",
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


async def enroll_session(owner, expected):
    """User-operated sign-in in a NEW server browser; never receive passwords."""
    if not HANDLE.fullmatch(expected):
        raise ValueError("Expected a valid X handle without @")
    async with browser_context(headless=False) as context:
        page = await context.new_page()
        await page.goto("https://x.com/login", wait_until="domcontentloaded")
        print("Sign in to Curie's account in the opened server browser.")
        print(
            "Complete any verification yourself. No passwords belong in this terminal."
        )
        await asyncio.to_thread(
            input, "Press Enter only when Curie's home feed is visible: "
        )
        await verify_account(page, expected)
        put_credential(
            owner,
            PROVIDER,
            {
                "handle": expected,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "storage_state": await context.storage_state(),
            },
        )
        print("Curie's browser session is saved in the encrypted credential vault.")
