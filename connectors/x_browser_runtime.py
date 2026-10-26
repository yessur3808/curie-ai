"""Small async DOM adapter for an installed, sandboxed Firefox browser.

All WebDriver commands run on one worker thread. Browser requests are observed,
never intercepted, rewritten or replayed. No authentication values are exported.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import os
from pathlib import Path
import shutil
from types import SimpleNamespace


class FirefoxContext:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="curie-x")
        self.driver = None
        self.pages = [FirefoxPage(self)]

    async def run(self, operation):
        return await asyncio.get_running_loop().run_in_executor(
            self.executor, operation
        )

    def start(self, path, headless, loop):
        from selenium import webdriver
        from selenium.webdriver.firefox.service import Service

        options = webdriver.FirefoxOptions()
        binary = os.getenv("CURIE_X_FIREFOX_BINARY")
        snap = Path("/snap/firefox/current/usr/lib/firefox/firefox")
        if binary or snap.is_file():
            options.binary_location = binary or str(snap)
        options.add_argument("-profile")
        options.add_argument(str(path))  # In place, not a temporary profile copy.
        if headless:
            options.add_argument("-headless")
        options.accept_insecure_certs = False
        options.enable_bidi = True
        options.page_load_strategy = "eager"
        executable = os.getenv("CURIE_X_GECKODRIVER") or shutil.which("geckodriver")
        if not executable:
            raise RuntimeError("firefox_driver_not_installed")
        service = Service(executable, service_args=["--host", "127.0.0.1"])
        self.driver = webdriver.Firefox(options=options, service=service)
        self.driver.set_page_load_timeout(30)
        self.driver.set_script_timeout(15)

        def response(event):
            data = (
                event.get("response", {}) if isinstance(event, dict) else event.response
            )
            if isinstance(data, dict):
                status, url = data.get("status"), data.get("url", "")
            else:
                status, url = data.status, data.url
            # Move observations onto the asyncio thread, without retaining headers.
            loop.call_soon_threadsafe(self.pages[0].response, status, url)

        self.driver.network.add_event_handler("response_started", response)


@asynccontextmanager
async def firefox_context(path, headless=True):
    context = FirefoxContext()
    loop = asyncio.get_running_loop()
    try:
        await context.run(lambda: context.start(path, headless, loop))
        yield context
    finally:
        # Queued behind any in-flight command, including a cancelled startup.
        try:
            await asyncio.shield(
                context.run(lambda: context.driver.quit() if context.driver else None)
            )
        finally:
            context.executor.shutdown(wait=False)


class FirefoxPage:
    def __init__(self, context):
        self.context = context
        self.url = "about:blank"
        self.listeners = []

    def on(self, event, callback):
        if event != "response":
            raise ValueError("unsupported_browser_event")
        self.listeners.append(callback)

    def response(self, status, url):
        for callback in self.listeners:
            callback(SimpleNamespace(status=status, url=url))

    async def refresh_url(self):
        self.url = await self.context.run(lambda: self.context.driver.current_url)

    async def goto(self, url, **kwargs):
        await self.context.run(lambda: self.context.driver.get(url))
        await self.refresh_url()

    def locator(self, selector):
        return Locator(self.context, lambda: [self.context.driver]).locator(selector)

    def get_by_role(self, role):
        if role != "dialog":
            raise ValueError("unsupported_browser_role")
        return self.locator('[role="dialog"]')


class Locator:
    """Only the DOM operations used by the bounded X reader."""

    def __init__(self, context, find):
        self.context, self.find = context, find

    def locator(self, selector):
        by = "xpath" if selector == ".." else "css selector"
        return Locator(
            self.context,
            lambda: [
                child
                for node in self.find()
                for child in node.find_elements(by, selector)
            ],
        )

    @property
    def first(self):
        return Locator(self.context, lambda: self.find()[:1])

    @property
    def last(self):
        return Locator(self.context, lambda: self.find()[-1:])

    def get_by_text(self, text, exact=False):
        def find():
            nodes = self.find()
            if hasattr(text, "search"):
                return [
                    node
                    for node in nodes
                    if node.is_displayed() and text.search(node.text)
                ]
            # Batch DOM text checks; hundreds of WebDriver round trips per post
            # would consume the research deadline on ordinary article trees.
            return self.context.driver.execute_script(
                """
                const [roots, text, exact] = arguments;
                return roots.flatMap(root => [root, ...root.querySelectorAll('*')])
                    .filter(node => node.getClientRects().length &&
                        getComputedStyle(node).visibility !== 'hidden' &&
                        (exact ? node.innerText?.trim() === text : node.innerText?.includes(text)));
            """,
                nodes,
                text,
                exact,
            )

        return Locator(self.context, find)

    async def count(self):
        return await self.context.run(lambda: len(self.find()))

    async def all(self):
        nodes = await self.context.run(self.find)
        return [Locator(self.context, lambda node=node: [node]) for node in nodes]

    async def get_attribute(self, name):
        return await self.context.run(lambda: self.find()[0].get_dom_attribute(name))

    async def inner_text(self):
        return await self.context.run(lambda: self.find()[0].text)

    async def wait_for(self, timeout=10000):
        from selenium.webdriver.support.ui import WebDriverWait

        await self.context.run(
            lambda: WebDriverWait(self.context.driver, timeout / 1000).until(
                lambda _: any(node.is_displayed() for node in self.find())
            )
        )

    async def scroll_into_view_if_needed(self):
        await self.context.run(
            lambda: self.context.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'end'});", self.find()[0]
            )
        )
