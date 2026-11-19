"""Browser tests for the Packet Capture pane and the parser behind it.

The parser runs where it runs in the product — in Chromium, over the PDML
fixtures committed from real Labs — and is asserted on what a Guide names:
the fields are there, nested as the protocol nests them, carrying their
byte offsets.  The pane is driven against a live server whose Lab is a fake
and whose tshark is a pipe the test writes those same fixtures into, so
everything from the Link menu to the expanded field tree is real except
the packets' origin.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from mniplab.constants import LABS_DIR
from mniplab.tests.browser import BrowserContext, LiveServerCase, Page, box_of, choose_account_item
from mniplab.tests.fakes import FAKE_LAB_KEY, FakeCaptureSpawner
from mniplab.tests.test_web_capture import switch_lab, two_node_lab
from mniplab.webserver import registry
from mniplab.webserver.capture import CaptureService

pytestmark = pytest.mark.browser_services

FIXTURES = Path(__file__).parent / "fixtures" / "pdml"

# The Capture pane on show; Packet Analysis holds up to two, one per tab.
PANE = "#packet-analysis .capture-pane:not([hidden])"
ROWS = f"{PANE} .capture-packet-rows"
STATUS = f"{PANE} .packet-analysis-status"
PROBLEM = f"{PANE} .packet-analysis-problem"
# The selected Capture's state, count and actions, which sit in the tab row.
HEADER = "#packet-analysis .packet-analysis-header-slot"


def fixture_packets(name: str) -> list:
    """One fixture's packets, each a whole <packet> element."""
    text = (FIXTURES / f"{name}.pdml").read_text(encoding="utf-8")
    packets = []
    for part in text.split("</packet>")[:-1]:
        packets.append(part[part.index("<packet>"):] + "</packet>")
    return packets


# Parses every packet of one fixture and reports, per packet, the path of
# field names down to each field the test asks about, and its offsets.
PARSE_SCRIPT = """async ({packets, fields}) => {
    const { parsePacket, findField } = await import('/static/js/capture/pdml.js');
    return packets.map(pdml => {
        const tree = parsePacket(pdml);
        const found = {};
        for (const name of fields) {
            const path = findField(tree, name);
            found[name] = path && {
                path: path.map(n => n.name),
                pos: path[path.length - 1].pos,
                size: path[path.length - 1].size,
                label: path[path.length - 1].label,
            };
        }
        return { protocols: tree.protocols.map(p => p.name), found };
    });
}"""


