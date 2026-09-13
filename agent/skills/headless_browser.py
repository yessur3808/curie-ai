"""Owner-scoped headless Chromium sessions for dynamic websites."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any

from agent.skills.find_info import is_safe_url

_LOCK = asyncio.Lock()
_SESSIONS: dict[str, dict[str, Any]] = {}


def _state_path(owner_id: str) -> Path:
    root = Path(os.getenv("CURIE_BROWSER_STATE_ROOT", ".curie-browser")).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root / f"{hashlib.sha256(owner_id.encode()).hexdigest()[:24]}.json"


async def _session(owner_id: str) -> dict[str, Any]:
    async with _LOCK:
        if owner_id in _SESSIONS:
            return _SESSIONS[owner_id]
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Headless browser support is not installed") from exc
        runtime = await async_playwright().start()
        browser = await runtime.chromium.launch(
            headless=True, args=["--disable-dev-shm-usage"]
        )
        state = _state_path(owner_id)
        context_args = {"storage_state": str(state)} if state.is_file() else {}
        context = await browser.new_context(
            **context_args, accept_downloads=False, service_workers="block"
        )

        async def guard_request(route) -> None:
            url = route.request.url
            if url.startswith(("data:", "blob:", "about:")):
                await route.continue_()
            elif await is_safe_url(url):
                await route.continue_()
            else:
                await route.abort("blockedbyclient")

        # Validate every subresource, not only top-level navigation. This stops a
        # public page from using Chromium as a proxy into localhost or metadata IPs.
        await context.route("**/*", guard_request)
        page = await context.new_page()
        value = {
            "runtime": runtime,
            "browser": browser,
            "context": context,
            "page": page,
        }
        _SESSIONS[owner_id] = value
        return value


async def _save(owner_id: str, session: dict[str, Any]) -> None:
    path = _state_path(owner_id)
    await session["context"].storage_state(path=str(path))
    path.chmod(0o600)


async def open_page(owner_id: str, url: str) -> dict:
    if not await is_safe_url(url):
        raise PermissionError("Browser URL was blocked by the network safety policy")
    session = await _session(owner_id)
    response = await session["page"].goto(
        url, wait_until="domcontentloaded", timeout=30_000
    )
    await _save(owner_id, session)
    return await snapshot(owner_id, status=response.status if response else None)


async def snapshot(owner_id: str, *, status: int | None = None) -> dict:
    session = await _session(owner_id)
    page = session["page"]
    body = (await page.locator("body").inner_text(timeout=10_000))[:12_000]
    elements = await page.locator("a,button,input,textarea,select").evaluate_all(
        """els => els.slice(0, 100).map((e, i) => ({
          index: i,
          tag: e.tagName.toLowerCase(),
          text: (e.innerText || e.getAttribute('aria-label') || e.getAttribute('placeholder') || '').trim().slice(0, 160),
          type: e.getAttribute('type') || ''
        }))"""
    )
    return {
        "url": page.url,
        "title": await page.title(),
        "status": status,
        "text": body,
        "interactive": elements,
        "untrusted": True,
    }


async def click_text(owner_id: str, text: str) -> dict:
    if not text.strip() or len(text) > 200:
        raise ValueError("Browser click text must be between 1 and 200 characters")
    session = await _session(owner_id)
    page = session["page"]
    await page.get_by_text(text, exact=True).first.click(timeout=15_000)
    await page.wait_for_load_state("domcontentloaded", timeout=15_000)
    if not await is_safe_url(page.url):
        await page.go_back(wait_until="domcontentloaded")
        raise PermissionError("Browser navigation reached a blocked URL")
    await _save(owner_id, session)
    return await snapshot(owner_id)


async def fill_label(owner_id: str, label: str, value: str) -> dict:
    if any(
        word in label.casefold()
        for word in ("password", "passcode", "secret", "token", "otp", "2fa")
    ):
        raise PermissionError("Curie will not receive or fill authentication secrets")
    if len(label) > 200 or len(value) > 4_000:
        raise ValueError("Browser field input exceeds its limit")
    session = await _session(owner_id)
    await session["page"].get_by_label(label, exact=True).first.fill(
        value, timeout=15_000
    )
    await _save(owner_id, session)
    return await snapshot(owner_id)


async def close_session(owner_id: str) -> bool:
    async with _LOCK:
        session = _SESSIONS.pop(owner_id, None)
    if not session:
        return False
    await session["context"].close()
    await session["browser"].close()
    await session["runtime"].stop()
    return True
