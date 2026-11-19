"""Live-server harness shared by the browser tests.

Every browser test drives a real uvicorn server on an ephemeral port with
headless Chromium over real HTTP. This module is the one place that knows how
to bring that up, sign a page in past the login screen, and paint a Topology
on the canvas; the test files hold only their tests and their payloads.

Imported only by the browser tests, never by ``conftest.py``: ``playwright``
is optional and the unprivileged suite must collect without it.
"""

import asyncio
import contextlib
import os
import socket
import threading
import time
import unittest
from typing import Any, Iterator, Literal

import pytest
import uvicorn
try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        FilePayload,
        FloatRect,
        Locator,
        Page,
        Playwright,
        Route,
        ViewportSize,
        expect,
        sync_playwright,
    )
except ImportError:
    Browser = Any  # type: ignore[misc,assignment]
    BrowserContext = Any  # type: ignore[misc,assignment]
    FilePayload = Any  # type: ignore[misc,assignment]
    FloatRect = Any  # type: ignore[misc,assignment]
    Locator = Any  # type: ignore[misc,assignment]
    Page = Any  # type: ignore[misc,assignment]
    Playwright = Any  # type: ignore[misc,assignment]
    Route = Any  # type: ignore[misc,assignment]
    ViewportSize = Any  # type: ignore[misc,assignment]
    sync_playwright = None  # type: ignore[assignment]
    expect = None  # type: ignore[assignment]

from mniplab.webserver import create_app
from mniplab.webserver.auth import active_sessions, create_session

# Names a folder to save review screenshots in; unset, no screenshot is taken.
MNIPLAB_TEST_SCREENSHOTS = "MNIPLAB_TEST_SCREENSHOTS"


