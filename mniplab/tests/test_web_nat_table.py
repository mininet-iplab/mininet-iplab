"""Browser tests for the NAT Table tab in Packet Analysis.

The pane is driven against a live server whose Lab is the fake the Capture
tests use — ``r1`` with Router NAT on ``r1-eth1``, beside a hidden NAT
Gateway — and whose ``conntrack`` is a reader handing back the table
committed from ``static-lab-nat``, its lifetimes falling by two seconds a
read as the kernel's would between polls.  Its ``iptables`` is the same reader's
rules, which a test changes the way a Terminal would.  Everything from the Node
menu to the countdown is real except the table's and the rules' origin.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, List

import pytest

from mniplab.labhandle import NodeAttachment
from mniplab.tests.browser import LiveServerCase
from mniplab.tests.test_web_capture import two_node_lab
from mniplab.webserver import registry

pytestmark = pytest.mark.browser_services

TABLE = (Path(__file__).parent / "fixtures" / "conntrack" / "static-lab-nat-r1.txt").read_text(
    encoding="utf-8"
)

PANE = "#packet-analysis .nat-table-pane"
# The tab row's share of the pane: its state and actions.
HEADER = "#packet-analysis .packet-analysis-header-slot"
STATUS = f"{PANE} .nat-table-status"
ERROR = f"{PANE} .nat-table-error"
SECTION = f"{PANE} .nat-table-table-section[data-node='r1']"
ROWS = f"{SECTION} tbody tr"
RULES = f"{SECTION} .nat-table-rules"
RULE_ITEMS = f"{SECTION} .nat-table-rules li"
RULES_EMPTY = f"{SECTION} .nat-table-rules-empty"

# Seconds the committed table's lifetimes fall by between two reads.
TICK = 2

# Long enough for a read already in flight when the pane stops polling to
# reach the reader, so it is not counted as one made after.
IN_FLIGHT = 0.5


class CountingDownReader:
    """Hands back r1's committed table, every lifetime lower on each read.

    Attributes:
        reads: How many times the table was read.
        attachments: Every Node read, in order.
        forget_udp: Once set, the UDP connection has expired and is gone.
        forget_all: Once set, every entry has expired.
        rules: The lines ``iptables -t nat -S`` would print, changed in a test
            the way a rule typed into the Router's Terminal would change them.
    """

    def __init__(self) -> None:
        self.reads = 0
        self.attachments: List[NodeAttachment] = []
        self.forget_udp = False
        self.forget_all = False
        self.rules: List[str] = ["-A POSTROUTING -o r1-eth1 -j MASQUERADE"]
        self._lock = threading.Lock()

    def read(self, attachment: NodeAttachment) -> str:
        with self._lock:
            elapsed = self.reads * TICK
            self.reads += 1
            self.attachments.append(attachment)
        lines = []
        for line in TABLE.splitlines():
            if self.forget_all or (self.forget_udp and line.startswith("udp")):
                continue
            words = line.split()
            words[2] = str(max(0, int(words[2]) - elapsed))
            lines.append(" ".join(words))
        return "\n".join(lines) + "\n"

    def read_rules(self, attachment: NodeAttachment) -> str:
        return "\n".join(["-P POSTROUTING ACCEPT", *self.rules]) + "\n"


def wait_until(condition: Any, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.05)


class NatTablePaneTests(LiveServerCase):
    """The NAT Table tab, from a translating Router's menu to a countdown."""

    def setUp(self) -> None:
        self.reader = CountingDownReader()
        previous = registry.nat_table_reader
        registry.nat_table_reader = self.reader
        self.addCleanup(setattr, registry, "nat_table_reader", previous)
        self.lab = two_node_lab()
        registry.lab_service.lab = self.lab
        self.addCleanup(setattr, registry.lab_service, "lab", None)

    def node_menu(self, page: Any, node: str) -> None:
        """Open a Node's menu, as clicking the Node does."""
        page.wait_for_selector("svg#topology .node")
        page.evaluate(f"""async () => {{
            const {{ appState }} = await import('/static/js/state/app-state.js');
            const {{ showNodeContextMenu }} = await import('/static/js/topology/interactions.js');
            const node = appState.currentTopology.nodes.find(n => n.id === '{node}');
            showNodeContextMenu(node, {{ clientX: 0, clientY: 0 }});
        }}""")

    def open_nat_table(self, page: Any) -> None:
        self.node_menu(page, "r1")
        page.click("#node-nat-table-btn")
        page.wait_for_selector(f"{ROWS}")

    def cells(self, page: Any) -> List[List[str]]:
        return page.eval_on_selector_all(
            ROWS, "rows => rows.map(r => [...r.children].map(c => c.textContent))"
        )

    def rule_items(self, page: Any) -> List[str]:
        return page.eval_on_selector_all(RULE_ITEMS, "items => items.map(i => i.textContent)")

    def lifetime_of(self, page: Any, protocol: str) -> int:
        for row in self.cells(page):
            if row[0] == protocol:
                match = re.match(r"(\d+) s$", row[3])
                assert match is not None
                return int(match.group(1))
        raise AssertionError(f"no {protocol} row")

    def test_only_a_translating_router_offers_a_nat_table(self) -> None:
        with self.signed_in_page() as page:
            self.node_menu(page, "r1")
            on_r1 = page.is_visible("#node-nat-table-btn")
            self.node_menu(page, "h1")
            on_h1 = page.is_visible("#node-nat-table-btn")

        self.assertEqual((on_r1, on_h1), (True, False))

    def test_a_lab_where_nothing_translates_offers_none(self) -> None:
        registry.lab_service.lab = two_node_lab(outside=False)

        with self.signed_in_page() as page:
            self.node_menu(page, "r1")
            offered = page.is_visible("#node-nat-table-btn")

        self.assertFalse(offered)

    def test_the_tab_opens_in_the_packet_analysis_pane_beside_the_captures_named_by_router(self) -> None:
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            tab = page.inner_text("#packet-analysis [role='tab'][aria-selected='true']")
            heading = page.inner_text(f"{SECTION} .nat-table-heading")
            pane_heading = page.inner_text("#packet-analysis-heading")
            fields = page.get_attribute(PANE, "class") or ""

        self.assertEqual(tab, "NAT Table")
        self.assertEqual(heading, "r1 · r1-eth1")
        self.assertEqual(pane_heading, "Packet Analysis")
        # A sibling of the Capture panes: no filter, no packet list, no field tree.
        self.assertNotIn("capture-filter", fields)
        self.assertEqual(self.reader.attachments[0].node, "r1")

    def test_each_row_pairs_the_inside_and_outside_tuples_with_state_and_lifetime(self) -> None:
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            headers = page.eval_on_selector_all(
                f"{SECTION} th", "ths => ths.map(t => [t.textContent, t.getAttribute('scope')])"
            )
            rows = self.cells(page)

        self.assertEqual([h[0] for h in headers], [
            "Protocol", "Inside", "Outside", "Expires in", "State", "Destination",
        ])
        self.assertTrue(all(scope == "col" for _, scope in headers))
        # Ordered by inside address, protocol and port; h1 to h2 is not an entry.
        self.assertEqual([r[:3] + r[4:] for r in rows], [
            ["ICMP", "100.64.1.2 id 39", "100.64.0.2 id 39", "REPLIED", "100.64.0.1"],
            ["TCP", "100.64.1.2:48072", "100.64.0.2:48072", "TIME_WAIT", "100.64.0.1:18080"],
            ["TCP", "100.64.1.2:51936", "100.64.0.2:51936", "ESTABLISHED", "100.64.0.1:18081"],
            ["UDP", "100.64.1.2:51330", "100.64.0.2:51330", "UNREPLIED", "100.64.0.1:9999"],
        ])
        # The countdown sits beside the pair it belongs to.
        self.assertRegex(rows[0][3], r"^\d+ s$")
        self.assertRegex(rows[2][3], r"^\d+ d \d+ h$")

    def test_the_lifetime_counts_down_as_the_view_refreshes(self) -> None:
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            first = self.lifetime_of(page, "UDP")
            reads = self.reader.reads
            wait_until(lambda: self.reader.reads > reads + 1)
            page.wait_for_function(
                f"""() => [...document.querySelectorAll("{ROWS}")]
                    .some(r => r.children[0].textContent === 'UDP'
                        && parseInt(r.children[3].textContent) < {first})"""
            )
            later = self.lifetime_of(page, "UDP")

        self.assertLess(later, first)

    def test_an_expired_entry_leaves_and_the_change_is_announced(self) -> None:
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            self.reader.forget_udp = True
            page.wait_for_function(f"() => document.querySelectorAll(\"{ROWS}\").length === 3")
            page.wait_for_selector(f"{STATUS}:has-text('r1 now has 3 entries.')")
            role = page.get_attribute(STATUS, "role")
            live = page.get_attribute(STATUS, "aria-live")
            classes = page.get_attribute(STATUS, "class") or ""

        self.assertEqual((role, live), ("status", "polite"))
        # Spoken, not shown: the table already shows the row gone.
        self.assertIn("sr-only", classes.split())

    def test_the_countdown_is_not_announced(self) -> None:
        # Only the status speaks; the table refreshing every two seconds must not.
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            live_ancestors = page.eval_on_selector(
                f"{SECTION} .nat-table-lifetime",
                "cell => { let n = cell, found = []; while (n) { if (n.getAttribute && n.getAttribute('aria-live')) found.push(n.getAttribute('aria-live')); n = n.parentElement; } return found; }",
            )
            status_before = page.inner_text(STATUS)
            reads = self.reader.reads
            wait_until(lambda: self.reader.reads > reads + 1)
            time.sleep(0.3)
            status_after = page.inner_text(STATUS)

        self.assertEqual(live_ancestors, [])
        self.assertEqual(status_before, status_after)

    def test_updates_can_be_paused_and_resumed(self) -> None:
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            name = page.get_attribute(f"{HEADER} .nat-table-pause", "aria-label")
            page.click(f"{HEADER} .nat-table-pause")
            pressed = page.get_attribute(f"{HEADER} .nat-table-pause", "aria-pressed")
            state = page.inner_text(f"{HEADER} .nat-table-state")
            paused_name = page.get_attribute(f"{HEADER} .nat-table-pause", "aria-label")
            time.sleep(IN_FLIGHT)
            reads = self.reader.reads
            time.sleep(2.5)
            reads_while_paused = self.reader.reads - reads
            page.click(f"{HEADER} .nat-table-pause")
            wait_until(lambda: self.reader.reads > reads)
            resumed = page.get_attribute(f"{HEADER} .nat-table-pause", "aria-pressed")

        self.assertEqual((pressed, resumed), ("true", "false"))
        self.assertEqual(state, "Paused")
        # One name throughout; aria-pressed carries the state.
        self.assertEqual((name, paused_name), ("Pause updates", "Pause updates"))
        self.assertEqual(reads_while_paused, 0)

    def test_the_tables_region_is_keyboard_reachable(self) -> None:
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            region = page.eval_on_selector(
                f"{PANE} .nat-table-tables",
                "el => [el.getAttribute('role'), el.getAttribute('aria-label'), el.tabIndex]",
            )
            controls = page.get_attribute("#packet-analysis [role='tab'][aria-selected='true']", "aria-controls")
            panel_role = page.get_attribute(f"#{controls}", "role")
            caption = page.inner_text(f"{SECTION} caption")

        self.assertEqual(region, ["region", "NAT Tables", 0])
        self.assertEqual(panel_role, "tabpanel")
        self.assertIn("counts down", caption)

    def test_the_rules_strip_shows_the_masquerade_the_router_runs(self) -> None:
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            page.wait_for_selector(RULE_ITEMS)
            items = self.rule_items(page)

        self.assertEqual(items, ["Masquerade out of r1-eth1"])

    def test_a_rule_typed_in_the_routers_terminal_appears_on_the_next_refresh(self) -> None:
        typed = "-A PREROUTING -p tcp -m tcp --dport 2222 -j REDIRECT --to-ports 22"
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            self.reader.rules.append(typed)
            page.wait_for_selector(f"{RULE_ITEMS}:has-text('REDIRECT')")
            items = self.rule_items(page)

        self.assertIn(typed, items)

    def test_the_rules_strip_has_no_controls_and_its_changes_are_not_announced(self) -> None:
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            controls = len(page.query_selector_all(f"{RULES} button, {RULES} input, {RULES} a"))
            status_before = page.inner_text(STATUS)
            self.reader.rules.append("-A PREROUTING -p tcp -m tcp --dport 2222 -j REDIRECT --to-ports 22")
            page.wait_for_selector(f"{RULE_ITEMS}:has-text('REDIRECT')")
            status_after = page.inner_text(STATUS)
            live_ancestors = page.eval_on_selector(
                RULES,
                "cell => { let n = cell, found = []; while (n) { if (n.getAttribute && n.getAttribute('aria-live')) found.push(n.getAttribute('aria-live')); n = n.parentElement; } return found; }",
            )

        self.assertEqual(controls, 0)
        self.assertEqual(live_ancestors, [])
        self.assertEqual(status_before, status_after)

    def test_a_router_with_no_rules_says_so_in_a_word(self) -> None:
        self.reader.rules.clear()

        with self.signed_in_page() as page:
            self.node_menu(page, "r1")
            page.click("#node-nat-table-btn")
            page.wait_for_selector(f"{RULES_EMPTY}:visible")
            empty = page.inner_text(RULES_EMPTY)
            list_hidden = page.eval_on_selector(RULES, "list => list.hidden")

        self.assertEqual(empty, "No rules")
        self.assertTrue(list_hidden)

    def test_it_does_not_count_against_the_two_captures(self) -> None:
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            refusal = page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                return appState.packetAnalysis.refusal('h1', 'h1-eth0');
            }""")

        self.assertIsNone(refusal)

    def test_closing_the_tab_stops_the_reads(self) -> None:
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            page.click(f"{HEADER} .nat-table-close")
            pane_hidden = page.eval_on_selector("#packet-analysis", "el => el.hidden")
            time.sleep(IN_FLIGHT)
            reads = self.reader.reads
            time.sleep(2.5)

        self.assertTrue(pane_hidden)
        self.assertEqual(self.reader.reads, reads)

    def test_a_table_that_cannot_be_read_is_read_out(self) -> None:
        def refuse(attachment: NodeAttachment) -> str:
            raise ValueError("conntrack is not installed on this host")

        self.reader.read = refuse  # type: ignore[method-assign]

        with self.signed_in_page() as page:
            self.node_menu(page, "r1")
            page.click("#node-nat-table-btn")
            page.wait_for_selector(f"{ERROR}:has-text('conntrack is not installed')")

    def test_the_explanation_is_a_disclosure_closed_at_first(self) -> None:
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            info = f"{HEADER} .nat-table-info"
            before = (page.get_attribute(info, "aria-expanded"), page.is_visible(f"{PANE} .nat-table-note"))
            controls = page.get_attribute(info, "aria-controls")
            name = page.get_attribute(info, "aria-label")
            page.click(info)
            after = (page.get_attribute(info, "aria-expanded"), page.is_visible(f"#{controls}"))
            text = page.inner_text(f"#{controls}")

        self.assertEqual(before, ("false", False))
        self.assertEqual(after, ("true", True))
        self.assertEqual(name, "About the NAT Table")
        self.assertIn("Expires in", text)

    def test_the_header_is_three_labelled_buttons_large_enough_to_hit(self) -> None:
        with self.signed_in_page() as page:
            self.open_nat_table(page)
            buttons = page.eval_on_selector_all(
                f"{HEADER} button",
                "bs => bs.map(b => { const r = b.getBoundingClientRect();"
                " return [b.getAttribute('aria-label'), Math.min(r.width, r.height)]; })",
            )
            state = page.inner_text(f"{HEADER} .nat-table-state")

        self.assertEqual([b[0] for b in buttons], ["About the NAT Table", "Pause updates", "Close NAT Table"])
        # WCAG 2.2 target size (minimum): 24 by 24 CSS pixels.
        self.assertTrue(all(size >= 24 for _, size in buttons), buttons)
        self.assertEqual(state, "Refreshing every 2 s")

    def test_the_header_sits_in_the_tab_row_like_a_captures(self) -> None:
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1920, "height": 1080})
            self.open_nat_table(page)
            g = page.evaluate("""() => {
                const box = (selector) => {
                    const r = document.querySelector(selector).getBoundingClientRect();
                    return { top: r.top, bottom: r.bottom, left: r.left, right: r.right };
                };
                return {
                    tab: box("#packet-analysis [role=tab][aria-selected=true]"),
                    state: box("#packet-analysis .nat-table-state"),
                    about: box("#packet-analysis .nat-table-info"),
                    pause: box("#packet-analysis .nat-table-pause"),
                    close: box("#packet-analysis .nat-table-close"),
                    tables: box("#packet-analysis .nat-table-tables"),
                };
            }""")

        middle = (g["tab"]["top"] + g["tab"]["bottom"]) / 2
        for part in ("state", "about", "pause", "close"):
            with self.subTest(part=part):
                self.assertLessEqual(abs((g[part]["top"] + g[part]["bottom"]) / 2 - middle), 8)
        self.assertLessEqual(g["close"]["bottom"], g["tables"]["top"])

    def test_a_router_with_no_entries_says_so_in_a_word(self) -> None:
        self.reader.forget_all = True

        with self.signed_in_page() as page:
            self.node_menu(page, "r1")
            page.click("#node-nat-table-btn")
            page.wait_for_selector(f"{SECTION} .nat-table-empty:visible")
            empty = page.inner_text(f"{SECTION} .nat-table-empty")
            table_hidden = page.eval_on_selector(f"{SECTION} table", "t => t.hidden")

        self.assertEqual(empty, "No entries yet")
        self.assertTrue(table_hidden)
