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
    monkeypatch.setenv("CURIE_X_BROWSER_ENGINE", "firefox")
    monkeypatch.setenv("CURIE_X_BROWSER_PROFILE_ROOT", str(tmp_path / "profiles"))
    return tmp_path / "profiles"


def test_chrome_profile_is_separate_and_requires_matching_enrollment(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("CURIE_X_BROWSER_PROFILE_ROOT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("CURIE_X_BROWSER_ENGINE", "firefox")
    firefox = xb.profile_path("owner")
    monkeypatch.setenv("CURIE_X_BROWSER_ENGINE", "chrome")
    assert xb.profile_path("owner") != firefox
    assert xb.profile_path("owner").parent == tmp_path / ".local/share/curie/x-chrome"
    metadata = {"handle": "CurieTest", "mode": "persistent_firefox"}
    monkeypatch.setattr(xb, "get_credential", lambda *a: metadata)
    with xb.profile_lease("owner") as path:
        (path / "prefs.js").write_text("{}")
        assert not xb.session_metadata("owner")["ready"]
        metadata["mode"] = "persistent_chrome"
        assert not xb.session_metadata("owner")["ready"]
        (path / "Default").mkdir()
        (path / "Default/Preferences").write_text("{}")
    assert xb.session_metadata("owner")["ready"]


def test_unknown_browser_engine_fails_closed(monkeypatch):
    monkeypatch.setenv("CURIE_X_BROWSER_ENGINE", "unrecognized")
    with pytest.raises(xb.BrowserAccessError, match="browser_engine_invalid"):
        xb.profile_path("owner")


@pytest.mark.asyncio
async def test_chrome_uses_private_profile_and_closes_after_failure(
    profiles, monkeypatch
):
    from connectors import x_browser_runtime as runtime

    monkeypatch.setenv("CURIE_X_BROWSER_ENGINE", "chrome")
    closed = []

    @asynccontextmanager
    async def chrome(path, headless):
        assert path == xb.profile_path("owner")
        assert headless
        try:
            yield SimpleNamespace()
        finally:
            closed.append(True)

    monkeypatch.setattr(runtime, "chrome_context", chrome)
    with pytest.raises(RuntimeError):
        async with xb.browser_context("owner"):
            with pytest.raises(xb.BrowserAccessError, match="browser_profile_busy"):
                with xb.profile_lease("owner"):
                    pass
            raise RuntimeError("fixture failure")
    assert closed == [True]
    with xb.profile_lease("owner"):
        pass


@pytest.mark.asyncio
async def test_existing_chrome_enrollment_verifies_before_saving(profiles, monkeypatch):
    monkeypatch.setenv("CURIE_X_BROWSER_ENGINE", "chrome")
    page = SimpleNamespace()

    launches = []

    @asynccontextmanager
    async def context(owner, **kwargs):
        launches.append(kwargs)
        yield SimpleNamespace(pages=[page])

    monkeypatch.setattr(xb, "browser_context", context)
    saved = []
    monkeypatch.setattr(xb, "put_credential", lambda *args: saved.append(args))
    navigate = AsyncMock(side_effect=xb.BrowserAccessError("wrong_account"))
    monkeypatch.setattr(xb, "_navigate", navigate)
    with pytest.raises(xb.BrowserAccessError, match="wrong_account"):
        await xb.enroll_existing_session("owner", "CurieTest")
    assert not saved
    navigate.side_effect = None
    await xb.enroll_existing_session("owner", "CurieTest", headless=False)
    assert launches == [{"headless": True}, {"headless": False}]
    navigate.assert_awaited_with(page, "https://x.com/home", "CurieTest")
    assert saved[0][2]["mode"] == "persistent_chrome"
    assert set(saved[0][2]) == {"handle", "mode", "saved_at"}


@pytest.mark.parametrize("headed", [False, True])
def test_login_cli_loads_live_instance_before_enrollment(monkeypatch, tmp_path, headed):
    from scripts import x_browser_login as login
    from scripts.ai_scout_run import load_instance_environment

    (tmp_path / ".env").write_text(
        "CURIE_LOCAL_MEMORY_DB=legacy.sqlite3\nCURIE_X_BROWSER_ENGINE=firefox\n"
    )
    (tmp_path / "instances").mkdir()
    (tmp_path / "instances/curie.env").write_text(
        "CURIE_LOCAL_MEMORY_DB=live.sqlite3\nCURIE_X_BROWSER_ENGINE=chrome\n"
    )
    monkeypatch.delenv("CURIE_INSTANCE", raising=False)
    monkeypatch.delenv("CURIE_LOCAL_MEMORY_DB", raising=False)
    monkeypatch.setenv("CURIE_X_BROWSER_ENGINE", "firefox")
    monkeypatch.setattr(
        login, "load_instance_environment", lambda: load_instance_environment(tmp_path)
    )
    calls = []

    async def enroll(owner, handle, **kwargs):
        assert os.environ["CURIE_LOCAL_MEMORY_DB"] == "live.sqlite3"
        assert xb.browser_engine() == "chrome"
        calls.append((owner, handle, kwargs))

    monkeypatch.setattr(xb, "enroll_existing_session", enroll)
    monkeypatch.setattr(
        "sys.argv",
        [
            "x_browser_login",
            "--owner-id",
            "owner",
            "--handle",
            "@CurieTest",
            "--verify-existing",
        ]
        + (["--headed"] if headed else []),
    )
    login.main()
    assert calls == [("owner", "CurieTest", {"headless": not headed})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,landing_heading,reason",
    [
        ("/", True, "login_or_verification_required"),
        ("/", False, "browser_unavailable_or_timeout"),
        ("/home", False, "browser_unavailable_or_timeout"),
        ("/i/flow/login", False, "login_or_verification_required"),
    ],
)
async def test_navigation_distinguishes_signed_out_landing_from_timeout(
    path, landing_heading, reason
):
    missing_profile = SimpleNamespace(wait_for=AsyncMock(side_effect=TimeoutError))

    def locator(selector):
        if selector == 'a[data-testid="AppTabBar_Profile_Link"]':
            return missing_profile
        assert selector == "h1, h2"

        def get_by_text(text, exact):
            assert text == "Happening now." and exact
            return SimpleNamespace(count=AsyncMock(return_value=int(landing_heading)))

        return SimpleNamespace(get_by_text=get_by_text)

    page = SimpleNamespace(
        url="https://x.com" + path,
        goto=AsyncMock(return_value=SimpleNamespace(status=200)),
        locator=locator,
    )
    with pytest.raises(xb.BrowserAccessError, match=reason):
        await xb._navigate(page, "https://x.com/home", "CurieTest")


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
    reason="requires installed Chrome or Firefox and geckodriver",
)
@pytest.mark.parametrize("engine", ["firefox", "chrome"])
async def test_real_browser_keeps_cookie_local_storage_and_service_worker(
    profiles, monkeypatch, engine
):
    """Two real browser lifetimes, local fixture only; no user account or live X."""
    monkeypatch.setenv("CURIE_X_BROWSER_ENGINE", engine)
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

    async def evaluate(context, body):
        if engine == "chrome":
            return await context.pages[0].evaluate("async () => {" + body + "}")
        return await context.run(
            lambda: context.driver.execute_async_script(
                "const done = arguments[0]; (async () => {"
                + body
                + "})().then(done, e => done(String(e)));"
            )
        )

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
            await evaluate(context, "await fetch('/limited');")
            await asyncio.wait_for(limited.wait(), timeout=5)
            await evaluate(
                context,
                """
                document.cookie = 'curie_fixture=retained; Max-Age=3600; SameSite=Lax';
                localStorage.setItem('curie_fixture', 'retained');
                await navigator.serviceWorker.register('/sw.js');
                await navigator.serviceWorker.ready;
            """,
            )
        async with xb.browser_context("fixture") as context:
            page = context.pages[0]
            await page.goto(url)
            assert "curie_fixture=retained" in await evaluate(
                context, "return document.cookie;"
            )
            assert (
                await evaluate(context, "return localStorage.getItem('curie_fixture');")
                == "retained"
            )
            assert (
                await evaluate(
                    context,
                    "return (await navigator.serviceWorker.getRegistrations()).length;",
                )
                == 1
            )
        async with xb.browser_context("other-fixture") as context:
            page = context.pages[0]
            await page.goto(url)
            assert "curie_fixture=retained" not in await evaluate(
                context, "return document.cookie;"
            )
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)
