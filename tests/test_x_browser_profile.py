"""Persistent browser lifecycle, owner separation and enrollment boundaries."""

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from connectors import x_browser as xb


@pytest.fixture
def profiles(monkeypatch, tmp_path):
    monkeypatch.setenv("CURIE_X_BROWSER_PROFILE_ROOT", str(tmp_path / "profiles"))
    return tmp_path / "profiles"


def test_profile_is_owner_scoped_private_and_exclusively_leased(profiles):
    first = xb.profile_path("owner")
    assert first != xb.profile_path("other")
    assert first == xb.profile_path("owner")
    assert xb.profile_path("../../escape").parent == profiles
    with xb.profile_lease("owner") as path:
        assert path == first
        assert path.stat().st_mode & 0o777 == 0o700
        assert path.parent.stat().st_mode & 0o777 == 0o700
        with pytest.raises(xb.BrowserAccessError, match="browser_profile_busy"):
            with xb.profile_lease("owner"):
                pytest.fail("same profile must not open concurrently")
        with xb.profile_lease("other"):
            pass
    with xb.profile_lease("owner"):
        pass


def test_profile_lease_releases_after_exception(profiles):
    with pytest.raises(RuntimeError):
        with xb.profile_lease("owner"):
            raise RuntimeError("fixture failure")
    with xb.profile_lease("owner"):
        pass


def test_profile_rejects_symlinks_and_empty_owner(profiles, tmp_path):
    profiles.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    xb.profile_path("owner").symlink_to(outside)
    with pytest.raises(xb.BrowserAccessError, match="browser_profile_unsafe"):
        with xb.profile_lease("owner"):
            pass
    with pytest.raises(xb.BrowserAccessError, match="owner_required"):
        xb.profile_path("")


def test_legacy_cookie_record_requires_explicit_profile_enrollment(
    profiles, monkeypatch
):
    monkeypatch.setattr(
        xb,
        "get_credential",
        lambda *a: {
            "handle": "CurieTest",
            "storage_state": {"cookies": []},
        },
    )
    assert not xb.session_metadata("owner")["ready"]


def test_enrollment_metadata_requires_profile_on_this_host(profiles, monkeypatch):
    monkeypatch.setattr(
        xb,
        "get_credential",
        lambda *a: {
            "handle": "CurieTest",
            "mode": xb.PROFILE_MODE,
            "saved_at": "today",
        },
    )
    assert not xb.session_metadata("owner")["ready"]
    with xb.profile_lease("owner") as path:
        (path / "prefs.js").write_text("{}")
    assert xb.session_metadata("owner")["ready"]


@pytest.mark.asyncio
async def test_regular_browser_profile_reuse(profiles, monkeypatch):
    from connectors import x_browser_runtime as runtime

    calls = []
    closed = []

    @asynccontextmanager
    async def firefox(path, headless):
        calls.append((path, headless))
        try:
            yield SimpleNamespace()
        finally:
            closed.append(path)

    monkeypatch.setattr(runtime, "firefox_context", firefox)
    async with xb.browser_context("owner", headless=False):
        with pytest.raises(xb.BrowserAccessError, match="browser_profile_busy"):
            with xb.profile_lease("owner"):
                pass
    async with xb.browser_context("owner"):
        pass
    assert calls == [
        (xb.profile_path("owner"), False),
        (xb.profile_path("owner"), True),
    ]
    assert closed == [xb.profile_path("owner")] * 2


@pytest.mark.asyncio
async def test_launch_failure_releases_profile_lock(profiles, monkeypatch):
    from connectors import x_browser_runtime as runtime

    @asynccontextmanager
    async def firefox(*args, **kwargs):
        raise RuntimeError("fixture failure")
        yield

    monkeypatch.setattr(runtime, "firefox_context", firefox)
    with pytest.raises(RuntimeError):
        async with xb.browser_context("owner"):
            pass
    with xb.profile_lease("owner"):
        pass


@pytest.mark.asyncio
async def test_cancellation_closes_browser_before_releasing_profile(
    profiles, monkeypatch
):
    import threading
    from connectors import x_browser_runtime as runtime

    started, release = threading.Event(), threading.Event()
    closed = []

    def start(context, path, headless, loop):
        started.set()
        release.wait(timeout=5)
        context.driver = SimpleNamespace(quit=lambda: closed.append(True))

    monkeypatch.setattr(runtime.FirefoxContext, "start", start)

    async def open_browser():
        async with xb.browser_context("owner"):
            pass

    task = asyncio.create_task(open_browser())
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    with pytest.raises(xb.BrowserAccessError, match="browser_profile_busy"):
        with xb.profile_lease("owner"):
            pass
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]
    with xb.profile_lease("owner"):
        pass