class PdmlParserTests(LiveServerCase):
    """PDML in, the field tree a Learner expands out."""

    context: BrowserContext
    page: Page

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        assert cls.browser is not None
        cls.context = cls.browser.new_context()
        cls.page = cls.context.new_page()
        cls.page.goto(cls.base_url + "/")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.context.close()
        super().tearDownClass()

    def parse(self, name: str, *fields: str) -> list:
        return self.page.evaluate(
            PARSE_SCRIPT, {"packets": fixture_packets(name), "fields": list(fields)}
        )

    def test_the_dhcp_message_type_sits_inside_option_53(self) -> None:
        packets = self.parse("dhcp", "dhcp.option.dhcp", "dhcp.ip.your")

        self.assertEqual(len(packets), 4)
        message_type = packets[0]["found"]["dhcp.option.dhcp"]
        self.assertEqual(message_type["path"][0], "dhcp")
        self.assertIn("dhcp.option.type", message_type["path"])
        self.assertIn("Discover", message_type["label"])
        self.assertIn("ACK", packets[3]["found"]["dhcp.option.dhcp"]["label"])

    def test_the_offered_address_carries_its_bytes(self) -> None:
        offer = self.parse("dhcp", "dhcp.ip.your")[1]["found"]["dhcp.ip.your"]

        self.assertEqual(offer["size"], 4)
        self.assertIsInstance(offer["pos"], int)

    def test_ip_addresses_keep_their_offsets(self) -> None:
        source = self.parse("dhcp", "ip.src")[0]["found"]["ip.src"]

        self.assertEqual((source["pos"], source["size"]), (26, 4))

    def test_the_layers_are_the_packets_own_in_order(self) -> None:
        protocols = self.parse("dhcp")[0]["protocols"]

        self.assertEqual(protocols, ["frame", "eth", "ip", "udp", "dhcp"])

    def test_wiresharks_hidden_duplicates_are_left_out(self) -> None:
        found = self.parse("dhcp", "ip.addr")[0]["found"]

        self.assertIsNone(found["ip.addr"])

    def test_ospf_hello_and_ls_update_are_named(self) -> None:
        hello, update = self.parse("ospf", "ospf.msg", "ospf.hello", "ospf.lsa")

        self.assertIn("Hello Packet", hello["found"]["ospf.msg"]["label"])
        self.assertIsNotNone(hello["found"]["ospf.hello"])
        self.assertIn("LS Update", update["found"]["ospf.msg"]["label"])
        self.assertEqual(update["found"]["ospf.lsa"]["path"][0], "ospf")

    def test_the_bgp_as_path_nests_inside_its_path_attribute(self) -> None:
        opened, update = self.parse(
            "bgp", "bgp.open.myas", "bgp.update.path_attribute.as_path_segment",
            "bgp.update.path_attribute.multi_exit_disc",
        )

        self.assertIsNotNone(opened["found"]["bgp.open.myas"])
        as_path = update["found"]["bgp.update.path_attribute.as_path_segment"]
        self.assertEqual(as_path["path"][0], "bgp")
        self.assertIn("bgp.update.path_attribute", as_path["path"])
        self.assertIsNotNone(update["found"]["bgp.update.path_attribute.multi_exit_disc"])

    def test_dns_queries_and_answers_parse_with_and_without_dnssec(self) -> None:
        plain = self.parse("dns", "dns.qry.name", "dns.rrsig.signers_name")
        signed = self.parse("dnssec", "dns.qry.name", "dns.rrsig.signers_name", "dns.dnskey.flags")

        self.assertIsNotNone(plain[1]["found"]["dns.qry.name"])
        self.assertIsNone(plain[1]["found"]["dns.rrsig.signers_name"])
        self.assertIsNotNone(signed[1]["found"]["dns.rrsig.signers_name"])
        self.assertIsNotNone(signed[1]["found"]["dns.dnskey.flags"])

    def test_icmp_and_arp_are_named(self) -> None:
        packets = self.parse("icmp-arp", "arp.opcode", "icmp.type")

        self.assertIsNotNone(packets[0]["found"]["arp.opcode"])
        self.assertIsNotNone(packets[2]["found"]["icmp.type"])

    def test_the_router_advertisement_flags_are_fields(self) -> None:
        advertisement = self.parse(
            "router-advertisement",
            "icmpv6.nd.ra.flag.m", "icmpv6.nd.ra.flag.o", "icmpv6.opt.prefix.flag.a",
        )[0]["found"]

        for name in ("icmpv6.nd.ra.flag.m", "icmpv6.nd.ra.flag.o", "icmpv6.opt.prefix.flag.a"):
            self.assertIsNotNone(advertisement[name], name)
        self.assertIn("icmpv6.nd.ra.flag", advertisement["icmpv6.nd.ra.flag.m"]["path"])

    def test_every_fixture_packet_parses_to_named_layers(self) -> None:
        for path in sorted(FIXTURES.glob("*.pdml")):
            for packet in self.parse(path.stem):
                with self.subTest(fixture=path.stem):
                    self.assertGreaterEqual(len(packet["protocols"]), 2)
                    self.assertNotIn("", packet["protocols"])


def wait_until(condition: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.05)