def free_port() -> int:
    """Return an ephemeral TCP port free to bind on localhost."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def run_async(coro: Any) -> Any:
    """Run a coroutine to completion from a test that already holds a browser.

    Playwright's sync API keeps an event loop running on the test thread, so
    ``asyncio.run`` refuses there; the coroutine runs on its own thread with
    its own loop instead. Use it to register a running Lab with the
    ``SessionRegistry`` before driving its page.

    Args:
        coro: The coroutine to run.

    Returns:
        The coroutine's result.
    """
    outcome: dict[str, Any] = {}

    def runner() -> None:
        try:
            outcome["result"] = asyncio.run(coro)
        except BaseException as exc:  # re-raised on the caller's thread
            outcome["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


class LiveServer:
    """A uvicorn server on its own ephemeral port, startable and stoppable.

    ``LiveServerCase`` holds one per test class. A test that needs an endpoint
    to genuinely stop answering constructs a second one and stops it itself.
    """

    def __init__(self) -> None:
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(uvicorn.Config(
            create_app(),
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
        ))
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._stopped = False

    def start(self) -> None:
        """Start serving and block until the port is accepting connections."""
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("live server did not start within 10s")
            time.sleep(0.05)

    def stop(self) -> None:
        """Shut the server down; safe to call more than once."""
        if self._stopped:
            return
        self._stopped = True
        self._server.should_exit = True
        self._thread.join(timeout=5)


class LiveServerMixin:
    """Harness providing an ephemeral uvicorn server and Playwright browser instance."""

    playwright: Playwright | None = None
    browser: Browser | None = None
    server: LiveServer
    base_url: str = ""

    @classmethod
    def setUpClass(cls) -> None:
        if sync_playwright is None:
            raise unittest.SkipTest("playwright is not installed")
        cls.playwright = None
        cls.browser = None

        cls.server = LiveServer()
        cls.base_url = cls.server.base_url

        try:
            cls.server.start()
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.browser is not None:
            cls.browser.close()
        if cls.playwright is not None:
            cls.playwright.stop()

        if hasattr(cls, "server"):
            cls.server.stop()

    @contextlib.contextmanager
    def signed_in_page(
        self, role: str = "instructor", wait_for_topology: bool = True
    ) -> Iterator[Page]:
        """Yield a fresh page signed in past the login screen; close it afterwards.

        Args:
            role: The Session's role — ``"instructor"`` or ``"learner"``.
            wait_for_topology: Block until the topology WebSocket has connected
                and delivered its initial (empty) topology, so a test can
                inject its own node list on top. A test that observes the
                WebSocket's own connection states turns this off.
        """
        assert self.browser is not None
        # The Session store is process-global. Sessions leaked by earlier tests
        # would otherwise show up wherever the page enumerates them — the
        # instructor's signed-in list, and the Tab sequence through it.
        active_sessions.clear()
        session_id = create_session("browser-tester", role)
        context = self.browser.new_context()
        context.add_cookies([
            {"name": "session", "value": session_id, "url": self.base_url},
        ])
        try:
            page = context.new_page()
            page.goto(self.base_url + "/")
            page.wait_for_selector("#app:not(.hidden)")
            if wait_for_topology:
                page.wait_for_function("""async () => {
                    const { appState } = await import('/static/js/state/app-state.js');
                    return !!(appState.labsWs && appState.labsWs.readyState === 1);
                }""")
                page.wait_for_selector(
                    "#ws-status .ws-status-dot[data-state='connected']"
                )
                page.wait_for_selector(
                    "#topology-empty-state:not([style*='display: none']), "
                    "svg#topology:not([style*='display: none'])"
                )
            yield page
        finally:
            context.close()

    def render_topology(
        self,
        page: Page,
        topo: dict[str, Any],
        authored: dict[str, Any],
        settle_ms: int = 500,
    ) -> None:
        """(Re-)render ``topo`` on the already signed-in ``page``.

        ``showMainApp`` attaches the toolbar handlers and ``initTopology``
        builds the SVG and zoom behaviour. A complete Authored Layout makes
        ``renderTopology`` place every Node statically and pin it, so
        positions are stable for assertions.

        Args:
            page: A page from ``signed_in_page``.
            topo: The Topology payload, as the Lab's ``topology`` route would return it.
            authored: The Authored Layout that pins every Node in ``topo``.
            settle_ms: How long to let the fit-to-view transition run before
                returning; zoom assertions need the fitted transform.
        """
        page.evaluate(
            """async ({topo, authored}) => {
                const { appState } = await import('/static/js/state/app-state.js');
                const { showMainApp } = await import('/static/js/lab/auth.js');
                const { showTopologyEmptyState } = await import('/static/js/lab/lifecycle.js');
                const { initTopology, renderTopology } = await import('/static/js/topology/renderer.js');

                appState.isLabRunning = true;
                appState.labKey = 'fake';
                showTopologyEmptyState(false);
                await showMainApp();
                initTopology();
                appState.authoredLayout = authored;
                appState.isInitialLoad = true;
                renderTopology(topo);
            }""",
            {"topo": topo, "authored": authored},
        )
        page.wait_for_timeout(settle_ms)


class LiveServerCase(LiveServerMixin, unittest.TestCase):
    """Base class for browser tests: one live server and one Chromium per class.

    Subclasses inherit the ``browser`` and ``requires_browser`` marks; a
    browser test file adds only its area, one ``browser_*`` mark.
    """

    pytestmark = [pytest.mark.browser, pytest.mark.requires_browser]


def box_of(locator: Locator) -> FloatRect:
    """Return a locator's box on screen, failing when it takes no room.

    Args:
        locator: The element whose box to read.

    Returns:
        The element's bounding box in CSS pixels.
    """
    box = locator.bounding_box()
    assert box is not None, f"{locator} takes no room on the page"
    return box


# The account menu's item roles. Playwright's own role alias is not public, so
# this narrows it to the two that the callers pass.
AccountItemRole = Literal["menuitem", "menuitemcheckbox"]


def choose_account_item(page: Page, name: str, role: AccountItemRole = "menuitem",
                        exact: bool = True) -> None:
    """Open the header's account menu and choose one of its items.

    Your Session, Server settings, Log out, the Dark mode switch and
    Sessions & Terminals live in that menu, not on the header itself.

    Args:
        page: The signed-in page.
        name: The item's accessible name.
        role: ``menuitem``, or ``menuitemcheckbox`` for Dark mode and
            Sessions & Terminals.
        exact: Match ``name`` exactly; Your Session's name ends in its code.
    """
    page.locator("#account-menu-btn").click()
    page.get_by_role("menu").get_by_role(role, name=name, exact=exact).click()


def screenshots(page: Page, prefix: str, name: str, target: str | Locator | None = None,
                fit: bool = False, open_sidebar: bool = False) -> None:
    """Save the screen a test is showing, at each review width in light and dark.

    Does nothing unless ``MNIPLAB_TEST_SCREENSHOTS`` names a folder; the folder
    is created if missing. Each picture is ``<prefix>-<name>-<width>-<theme>.png``.
    The page's viewport size and ``data-theme`` are put back afterwards.

    Args:
        page: The page to photograph.
        prefix: Filename prefix, usually the ticket the screen belongs to.
        name: The screen's name in the filename.
        target: A control, or its selector, to scroll into view at each width.
        fit: Click the canvas Fit button at each width, after a 300 ms settle.
        open_sidebar: Open the folded Sidebar at any width where ``target``
            is hidden.
    """
    folder = os.environ.get(MNIPLAB_TEST_SCREENSHOTS)
    if not folder:
        return
    os.makedirs(folder, exist_ok=True)
    # A theme switch would otherwise be caught halfway through its colour transitions.
    page.add_style_tag(content="*, *::before, *::after { transition: none !important; }")
    viewport = page.viewport_size
    theme_before = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
    for theme in ("light", "dark"):
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        for width in (1280, 1024, 375):
            page.set_viewport_size({"width": width, "height": 720})
            if fit:
                page.locator("#canvas-zoom-fit").click()
                page.wait_for_timeout(300)
            if target is not None:
                control = page.locator(target) if isinstance(target, str) else target
                if open_sidebar and not control.is_visible():
                    page.get_by_role("button", name="Details", exact=True).click()
                control.scroll_into_view_if_needed()
            page.screenshot(path=os.path.join(folder, f"{prefix}-{name}-{width}-{theme}.png"))
    if viewport:
        page.set_viewport_size(viewport)
    page.evaluate("""theme => theme === null
        ? document.documentElement.removeAttribute('data-theme')
        : document.documentElement.setAttribute('data-theme', theme)""", theme_before)

