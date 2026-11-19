"""The running-Lab page as four Panes in one workspace.

Runs on ``mniplab/tests/browser.py``'s ``LiveServerCase``: a real uvicorn
server and headless Chromium, with a fake running Lab whose Terminals are
real ptys and whose Captures are fed the PDML fixtures. Every assertion is
about what a Learner sees or can do: where a Pane is and how big, whether a
session is still open, what a key or a button does, what a control is
called. Window sizes follow a laptop's browser viewport (screen minus about
110px of browser chrome).
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import struct
import termios
import time
from pathlib import Path
from typing import Callable

import pytest

from mniplab.tests.browser import FloatRect, LiveServerCase, Page, ViewportSize, box_of, expect, choose_account_item
from mniplab.tests.fakes import FakeCaptureSpawner
from mniplab.tests.test_web_capture import two_node_lab
from mniplab.tests.test_web_terminal_budget import FakeSpawner
from mniplab.webserver import registry
from mniplab.webserver.capture import CaptureService
from mniplab.webserver.services import TerminalService

pytestmark = pytest.mark.browser_workspace

FIXTURES = Path(__file__).parent / "fixtures" / "pdml"
ROWS = "#packet-analysis .capture-pane:not([hidden]) .capture-packet-rows tr"

LAPTOPS: tuple[ViewportSize, ...] = (
    {"width": 1366, "height": 658},
    {"width": 1440, "height": 790},
    {"width": 1920, "height": 970},
)
LAPTOP = LAPTOPS[0]
PHONE: ViewportSize = {"width": 375, "height": 812}

TOPOLOGY = ".main-content"
GUIDE = "#lab-guide-container"
TERMINAL = "#lab-tabpanel > [data-pane='terminal']"
PACKET = "#packet-analysis"


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll ``condition`` until it holds, or fail after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.05)


class PaneArrangementTests(LiveServerCase):
    """The default Pane arrangement and what each Pane shows."""

    def setUp(self) -> None:
        """A running two-Node Lab with real pty Terminals and fake Captures."""
        super().setUp()
        lab = two_node_lab()
        for node in lab.hosts:
            node.pid = os.getpid()
        old_lab = registry.lab_service.lab
        registry.lab_service.lab = lab
        registry.lab_service.lab_example = "static-lab"
        self.addCleanup(setattr, registry.lab_service, "lab", old_lab)

        self.spawner = FakeSpawner()
        self.addCleanup(self.spawner.cleanup)
        terminals = registry.terminal_service
        registry.terminal_service = TerminalService(spawner=self.spawner)
        self.addCleanup(setattr, registry, "terminal_service", terminals)

        self.captures = FakeCaptureSpawner()
        captures = registry.capture_service
        registry.capture_service = CaptureService(spawner=self.captures, grace_seconds=5)
        self.addCleanup(setattr, registry, "capture_service", captures)
        self.addCleanup(self._stop_captures)

    def _stop_captures(self) -> None:
        # On the server's own loop: that is where the service reads its pipes.
        if self.captures.loop is not None:
            asyncio.run_coroutine_threadsafe(
                registry.capture_service.stop_all("test over"), self.captures.loop
            ).result(timeout=10)

    def running_page(self, page: Page, viewport: ViewportSize | None = None) -> None:
        """Size the window and wait for the running Lab's Topology."""
        page.set_viewport_size(viewport or LAPTOP)
        page.wait_for_selector("g.node")
        expect(page.locator("#stop-lab-btn")).to_be_visible()

    def open_terminal(self, page: Page, name: str = "r1") -> None:
        """Open a Node's Terminal and wait for its live xterm surface."""
        page.evaluate("""async (name) => {
            const { appState } = await import('/static/js/state/app-state.js');
            appState.tabManager.openTerminal(name);
        }""", name)
        page.wait_for_selector(
            f".terminal-instance[data-node='{name}'] .xterm-helper-textarea"
        )
        page.wait_for_timeout(200)

    def open_capture(self, page: Page, interface: str = "h1-eth0") -> None:
        """Start a Capture from the r1–h1 Link's menu, as a Learner would."""
        page.evaluate("""async () => {
            const { appState } = await import('/static/js/state/app-state.js');
            const { showLinkContextMenu } = await import('/static/js/topology/interactions.js');
            const link = appState.currentTopology.links.find(l =>
                [l.source.id, l.target.id].sort().join() === 'h1,r1');
            showLinkContextMenu(link, { clientX: 0, clientY: 0 });
        }""")
        page.click("#link-capture-btn")
        page.click(f".link-capture-end[data-interface='{interface}']")
        wait_until(lambda: len(self.captures.processes) >= 1)
        page.wait_for_selector(
            "#packet-analysis .capture-pane:not([hidden]) "
            ".packet-analysis-status:has-text('Capturing on')"
        )
        page.wait_for_timeout(200)

    def emit(self, name: str = "dhcp") -> None:
        """Feed one PDML fixture into the running Capture."""
        self.captures.processes[0].emit((FIXTURES / f"{name}.pdml").read_bytes())

    @staticmethod
    def box(page: Page, selector: str) -> FloatRect:
        """The selector's box on screen; the selector must take room."""
        return box_of(page.locator(selector))

    @staticmethod
    def box_or_none(page: Page, selector: str) -> FloatRect | None:
        """The selector's box on screen, or None when it takes no room."""
        return page.locator(selector).bounding_box()

    def test_the_default_puts_guide_left_topology_centre_terminal_over_packet_analysis_right(self) -> None:
        for viewport in LAPTOPS:
            with self.subTest(viewport=viewport), self.signed_in_page() as page:
                self.running_page(page, viewport)
                self.open_terminal(page)
                self.open_capture(page)
                workspace = self.box(page, "#lab-tabpanel")
                guide = self.box(page, GUIDE)
                topology = self.box(page, TOPOLOGY)
                terminal = self.box(page, TERMINAL)
                packet = self.box(page, PACKET)

                for name, pane in (("Guide", guide), ("Topology", topology),
                                   ("Terminal", terminal), ("Packet Analysis", packet)):
                    self.assertIsNotNone(pane, name)
                self.assertAlmostEqual(guide["x"], workspace["x"], delta=8)
                self.assertGreaterEqual(topology["x"], guide["x"] + guide["width"])
                self.assertGreaterEqual(terminal["x"], topology["x"] + topology["width"])
                self.assertAlmostEqual(packet["x"], terminal["x"], delta=1)
                self.assertGreaterEqual(packet["y"], terminal["y"] + terminal["height"])
                self.assertAlmostEqual(
                    terminal["x"] + terminal["width"], workspace["x"] + workspace["width"], delta=8
                )
                self.assertAlmostEqual(guide["width"], workspace["width"] * 0.2, delta=16)
                self.assertAlmostEqual(terminal["width"], workspace["width"] * 0.4, delta=16)

    def test_the_guide_and_packet_analysis_are_panes_of_the_workspace_and_never_move_out(self) -> None:
        parents = (
            "() => ['topology', 'guide', 'terminal', 'packet'].map(id => "
            "document.querySelector(`[data-pane='${id}']`).parentElement.id)"
        )
        with self.signed_in_page() as page:
            self.running_page(page)
            before = page.evaluate(parents)
            guide = page.locator(GUIDE).element_handle()
            packet = page.locator(PACKET).element_handle()
            self.open_terminal(page)
            self.open_capture(page)
            page.get_by_role("button", name="Close all terminals").click()
            page.wait_for_timeout(200)
            after = page.evaluate(parents)
            same = page.evaluate(
                "([g, p]) => g === document.getElementById('lab-guide-container')"
                " && p === document.getElementById('packet-analysis')",
                [guide, packet],
            )

        self.assertEqual(before, ["lab-tabpanel"] * 4)
        self.assertEqual(after, ["lab-tabpanel"] * 4)
        self.assertTrue(same)

    def test_a_pane_with_nothing_to_show_takes_no_room(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            workspace = self.box(page, "#lab-tabpanel")
            alone = self.box(page, TOPOLOGY)
            nothing_open = (self.box_or_none(page, TERMINAL), self.box_or_none(page, PACKET))

            self.open_capture(page)
            packet_only = (self.box_or_none(page, TERMINAL), self.box(page, PACKET))

            self.open_terminal(page)
            page.get_by_role("button", name="Stop capture").click()
            page.wait_for_timeout(300)
            terminal_only = (self.box(page, TERMINAL), self.box_or_none(page, PACKET))

        self.assertEqual(nothing_open, (None, None))
        # The empty Right Slot takes no room: the Topology reaches the edge.
        self.assertAlmostEqual(
            alone["x"] + alone["width"], workspace["x"] + workspace["width"], delta=8
        )
        self.assertIsNone(packet_only[0])
        self.assertAlmostEqual(packet_only[1]["height"], workspace["height"], delta=8)
        self.assertIsNone(terminal_only[1])
        self.assertAlmostEqual(terminal_only[0]["height"], workspace["height"], delta=8)

    def test_shrinking_then_growing_the_window_keeps_every_pane_inside_the_page(self) -> None:
        inside = """() => [...document.querySelectorAll('[data-pane]')]
            .filter(el => el.getClientRects().length)
            .every(el => {
                const b = el.getBoundingClientRect();
                return b.left >= 0 && b.right <= window.innerWidth + 0.5
                    && b.top >= 0 && b.bottom <= window.innerHeight + 0.5;
            })"""
        with self.signed_in_page() as page:
            self.running_page(page, {"width": 1920, "height": 970})
            self.open_terminal(page)
            self.open_capture(page)
            seen = {}
            sizes: tuple[ViewportSize, ...] = (
                {"width": 1100, "height": 640}, {"width": 900, "height": 600},
                {"width": 1920, "height": 970},
            )
            for viewport in sizes:
                page.set_viewport_size(viewport)
                page.wait_for_timeout(300)
                seen[viewport["width"]] = (page.evaluate(inside), self.box(page, TERMINAL))

        for width, (fits, _) in seen.items():
            with self.subTest(width=width):
                self.assertTrue(fits)
        self.assertAlmostEqual(seen[1920][1]["width"], 1920 * 0.4, delta=16)

    def test_at_phone_width_the_open_panes_stack_topology_guide_terminal_packet_analysis(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page)
            self.open_capture(page)
            page.set_viewport_size(PHONE)
            page.wait_for_timeout(300)
            boxes = [
                page.locator(sel).evaluate("el => { const b = el.getBoundingClientRect();"
                                           " return {x: b.x, y: b.y, width: b.width, height: b.height}; }")
                for sel in (TOPOLOGY, GUIDE, TERMINAL, PACKET)
            ]
            tab_order = page.evaluate(
                "() => [...document.querySelectorAll('#lab-tabpanel > [data-pane]')]"
                ".map(el => el.dataset.pane)"
            )

        for upper, lower in zip(boxes, boxes[1:]):
            self.assertGreaterEqual(lower["y"], upper["y"] + upper["height"])
            self.assertAlmostEqual(lower["x"], upper["x"], delta=1)
            self.assertAlmostEqual(lower["width"], upper["width"], delta=1)
        for pane in boxes:
            self.assertGreaterEqual(pane["height"], 300)
        self.assertEqual(tab_order, ["topology", "guide", "terminal", "packet"])

    def test_the_guide_title_row_stays_put_while_the_guide_text_scrolls(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            page.wait_for_selector("#lab-guide-content h1, #lab-guide-content h2")
            heading = page.get_by_role("heading", name="Guide", exact=True)
            before = heading.bounding_box()
            text = page.locator("#lab-guide-content")
            scrollable = text.evaluate("el => el.scrollHeight > el.clientHeight")
            text.evaluate("el => { el.scrollTop = el.scrollHeight; }")
            scrolled = text.evaluate("el => el.scrollTop")
            after = heading.bounding_box()

        self.assertTrue(scrollable)
        self.assertGreater(scrolled, 0)
        self.assertEqual(before, after)

    def test_the_nodes_list_running_tabs_position_radio_separators_expand_and_full_view_are_gone(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page)
            self.open_capture(page)
            page.get_by_role("button", name="Open view and layout options").click()
            view_menu_radios = page.locator("#view-options-menu").get_by_role("radio").count()
            page.keyboard.press("Escape")
            separators = sum(
                page.get_by_role("separator", name=name).count()
                for name in ("Resize Terminal", "Resize Packet Analysis")
            )
            gone = {
                "Nodes list": page.locator("#node-list").count(),
                "running tabs": page.get_by_role("tab", name="Nodes", exact=True).count(),
                "Expand": page.get_by_role("button", name="Expand Packet Analysis").count(),
                "Full view": page.get_by_role("button", name="Expand terminal to full view").count(),
            }

        self.assertEqual(view_menu_radios, 0)
        self.assertEqual(separators, 0)
        self.assertEqual(gone, dict.fromkeys(gone, 0))

    def test_while_a_lab_runs_the_sidebar_button_is_hidden_and_ctrl_b_changes_nothing(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            page.locator("svg#topology").click(position={"x": 5, "y": 5})
            boxes = {pane: self.box(page, sel) for pane, sel in (("topology", TOPOLOGY), ("guide", GUIDE))}
            button = page.locator("#toggle-sidebar")
            hidden = button.is_hidden()
            page.keyboard.press("Control+b")
            page.wait_for_timeout(400)
            after = {pane: self.box(page, sel) for pane, sel in (("topology", TOPOLOGY), ("guide", GUIDE))}
            sidebar_hidden = page.locator(".sidebar").is_hidden()

        self.assertTrue(hidden)
        self.assertTrue(sidebar_hidden)
        self.assertEqual(after, boxes)

    def test_ctrl_t_and_the_terminal_button_hide_and_show_the_terminal_pane(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page)
            page.locator("svg#topology").click(position={"x": 5, "y": 5})
            page.keyboard.press("Control+t")
            page.wait_for_timeout(200)
            hidden_by_key = self.box_or_none(page, TERMINAL)
            page.keyboard.press("Control+t")
            page.wait_for_timeout(200)
            shown_by_key = self.box_or_none(page, TERMINAL)
            page.locator("#toggle-terminal-pane").click()
            page.wait_for_timeout(200)
            hidden_by_button = self.box_or_none(page, TERMINAL)
            page.locator("#toggle-terminal-pane").click()
            page.wait_for_timeout(200)
            shown_by_button = self.box_or_none(page, TERMINAL)
            page.locator("#terminal-r1 .xterm-helper-textarea").focus()
            page.keyboard.type("still-here")
            expect(page.locator("#terminal-r1 .xterm-rows")).to_contain_text("still-here")

        self.assertIsNone(hidden_by_key)
        self.assertIsNotNone(shown_by_key)
        self.assertIsNone(hidden_by_button)
        self.assertIsNotNone(shown_by_button)

    def test_the_topology_buttons_are_reachable_and_every_pane_is_a_named_region(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page)
            self.open_capture(page)
            role = page.locator("svg#topology").get_attribute("role")
            label = page.locator("svg#topology").get_attribute("aria-label")
            node_buttons = page.locator("svg#topology").get_by_role(
                "button", name="router r1", exact=False
            ).count()
            link_buttons = page.locator("svg#topology").get_by_role(
                "button", name="Link between", exact=False
            ).count()
            regions = {
                name: page.get_by_role("region", name=name, exact=True).count()
                for name in ("Topology", "Guide", "Terminal", "Packet Analysis")
            }

        self.assertIsNone(role)
        self.assertEqual(label, "Network topology visualization")
        self.assertGreaterEqual(node_buttons, 1)
        self.assertGreaterEqual(link_buttons, 1)
        self.assertEqual(regions, dict.fromkeys(regions, 1))

    def pty_size(self, index: int = 0) -> tuple[int, int]:
        """The window size a shell on the Terminal's pty would read: (cols, rows)."""
        packed = fcntl.ioctl(self.spawner.slaves[index], termios.TIOCGWINSZ, b"\0" * 8)
        rows, cols, _, _ = struct.unpack("HHHH", packed)
        return cols, rows

    def terminal_size(self, page: Page, name: str = "r1") -> tuple[int, int]:
        """The columns and rows the Terminal shows."""
        return tuple(page.evaluate("""async (name) => {
            const { appState } = await import('/static/js/state/app-state.js');
            const term = appState.tabManager.terminals.get(name).term;
            return [term.cols, term.rows];
        }""", name))

    def wait_for_pty_to_match(self, page: Page) -> tuple[int, int]:
        """Wait until the pty's window size is the size the Terminal shows."""
        deadline = time.monotonic() + 5
        while self.pty_size() != self.terminal_size(page):
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"pty {self.pty_size()} never matched {self.terminal_size(page)}"
                )
            page.wait_for_timeout(50)
        return self.pty_size()

    def test_a_terminal_echoes_and_is_resized_as_packet_analysis_comes_and_goes(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page)
            page.locator("#terminal-r1 .xterm-helper-textarea").focus()
            page.keyboard.type("hello-pane")
            expect(page.locator("#terminal-r1 .xterm-rows")).to_contain_text("hello-pane")
            alone = self.wait_for_pty_to_match(page)
            self.open_capture(page)
            page.wait_for_timeout(300)
            shared = self.wait_for_pty_to_match(page)
            self.emit()
            page.wait_for_function(f"() => document.querySelectorAll('{ROWS}').length === 4")
            self.emit()
            page.wait_for_function(f"() => document.querySelectorAll('{ROWS}').length === 8")

        self.assertEqual(alone[0], shared[0])
        self.assertLess(shared[1], alone[1], "fewer rows above Packet Analysis")

    def test_the_instructors_overview_covers_the_whole_workspace(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            self.open_terminal(page)
            self.open_capture(page)
            workspace = self.box(page, "#lab-tabpanel")
            choose_account_item(page, "Sessions & Terminals", "menuitemcheckbox")
            overview = self.box(page, "#workspace-monitor")
            covered = [page.locator(sel).is_visible() for sel in (TOPOLOGY, GUIDE, TERMINAL, PACKET)]
            page.get_by_role("button", name="Back to topology", exact=True).click()
            back = [page.locator(sel).is_visible() for sel in (TOPOLOGY, GUIDE, TERMINAL, PACKET)]

        self.assertAlmostEqual(overview["x"], workspace["x"], delta=1)
        self.assertAlmostEqual(overview["width"], workspace["width"], delta=1)
        self.assertAlmostEqual(overview["height"], workspace["height"], delta=1)
        self.assertEqual(covered, [False] * 4)
        self.assertEqual(back, [True] * 4)

    # -- Moving Panes from their Pane actions menus -----------------------

    PANE_NAMES = {"topology": "Topology", "guide": "Guide", "terminal": "Terminal",
                  "packet": "Packet Analysis"}
    SELECTORS = {"topology": TOPOLOGY, "guide": GUIDE, "terminal": TERMINAL, "packet": PACKET}

    def actions(self, page: Page, pane: str):
        """A Pane's Pane actions menu button."""
        return page.get_by_role("button", name=f"{self.PANE_NAMES[pane]} pane actions", exact=True)

    def menu_items(self, page: Page, pane: str) -> list[str]:
        """Open a Pane's menu and list its items; the menu is left open."""
        self.actions(page, pane).click()
        return page.get_by_role("menuitem").all_text_contents()

    def choose(self, page: Page, pane: str, item: str) -> None:
        """Pick one item from a Pane's menu and let the Panes slide into place."""
        self.actions(page, pane).click()
        page.get_by_role("menuitem", name=item, exact=True).click()
        page.wait_for_timeout(250)

    def dom_order(self, page: Page) -> list[str]:
        """The Panes in the order they sit in the page."""
        return page.evaluate(
            "() => [...document.querySelectorAll('#lab-tabpanel > [data-pane]')]"
            ".map(el => el.dataset.pane)"
        )

    def shown_order(self, page: Page) -> list[str]:
        """The Panes on show, left to right and top to bottom, by their boxes."""
        boxes = {p: self.box_or_none(page, s) for p, s in self.SELECTORS.items()}
        shown = {p: b for p, b in boxes.items() if b}
        return sorted(shown, key=lambda p: (round(shown[p]["x"] / 40), shown[p]["y"]))

    def prepared_page(self, page: Page) -> None:
        """A running Lab with a Terminal and a Capture open."""
        self.running_page(page)
        self.open_terminal(page)
        self.open_capture(page)

    def test_every_title_bar_has_a_grip_and_a_pane_actions_menu_button(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            counts = {pane: self.actions(page, pane).count() for pane in self.PANE_NAMES}
            grips = page.locator("#lab-tabpanel > [data-pane] .pane-grip").count()
            bars = {
                "topology": page.locator(".controls .pane-menu").count(),
                "guide": page.locator(".lab-guide-toolbar .pane-menu").count(),
                "terminal": page.locator(".terminal-tab-bar .pane-menu").count(),
                "packet": page.locator(".packet-analysis-tabbar .pane-menu").count(),
            }
            names = [page.get_by_role("heading", name=n, exact=True).count()
                     for n in ("Guide", "Packet Analysis")]

        self.assertEqual(counts, dict.fromkeys(counts, 1))
        self.assertEqual(grips, 4)
        self.assertEqual(bars, dict.fromkeys(bars, 1))
        self.assertEqual(names, [1, 1])

    def test_every_menu_item_is_on_screen_whichever_slot_and_window_the_pane_is_in(self) -> None:
        def stray(page: Page) -> list[str]:
            # The open menu only: the header's account menu shares the list's look.
            return page.evaluate("""() => [...document.querySelectorAll('.pane-menu-list:not([hidden]) [role=menuitem]')]
                .filter(el => { const r = el.getBoundingClientRect();
                    return r.left < 0 || r.top < 0 || r.right > innerWidth
                        || r.bottom > innerHeight || !r.width; })
                .map(el => el.textContent)""")

        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.choose(page, "terminal", "Move to Left")
            self.choose(page, "packet", "Move to Bottom")
            seen = {}
            for height in (658, 300):
                page.set_viewport_size({"width": LAPTOP["width"], "height": height})
                page.wait_for_timeout(250)
                for pane in self.PANE_NAMES:
                    self.menu_items(page, pane)
                    seen[(height, pane)] = stray(page)
                    page.keyboard.press("Escape")
            self.menu_items(page, "packet")
            last = page.get_by_role("menuitem").last
            last.scroll_into_view_if_needed()
            reachable = last.bounding_box()

        self.assertEqual(seen, {key: [] for key in seen})
        self.assertIsNotNone(reachable)

    def test_the_menu_works_with_the_keyboard(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            button = self.actions(page, "terminal")
            button.focus()
            page.keyboard.press("Enter")
            items = page.get_by_role("menuitem")
            expect(items.first).to_be_focused()
            page.keyboard.press("ArrowDown")
            expect(items.nth(1)).to_be_focused()
            page.keyboard.press("End")
            expect(items.last).to_be_focused()
            page.keyboard.press("ArrowDown")
            expect(items.first).to_be_focused()
            page.keyboard.press("ArrowUp")
            expect(items.last).to_be_focused()
            page.keyboard.press("Home")
            expect(items.first).to_be_focused()
            page.keyboard.press("Escape")
            expect(items).to_have_count(0)
            expect(button).to_be_focused()
            expect(button).to_have_attribute("aria-expanded", "false")
            page.keyboard.press("Space")
            expect(items.first).to_be_visible()
            page.keyboard.press("Tab")
            expect(items).to_have_count(0)
            button.focus()
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            page.wait_for_timeout(250)
            moved = self.box(page, TERMINAL)

        self.assertAlmostEqual(moved["x"], 0, delta=16)

    def test_an_outside_click_closes_an_open_menu(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.menu_items(page, "guide")
            page.locator("svg#topology").click(position={"x": 500, "y": 480})
            expect(page.get_by_role("menuitem")).to_have_count(0)

    def test_a_pane_outside_the_centre_offers_the_other_slots_the_centre_and_a_swap(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            terminal = self.menu_items(page, "terminal")
            page.keyboard.press("Escape")
            guide = self.menu_items(page, "guide")
            page.keyboard.press("Escape")

        self.assertEqual(terminal, ["Move to Left", "Move to Bottom",
                                    "Move to Centre, swapping with Topology",
                                    "Swap places with Packet Analysis", "Full view", "Close"])
        self.assertEqual(guide, ["Move to Right (full)", "Move to Bottom",
                                 "Move to Centre, swapping with Topology", "Full view", "Close"])

    def test_a_full_slot_is_shown_but_unavailable(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            before = self.box(page, GUIDE)
            self.actions(page, "guide").click()
            full = page.get_by_role("menuitem", name="Move to Right (full)", exact=True)
            expect(full).to_have_attribute("aria-disabled", "true")
            full.click(force=True)
            page.wait_for_timeout(250)
            after = self.box(page, GUIDE)

        self.assertEqual(before, after)

    def test_the_pane_in_the_centre_offers_swaps_and_full_view_and_the_topology_never_closes(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            topology = self.menu_items(page, "topology")

        self.assertEqual(topology, ["Move to Left, swapping with Guide",
                                    "Move to Right, swapping with Terminal",
                                    "Move to Right, swapping with Packet Analysis", "Full view"])

    def test_moving_to_a_side_slot_puts_the_pane_there(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            workspace = self.box(page, "#lab-tabpanel")
            self.choose(page, "terminal", "Move to Left")
            left = self.box(page, TERMINAL)
            guide = self.box(page, GUIDE)
            packet = self.box(page, PACKET)
            topology = self.box(page, TOPOLOGY)
            self.choose(page, "packet", "Move to Bottom")
            bottom = self.box(page, PACKET)
            topology_after = self.box(page, TOPOLOGY)

        self.assertAlmostEqual(left["x"], workspace["x"], delta=8)
        self.assertAlmostEqual(guide["x"], left["x"], delta=1)
        self.assertGreaterEqual(left["y"], guide["y"] + guide["height"] - 1)
        self.assertGreater(packet["x"], topology["x"] + topology["width"] - 1)
        self.assertAlmostEqual(bottom["x"], workspace["x"], delta=8)
        self.assertGreater(bottom["y"], topology_after["y"] + topology_after["height"] - 1)
        self.assertAlmostEqual(bottom["width"], workspace["width"], delta=16)

    def test_two_panes_at_the_bottom_sit_side_by_side(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.choose(page, "packet", "Move to Bottom")
            self.choose(page, "terminal", "Move to Bottom")
            terminal = self.box(page, TERMINAL)
            packet = self.box(page, PACKET)

        self.assertAlmostEqual(terminal["y"], packet["y"], delta=1)
        self.assertGreater(terminal["x"], packet["x"] + packet["width"] - 1)

    def test_swapping_places_with_the_pane_in_the_same_slot(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.choose(page, "terminal", "Swap places with Packet Analysis")
            terminal = self.box(page, TERMINAL)
            packet = self.box(page, PACKET)

        self.assertGreater(terminal["y"], packet["y"] + packet["height"] - 1)

    def test_a_pane_moved_to_the_centre_swaps_with_the_topology(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            before = self.box(page, TERMINAL)
            topology_before = self.box(page, TOPOLOGY)
            self.choose(page, "terminal", "Move to Centre, swapping with Topology")
            terminal = self.box(page, TERMINAL)
            topology = self.box(page, TOPOLOGY)

        self.assertAlmostEqual(terminal["x"], topology_before["x"], delta=2)
        self.assertAlmostEqual(terminal["width"], topology_before["width"], delta=2)
        self.assertAlmostEqual(topology["x"], before["x"], delta=2)
        self.assertAlmostEqual(topology["width"], before["width"], delta=2)

    def test_the_centre_pane_swaps_with_any_other_pane_from_its_own_menu(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            guide_before = self.box(page, GUIDE)
            topology_before = self.box(page, TOPOLOGY)
            self.choose(page, "topology", "Move to Left, swapping with Guide")
            guide = self.box(page, GUIDE)
            topology = self.box(page, TOPOLOGY)

        # The Topology at the Left is at least 300 wide, so the Centre starts
        # where the Left Slot ends, whatever that width is.
        self.assertAlmostEqual(guide["x"], topology["x"] + topology["width"] + 6, delta=2)
        self.assertAlmostEqual(topology["x"], guide_before["x"], delta=2)

    def test_with_every_terminal_closed_the_topology_is_shown_in_the_centre(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            centre = self.box(page, TOPOLOGY)
            self.choose(page, "terminal", "Move to Centre, swapping with Topology")
            page.get_by_role("button", name="Close all terminals").click()
            page.wait_for_timeout(300)
            terminal = self.box_or_none(page, TERMINAL)
            topology = self.box(page, TOPOLOGY)
            self.open_terminal(page, "h1")
            back = self.box(page, TERMINAL)

        self.assertIsNone(terminal)
        self.assertAlmostEqual(topology["x"], centre["x"], delta=2)
        self.assertAlmostEqual(topology["width"], centre["width"], delta=2)
        self.assertAlmostEqual(back["width"], centre["width"], delta=2)

    def test_the_page_order_and_tab_order_follow_the_screen_after_each_move(self) -> None:
        moves = (
            ("terminal", "Move to Left", ["guide", "terminal", "topology", "packet"]),
            ("packet", "Move to Bottom", ["guide", "terminal", "topology", "packet"]),
            ("terminal", "Move to Centre, swapping with Topology",
             ["guide", "topology", "terminal", "packet"]),
            ("guide", "Swap places with Topology", ["topology", "guide", "terminal", "packet"]),
        )
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.assertEqual(self.dom_order(page)[:4], ["guide", "topology", "terminal", "packet"])
            for pane, item, order in moves:
                with self.subTest(item=item):
                    self.choose(page, pane, item)
                    self.assertEqual(self.dom_order(page)[:4], order)

    def test_focus_stays_on_the_menu_button_that_made_the_move(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            for pane, item in (("terminal", "Move to Left"),
                               ("terminal", "Move to Centre, swapping with Topology"),
                               ("guide", "Move to Bottom")):
                with self.subTest(item=item):
                    self.actions(page, pane).focus()
                    page.keyboard.press("Enter")
                    page.get_by_role("menuitem", name=item, exact=True).focus()
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(250)
                    expect(self.actions(page, pane)).to_be_focused()

    def test_every_session_survives_every_kind_of_move(self) -> None:
        moves = (
            ("terminal", "Move to Left"),
            ("packet", "Move to Bottom"),
            ("terminal", "Move to Centre, swapping with Topology"),
            ("topology", "Move to Centre, swapping with Terminal"),
            ("guide", "Move to Right"),
        )
        with self.signed_in_page() as page:
            self.prepared_page(page)
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                const ws = appState.tabManager.terminals.get('r1').ws;
                window.__terminalSocket = ws;
            }""")
            typed = 0
            rows = 0
            for pane, item in moves:
                with self.subTest(item=item):
                    self.choose(page, pane, item)
                    page.wait_for_timeout(300)
                    open_ = page.evaluate("() => window.__terminalSocket.readyState === 1")
                    self.assertTrue(open_)
                    typed += 1
                    page.locator("#terminal-r1 .xterm-helper-textarea").focus()
                    word = f"echo{typed}"
                    page.keyboard.type(word)
                    expect(page.locator("#terminal-r1 .xterm-rows")).to_contain_text(word)
                    self.wait_for_pty_to_match(page)
                    self.emit()
                    rows += 4
                    page.wait_for_function(f"() => document.querySelectorAll('{ROWS}').length === {rows}")

    def test_the_pty_gets_the_new_window_size_when_the_terminal_moves(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            before = self.wait_for_pty_to_match(page)
            self.choose(page, "terminal", "Move to Bottom")
            wide = self.wait_for_pty_to_match(page)

        self.assertGreater(wide[0], before[0])
        self.assertLessEqual(wide[1], before[1])

    def test_each_move_is_announced_once(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            status = page.locator("#pane-arrangement-status")
            page.evaluate("""() => {
                const status = document.getElementById('pane-arrangement-status');
                window.__announced = 0;
                new MutationObserver(() => { if (status.textContent) window.__announced += 1; })
                    .observe(status, { childList: true, characterData: true, subtree: true });
            }""")
            self.choose(page, "terminal", "Move to Left")
            expect(status).to_have_text("Terminal pane moved to the Left")
            self.choose(page, "packet", "Move to Bottom")
            expect(status).to_have_text("Packet Analysis pane moved to the Bottom")
            self.choose(page, "terminal", "Move to Centre, swapping with Topology")
            expect(status).to_have_text("Terminal pane swapped with Topology pane")
            page.wait_for_timeout(300)
            count = page.evaluate("() => window.__announced")

        self.assertEqual(count, 3)

    def test_the_arrangement_survives_a_reload_with_the_topology_at_a_side(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.choose(page, "terminal", "Move to Centre, swapping with Topology")
            self.choose(page, "guide", "Move to Bottom")
            expected = self.shown_order(page)
            page.reload()
            self.running_page(page)
            self.open_terminal(page)
            self.open_capture(page)
            after = self.shown_order(page)
            terminal = self.box(page, TERMINAL)
            topology = self.box(page, TOPOLOGY)

        self.assertEqual(after, expected)
        self.assertGreater(topology["x"], terminal["x"] + terminal["width"] - 1)

    def test_a_lab_starting_again_keeps_the_arrangement(self) -> None:
        relay = """async (running) => {
            const { appState } = await import('/static/js/state/app-state.js');
            const { layoutPanes } = await import('/static/js/ui/pane-arrangement.js');
            appState.isLabRunning = running;
            layoutPanes();
        }"""
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.choose(page, "terminal", "Move to Centre, swapping with Topology")
            before = self.shown_order(page)
            page.evaluate(relay, False)
            page.evaluate(relay, True)
            page.wait_for_timeout(300)
            after = self.shown_order(page)

        self.assertEqual(after, before)

    def test_reset_pane_arrangement_returns_the_default(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            default = self.shown_order(page)
            self.choose(page, "terminal", "Move to Centre, swapping with Topology")
            self.assertNotEqual(self.shown_order(page), default)
            page.get_by_role("button", name="Open view and layout options").click()
            page.get_by_role("button", name="Reset pane arrangement", exact=True).click()
            page.wait_for_timeout(300)
            after = self.shown_order(page)
            status = page.locator("#pane-arrangement-status").text_content()

        self.assertEqual(after, default)
        self.assertEqual(status, "Pane arrangement reset")

    def test_reset_browser_preferences_returns_the_default(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            default = self.shown_order(page)
            self.choose(page, "terminal", "Move to Centre, swapping with Topology")
            page.get_by_role("button", name="Help", exact=True).click()
            page.get_by_role("button", name="Reset browser preferences…", exact=True).click()
            with page.expect_navigation(wait_until="domcontentloaded"):
                page.get_by_role("button", name="Reset and reload", exact=True).click()
            self.running_page(page)
            self.open_terminal(page)
            self.open_capture(page)
            after = self.shown_order(page)

        self.assertEqual(after, default)

    def test_a_malformed_saved_arrangement_is_replaced_by_the_default(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            default = self.shown_order(page)
            page.evaluate("""() => localStorage.setItem('mniplab-pane-arrangement',
                '{"slots":{"left":["guide","guide"],"centre":[]}}')""")
            page.reload()
            self.running_page(page)
            self.open_terminal(page)
            self.open_capture(page)
            after = self.shown_order(page)

        self.assertEqual(after, default)

    def test_panes_are_reordered_only_when_the_order_changes_never_on_a_resize(self) -> None:
        count = "() => window.__moves"
        with self.signed_in_page() as page:
            self.prepared_page(page)
            page.evaluate("""() => {
                window.__moves = 0;
                const original = Element.prototype.moveBefore;
                const insert = Element.prototype.insertBefore;
                if (original) Element.prototype.moveBefore = function (...a) {
                    window.__moves += 1; return original.apply(this, a); };
                Element.prototype.insertBefore = function (...a) {
                    if (this.id === 'lab-tabpanel') window.__moves += 1;
                    return insert.apply(this, a); };
            }""")
            sizes: tuple[ViewportSize, ...] = (
                {"width": 1100, "height": 640}, {"width": 1920, "height": 970}, LAPTOP,
            )
            for viewport in sizes:
                page.set_viewport_size(viewport)
                page.wait_for_timeout(300)
            resized = page.evaluate(count)
            self.choose(page, "terminal", "Move to Left")
            moved = page.evaluate(count)

        self.assertEqual(resized, 0)
        self.assertGreater(moved, 0)

    def test_at_phone_width_only_full_view_and_close_are_offered_and_no_move_items(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            page.set_viewport_size(PHONE)
            page.wait_for_timeout(300)
            visible = [self.actions(page, pane).is_visible() for pane in self.PANE_NAMES]
            items = {}
            for pane in self.PANE_NAMES:
                items[pane] = self.menu_items(page, pane)
                page.keyboard.press("Escape")

        self.assertEqual(visible, [True] * 4)
        self.assertEqual(items, {
            "topology": ["Full view"], "guide": ["Full view", "Close"],
            "terminal": ["Full view", "Close"], "packet": ["Full view", "Close"],
        })

    def test_at_phone_width_a_pane_can_be_closed_from_its_menu(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            page.set_viewport_size(PHONE)
            page.wait_for_timeout(300)
            self.choose(page, "guide", "Close")
            gone = self.box_or_none(page, GUIDE)

        self.assertIsNone(gone)

    # -- Slot sizes: separators, minimums, short windows, the narrow toolbar --

    TALL: tuple[ViewportSize, ...] = ({"width": 1440, "height": 790}, {"width": 1920, "height": 970})
    EDGE = {"left": "Resize the Left Slot", "right": "Resize the Right Slot",
            "bottom": "Resize the Bottom Slot"}
    SPLIT = {"left": "Share the Left Slot", "right": "Share the Right Slot",
             "bottom": "Share the Bottom Slot"}

    @staticmethod
    def separator(page: Page, name: str):
        """A separator by its accessible name."""
        return page.get_by_role("separator", name=name, exact=True)

    def slot(self, page: Page, selector: str) -> dict[str, float]:
        """A Pane's grid area: its box plus the gutter the workspace leaves around it."""
        b = self.box(page, selector)
        return {"x": b["x"] - 3, "y": b["y"] - 3, "w": b["width"] + 6, "h": b["height"] + 6}

    def workspace(self, page: Page) -> FloatRect:
        """The Lab workspace's box."""
        return self.box(page, "#lab-tabpanel")

    def press(self, page: Page, name: str, key: str) -> None:
        """Focus a separator and press a key, as a keyboard user would."""
        self.separator(page, name).focus()
        page.keyboard.press(key)
        page.wait_for_timeout(120)

    def text(self, page: Page, name: str) -> str:
        """What a separator reports in words."""
        return self.separator(page, name).get_attribute("aria-valuetext")

    def stored(self, page: Page) -> dict:
        """The arrangement the page keeps."""
        return page.evaluate(
            "() => JSON.parse(localStorage.getItem('mniplab-pane-arrangement'))")

    def test_every_shown_slot_edge_and_split_is_a_named_separator_and_empty_slots_have_none(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            names = lambda: sorted(  # noqa: E731
                page.locator("[role=separator][data-separator]:visible").evaluate_all("els => els.map(e => e.getAttribute('aria-label'))"))
            default = names()
            self.choose(page, "packet", "Move to Bottom")
            bottom_one = names()
            self.choose(page, "terminal", "Move to Bottom")
            bottom_two = names()

        self.assertEqual(default, sorted([self.EDGE["left"], self.EDGE["right"], self.SPLIT["right"]]))
        self.assertEqual(bottom_one, sorted([self.EDGE["left"], self.EDGE["right"], self.EDGE["bottom"]]))
        self.assertEqual(bottom_two, sorted([self.EDGE["left"], self.EDGE["bottom"], self.SPLIT["bottom"]]))

    def test_each_separator_sits_in_the_page_order_beside_the_pane_it_resizes(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            order = lambda: page.evaluate("""() => [...document.querySelectorAll('#lab-tabpanel > *')]
                .filter(el => el.dataset.pane || el.getAttribute('role') === 'separator')
                .filter(el => !el.hidden && getComputedStyle(el).display !== 'none')
                .map(el => el.dataset.pane || el.getAttribute('aria-label'))""")  # noqa: E731
            default = order()
            self.separator(page, self.EDGE["left"]).focus()
            page.keyboard.press("Tab")
            landed = page.evaluate("() => document.activeElement.closest('[data-pane]')?.dataset.pane")
            self.choose(page, "packet", "Move to Bottom")
            self.choose(page, "guide", "Move to Right")
            moved = order()

        self.assertEqual(default, ["guide", self.EDGE["left"], "topology", self.EDGE["right"],
                                   "terminal", self.SPLIT["right"], "packet"])
        self.assertEqual(landed, "topology")
        self.assertEqual(moved, ["topology", self.EDGE["right"], "terminal", self.SPLIT["right"],
                                 "guide", self.EDGE["bottom"], "packet"])

    def test_the_left_edge_takes_arrows_home_and_end_and_reports_its_size_in_words(self) -> None:
        for viewport in LAPTOPS:
            with self.subTest(viewport=viewport), self.signed_in_page() as page:
                self.prepared_page_at(page, viewport)
                name = self.EDGE["left"]
                width = self.workspace(page)["width"]
                start = self.slot(page, GUIDE)["w"]
                self.assertEqual(self.text(page, name), f"Left Slot, {round(start / width * 100)}% of the width")
                self.press(page, name, "ArrowRight")
                grown = self.slot(page, GUIDE)["w"]
                self.press(page, name, "ArrowLeft")
                self.press(page, name, "ArrowLeft")
                shrunk = self.slot(page, GUIDE)["w"]
                self.press(page, name, "Home")
                home = self.slot(page, GUIDE)["w"]
                home_text = self.text(page, name)
                self.press(page, name, "End")
                centre = self.slot(page, TOPOLOGY)["w"]

                self.assertAlmostEqual(grown - start, width * 0.02, delta=3)
                self.assertAlmostEqual(start - shrunk, width * 0.02, delta=3)
                self.assertAlmostEqual(home, 240, delta=2)
                self.assertEqual(home_text, f"Left Slot, {int(240 / width * 100 + 0.5)}% of the width")
                self.assertAlmostEqual(centre, 400, delta=2)

    def test_the_right_edge_grows_with_the_arrow_pointing_away_from_the_slot(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            name = self.EDGE["right"]
            start = self.slot(page, TERMINAL)["w"]
            self.press(page, name, "ArrowLeft")
            grown = self.slot(page, TERMINAL)["w"]
            self.press(page, name, "ArrowRight")
            self.press(page, name, "ArrowRight")
            shrunk = self.slot(page, TERMINAL)["w"]
            self.press(page, name, "Home")
            home = self.slot(page, TERMINAL)["w"]
            self.press(page, name, "End")
            centre = self.slot(page, TOPOLOGY)["w"]

        self.assertGreater(grown, start + 10)
        self.assertLess(shrunk, start - 10)
        self.assertAlmostEqual(home, 420, delta=2)
        self.assertAlmostEqual(centre, 400, delta=2)

    def test_the_line_between_two_panes_moves_down_and_up_and_keeps_each_at_140(self) -> None:
        for viewport in LAPTOPS:
            with self.subTest(viewport=viewport), self.signed_in_page() as page:
                self.prepared_page_at(page, viewport)
                name = self.SPLIT["right"]
                self.assertIn("Terminal ", self.text(page, name))
                self.assertTrue(self.text(page, name).endswith("% of the Right Slot"))
                start = self.slot(page, TERMINAL)["h"]
                self.press(page, name, "ArrowDown")
                down = self.slot(page, TERMINAL)["h"]
                self.press(page, name, "ArrowUp")
                self.press(page, name, "ArrowUp")
                up = self.slot(page, TERMINAL)["h"]
                self.press(page, name, "Home")
                first = self.slot(page, TERMINAL)["h"]
                self.press(page, name, "End")
                second = self.slot(page, PACKET)["h"]

                self.assertGreater(down, start)
                self.assertLess(up, start)
                self.assertAlmostEqual(first, 140, delta=2)
                self.assertAlmostEqual(second, 140, delta=2)

    def prepared_page_at(self, page: Page, viewport: ViewportSize) -> None:
        """A running Lab with a Terminal and a Capture open, in a window of this size."""
        self.running_page(page, viewport)
        self.open_terminal(page)
        self.open_capture(page)

    def test_the_bottom_edge_and_the_line_between_side_by_side_panes(self) -> None:
        for viewport in self.TALL:
            with self.subTest(viewport=viewport), self.signed_in_page() as page:
                self.prepared_page_at(page, viewport)
                self.choose(page, "packet", "Move to Bottom")
                name = self.EDGE["bottom"]
                height = self.workspace(page)["height"]
                start = self.slot(page, PACKET)["h"]
                self.assertEqual(self.text(page, name), f"Bottom Slot, {round(start / height * 100)}% of the height")
                self.press(page, name, "ArrowUp")
                grown = self.slot(page, PACKET)["h"]
                self.press(page, name, "Home")
                home = self.slot(page, PACKET)["h"]
                self.press(page, name, "End")
                centre = self.slot(page, TOPOLOGY)["h"]
                self.choose(page, "terminal", "Move to Bottom")
                split = self.SPLIT["bottom"]
                self.press(page, split, "Home")
                first = self.slot(page, PACKET)["w"]
                self.press(page, split, "End")
                last = self.slot(page, TERMINAL)["w"]
                self.press(page, split, "ArrowLeft")
                nudged = self.slot(page, TERMINAL)["w"]

                self.assertGreater(grown, start)
                self.assertAlmostEqual(home, 260, delta=2)
                self.assertAlmostEqual(centre, 280, delta=2)
                self.assertAlmostEqual(first, 360, delta=2)
                self.assertAlmostEqual(last, 420, delta=2)
                self.assertGreater(nudged, last)

    def test_a_pointer_drag_moves_a_separator_and_stops_at_the_minimum(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            name = self.EDGE["left"]
            start = self.slot(page, GUIDE)["w"]
            width = self.workspace(page)["width"]
            at = self.box(page, f"[role=separator][aria-label='{name}']")
            x, y = at["x"] + at["width"] / 2, at["y"] + at["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 60, y, steps=6)
            page.mouse.up()
            page.wait_for_timeout(120)
            dragged = self.slot(page, GUIDE)["w"]
            text = self.text(page, name)
            at = self.box(page, f"[role=separator][aria-label='{name}']")
            page.mouse.move(at["x"] + at["width"] / 2, y)
            page.mouse.down()
            page.mouse.move(5, y, steps=8)
            page.mouse.up()
            page.wait_for_timeout(120)
            squeezed = self.slot(page, GUIDE)["w"]

        self.assertAlmostEqual(dragged - start, 60, delta=3)
        self.assertEqual(text, f"Left Slot, {round(dragged / width * 100)}% of the width")
        self.assertAlmostEqual(squeezed, 240, delta=2)

    def test_a_terminal_alone_in_a_side_slot_is_never_narrower_than_420(self) -> None:
        cases = (
            ("guide", None, GUIDE, "left", 240),
            ("terminal", ("packet", "Move to Left"), TERMINAL, "right", 420),
            ("packet", ("terminal", "Move to Left"), PACKET, "right", 360),
            ("topology", ("guide", "Move to Centre, swapping with Topology"), TOPOLOGY, "left", 300),
        )
        for pane, move, selector, side, minimum in cases:
            with self.subTest(pane=pane), self.signed_in_page() as page:
                self.prepared_page(page)
                if move:
                    self.choose(page, *move)
                self.press(page, self.EDGE[side], "Home")
                self.assertAlmostEqual(self.slot(page, selector)["w"], minimum, delta=2)

    def test_a_window_too_narrow_for_every_open_slot_keeps_the_centre_and_shares_the_rest(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            page.set_viewport_size({"width": 900, "height": 790})
            page.wait_for_timeout(300)
            left, centre, right = (self.slot(page, s) for s in (GUIDE, TOPOLOGY, TERMINAL))
            width = self.workspace(page)["width"]

        self.assertAlmostEqual(centre["w"], 400, delta=2)
        self.assertAlmostEqual(left["w"] + right["w"], width - 400, delta=2)
        self.assertAlmostEqual(left["w"] / right["w"], 240 / 420, delta=0.03)

    def test_sizes_survive_a_reload_and_reset_pane_arrangement_returns_the_defaults(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            default = self.slot(page, GUIDE)["w"]
            self.press(page, self.EDGE["left"], "ArrowRight")
            self.press(page, self.SPLIT["right"], "ArrowDown")
            kept = (self.slot(page, GUIDE)["w"], self.slot(page, TERMINAL)["h"])
            page.reload()
            self.prepared_page(page)
            after = (self.slot(page, GUIDE)["w"], self.slot(page, TERMINAL)["h"])
            page.get_by_role("button", name="Open view and layout options").click()
            page.get_by_role("button", name="Reset pane arrangement", exact=True).click()
            page.wait_for_timeout(300)
            reset = self.slot(page, GUIDE)["w"]

        self.assertGreater(kept[0], default)
        self.assertAlmostEqual(after[0], kept[0], delta=2)
        self.assertAlmostEqual(after[1], kept[1], delta=2)
        self.assertAlmostEqual(reset, default, delta=2)

    def test_the_topology_refits_once_when_a_drag_ends_not_on_every_move(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            page.evaluate("""async () => {
                window.__refits = 0;
                const { appState } = await import('/static/js/state/app-state.js');
                const svg = appState.svg.node();
                new MutationObserver(() => { window.__refits += 1; })
                    .observe(svg, { attributes: true, attributeFilter: ['width'] });
            }""")
            name = self.EDGE["left"]
            at = self.box(page, f"[role=separator][aria-label='{name}']")
            x, y = at["x"] + at["width"] / 2, at["y"] + at["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            for step in range(1, 9):
                page.mouse.move(x + step * 12, y)
                page.wait_for_timeout(60)
            during = page.evaluate("() => window.__refits")
            page.mouse.up()
            page.wait_for_timeout(300)
            after = page.evaluate("() => window.__refits")

        self.assertEqual(during, 0)
        self.assertEqual(after, 1)

    def test_in_a_window_too_short_for_the_bottom_its_panes_show_at_the_right_and_return(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            page.evaluate("""() => {
                const status = document.getElementById('pane-arrangement-status');
                window.__said = [];
                new MutationObserver(() => { if (status.textContent) window.__said.push(status.textContent); })
                    .observe(status, { childList: true, characterData: true, subtree: true });
            }""")
            page.set_viewport_size(self.TALL[0])
            page.wait_for_timeout(300)
            self.choose(page, "packet", "Move to Bottom")
            tall = self.slot(page, PACKET)
            stored = self.stored(page)["slots"]
            said = page.evaluate("() => window.__said.length")
            page.set_viewport_size({"width": 1366, "height": 600})
            page.wait_for_timeout(400)
            terminal, packet = self.slot(page, TERMINAL), self.slot(page, PACKET)
            fallen_stored = self.stored(page)["slots"]
            fallen_said = page.evaluate("() => window.__said.slice(%d)" % said)
            names = page.locator("[role=separator][data-separator]:visible").evaluate_all("els => els.map(e => e.getAttribute('aria-label'))")
            page.set_viewport_size(self.TALL[0])
            page.wait_for_timeout(400)
            back = self.slot(page, PACKET)
            back_said = page.evaluate("() => window.__said.slice(%d)" % (said + len(fallen_said)))

        self.assertGreater(tall["w"], 1000)
        self.assertEqual(stored["bottom"], ["packet"])
        self.assertEqual(fallen_stored, stored)
        self.assertAlmostEqual(packet["x"], terminal["x"], delta=2)
        self.assertAlmostEqual(packet["y"], terminal["y"] + terminal["h"], delta=2)
        self.assertEqual(len(fallen_said), 1)
        self.assertIn("Right", fallen_said[0])
        self.assertNotIn(self.EDGE["bottom"], names)
        self.assertGreater(back["w"], 1000)
        self.assertEqual(len(back_said), 1)
        self.assertIn("Bottom", back_said[0])

    def test_the_topology_toolbar_stays_on_one_row_with_icons_only_in_a_narrow_slot(self) -> None:
        buttons = "#view-options-btn, #lab-prompt-btn, #tools-btn, #stop-lab-btn"
        with self.signed_in_page() as page:
            self.prepared_page_at(page, self.TALL[1])
            page.evaluate("() => { document.getElementById('lab-prompt-btn').style.display = ''; }")
            shown = """(sel) => [...document.querySelectorAll(sel)].map(b => {
                const label = b.querySelector('.btn-label');
                return label ? (label.getBoundingClientRect().width > 1 ? label.textContent.trim() : '')
                             : b.innerText.trim(); })"""
            wide = page.evaluate(shown, buttons)
            self.press(page, self.EDGE["left"], "End")
            self.press(page, self.EDGE["right"], "End")
            narrow_width = self.box(page, TOPOLOGY)["width"]
            narrow = page.evaluate("""(sel) => [...document.querySelectorAll(sel)].map(b => {
                const label = b.querySelector('.btn-label');
                return { id: b.id, label: b.getAttribute('aria-label'), box: b.getBoundingClientRect().toJSON(),
                    text: label ? (label.getBoundingClientRect().width > 1 ? label.textContent.trim() : '')
                                : b.innerText.trim() }; })""", buttons)
            tops = {round(b["box"]["y"]) for b in narrow}
            inside = all(b["box"]["right"] <= self.box(page, TOPOLOGY)["x"] + narrow_width for b in narrow)
            tools = page.get_by_role("button", name="Tools").is_visible()

        self.assertEqual(wide, ["View", "Lab Prompt", "Tools", "Stop Lab"])
        self.assertLess(narrow_width, 520)
        by_id = {b["id"]: b for b in narrow}
        self.assertEqual(by_id["stop-lab-btn"]["text"], "Stop Lab")
        for ident, label in (("view-options-btn", "view"), ("lab-prompt-btn", "lab prompt")):
            self.assertEqual(by_id[ident]["text"], "")
            self.assertIn(label, by_id[ident]["label"].lower())
        self.assertEqual(by_id["tools-btn"]["text"], "")
        self.assertTrue(tools)
        self.assertEqual(len(tops), 1)
        self.assertTrue(inside)

    # -- Dragging a Pane by its title bar onto Slot targets --

    def grip(self, page: Page, pane: str) -> tuple[float, float]:
        """The middle of a Pane's grip: a handle that always starts a drag."""
        box = box_of(page.locator(f"[data-pane='{pane}'] .pane-grip"))
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def at(self, page: Page, fx: float, fy: float) -> tuple[float, float]:
        """A point in the workspace, as a fraction of its width and height."""
        box = self.workspace(page)
        return box["x"] + box["width"] * fx, box["y"] + box["height"] * fy

    def drag(self, page: Page, start: tuple[float, float], end: tuple[float, float]) -> None:
        """Press at ``start``, move to ``end`` in steps and release."""
        page.mouse.move(*start)
        page.mouse.down()
        page.mouse.move(start[0] + 10, start[1] + 10, steps=3)
        page.mouse.move(*end, steps=10)
        page.mouse.up()
        page.wait_for_timeout(300)

    def test_dragging_a_title_bar_by_its_grip_onto_half_a_slot_puts_the_pane_there(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.drag(page, self.grip(page, "terminal"), self.at(page, 0.05, 0.4))
            guide = self.box(page, GUIDE)
            terminal = self.box(page, TERMINAL)
            order = self.shown_order(page)

        self.assertAlmostEqual(terminal["x"], guide["x"], delta=1)
        self.assertAlmostEqual(terminal["width"], guide["width"], delta=1)
        self.assertGreaterEqual(terminal["y"], guide["y"] + guide["height"] - 1)
        self.assertEqual(order, ["guide", "terminal", "topology", "packet"])

    def press_and_move(self, page: Page, start: tuple[float, float], end: tuple[float, float]) -> None:
        """Press at ``start`` and move to ``end`` without releasing."""
        page.mouse.move(*start)
        page.mouse.down()
        page.mouse.move(start[0] + 10, start[1] + 10, steps=3)
        page.mouse.move(*end, steps=10)

    def targets(self, page: Page) -> dict[str, dict]:
        """Each drawn drop target by its result in words, with its box and Slot."""
        return page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('.pane-drop-target')]
            .map(el => { const r = el.getBoundingClientRect(); return [el.querySelector('span').textContent,
                { x: r.x, y: r.y, width: r.width, height: r.height, slot: el.dataset.slot,
                  hot: el.classList.contains('pane-drop-hot') }]; }))""")

    def empty_part_of_bar(self, page: Page, pane: str) -> tuple[float, float]:
        """A point on a Pane's title bar that is none of its controls."""
        point = page.evaluate("""(pane) => {
            const bar = document.querySelector(`[data-pane='${pane}'] .pane-bar`);
            const box = bar.getBoundingClientRect();
            for (let x = box.right - 4; x > box.left; x -= 4) {
                const y = box.top + box.height / 2;
                if (document.elementFromPoint(x, y) === bar) return [x, y];
            }
            return null;
        }""", pane)
        self.assertIsNotNone(point, pane)
        return point[0], point[1]

    def test_dragging_from_an_empty_part_of_any_title_bar_starts_a_drag(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            # The Terminal and Packet Analysis rows are mostly tabs: only their grips are sure handles.
            for pane in ("topology", "guide"):
                with self.subTest(pane=pane):
                    self.press_and_move(page, self.empty_part_of_bar(page, pane), self.at(page, 0.5, 0.5))
                    drawn = page.locator(".pane-drop-target").count()
                    page.mouse.up()
                    page.keyboard.press("Escape")
                    self.assertGreater(drawn, 0)

    def test_a_press_on_a_title_bar_button_or_tab_never_drags_and_still_clicks(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            before = self.shown_order(page)
            button = box_of(page.get_by_role("button", name="Guide pane actions", exact=True))
            tab = box_of(page.locator("[data-pane='terminal'] [role='tab']").first)
            for box in (button, tab):
                self.press_and_move(
                    page, (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2), self.at(page, 0.9, 0.9)
                )
                self.assertEqual(page.locator(".pane-drop-target").count(), 0)
                page.mouse.up()
            page.wait_for_timeout(300)
            after = self.shown_order(page)
            page.get_by_role("button", name="Guide pane actions", exact=True).click()
            opened = page.get_by_role("menuitem").count()

        self.assertEqual(before, after)
        self.assertGreater(opened, 0)

    def test_a_press_that_moves_less_than_five_pixels_starts_no_drag(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            x, y = self.grip(page, "terminal")
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 3, y + 2, steps=2)
            small = page.locator(".pane-drop-target").count()
            page.mouse.move(x + 8, y, steps=2)
            large = page.locator(".pane-drop-target").count()
            page.mouse.up()

        self.assertEqual(small, 0)
        self.assertGreater(large, 0)

    def test_while_dragging_the_terminal_every_target_is_drawn_and_labelled(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            workspace = self.workspace(page)
            self.press_and_move(page, self.grip(page, "terminal"), self.at(page, 0.5, 0.5))
            found = self.targets(page)
            page.mouse.up()

        self.assertEqual(set(found), {
            "Put Terminal above Guide", "Put Terminal below Guide",
            "Put Terminal above Packet Analysis", "Put Terminal below Packet Analysis",
            "Put Terminal at the Bottom",
            "Put Terminal in the Centre; Topology takes its place",
        })
        strip = found["Put Terminal at the Bottom"]
        self.assertAlmostEqual(strip["x"], workspace["x"], delta=2)
        self.assertAlmostEqual(strip["width"], workspace["width"], delta=2)
        self.assertAlmostEqual(strip["height"], max(260, workspace["height"] / 3), delta=2)
        self.assertAlmostEqual(strip["y"] + strip["height"], workspace["y"] + workspace["height"], delta=2)
        for result, box in found.items():
            if box["slot"] in ("left", "right"):
                self.assertLessEqual(box["y"] + box["height"], strip["y"] + 1, result)

    def test_an_empty_side_slot_is_a_strip_along_its_edge_and_a_full_slot_offers_swaps(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            workspace = self.workspace(page)
            self.choose(page, "guide", "Move to Bottom")
            page.wait_for_timeout(300)
            self.press_and_move(page, self.grip(page, "guide"), self.at(page, 0.5, 0.3))
            found = self.targets(page)
            page.mouse.up()

        left = found["Put Guide at the Left"]
        self.assertAlmostEqual(left["x"], workspace["x"], delta=2)
        self.assertAlmostEqual(left["width"], workspace["width"] / 5, delta=24)
        self.assertIn("Swap Guide with Terminal", found)
        self.assertIn("Swap Guide with Packet Analysis", found)

    def test_the_topology_is_dragged_like_any_pane_and_the_centre_pane_is_offered_swaps_only(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.press_and_move(page, self.grip(page, "topology"), self.at(page, 0.5, 0.5))
            found = self.targets(page)
            page.mouse.up()
            self.drag(page, self.grip(page, "topology"), self.at(page, 0.95, 0.45))
            order = self.shown_order(page)
            terminal = self.box(page, TERMINAL)
            packet = self.box(page, PACKET)
            topology = self.box(page, TOPOLOGY)

        self.assertEqual(set(found), {
            "Swap Topology with Guide", "Swap Topology with Terminal", "Swap Topology with Packet Analysis",
        })
        self.assertEqual(order[-1], "topology")
        self.assertGreater(topology["y"], packet["y"] - 1)
        self.assertGreater(terminal["x"], 0)

    def test_the_hovered_target_is_highlighted_and_its_shade_is_where_the_pane_lands(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.press_and_move(page, self.grip(page, "terminal"), self.at(page, 0.05, 0.4))
            hot = [name for name, box in self.targets(page).items() if box["hot"]]
            shade = page.locator(".pane-drop-shade")
            shaded = box_of(shade)
            said = shade.text_content()
            page.mouse.up()
            page.wait_for_timeout(400)
            landed = self.box(page, TERMINAL)

        self.assertEqual(hot, ["Put Terminal below Guide"])
        self.assertEqual(said, "Put Terminal below Guide")
        for key in ("x", "y", "width", "height"):
            self.assertAlmostEqual(shaded[key], landed[key], delta=14, msg=key)

    def test_a_label_with_the_panes_name_follows_the_pointer(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.press_and_move(page, self.grip(page, "packet"), self.at(page, 0.5, 0.4))
            label = page.locator(".pane-drag-label")
            first = box_of(label)
            name = label.text_content()
            page.mouse.move(*self.at(page, 0.6, 0.6), steps=4)
            second = box_of(label)
            page.mouse.up()

        self.assertEqual(name, "Packet Analysis pane")
        self.assertGreater(second["x"], first["x"] + 40)
        self.assertGreater(second["y"], first["y"] + 40)

    def test_on_drop_the_panes_slide_and_under_reduced_motion_they_do_not(self) -> None:
        slides = (
            "() => document.getAnimations().filter(a => !(a instanceof CSSTransition)"
            " && !(a instanceof CSSAnimation) && a.effect.target.matches('[data-pane]')).length"
        )
        results = {}
        for motion in ("no-preference", "reduce"):
            with self.signed_in_page() as page:
                page.emulate_media(reduced_motion=motion)
                self.prepared_page(page)
                x, y = self.grip(page, "terminal")
                end = self.at(page, 0.05, 0.4)
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 10, y + 10, steps=3)
                page.mouse.move(*end, steps=10)
                page.mouse.up()
                results[motion] = page.evaluate(slides)
        self.assertGreater(results["no-preference"], 0)
        self.assertEqual(results["reduce"], 0)

    def test_a_drop_outside_every_target_or_escape_changes_nothing(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            before = self.shown_order(page)
            boxes = {p: self.box(page, s) for p, s in self.SELECTORS.items()}
            # Over the Topology's own Centre is a target (a swap); the strip
            # between the Slots' targets does not exist, so aim outside the
            # workspace, over the page header.
            self.drag(page, self.grip(page, "terminal"), (400, 4))
            outside = {p: self.box(page, s) for p, s in self.SELECTORS.items()}
            self.press_and_move(page, self.grip(page, "terminal"), self.at(page, 0.05, 0.4))
            page.keyboard.press("Escape")
            overlay = page.locator(".pane-drag-overlay").count()
            page.mouse.up()
            page.wait_for_timeout(300)
            escaped = {p: self.box(page, s) for p, s in self.SELECTORS.items()}
            after = self.shown_order(page)
            echoed = page.locator("#terminal-r1 .xterm-rows").text_content()

        self.assertEqual(before, after)
        self.assertEqual(overlay, 0)
        assert echoed is not None
        self.assertNotIn("^[", echoed)
        self.assertEqual(boxes, outside)
        self.assertEqual(boxes, escaped)

    def test_escape_at_any_other_time_still_reaches_the_terminal(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.drag(page, self.grip(page, "terminal"), self.at(page, 0.05, 0.4))
            page.locator("#terminal-r1 .xterm-helper-textarea").focus()
            page.keyboard.press("Escape")
            # The pty echoes the escape character as ^[ .
            expect(page.locator("#terminal-r1 .xterm-rows")).to_contain_text("^[")

    def test_a_cancelled_pointer_ends_the_drag_and_changes_nothing(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            before = self.shown_order(page)
            self.press_and_move(page, self.grip(page, "terminal"), self.at(page, 0.05, 0.4))
            page.evaluate("""() => document.querySelector("[data-pane='terminal'] .pane-bar")
                .dispatchEvent(new PointerEvent('pointercancel', { bubbles: true }))""")
            overlay = page.locator(".pane-drag-overlay").count()
            page.mouse.up()
            page.wait_for_timeout(300)
            after = self.shown_order(page)

        self.assertEqual(overlay, 0)
        self.assertEqual(before, after)

    def test_no_drag_starts_at_phone_width(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            page.set_viewport_size(PHONE)
            page.wait_for_timeout(300)
            bar = box_of(page.locator("[data-pane='topology'] .pane-bar"))
            self.press_and_move(
                page, (bar["x"] + bar["width"] - 4, bar["y"] + bar["height"] / 2), (200, 600)
            )
            drawn = page.locator(".pane-drop-target").count()
            page.mouse.up()

        self.assertEqual(drawn, 0)

    def test_after_a_drag_the_order_announcement_and_sessions_survive_as_after_a_menu_move(self) -> None:
        drops = (
            ("terminal", (0.05, 0.4), "Terminal pane moved to the Left", ["guide", "terminal", "topology", "packet"]),
            ("packet", (0.5, 0.95), "Packet Analysis pane moved to the Bottom",
             ["guide", "terminal", "topology", "packet"]),
            ("terminal", (0.5, 0.4), "Terminal pane swapped with Topology pane",
             ["guide", "topology", "terminal", "packet"]),
        )
        with self.signed_in_page() as page:
            self.prepared_page(page)
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                window.__terminalSocket = appState.tabManager.terminals.get('r1').ws;
            }""")
            status = page.locator("#pane-arrangement-status")
            typed = 0
            rows = 0
            for pane, (fx, fy), message, order in drops:
                with self.subTest(message=message):
                    self.drag(page, self.grip(page, pane), self.at(page, fx, fy))
                    expect(status).to_have_text(message)
                    self.assertEqual(self.dom_order(page)[:4], order)
                    self.assertTrue(page.evaluate("() => window.__terminalSocket.readyState === 1"))
                    typed += 1
                    page.locator("#terminal-r1 .xterm-helper-textarea").focus()
                    page.keyboard.type(f"drag{typed}")
                    expect(page.locator("#terminal-r1 .xterm-rows")).to_contain_text(f"drag{typed}")
                    self.wait_for_pty_to_match(page)
                    self.emit()
                    rows += 4
                    page.wait_for_function(f"() => document.querySelectorAll('{ROWS}').length === {rows}")

    # -- Full view --

    EXIT = "Exit full view"

    def enter_full_view(self, page: Page, pane: str) -> None:
        """Give a Pane the whole page from its Pane actions menu."""
        self.choose(page, pane, "Full view")

    def viewport_box(self, page: Page) -> dict[str, float]:
        """The browser window as a box, for comparing with a Pane's box."""
        size = page.viewport_size
        assert size is not None, "the page has a window size"
        return {"x": 0, "y": 0, "width": size["width"], "height": size["height"]}

    def test_every_panes_full_view_covers_the_whole_page_and_offers_exit_in_its_title_bar_and_menu(self) -> None:
        for pane in self.PANE_NAMES:
            with self.subTest(pane=pane), self.signed_in_page() as page:
                self.prepared_page(page)
                self.enter_full_view(page, pane)
                covered = self.box(page, self.SELECTORS[pane])
                window = self.viewport_box(page)
                exit_button = page.locator(f"[data-pane='{pane}']").get_by_role("button", name=self.EXIT)
                items = self.menu_items(page, pane)
                visible = exit_button.is_visible()
                top = page.evaluate(
                    "([sel, x, y]) => document.elementFromPoint(x, y).closest('[data-pane]')?.matches(sel)",
                    [self.SELECTORS[pane], covered["x"] + 40, 4],
                )

                self.assertEqual(covered, window)
                self.assertTrue(visible, "a visible Exit full view button in the title bar")
                self.assertIn(self.EXIT, items)
                self.assertNotIn("Full view", items)
                self.assertTrue(top, "the Pane is above the header")

    def test_exit_full_view_by_the_button_and_by_the_menu_restores_the_arrangement(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            boxes = {p: self.box(page, s) for p, s in self.SELECTORS.items()}
            self.enter_full_view(page, "terminal")
            page.locator("[data-pane='terminal']").get_by_role("button", name=self.EXIT).click()
            page.wait_for_timeout(300)
            by_button = {p: self.box(page, s) for p, s in self.SELECTORS.items()}
            gone = page.get_by_role("button", name=self.EXIT).count()
            self.enter_full_view(page, "packet")
            self.choose(page, "packet", self.EXIT)
            by_menu = {p: self.box(page, s) for p, s in self.SELECTORS.items()}
            focused = page.evaluate("() => document.activeElement.getAttribute('aria-label')")

        self.assertEqual(by_button, boxes)
        self.assertEqual(gone, 0)
        self.assertEqual(by_menu, boxes)
        self.assertEqual(focused, "Packet Analysis pane actions")

    def test_separators_are_hidden_in_full_view(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            names = [*self.EDGE.values(), *self.SPLIT.values()]

            def shown() -> int:
                return sum(page.get_by_role("separator", name=name).count() for name in names)

            before = shown()
            self.enter_full_view(page, "guide")
            during = shown()
            self.choose(page, "guide", self.EXIT)
            after = shown()

        self.assertGreater(before, 0)
        self.assertEqual(during, 0)
        self.assertEqual(after, before)

    def test_escape_never_leaves_full_view_and_still_reaches_the_terminal(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.enter_full_view(page, "terminal")
            page.locator("#terminal-r1 .xterm-helper-textarea").focus()
            page.keyboard.press("Escape")
            expect(page.locator("#terminal-r1 .xterm-rows")).to_contain_text("^[")
            page.evaluate("() => document.querySelector('[data-pane=terminal] [role=tab]').focus()")
            page.keyboard.press("Escape")
            page.get_by_role("button", name="Cancel").click()
            covered = self.box(page, TERMINAL)

        self.assertEqual(covered, self.viewport_box(page))

    def test_starting_stopping_or_switching_a_lab_ends_full_view(self) -> None:
        relay = """async ([running, key]) => {
            const { appState } = await import('/static/js/state/app-state.js');
            const { layoutPanes } = await import('/static/js/ui/pane-arrangement.js');
            appState.isLabRunning = running;
            if (key !== null) appState.labKey = key;
            layoutPanes();
        }"""
        for change, steps in (
            ("stop", [[False, None]]),
            ("start", [[False, None], [True, None]]),
            ("switch", [[True, "another-lab"]]),
        ):
            with self.subTest(change=change), self.signed_in_page() as page:
                self.prepared_page(page)
                boxes = {p: self.box(page, s) for p, s in self.SELECTORS.items()}
                self.enter_full_view(page, "topology")
                for step in steps:
                    page.evaluate(relay, step)
                page.wait_for_timeout(300)
                if change == "stop":
                    page.evaluate(relay, [True, None])
                    page.wait_for_timeout(300)
                after = {p: self.box(page, s) for p, s in self.SELECTORS.items()}

                self.assertEqual(after, boxes)
                self.assertEqual(page.get_by_role("button", name=self.EXIT).count(), 0)

    def test_a_terminal_in_full_view_is_resized_to_it_and_back(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            normal = self.wait_for_pty_to_match(page)
            self.enter_full_view(page, "terminal")
            page.wait_for_timeout(300)
            full = self.wait_for_pty_to_match(page)
            self.choose(page, "terminal", self.EXIT)
            page.wait_for_timeout(300)
            back = self.wait_for_pty_to_match(page)

        self.assertGreater(full[0], normal[0])
        self.assertGreater(full[1], normal[1])
        self.assertEqual(back, normal)

    def test_full_view_is_announced_and_no_drag_starts_in_it(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            status = page.locator("#pane-arrangement-status")
            self.enter_full_view(page, "terminal")
            expect(status).to_have_text("Terminal pane in full view")
            self.press_and_move(page, self.grip(page, "terminal"), self.at(page, 0.5, 0.5))
            overlay = page.locator(".pane-drag-overlay").count()
            page.mouse.up()
            page.wait_for_timeout(300)
            still = self.box(page, TERMINAL)
            self.choose(page, "terminal", self.EXIT)
            expect(status).to_have_text("Terminal pane left full view")

        self.assertEqual(overlay, 0)
        self.assertEqual(still, self.viewport_box(page))

    # -- Closing and reopening --

    def pane_checkbox(self, page: Page, pane: str):
        """A Pane's checkbox in the View menu's Panes group, with the menu open."""
        menu = page.locator("#view-options-menu")
        if not menu.is_visible():
            page.get_by_role("button", name="Open view and layout options").click()
        return menu.get_by_role("group", name="Panes").get_by_role(
            "checkbox", name=self.PANE_NAMES[pane], exact=True
        )

    def reopen(self, page: Page, pane: str) -> None:
        """Tick a Pane's checkbox in the View menu and close the menu."""
        self.pane_checkbox(page, pane).check()
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

    def test_close_is_offered_for_the_guide_terminal_and_packet_analysis_never_the_topology(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            offered = {}
            for pane in self.PANE_NAMES:
                offered[pane] = "Close" in self.menu_items(page, pane)
                page.keyboard.press("Escape")

        self.assertEqual(offered, {"topology": False, "guide": True, "terminal": True, "packet": True})

    def test_closing_the_terminal_or_packet_analysis_pane_ends_no_terminal_and_no_capture(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.emit()
            page.wait_for_function(f"() => document.querySelectorAll('{ROWS}').length === 4")
            self.choose(page, "terminal", "Close")
            self.choose(page, "packet", "Close")
            closed = (self.box_or_none(page, TERMINAL), self.box_or_none(page, PACKET))
            self.emit()
            page.wait_for_timeout(300)
            self.reopen(page, "terminal")
            self.reopen(page, "packet")
            page.locator("#terminal-r1 .xterm-helper-textarea").focus()
            page.keyboard.type("still-open")
            expect(page.locator("#terminal-r1 .xterm-rows")).to_contain_text("still-open")
            page.wait_for_function(f"() => document.querySelectorAll('{ROWS}').length === 8")
            tabs = page.locator("[data-pane='terminal'] [role='tab']").count()

        self.assertEqual(closed, (None, None))
        self.assertEqual(tabs, 1)

    def test_closing_the_pane_in_the_centre_brings_the_topology_back_and_reopening_puts_it_where_it_was(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.choose(page, "terminal", "Move to Centre, swapping with Topology")
            before = {p: self.box(page, s) for p, s in self.SELECTORS.items()}
            self.choose(page, "terminal", "Close")
            closed = {p: self.box_or_none(page, s) for p, s in self.SELECTORS.items()}
            self.reopen(page, "terminal")
            after = {p: self.box(page, s) for p, s in self.SELECTORS.items()}
            order = self.shown_order(page)

        self.assertIsNone(closed["terminal"])
        closed_topology = closed["topology"]
        assert closed_topology is not None, "the Topology takes the room the Terminal left"
        self.assertAlmostEqual(closed_topology["width"], before["terminal"]["width"], delta=2)
        self.assertAlmostEqual(closed_topology["x"], before["terminal"]["x"], delta=2)
        self.assertAlmostEqual(after["terminal"]["x"], before["topology"]["x"], delta=2)
        self.assertEqual(order, ["guide", "topology", "terminal", "packet"])

    def test_closing_announces_moves_focus_to_the_topology_actions_button_and_reopening_is_announced(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            status = page.locator("#pane-arrangement-status")
            self.choose(page, "guide", "Close")
            expect(status).to_have_text("Guide pane closed")
            focused = page.evaluate("() => document.activeElement.getAttribute('aria-label')")
            self.reopen(page, "guide")
            expect(status).to_have_text("Guide pane reopened")

        self.assertEqual(focused, "Topology pane actions")

    def test_closing_from_the_view_menu_or_ctrl_t_does_not_move_focus_to_the_topology_button(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.pane_checkbox(page, "guide").uncheck()
            from_checkbox = page.evaluate("() => document.activeElement.getAttribute('aria-label')")
            page.keyboard.press("Escape")
            page.locator("svg#topology").click(position={"x": 5, "y": 5})
            page.keyboard.press("Control+t")
            page.wait_for_timeout(200)
            from_key = page.evaluate("() => document.activeElement.getAttribute('aria-label')")

        self.assertNotEqual(from_checkbox, "Topology pane actions")
        self.assertNotEqual(from_key, "Topology pane actions")

    def stood_in_centre(self, page: Page) -> None:
        """Store the Terminal in the Centre, then leave it with no Terminal to show."""
        self.choose(page, "terminal", "Move to Centre, swapping with Topology")
        page.get_by_role("button", name="Close all terminals").click()
        page.wait_for_timeout(300)

    def test_moving_a_pane_to_the_centre_while_the_topology_stands_in_swaps_with_the_topology(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.stood_in_centre(page)
            guide_before = self.box(page, GUIDE)
            status = page.locator("#pane-arrangement-status")
            self.choose(page, "guide", "Move to Centre, swapping with Topology")
            guide = self.box(page, GUIDE)
            topology = self.box(page, TOPOLOGY)
            terminal_x = self.box(page, PACKET)["x"]
            said = status.text_content()

        # The Topology at the Left is at least 300 wide, so the Centre starts later.
        self.assertGreater(guide["x"], topology["x"] + topology["width"])
        self.assertLess(guide["x"], terminal_x)
        self.assertAlmostEqual(topology["x"], guide_before["x"], delta=2)
        self.assertEqual(said, "Guide pane swapped with Topology pane")

    def test_dragging_a_pane_onto_the_centre_while_the_topology_stands_in_swaps_with_the_topology(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.stood_in_centre(page)
            guide_before = self.box(page, GUIDE)
            self.drag(page, self.grip(page, "guide"), self.at(page, 0.5, 0.4))
            guide = self.box(page, GUIDE)
            topology = self.box(page, TOPOLOGY)

        self.assertGreater(guide["x"], topology["x"] + topology["width"])
        self.assertAlmostEqual(topology["x"], guide_before["x"], delta=2)

    def test_the_view_menus_panes_group_shows_which_panes_are_open(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            opened = [self.pane_checkbox(page, p).is_checked() for p in ("guide", "terminal", "packet")]
            page.keyboard.press("Escape")
            self.choose(page, "guide", "Close")
            self.choose(page, "packet", "Close")
            after = [self.pane_checkbox(page, p).is_checked() for p in ("guide", "terminal", "packet")]
            names = page.locator("#view-options-menu").get_by_role("group", name="Panes").get_by_role(
                "checkbox").all_inner_texts()
            has_reset = page.get_by_role("button", name="Reset pane arrangement").count()

        self.assertEqual(opened, [True, True, True])
        self.assertEqual(after, [False, True, False])
        self.assertEqual(has_reset, 1)

    def test_a_reopened_pane_returns_to_its_slot_or_the_first_slot_with_room(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.choose(page, "guide", "Close")
            self.choose(page, "terminal", "Move to Left")
            self.choose(page, "packet", "Move to Left")
            full_left = self.box(page, TERMINAL), self.box(page, PACKET)
            self.reopen(page, "guide")
            guide = self.box(page, GUIDE)
            terminal = self.box(page, TERMINAL)
            topology = self.box(page, TOPOLOGY)

        self.assertAlmostEqual(full_left[0]["x"], full_left[1]["x"], delta=1)
        self.assertGreater(guide["x"], topology["x"], "the Left Slot was full: the Guide went to the Right")
        self.assertLess(terminal["x"], topology["x"])

    def test_a_pane_reopens_in_the_slot_it_was_closed_from(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            before = self.box(page, GUIDE)
            self.choose(page, "guide", "Close")
            gone = self.box_or_none(page, GUIDE)
            self.reopen(page, "guide")
            after = self.box(page, GUIDE)

        self.assertIsNone(gone)
        self.assertAlmostEqual(after["x"], before["x"], delta=2)
        self.assertAlmostEqual(after["width"], before["width"], delta=2)

    def test_opening_a_terminal_a_capture_or_the_nat_table_reopens_its_closed_pane(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.choose(page, "terminal", "Close")
            self.choose(page, "packet", "Close")
            self.open_terminal(page, "h1")
            terminal = self.box_or_none(page, TERMINAL)
            self.choose(page, "terminal", "Close")
            self.open_capture(page, "r1-eth0")
            packet = self.box_or_none(page, PACKET)
            self.choose(page, "packet", "Close")
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                appState.packetAnalysis.openNatTable();
            }""")
            page.wait_for_timeout(300)
            nat = self.box_or_none(page, PACKET)

        self.assertIsNotNone(terminal)
        self.assertIsNotNone(packet)
        self.assertIsNotNone(nat)

    def test_a_closed_pane_stays_closed_across_a_reload_even_with_a_restored_capture(self) -> None:
        with self.signed_in_page() as page:
            self.prepared_page(page)
            self.choose(page, "packet", "Close")
            page.reload()
            self.running_page(page)
            page.wait_for_timeout(500)
            packet = self.box_or_none(page, PACKET)
            checked = self.pane_checkbox(page, "packet").is_checked()

        self.assertIsNone(packet)
        self.assertFalse(checked)

    def test_hiding_the_terminal_pane_with_no_terminal_open_does_not_stop_a_later_terminal_bringing_it_back(self) -> None:
        with self.signed_in_page() as page:
            self.running_page(page)
            page.locator("svg#topology").click(position={"x": 5, "y": 5})
            page.keyboard.press("Control+t")
            page.wait_for_timeout(200)
            self.open_terminal(page)
            shown = self.box_or_none(page, TERMINAL)

        self.assertIsNotNone(shown)


class NoLabSidebarTests(LiveServerCase):
    """With no Lab running the sidebar is the Labs list, as before."""

    def test_the_sidebar_button_and_ctrl_b_open_and_close_the_labs_list(self) -> None:
        with self.signed_in_page() as page:
            page.set_viewport_size(LAPTOP)
            labs = page.get_by_role("list", name="Available labs")
            expect(labs).to_be_visible()
            page.locator("#toggle-sidebar").click()
            page.wait_for_timeout(400)
            closed_by_button = box_of(page.locator(".sidebar"))["width"]
            page.locator("#toggle-sidebar").click()
            page.wait_for_timeout(400)
            expect(labs).to_be_visible()
            page.evaluate("() => document.activeElement.blur()")
            page.keyboard.press("Control+b")
            page.wait_for_timeout(400)
            closed_by_key = box_of(page.locator(".sidebar"))["width"]

        self.assertLess(closed_by_button, 2)
        self.assertLess(closed_by_key, 2)