class CapturePaneTests(LiveServerCase):
    """The pane, from the Link menu to an expanded field tree."""

    def setUp(self) -> None:
        self.spawner = FakeCaptureSpawner()
        previous = registry.capture_service
        registry.capture_service = CaptureService(spawner=self.spawner, grace_seconds=5)
        self.addCleanup(setattr, registry, "capture_service", previous)
        self.addCleanup(self._stop_captures)
        registry.lab_service.lab = two_node_lab()
        self.addCleanup(setattr, registry.lab_service, "lab", None)

    def _stop_captures(self) -> None:
        # On the server's own loop: that is where the service reads its pipes.
        if self.spawner.loop is not None:
            asyncio.run_coroutine_threadsafe(
                registry.capture_service.stop_all("test over"), self.spawner.loop
            ).result(timeout=10)

    def open_capture_modal(self, page: Any) -> None:
        """Open the r1–h1 Link's menu, as clicking the Link does, and press Packet Capture."""
        page.wait_for_selector("svg#topology .node")
        page.evaluate("""async () => {
            const { appState } = await import('/static/js/state/app-state.js');
            const { showLinkContextMenu } = await import('/static/js/topology/interactions.js');
            const link = appState.currentTopology.links.find(l =>
                [l.source.id, l.target.id].sort().join() === 'h1,r1');
            showLinkContextMenu(link, { clientX: 0, clientY: 0 });
        }""")
        page.click("#link-capture-btn")
        page.wait_for_selector("#capture-modal[style*='flex']")

    def open_from_link_modal(self, page: Any, interface: str = "h1-eth0") -> None:
        self.open_capture_modal(page)
        page.click(f".link-capture-end[data-interface='{interface}']")
        wait_until(lambda: len(self.spawner.processes) == 1)
        page.wait_for_selector("#packet-analysis .capture-pane:not([hidden]) .packet-analysis-status:has-text('Capturing on')")

    def open_terminal(self, page: Any) -> None:
        page.evaluate("""async () => {
            const { appState } = await import('/static/js/state/app-state.js');
            appState.tabManager.openTerminal('r1');
        }""")
        page.wait_for_selector(".terminal-instance[data-node='r1'] .xterm-helper-textarea")


    def emit(self, name: str, index: int = 0) -> None:
        data = (FIXTURES / f"{name}.pdml").read_bytes()
        self.spawner.processes[index].emit(data)

    def lab_file(self, path: str) -> Path:
        """A file the fake Capture would have written, removed with its Lab
        directory afterwards: a stray Lab directory reads as a live Lab."""
        target = Path(path)
        lab_dir = Path(LABS_DIR) / FAKE_LAB_KEY
        made = lab_dir if not lab_dir.exists() else target.parent
        self.addCleanup(shutil.rmtree, made, True)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def tshark_filter(self, index: int) -> Any:
        argv = self.spawner.commands[index].argv
        return argv[argv.index("-f") + 1] if "-f" in argv else None

    def wait_rows(self, page: Any, count: int) -> None:
        page.wait_for_function(
            f"() => document.querySelectorAll('{ROWS} tr').length === {count}"
        )

    def test_the_link_menu_opens_a_capture_modal_naming_both_ends(self) -> None:
        with self.signed_in_page() as page:
            self.open_capture_modal(page)
            ends = page.eval_on_selector_all(
                ".link-capture-end", "els => els.map(e => e.textContent)"
            )
            focus_inside = page.evaluate(
                "() => document.getElementById('capture-modal').contains(document.activeElement)"
            )

        self.assertEqual(sorted(ends), ["Capture on h1-eth0 (h1)", "Capture on r1-eth0 (r1)"])
        self.assertTrue(focus_inside)

    def test_a_link_to_a_switch_offers_the_switch_port_and_captures_it_in_the_root_namespace(self) -> None:
        registry.lab_service.lab = switch_lab(FAKE_LAB_KEY)
        with self.signed_in_page() as page:
            page.wait_for_selector("svg#topology .node")
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                const { showLinkContextMenu } = await import('/static/js/topology/interactions.js');
                const link = appState.currentTopology.links.find(l =>
                    [l.source.id, l.target.id].sort().join() === 'h1,s1');
                showLinkContextMenu(link, { clientX: 0, clientY: 0 });
            }""")
            page.click("#link-capture-btn")
            page.wait_for_selector("#capture-modal[style*='flex']")
            ends = page.eval_on_selector_all(
                ".link-capture-end", "els => els.map(e => e.textContent)"
            )
            page.click(".link-capture-end[data-interface='s1-eth1']")
            wait_until(lambda: len(self.spawner.processes) == 1)
            page.wait_for_selector("#packet-analysis .capture-pane:not([hidden]) .packet-analysis-status:has-text('Capturing on')")
            tab = page.inner_text("#packet-analysis [role='tab'][aria-selected='true']")

        self.assertEqual(sorted(ends), ["Capture on h1-eth0 (h1)", "Capture on s1-eth1 (s1)"])
        self.assertEqual(tab, "s1-eth1 (s1)")
        argv = self.spawner.commands[0].argv
        self.assertNotIn("nsenter", argv)
        self.assertEqual(argv[argv.index("-i") + 1], f"{FAKE_LAB_KEY}-s1-eth1")

    def test_a_switch_node_menu_has_no_packet_capture_entry(self) -> None:
        registry.lab_service.lab = switch_lab(FAKE_LAB_KEY)
        with self.signed_in_page() as page:
            page.wait_for_selector("svg#topology .node")
            hidden = page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                const { showNodeContextMenu } = await import('/static/js/topology/interactions.js');
                const node = appState.currentTopology.nodes.find(n => n.id === 's1');
                showNodeContextMenu(node, { clientX: 0, clientY: 0 });
                const button = document.getElementById('node-capture-btn');
                return [button.classList.contains('hidden'), button.getAttribute('aria-hidden')];
            }""")

        self.assertEqual(hidden, [True, "true"])

    def test_both_ends_of_a_switch_to_switch_link_are_offered_and_the_note_stays_true(self) -> None:
        with self.signed_in_page() as page:
            page.wait_for_selector("svg#topology .node")
            offered = page.evaluate("""async () => {
                const { renderCaptureEnds } = await import('/static/js/capture/packet-analysis.js');
                const ends = (a, b) => {
                    const box = document.createElement('div');
                    renderCaptureEnds(box, {
                        source: { id: a[0], type: a[1] }, source_intf: a[0] + '-eth1',
                        target: { id: b[0], type: b[1] }, target_intf: b[0] + '-eth2',
                    }, () => {}, null);
                    return [[...box.querySelectorAll('button')].map(e => e.textContent),
                            box.querySelector('.link-capture-none')?.textContent ?? null];
                };
                return [ends(['s1', 'switch'], ['s2', 'switch']), ends(['c1', 'container'], ['c2', 'container'])];
            }""")

        self.assertEqual(offered[0][0], ["Capture on s1-eth1 (s1)", "Capture on s2-eth2 (s2)"])
        self.assertIsNone(offered[0][1])
        self.assertEqual(offered[1][0], [])
        self.assertIn("Router, Host, Speaker or Switch", offered[1][1])

    def test_the_configure_link_modal_is_left_as_it_was(self) -> None:
        with self.signed_in_page() as page:
            capture_in_config = page.locator("#link-modal .link-capture-end, #link-modal #link-capture-ends").count()

        self.assertEqual(capture_in_config, 0)

    def test_choosing_an_end_closes_the_modal_and_starts_the_capture(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)

            self.assertFalse(page.is_visible("#capture-modal"))
            self.assertTrue(page.is_visible("#packet-analysis"))
            self.assertEqual(page.inner_text("#packet-analysis [role='tab'][aria-selected='true']"), "h1-eth0 (h1)")

        argv = self.spawner.commands[0].argv
        self.assertEqual(argv[argv.index("-i") + 1], "h1-eth0")

    def test_packets_stream_in_as_rows_and_expand_into_their_fields(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            self.emit("dhcp")
            page.wait_for_function(
                "() => document.querySelectorAll('#packet-analysis .capture-pane:not([hidden]) .capture-packet-rows tr').length === 4"
            )
            first_row = page.inner_text("#packet-analysis .capture-pane:not([hidden]) .capture-packet-rows tr:first-child")

            page.click("#packet-analysis .capture-pane:not([hidden]) .capture-packet-rows tr:first-child")
            page.click("#packet-analysis .capture-pane:not([hidden]) .capture-packet-detail details[data-field='dhcp'] > summary")
            dhcp_open = page.eval_on_selector(
                "#packet-analysis .capture-pane:not([hidden]) .capture-packet-detail details[data-field='dhcp']", "el => el.open"
            )
            option = page.locator(
                "#packet-analysis .capture-pane:not([hidden]) .capture-packet-detail [data-field='dhcp.option.type']"
            ).first.get_attribute("data-pos")

        self.assertIn("DHCP", first_row)
        self.assertIn("Discover", first_row)
        self.assertTrue(dhcp_open)
        self.assertIsNotNone(option)

    def test_the_fields_appear_only_once_a_packet_is_selected(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            self.emit("dhcp")
            page.wait_for_function(
                "() => document.querySelectorAll('#packet-analysis .capture-pane:not([hidden]) .capture-packet-rows tr').length === 4"
            )
            hidden_before = page.eval_on_selector("#packet-analysis .capture-pane:not([hidden]) .capture-packet-detail", "el => el.hidden")
            list_before = page.eval_on_selector("#packet-analysis .capture-pane:not([hidden]) .capture-packet-list", "el => el.clientHeight")

            page.click("#packet-analysis .capture-pane:not([hidden]) .capture-packet-rows tr:nth-child(2)")
            hidden_after = page.eval_on_selector("#packet-analysis .capture-pane:not([hidden]) .capture-packet-detail", "el => el.hidden")
            list_after = page.eval_on_selector("#packet-analysis .capture-pane:not([hidden]) .capture-packet-list", "el => el.clientHeight")

        self.assertTrue(hidden_before)
        self.assertFalse(hidden_after)
        # With nothing selected the list has the pane to itself.
        self.assertGreater(list_before, list_after)

    def test_hiding_the_fields_returns_focus_to_the_packet(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            self.emit("dhcp")
            page.wait_for_function(
                "() => document.querySelectorAll('#packet-analysis .capture-pane:not([hidden]) .capture-packet-rows tr').length === 4"
            )
            page.click("#packet-analysis .capture-pane:not([hidden]) .capture-packet-rows tr:nth-child(2)")
            page.click("#packet-analysis .capture-pane:not([hidden]) .capture-packet-detail .capture-detail-close")
            closed_by_button = page.eval_on_selector("#packet-analysis .capture-pane:not([hidden]) .capture-packet-detail", "el => el.hidden")
            focused = page.evaluate("() => document.activeElement.getAttribute('aria-label')")
            pressed = page.evaluate("() => document.activeElement.getAttribute('aria-pressed')")

            page.keyboard.press("ArrowDown")
            shown_again = page.eval_on_selector("#packet-analysis .capture-pane:not([hidden]) .capture-packet-detail", "el => !el.hidden")
            page.keyboard.press("Escape")
            closed_by_escape = page.eval_on_selector("#packet-analysis .capture-pane:not([hidden]) .capture-packet-detail", "el => el.hidden")

        self.assertTrue(closed_by_button)
        self.assertIn("Packet 2", focused)
        self.assertEqual(pressed, "false")
        self.assertTrue(shown_again)
        self.assertTrue(closed_by_escape)

    def test_the_pane_shares_the_right_slot_and_a_terminal_stays_usable(self) -> None:
        with self.signed_in_page() as page:
            self.open_terminal(page)
            self.open_from_link_modal(page)

            terminal = page.eval_on_selector(".terminal-content", "el => el.getBoundingClientRect().toJSON()")
            analysis_box = page.eval_on_selector("#packet-analysis", "el => el.getBoundingClientRect().toJSON()")
            page.focus(".terminal-instance[data-node='r1'] .xterm-helper-textarea")
            focused = page.evaluate("() => document.activeElement.id")

        self.assertGreater(terminal["height"], 50)
        self.assertGreater(analysis_box["height"], 50)
        self.assertLessEqual(terminal["bottom"], analysis_box["top"] + 1)
        self.assertEqual(focused, "terminal-input-r1")

    def test_the_packet_list_is_keyboard_navigable(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            self.emit("dhcp")
            page.wait_for_function(
                "() => document.querySelectorAll('#packet-analysis .capture-pane:not([hidden]) .capture-packet-rows tr').length === 4"
            )
            page.focus("#packet-analysis .capture-pane:not([hidden]) .capture-packet-rows .capture-row-select[tabindex='0']")
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            selected = page.evaluate("() => document.activeElement.getAttribute('aria-label')")
            heading = page.inner_text("#packet-analysis .capture-pane:not([hidden]) .capture-packet-detail .capture-detail-heading")
            page.keyboard.press("Tab")
            left_list = page.evaluate(
                "() => !document.activeElement.classList.contains('capture-row-select')"
            )

        self.assertIn("Packet 2", selected)
        self.assertIn("Offer", heading)
        self.assertTrue(left_list)

    def test_the_status_is_announced_politely(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            live = page.get_attribute("#packet-analysis .capture-pane:not([hidden]) .packet-analysis-status", "aria-live")
            role = page.get_attribute("#packet-analysis .capture-pane:not([hidden]) .packet-analysis-status", "role")
            classes = page.get_attribute(STATUS, "class") or ""
            problem_hidden = page.eval_on_selector(PROBLEM, "el => el.hidden")
            state = page.inner_text(f"{HEADER} .packet-analysis-state")

        self.assertEqual((role, live), ("status", "polite"))
        # Routine news is spoken, not shown: the badge says it in a word.
        self.assertIn("sr-only", classes.split())
        self.assertTrue(problem_hidden)
        self.assertEqual(state, "Capturing")

    def test_a_reload_reattaches_to_the_running_capture(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            self.emit("dhcp")
            page.wait_for_function(
                "() => document.querySelectorAll('#packet-analysis .capture-pane:not([hidden]) .capture-packet-rows tr').length === 4"
            )

            page.reload()
            page.wait_for_function(
                "() => document.querySelectorAll('#packet-analysis .capture-pane:not([hidden]) .capture-packet-rows tr').length === 4"
            )

        self.assertEqual(len(self.spawner.commands), 1)
        self.assertEqual(self.spawner.stopped, [])

    def test_every_preset_names_the_expression_it_expands_to(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            presets = page.eval_on_selector_all(
                f"{PANE} .capture-preset",
                "els => els.map(e => [e.textContent, e.dataset.filter, e.title])",
            )
            repeated = page.locator(f"{PANE} .capture-filter-expansion").count()

        names = [name for name, _, _ in presets]
        self.assertEqual(names, ["All", "DHCP", "DNS", "OSPF", "BGP", "ICMP", "ARP"])
        for name, expression, title in presets[1:]:
            with self.subTest(preset=name):
                self.assertTrue(expression)
                self.assertIn(expression, title)
        # The filter box already shows the expression in force; no line repeats it.
        self.assertEqual(repeated, 0)

    def test_a_preset_restarts_the_capture_and_says_so(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            self.emit("icmp-arp")
            self.wait_rows(page, 4)

            page.click(f"{PANE} .capture-preset:has-text('DHCP')")
            wait_until(lambda: len(self.spawner.commands) == 2)
            page.wait_for_selector(f"{STATUS}:has-text('restarted')")
            status = page.inner_text(STATUS)
            rows = page.locator(f"{ROWS} tr").count()
            box = page.input_value(f"{PANE} .capture-filter-input")
            pressed = page.get_attribute(f"{PANE} .capture-preset:has-text('DHCP')", "aria-pressed")

        self.assertEqual(self.tshark_filter(1), "udp port 67 or udp port 68 or udp port 546 or udp port 547")
        # The unfiltered Capture had no one else watching: it is gone.
        self.assertIn(self.spawner.processes[0], self.spawner.stopped)
        self.assertEqual(rows, 0)
        self.assertIn("list was cleared", status)
        self.assertIn("DHCP", status)
        self.assertEqual(box, "udp port 67 or udp port 68 or udp port 546 or udp port 547")
        self.assertEqual(pressed, "true")

    def test_a_reload_reattaches_through_the_filter_it_had(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            page.click(f"{PANE} .capture-preset:has-text('ARP')")
            wait_until(lambda: len(self.spawner.commands) == 2)

            page.reload()
            page.wait_for_selector(f"{STATUS}:has-text('Capturing on')")
            box = page.input_value(f"{PANE} .capture-filter-input")

        self.assertEqual(len(self.spawner.commands), 2)
        self.assertEqual(box, "arp")

    def test_a_typed_expression_is_applied(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            page.fill(f"{PANE} .capture-filter-input", "host 10.0.0.1 and icmp")
            page.press(f"{PANE} .capture-filter-input", "Enter")
            wait_until(lambda: len(self.spawner.commands) == 2)

        self.assertEqual(self.tshark_filter(1), "host 10.0.0.1 and icmp")

    def test_an_invalid_expression_is_read_out_and_can_be_corrected(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            page.fill(f"{PANE} .capture-filter-input", "udp prt 5")
            page.click(f"{PANE} .capture-filter-apply")
            wait_until(lambda: len(self.spawner.commands) == 2
                       and len(self.spawner.processes) == 2)
            log = self.lab_file(self.spawner.commands[1].log_path)
            log.write_text(
                "tshark: Invalid capture filter \"udp prt 5\" for interface 'h1-eth0'.\n\n"
                "That string isn't a valid capture filter (can't parse filter expression: "
                "syntax error).\n"
            )
            self.spawner.processes[1].exit()
            page.wait_for_selector(f"{STATUS}:has-text('not a valid capture filter')")
            status = page.inner_text(STATUS)
            problem = page.inner_text(PROBLEM)

            page.fill(f"{PANE} .capture-filter-input", "udp port 5")
            page.click(f"{PANE} .capture-filter-apply")
            wait_until(lambda: len(self.spawner.commands) == 3
                       and len(self.spawner.processes) == 3)

        self.assertIn("syntax error", status)
        # A problem stays in view until it is put right.
        self.assertIn("syntax error", problem)
        self.assertEqual(self.tshark_filter(2), "udp port 5")

    def test_the_pcap_downloads_and_the_learner_is_told_it_goes_with_the_lab(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            pcap = Path(self.spawner.commands[0].pcap_path)
            ring = self.lab_file(str(pcap.with_name(pcap.stem + "_00001_20260923014330.pcapng")))
            ring.write_bytes(b"\x0a\x0d\x0d\x0a section")
            page.click(f"{HEADER} .capture-info")
            note = page.inner_text(f"{PANE} .capture-pcap-note")
            with page.expect_download() as download:
                page.click(f"{HEADER} .capture-download")
            saved = Path(download.value.path()).read_bytes()
            name = download.value.suggested_filename

        self.assertIn("deleted when the Lab stops", note)
        self.assertEqual(saved, b"\x0a\x0d\x0d\x0a section")
        self.assertTrue(name.endswith("h1-h1-eth0.pcapng"))

    def test_two_captures_run_as_tabs_and_a_third_is_refused(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page, "h1-eth0")
            self.open_capture_modal(page)
            page.click(".link-capture-end[data-interface='r1-eth0']")
            wait_until(lambda: len(self.spawner.processes) == 2)
            tabs = page.eval_on_selector_all(
                "#packet-analysis [role='tab']",
                "els => els.map(e => [e.textContent, e.getAttribute('aria-selected')])",
            )
            self.emit("dhcp", index=1)
            self.wait_rows(page, 4)
            page.focus("#packet-analysis [role='tab'][aria-selected='true']")
            page.keyboard.press("ArrowLeft")
            first_rows = page.locator(f"{ROWS} tr").count()

            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                appState.networkConsole.openNodeCaptureModal('r1');
            }""")
            page.click(".link-capture-end[data-interface='r1-eth1']")
            refusal = page.inner_text("#capture-modal-message")
            still_open = page.is_visible("#capture-modal")

        self.assertEqual(tabs, [["h1-eth0 (h1)", "false"], ["r1-eth0 (r1)", "true"]])
        self.assertEqual(first_rows, 0)
        self.assertIn("Two Captures", refusal)
        self.assertTrue(still_open)
        self.assertEqual(len(self.spawner.commands), 2)

    def test_home_and_end_select_the_first_and_last_capture_tab(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page, "h1-eth0")
            self.open_capture_modal(page)
            page.click(".link-capture-end[data-interface='r1-eth0']")
            wait_until(lambda: len(self.spawner.processes) == 2)
            tabs = page.locator("#packet-analysis").get_by_role("tab")

            page.focus("#packet-analysis [role='tab'][aria-selected='true']")
            page.keyboard.press("Home")
            first = tabs.evaluate_all("els => els.map(e => e.getAttribute('aria-selected'))")
            page.keyboard.press("End")
            last = tabs.evaluate_all("els => els.map(e => e.getAttribute('aria-selected'))")

        self.assertEqual(first, ["true", "false"])
        self.assertEqual(last, ["false", "true"])

    def test_dropped_packets_are_counted_and_announced(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                const pane = appState.packetAnalysis.active;
                pane.handle({ type: 'dropped', count: 3 });
                pane.handle({ type: 'rate_guard', active: true });
                pane.handle({ type: 'dropped', count: 2 });
            }""")
            notice = page.inner_text(f"{PANE} .capture-drop-notice")
            role = page.get_attribute(f"{PANE} .capture-drop-notice", "role")

        # A later status does not overwrite it: the count stays in view.
        self.assertIn("5 packets were not shown", notice)
        self.assertEqual(role, "status")

    def test_the_node_menu_reaches_the_routers_end_of_the_internet_gateway_link(self) -> None:
        with self.signed_in_page() as page:
            page.wait_for_selector("svg#topology .node")
            page.evaluate("""async () => {
                const { appState } = await import('/static/js/state/app-state.js');
                const { showNodeContextMenu } = await import('/static/js/topology/interactions.js');
                const r1 = appState.currentTopology.nodes.find(n => n.id === 'r1');
                showNodeContextMenu(r1, { clientX: 0, clientY: 0 });
            }""")
            page.click("#node-capture-btn")
            page.wait_for_selector(".link-capture-end[data-interface='r1-eth1']")
            listed = page.eval_on_selector_all(
                ".link-capture-end", "els => els.map(e => [e.dataset.interface, e.dataset.undrawn || ''])"
            )
            page.click(".link-capture-end[data-interface='r1-eth1']")
            wait_until(lambda: len(self.spawner.processes) == 1)

        # The Internet Gateway's Link is drawn now, so its Router end is too.
        self.assertEqual(listed, [["r1-eth0", ""], ["r1-eth1", ""]])
        argv = self.spawner.commands[0].argv
        self.assertEqual(argv[argv.index("-i") + 1], "r1-eth1")

    def test_the_filter_box_is_labelled_and_the_tabs_are_a_tab_list(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            label = page.evaluate(f"""() => {{
                const input = document.querySelector("{PANE} .capture-filter-input");
                return input.labels[0].textContent;
            }}""")
            tablist = page.get_attribute("#packet-analysis .capture-tabs", "role")
            controls = page.get_attribute("#packet-analysis [role='tab']", "aria-controls")
            panel_role = page.get_attribute(f"#{controls}", "role")

        self.assertIn("Capture filter", label)
        self.assertEqual((tablist, panel_role), ("tablist", "tabpanel"))

    def test_stop_ends_an_unwatched_capture_and_closes_the_pane(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            page.click(f"{HEADER} .packet-analysis-stop")
            wait_until(lambda: self.spawner.stopped == self.spawner.processes)
            pane_hidden = page.eval_on_selector("#packet-analysis", "el => el.hidden")

        self.assertTrue(pane_hidden)

    def test_the_explanations_wait_behind_the_info_button(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            info = f"{HEADER} .capture-info"
            before = (page.get_attribute(info, "aria-expanded"), page.is_visible(f"{PANE} .capture-note"))
            controls = page.get_attribute(info, "aria-controls")
            page.click(info)
            after = (page.get_attribute(info, "aria-expanded"), page.is_visible(f"#{controls}"))
            text = page.inner_text(f"#{controls}")

        self.assertEqual(before, ("false", False))
        self.assertEqual(after, ("true", True))
        for said in ("kernel", "deleted when the Lab stops", "Escape", "two Captures"):
            with self.subTest(said=said):
                self.assertIn(said, text)

    def test_the_header_is_labelled_buttons_large_enough_to_hit(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            buttons = page.eval_on_selector_all(
                f"{HEADER} button",
                "bs => bs.map(b => { const r = b.getBoundingClientRect();"
                " return [b.getAttribute('aria-label'), Math.min(r.width, r.height)]; })",
            )
            count = page.inner_text(f"{HEADER} .packet-analysis-count")

        self.assertEqual([b[0] for b in buttons], ["About this Capture", "Download pcap", "Stop capture"])
        # WCAG 2.2 target size (minimum): 24 by 24 CSS pixels.
        self.assertTrue(all(size >= 24 for _, size in buttons), buttons)
        self.assertEqual(count, "0 packets")

    def tab_row_geometry(self, page: Any) -> dict:
        """Where the tab row and what sits in or under it are, in page pixels."""
        return page.evaluate("""() => {
            const box = (selector) => {
                const el = document.querySelector(selector);
                const r = el.getBoundingClientRect();
                return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height };
            };
            const analysis = '#packet-analysis';
            return {
                width: box(analysis).right - box(analysis).left,
                heading: box(`${analysis} .packet-analysis-heading`),
                tab: box(`${analysis} [role=tab][aria-selected=true]`),
                state: box(`${analysis} .packet-analysis-state`),
                count: box(`${analysis} .packet-analysis-count`),
                about: box(`${analysis} .capture-info`),
                download: box(`${analysis} .capture-download`),
                stop: box(`${analysis} .packet-analysis-stop`),
                all: box(`${analysis} .capture-preset`),
                filter: box(`${analysis} .capture-filter-input`),
                list: box(`${analysis} .capture-packet-list`),
            };
        }""")

    def test_a_wide_packet_analysis_keeps_state_count_and_actions_in_its_tab_row(self) -> None:
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1920, "height": 1080})
            self.open_terminal(page)
            self.open_from_link_modal(page)
            g = self.tab_row_geometry(page)

        self.assertGreaterEqual(g["width"], 600)
        middle = (g["tab"]["top"] + g["tab"]["bottom"]) / 2
        for part in ("heading", "state", "count", "about", "download", "stop"):
            with self.subTest(part=part):
                self.assertLessEqual(abs((g[part]["top"] + g[part]["bottom"]) / 2 - middle), 8, g)
        self.assertLessEqual(g["tab"]["right"], g["state"]["left"])

    def test_the_presets_share_one_row_with_the_capture_filter(self) -> None:
        for width in (1920, 1600):
            with self.subTest(viewport=width), self.signed_in_page() as page:
                page.set_viewport_size({"width": width, "height": 1080})
                self.open_from_link_modal(page)
                g = self.tab_row_geometry(page)

                self.assertGreaterEqual(g["width"], 600)
                self.assertLessEqual(
                    abs((g["all"]["top"] + g["all"]["bottom"]) / 2 - (g["filter"]["top"] + g["filter"]["bottom"]) / 2),
                    6,
                )

    def test_a_narrow_packet_analysis_puts_the_state_row_back_under_the_tabs(self) -> None:
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": 1000, "height": 900})
            self.open_from_link_modal(page)
            self.open_capture_modal(page)
            page.click(".link-capture-end[data-interface='r1-eth0']")
            wait_until(lambda: len(self.spawner.processes) == 2)
            g = self.tab_row_geometry(page)
            tab_heights = page.locator("#packet-analysis").get_by_role("tab").evaluate_all(
                "els => els.map(e => e.getBoundingClientRect().height)"
            )

        self.assertLess(g["width"], 600)
        for part in ("state", "count", "about", "download", "stop"):
            with self.subTest(part=part):
                self.assertGreaterEqual(g[part]["top"], g["tab"]["bottom"] - 1)
        self.assertLessEqual(g["heading"]["height"], 24, "the heading stays on one line")
        self.assertEqual(len(tab_heights), 2)
        self.assertLessEqual(max(tab_heights), 36, "a Capture tab stays on one line")

    def test_every_packet_analysis_control_names_what_it_shows(self) -> None:
        with self.signed_in_page() as page:
            self.open_from_link_modal(page)
            pairs = page.eval_on_selector_all(
                "#packet-analysis button",
                "els => els.filter(e => e.textContent.trim() && e.offsetParent)"
                ".map(e => [e.textContent.trim(), e.getAttribute('aria-label') || e.textContent.trim()])",
            )

        self.assertTrue(pairs)
        for label, name in pairs:
            with self.subTest(control=label):
                self.assertIn(label.lower(), name.lower())





    def test_monitoring_keeps_the_capture_stream_and_selected_packet(self) -> None:
        self._assert_capture_survives_monitoring(1280)

    def test_mobile_monitoring_keeps_the_capture_stream_and_selected_packet(self) -> None:
        self._assert_capture_survives_monitoring(375)

    def _assert_capture_survives_monitoring(self, width: int) -> None:
        """Keep a streaming capture and its selected packet across views."""
        with self.signed_in_page() as page:
            page.set_viewport_size({"width": width, "height": 812})
            self.open_from_link_modal(page)
            if width == 375:
                main = box_of(page.locator(".main-content"))
                pane = box_of(page.locator("#packet-analysis"))
                self.assertGreaterEqual(pane["y"], main["y"] + main["height"])
                self.assertGreaterEqual(box_of(page.locator("#packet-analysis"))["height"], 200)
            self.emit("dhcp")
            page.wait_for_function(
                "() => document.querySelectorAll('#packet-analysis .capture-packet-rows tr').length === 4"
            )
            page.locator(f"{ROWS} tr").nth(1).click()
            pane_handle = page.locator(PANE).element_handle()
            process = self.spawner.processes[0]
            choose_account_item(page, "Sessions & Terminals", "menuitemcheckbox")
            self.assertFalse(page.locator("#packet-analysis").is_visible())
            self.emit("dhcp")
            page.wait_for_function(
                "() => document.querySelectorAll('#packet-analysis .capture-packet-rows tr').length === 8"
            )
            page.get_by_role("button", name="Back to topology", exact=True).click()
            self.assertTrue(page.locator("#packet-analysis").is_visible())
            self.assertTrue(pane_handle.evaluate("node => node.isConnected"))
            self.assertIs(self.spawner.processes[0], process)
            self.assertEqual(len(self.spawner.processes), 1)
            self.assertEqual(page.locator(f"{ROWS} tr").nth(1).locator(".capture-row-select").get_attribute("aria-pressed"), "true")
            self.assertFalse(page.locator(f"{PANE} .capture-packet-detail").evaluate("node => node.hidden"))