@pytest.mark.asyncio
async def test_enrollment_verifies_account_before_saving_metadata(
    profiles, monkeypatch
):
    page = SimpleNamespace(goto=AsyncMock())

    @asynccontextmanager
    async def context(owner, **kwargs):
        yield SimpleNamespace(pages=[page])

    monkeypatch.setattr(xb, "browser_context", context)
    monkeypatch.setattr(xb, "session_metadata", lambda _: {"ready": False})
    monkeypatch.setattr(xb, "_wait_for_login_confirmation", AsyncMock())
    saved = []
    monkeypatch.setattr(xb, "put_credential", lambda *args: saved.append(args))
    monkeypatch.setattr(
        xb,
        "verify_account",
        AsyncMock(side_effect=xb.BrowserAccessError("wrong_account")),
    )
    with pytest.raises(xb.BrowserAccessError, match="wrong_account"):
        await xb.enroll_session("owner", "CurieTest")
    assert not saved
    monkeypatch.setattr(xb, "verify_account", AsyncMock())
    await xb.enroll_session("owner", "CurieTest")
    assert saved[0][0] == "owner"
    assert set(saved[0][2]) == {"handle", "mode", "saved_at"}
    assert saved[0][2]["mode"] == xb.PROFILE_MODE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/i/flow/login", "/i/jf/onboarding/web?mode=login", "/account/access"]
)
async def test_both_login_flows_are_not_authenticated(path):
    with pytest.raises(xb.BrowserAccessError, match="login_or_verification_required"):
        await xb.verify_account(
            SimpleNamespace(url="https://x.com" + path), "CurieTest"
        )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("CURIE_RUN_BROWSER_TESTS") != "1",
    reason="requires installed Firefox and geckodriver",
)
async def test_real_firefox_keeps_cookie_local_storage_and_service_worker(profiles):
    """Two real browser lifetimes, local fixture only; no user account or live X."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            worker = self.path == "/sw.js"
            body = (
                b"self.addEventListener('install', () => self.skipWaiting());"
                if worker
                else b"""<!doctype html><title>Curie browser fixture</title>
                <a data-testid="AppTabBar_Profile_Link" href="/CurieTest">Profile</a>
                <article data-testid="tweet"><a href="/CurieTest/status/123">
                <time datetime="2026-10-27T10:00:00Z">Today</time></a>
                <div data-testid="tweetText">Browser fixture post.</div><span>Ad</span></article>
                """
            )
            self.send_response(429 if self.path == "/limited" else 200)
            self.send_header(
                "Content-Type", "application/javascript" if worker else "text/html"
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        async with xb.browser_context("fixture") as context:
            page = context.pages[0]
            await xb._navigate(page, url, "CurieTest")
            articles = page.locator('article[data-testid="tweet"]')
            assert await articles.count() == 1
            article = (await articles.all())[0]
            assert await article.get_by_text("Ad", exact=True).count() == 1
            assert (
                await article.locator("time").first.locator("..").get_attribute("href")
                == "/CurieTest/status/123"
            )
            assert (
                await article.locator('[data-testid="tweetText"]').inner_text()
                == "Browser fixture post."
            )
            await articles.last.scroll_into_view_if_needed()
            with pytest.raises(xb.BrowserAccessError, match="wrong_account"):
                await xb.verify_account(page, "AnotherAccount")
            limited = asyncio.Event()
            page.on(
                "response",
                lambda response: limited.set() if response.status == 429 else None,
            )
            await context.run(
                lambda: context.driver.execute_script("fetch('/limited')")
            )
            await asyncio.wait_for(limited.wait(), timeout=5)
            await context.run(lambda: context.driver.execute_async_script("""
                const done = arguments[arguments.length - 1];
                (async () => {
                document.cookie = 'curie_fixture=retained; Max-Age=3600; SameSite=Lax';
                localStorage.setItem('curie_fixture', 'retained');
                await navigator.serviceWorker.register('/sw.js');
                await navigator.serviceWorker.ready;
                })().then(() => done(true), e => done(String(e)));
            """))
        async with xb.browser_context("fixture") as context:
            page = context.pages[0]
            await page.goto(url)
            assert "curie_fixture=retained" in await context.run(
                lambda: context.driver.execute_script("return document.cookie")
            )
            assert (
                await context.run(
                    lambda: context.driver.execute_script(
                        "return localStorage.getItem('curie_fixture')"
                    )
                )
                == "retained"
            )
            assert (
                await context.run(
                    lambda: context.driver.execute_async_script(
                        "const done = arguments[0]; navigator.serviceWorker.getRegistrations().then(r => done(r.length));"
                    )
                )
                == 1
            )
        async with xb.browser_context("other-fixture") as context:
            page = context.pages[0]
            await page.goto(url)
            assert "curie_fixture=retained" not in await context.run(
                lambda: context.driver.execute_script("return document.cookie")
            )
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)
