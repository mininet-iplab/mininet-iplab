"""Shared scaffolding for the Lab Authoring browser tests.

Page fixtures, seeded drafts, keyboard helpers for the row editors and the
classroom journey, and the rendered accessibility audit.
"""

import asyncio
import copy
import os
import re
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from mniplab.labprocess import LabChild
from mniplab.persistent_config import ConfigCopies
from mniplab.tests.browser import LiveServerCase, LiveServerMixin, Locator, Page, expect, screenshots
from mniplab.tests.test_web_multi_lab import FakeLauncher
from mniplab.webserver import auth, registry
from mniplab.webserver.config import settings
from mniplab.webserver.groups import Groups


def upload_script(page: Page, name: str, python: str) -> None:
    """Upload a script as a new Python draft through the page's own session.

    Args:
        page: A signed-in Instructor's authoring page.
        name: The file name, such as lesson.py.
        python: The script.
    """
    page.evaluate("""async ([name, python]) => {
        const body = new FormData();
        body.append('file', new File([python], name, {type: 'text/x-python'}));
        const response = await fetch('/api/authoring/drafts/upload', {method: 'POST', body});
        if (!response.ok) throw new Error(await response.text());
    }""", [name, python])


def labs_explorer(page: Page) -> Locator:
    """The Labs explorer, unfolded if a draft had folded it.

    Args:
        page: A signed-in Instructor's authoring page.

    Returns:
        The explorer's complementary landmark.
    """
    # A draft opened on arrival folds the explorer once the drafts have loaded.
    page.wait_for_function(
        "() => document.querySelector('#authoring-status')?.textContent !== 'Loading drafts…'"
    )
    toggle = page.get_by_role("button", name="Labs", exact=True)
    if toggle.get_attribute("aria-expanded") == "false":
        toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    return page.get_by_role("complementary", name="Labs")


def open_from_explorer(page: Page, name: str) -> None:
    """Open a shared draft by its Catalog name from the Labs explorer.

    Args:
        page: A signed-in Instructor's authoring page.
        name: The draft's Catalog name.
    """
    labs_explorer(page).get_by_role("button", name=f"Open {name}", exact=True).click()


def draft_menu(page: Page, name: str) -> Locator:
    """Open a draft row's ⋯ menu in the Labs explorer and return the menu.

    Args:
        page: A signed-in Instructor's authoring page.
        name: The draft's Catalog name.

    Returns:
        The open menu holding Copy as new and Delete.
    """
    more = labs_explorer(page).get_by_role("button", name=f"More actions for {name}", exact=True)
    if more.get_attribute("aria-expanded") != "true":
        more.click()
    return page.locator(f"#{more.get_attribute('aria-controls')}")


def catalog_row(page: Page, search: str, example_id: str) -> Locator:
    """Search the Lab Catalog, expand one Lab Example's row and return it.

    Args:
        page: A signed-in Instructor's authoring page.
        search: What to type into Search labs.
        example_id: The Lab Example's Catalog id.

    Returns:
        The expanded row, showing its description and actions.
    """
    labs_explorer(page).get_by_role("searchbox", name="Search labs").fill(search)
    row = page.locator(f"#example-source-list li[data-example-id='{example_id}']")
    summary = row.locator("button[aria-expanded]")
    if summary.get_attribute("aria-expanded") != "true":
        summary.click()
    return row


def open_details(page: Page) -> None:
    """Unfold the Details sidebar, which every page load starts folded.

    Args:
        page: A signed-in Instructor's authoring page with a draft open.
    """
    toggle = page.get_by_role("button", name="Details", exact=True)
    if toggle.get_attribute("aria-expanded") == "false":
        toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    # The narrower canvas fits its view again; wait until that settles.
    page.wait_for_function("""() => {
        const view = document.getElementById('draft-canvas-g')?.getAttribute('transform');
        const settled = view === window.__detailsView;
        window.__detailsView = view;
        return settled;
    }""", polling=150)


def add_dhcp_pool(page: Page, subnet: str, start: str, end: str, lease: str = "", router: str = "",
                  dns: str = "") -> None:
    """Add one IPv4 Pool to the open DHCP Service, filling the fields that are given.

    Args:
        page: The page with a Node dialog open on its Services tab and DHCP enabled.
        subnet: Subnet CIDR, such as 10.50.0.0/24.
        start: First address of the range.
        end: Last address of the range.
        lease: Lease time in seconds, or empty for Kea's default.
        router: Router option address, or empty to omit it.
        dns: DNS server option address, or empty to omit it.
    """
    number = page.locator("#dhcp-pools-rows summary").count() + 1
    page.get_by_role("button", name="Add Pool", exact=True).click()
    for label, value in (("Subnet", subnet), ("Range start", start), ("Range end", end),
                         ("Lease time (seconds)", lease), ("Router", router), ("DNS server", dns)):
        if value:
            page.get_by_label(f"Pool {number} {label}").fill(value)


def open_interface(page: Page, node: Locator, peer: str) -> Locator:
    """Open a Node's dialog on its Interfaces tab and return the Interface that connects to ``peer``.

    Args:
        page: The page with the canvas focused and no dialog open.
        node: The Node's canvas item.
        peer: Name of the Node at the other end of the Interface's Link.
    """
    node.focus()
    page.keyboard.press("Enter")
    dialog = page.locator("#host-dialog")
    dialog.get_by_role("tab", name="Interfaces").click()
    return dialog.get_by_role("group", name=re.compile(rf"^\S+-eth\d+ to {re.escape(peer)}$"))


INTERFACE_LABELS = {"ipv4": "IPv4 address and prefix", "ipv6": "IPv6 address and prefix",
                    "mac": "MAC address, optional"}


def set_interface(page: Page, node: Locator, peer: str, **fields: str) -> None:
    """Fill an Interface's address fields on its Node's dialog, then close the dialog.

    Args:
        page: The page with the canvas focused and no dialog open.
        node: The Node's canvas item.
        peer: Name of the Node at the other end of the Interface's Link.
        **fields: Any of ``ipv4``, ``ipv6`` or ``mac`` with the value to enter.
    """
    interface = open_interface(page, node, peer)
    for field, value in fields.items():
        interface.get_by_label(INTERFACE_LABELS[field]).fill(value)
    page.keyboard.press("Escape")


def add_primary_zone(
    page: Page,
    apex: str,
    default_ttl: int = 300,
    dnssec: bool = False,
    records: tuple[dict, ...] | list[dict] = (),
) -> None:
    """Author a Primary Zone in the open DNS Service using keyboard controls.

    Args:
        page: Page with a Node dialog open on Services and DNS enabled.
        apex: Zone apex ending in a dot.
        default_ttl: Default TTL in seconds.
        dnssec: Whether to enable signing.
        records: Records with owner name, type, rdata and ttl.
    """
    number = page.locator("#dns-zones-rows > .row-list > .row-card").count() + 1
    page.get_by_role("button", name="Add Zone", exact=True).focus()
    page.keyboard.press("Enter")
    page.keyboard.type(apex)
    page.keyboard.press("Tab")
    page.keyboard.press("Control+a")
    page.keyboard.type(str(default_ttl))
    if dnssec:
        page.get_by_role("checkbox", name=f"Zone {number} DNSSEC", exact=True).focus()
        page.keyboard.press("Space")
    types = ("A", "AAAA", "CNAME", "PTR", "TXT", "MX", "NS", "SOA")
    for index, record in enumerate(records, 1):
        page.get_by_role(
            "button", name=f"Add Record to Zone {number}", exact=True
        ).focus()
        page.keyboard.press("Enter")
        page.keyboard.type(record["name"])
        page.keyboard.press("Tab")
        page.keyboard.press("Home")
        for _ in range(types.index(record["type"])):
            page.keyboard.press("ArrowDown")
        page.keyboard.press("Tab")
        page.keyboard.type(record["rdata"])
        if record["ttl"] != default_ttl:
            page.get_by_role(
                "button", name=f"More settings for Zone {number} Record {index}"
            ).focus()
            page.keyboard.press("Enter")
            page.get_by_label(f"Zone {number} Record {index} TTL (seconds)").focus()
            page.keyboard.press("Control+a")
            page.keyboard.type(str(record["ttl"]))


# A Router and a Host on one Link.
ROUTED: list = [("r1", 300, 100, "router", {}), ("h1", 100, 100, "host", {})]
ROUTED_LINKS = [("h1", "r1")]

# A Resolver that answers everyone.
RESOLVER = {"root_hints": [], "forwarders": [], "max_ttl": 300, "max_negative_ttl": 60,
            "access_control": [{"subnet": "0.0.0.0/0", "action": "allow"},
                               {"subnet": "::/0", "action": "allow"}]}

NODE_FIELDS: dict = {
    "host": {"ipv4_gateway": "", "ipv6_gateway": "", "startup": []},
    "container": {"image": "alpine:3.20", "ipv4_gateway": "", "ipv6_gateway": "", "startup": []},
    "router": {"protocols": [], "frr_config": "", "fast_timers": True,
               "subinterfaces": [], "startup": []},
    "switch": {},
    "nat": {"ip": "100.64.0.1/24", "subnet": "100.64.0.0/23"},
    "transit": {"ip": "100.64.0.1/24", "subnet": "100.64.0.0/23", "downstream_ids": ["@r1"]},
    "speaker": {"local_as": 65001, "neighbors": [{"peer": "10.0.0.2", "local_address": "10.0.0.1",
                                                  "local_as": 65001, "peer_as": 65000, "hold_time": None, "routes": []}],
                "routes": [], "startup": []},
}


class SeededDraftMixin:
    """Open the authoring page on a draft saved over HTTP with chosen Nodes and Links."""

    base_url: str

    def _seed(self, page: Page, nodes: list, links: tuple | list = (),
              address_family: str = "dual", persistent_config: bool = False) -> None:
        """Save one draft with these Nodes and Links over HTTP, then open it.

        Each Node is ``(name, x, y)`` or ``(name, x, y, type, fields)``; each
        Link is ``(first, second)`` or ``(first, second, first_end, second_end)``
        where an end holds overrides such as ``port`` or ``ipv4``.
        """
        page.set_viewport_size({"width": 1280, "height": 720})
        page.goto(self.base_url + "/authoring")
        page.wait_for_function(
            "() => document.querySelector('#authoring-status')?.textContent !== 'Loading drafts…'"
        )
        specs = []
        for node in nodes:
            name, x, y, kind, fields = (*node, "host", {}) if len(node) == 3 else node
            specs.append({"name": name, "x": x, "y": y, "type": kind,
                          "fields": {**NODE_FIELDS.get(kind, {}), **fields}})
        link_specs = [{"ends": [link[0], link[1]],
                       "overrides": [link[2], link[3]] if len(link) > 2 else [{}, {}]} for link in links]
        page.evaluate("""async ({specs, links, address_family, persistent_config}) => {
            const draft = await (await fetch('/api/authoring/drafts', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: '{}'})).json();
            draft.catalog.area = "Static";
            draft.address_family = address_family;
            draft.persistent_config = persistent_config;
            const ids = Object.fromEntries(specs.map(spec => [spec.name, crypto.randomUUID()]));
            // Fields may name other Nodes as "@name"; they become that Node's id.
            const resolve = value => JSON.parse(JSON.stringify(value), (key, item) =>
                typeof item === 'string' && item.startsWith('@') ? ids[item.slice(1)] : item);
            draft.topology.nodes = specs.map(spec => ({id: ids[spec.name], name: spec.name,
                type: spec.type, ...resolve(spec.fields)}));
            draft.layout.nodes = Object.fromEntries(specs.map(spec => [ids[spec.name], {x: spec.x, y: spec.y}]));
            const ports = {};
            const firstPort = name => specs.find(spec => spec.name === name).type === 'switch' ? 1 : 0;
            const end = (name, override) => ({node_id: ids[name],
                port: (ports[name] = (ports[name] ?? firstPort(name) - 1) + 1),
                interface_id: 'if-' + crypto.randomUUID().replaceAll('-', '').slice(0, 12),
                ipv4: '', ipv6: '', ...override});
            draft.topology.links = links.map(link => ({id: crypto.randomUUID(),
                ends: [end(link.ends[0], link.overrides[0]), end(link.ends[1], link.overrides[1])]}));
            const response = await fetch(`/api/authoring/drafts/${draft.id}`, {method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({revision: draft.revision, document: draft})});
            if (!response.ok) throw new Error(await response.text());
        }""", {"specs": specs, "links": link_specs, "address_family": address_family,
              "persistent_config": persistent_config})
        page.reload()
        page.wait_for_selector("#draft-form:not([hidden])")
        open_details(page)

    @staticmethod
    def _node(page: Page, name: str) -> Locator:
        """The canvas Node with this name."""
        return page.locator("#draft-canvas .node").filter(
            has=page.locator(".node-label", has_text=re.compile(f"^{re.escape(name)}$")))

    def _saved_draft(self, page: Page) -> dict:
        """The current draft read back over HTTP."""
        expect(page.locator("#status-draft-state")).to_have_text("Saved")
        return page.evaluate("""async () => {
            const drafts = await (await fetch('/api/authoring/drafts')).json();
            const draft = drafts.drafts[0];
            return (await fetch(`/api/authoring/drafts/${draft.id}`)).json();
        }""")

    def open_services(self, page: Page, name: str = "r1") -> None:
        """Open a Node's dialog on its Services tab, by keyboard."""
        self._node(page, name).focus()
        page.keyboard.press("Enter")
        page.locator("#node-tab-services").click()

    def saved_node(self, page: Page, name: str = "r1") -> dict:
        """One Node of the saved draft, read back over HTTP."""
        saved = self._saved_draft(page)
        return next(node for node in saved["topology"]["nodes"] if node["name"] == name)


class AuthoringPageCase(SeededDraftMixin, LiveServerCase):
    """The authoring page in single-Lab mode, with Previews as fake Lab processes.

    Drafts live in a temporary directory. Subclasses change the signed-up
    accounts through ``users``.

    Attributes:
        users: The ``MNIPLAB_USERS`` value in force for each test.
        tmp: The temporary authored-labs directory.
        launcher: The fake launcher that starts each Preview.
    """

    users = "instructor1:lab12345:instructor"
    tmp: tempfile.TemporaryDirectory
    launcher: FakeLauncher

    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.launcher = FakeLauncher()
        for managed in (patch.dict(os.environ, {"MNIPLAB_AUTHORED_LABS_DIR": self.tmp.name}),
                        patch.object(settings, "lab_mode", "single"),
                        patch.object(auth.settings, "users", self.users),
                        patch.object(registry.lab_service, "launcher", self.launcher)):
            managed.start()
            self.addCleanup(managed.stop)
        registry.lab_service.forget_all()
        self.addCleanup(registry.lab_service.forget_all)


def reach(page: Page, target: Locator) -> None:
    """Reach a control using Tab and the documented composite navigation keys.

    Args:
        page: Browser page receiving the keystrokes.
        target: Visible control to reach.
    """
    expect(target).to_be_attached()
    if target.evaluate("el => el instanceof SVGLineElement"):
        # Axis-aligned SVG strokes have no bounding-box area. Check their
        # visible canvas and paint before exercising the actual keyboard path.
        expect(target.locator("xpath=ancestor::*[local-name()='svg'][1]")).to_be_visible()
        expect(target).to_have_css("visibility", "visible")
        painted = target.evaluate("""el => {
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.stroke !== 'none' && parseFloat(style.strokeWidth) > 0
                && (rect.width > 0 || rect.height > 0);
        }""")
        assert painted, "SVG Link must have a visible, nonempty stroke"
    else:
        expect(target).to_be_visible()
    role = target.get_attribute("role")
    if role == "tab" and target.get_attribute("tabindex") == "-1":
        tablist = target.locator("xpath=ancestor::*[@role='tablist'][1]")
        reach(page, tablist.get_by_role("tab", selected=True))
        for _ in range(8):
            if target.evaluate("el => el === document.activeElement"):
                return
            page.keyboard.press("ArrowRight")
        raise AssertionError("tab is not reachable by arrows")
    if target.get_attribute("tabindex") == "-1":
        toolbar = target.locator("xpath=ancestor::*[@role='toolbar'][1]")
        if toolbar.count():
            reach(page, toolbar.locator('[tabindex="0"]'))
            for _ in range(10):
                if target.evaluate("el => el === document.activeElement"):
                    return
                page.keyboard.press("ArrowDown")
            raise AssertionError("palette item is not reachable by arrows")
    if (
        target.get_attribute("data-node-id")
        and target.get_attribute("tabindex") == "-1"
    ):
        canvas = target.locator("xpath=ancestor::*[@id='draft-canvas']")
        if not canvas.evaluate("el => el.contains(document.activeElement)"):
            reach(page, canvas.locator('[tabindex="0"]'))
        page.keyboard.press("Home")
        for _ in range(100):
            if target.evaluate("el => el === document.activeElement"):
                return
            page.keyboard.press("PageDown")
        raise AssertionError("Node is not reachable in reading order")
    if target.evaluate("el => el === document.activeElement"):
        return
    for _ in range(160):
        page.keyboard.press("Tab")
        if target.evaluate("el => el === document.activeElement"):
            return
    raise AssertionError(f"not reachable by Tab: {target}")


def activate(page: Page, target: Locator) -> None:
    """Activate a control by keyboard.

    Args:
        page: Browser page receiving the keystrokes.
        target: Control to reach and activate.
    """
    reach(page, target)
    page.keyboard.press("Enter")


def enter_text(page: Page, target: Locator, text: str) -> None:
    """Replace an input's value as a keyboard user would.

    Args:
        page: Browser page receiving the keystrokes.
        target: Input to edit.
        text: Replacement value.
    """
    reach(page, target)
    page.keyboard.press("Control+A")
    page.keyboard.insert_text(text)


class ClassroomFixture(LiveServerMixin, unittest.TestCase):
    """Temporary storage and separate Instructor/Learner browser Sessions.

    Unmarked, so a journey that starts real Labs can build on it and stay
    out of the browser tier; ``ClassroomBrowserFixture`` is the marked one.
    """

    real_labs = False

    def setUp(self) -> None:
        """Isolate classroom storage, launch boundaries and owned-Lab cleanup."""
        super().setUp()
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        auth.active_sessions.clear()
        patches: list[Any] = [
            patch(
                "mniplab.webserver.api.authored_labs_root",
                return_value=self.storage.name,
            ),
            patch(
                "mniplab.webserver.repositories.authored_labs_root",
                return_value=self.storage.name,
            ),
            patch.object(settings, "lab_mode", "multi"),
            patch.object(
                auth.settings,
                "users",
                "instructor1:lab12345:instructor,learner1:lab12345:learner",
            ),
            patch.object(
                registry,
                "groups",
                Groups(os.path.join(self.storage.name, "assignments.json")),
            ),
            patch.object(
                registry,
                "config_copies",
                ConfigCopies(os.path.join(self.storage.name, "copies")),
            ),
            patch.object(
                registry.lab_service,
                "launcher",
                LabChild.launch if self.real_labs else FakeLauncher(),
            ),
            patch.object(registry.lab_service, "lab_lock", asyncio.Lock()),
            patch.object(registry.terminal_service, "terminals_lock", asyncio.Lock()),
            patch.object(registry.capture_service, "_lock", asyncio.Lock()),
        ]
        self.conditions: list[dict[str, Any]] = []
        if not self.real_labs:
            patches.extend(
                [
                    patch(
                        "mniplab.labhandle.LocalLabHandle._op_get_running_config",
                        lambda _handle, node: f"hostname {node}\nend\n",
                    ),
                    patch(
                        "mniplab.labhandle.LocalLabHandle._op_get_link_conditions",
                        lambda _handle: copy.deepcopy(self.conditions),
                    ),
                ]
            )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        registry.lab_service.forget_all()
        self.addCleanup(registry.lab_service.forget_all)
        # Stop on the live server's own loop, before storage/patch cleanup.
        self.addCleanup(self.stop_labs)

    def stop_labs(self) -> None:
        """Stop only this fixture's Labs, including when an assertion fails."""
        assert self.browser is not None
        context = self.browser.new_context()
        try:
            context.add_cookies(
                [
                    {
                        "name": "session",
                        "value": auth.create_session("instructor1", "instructor"),
                        "url": self.base_url,
                    }
                ]
            )
            for lab in registry.lab_service.labs():
                stopped = context.request.post(
                    f"{self.base_url}/api/labs/{lab.key}/stop"
                )
                self.assertEqual(stopped.status, 200, stopped.text())
        finally:
            context.close()

    def session_page(self, username: str, role: str) -> Page:
        """Open an authenticated page without invalidating the other Session.

        Args:
            username: User represented by this Session.
            role: Instructor or Learner role.

        Returns:
            The authenticated Labs page.
        """
        assert self.browser is not None
        context = self.browser.new_context(viewport={"width": 1280, "height": 720})
        self.addCleanup(context.close)
        context.add_cookies(
            [
                {
                    "name": "session",
                    "value": auth.create_session(username, role),
                    "url": self.base_url,
                }
            ]
        )
        page = context.new_page()
        page.goto(self.base_url + "/")
        expect(page.locator("#app")).to_be_visible()
        return page

    def saved(self, page: Page) -> dict[str, Any]:
        """Read the open draft through the public HTTP application.

        Args:
            page: Authoring page with a saved draft.

        Returns:
            The persisted draft document.
        """
        expect(page.get_by_role("region", name="Status")).to_contain_text(
            "Draft: Saved"
        )
        draft_id = page.locator("#draft-id").input_value()
        response = page.request.get(f"{self.base_url}/api/authoring/drafts/{draft_id}")
        self.assertEqual(response.status, 200, response.text())
        return response.json()

    def preview(self, page: Page) -> None:
        """Start Preview and wait for the Lab's visible running state.

        Args:
            page: Authoring page with a buildable draft.
        """
        activate(page, page.get_by_role("button", name="Preview", exact=True))
        page.wait_for_selector("#status-preview-state:text-is('running')")
        expect(page.get_by_role("button", name="Stop Preview")).to_be_focused()

    def publish(self, page: Page, update: bool = False) -> None:
        """Publish through the readiness dialog by keyboard.

        Args:
            page: Authoring page with current Preview evidence.
            update: Update the existing Catalog entry instead of publishing new.
        """
        if not page.locator("#catalog-area").input_value():
            open_details(page)
            activate(page, page.get_by_role("tab", name="Lesson", exact=True))
            page.locator("#catalog-area").select_option("Static")
            self.saved(page)
        activate(page, page.get_by_role("button", name="Publish…"))
        dialog = page.get_by_role("dialog", name="Publish", exact=True)
        activate(
            page,
            dialog.get_by_role(
                "button", name="Update existing" if update else "Publish", exact=True
            ),
        )
        expect(dialog).to_be_hidden()
        expect(page.get_by_role("region", name="Status")).to_contain_text(
            "Updated" if update else "Published"
        )

    def change_preview(self, page: Page) -> None:
        """Supply two supported runtime changes at the fake Lab boundary.

        Args:
            page: Authoring page; the fake handle supplies the runtime state.
        """
        del page
        self.conditions[:] = [
            {
                "node1": "h1",
                "node2": "r1",
                "status": "up",
                "delay": "5ms",
                "jitter": None,
                "loss": None,
                "bandwidth": None,
                "scheduler": "hfsc",
            },
            {
                "node1": "h2",
                "node2": "r2",
                "status": "up",
                "delay": "7ms",
                "jitter": None,
                "loss": None,
                "bandwidth": None,
                "scheduler": "hfsc",
            },
        ]

    def use_lesson(self, page: Page) -> None:
        """Read the published Guide through the Learner's browser surface.

        Args:
            page: Learner page running the published Lab.
        """
        expect(page.get_by_role("region", name="Guide", exact=True)).to_be_visible()
        expect(page.get_by_role("article", name="Lab guide content")).to_contain_text(
            "First classroom sitting"
        )


class ClassroomBrowserFixture(ClassroomFixture, LiveServerCase):
    """The classroom fixture as a browser-tier test, with fake Lab processes."""


SIZES = ((1280, 720), (1024, 720), (375, 800), (320, 800))

# Observe actual rendered colours, including transparent ancestor backgrounds.
# Inactive controls and offscreen/assistive-only text are outside this sample.
TEXT_CONTRAST = """() => {
    const rgb = value => {
        const channels = (value.match(/[\\d.]+/g) || []).map(Number);
        // CSS color-mix computes to color(srgb ...), whose channels are 0–1.
        return value.startsWith('color(srgb ')
            ? channels.map((channel, index) => index < 3 ? channel * 255 : channel) : channels;
    };
    const over = (fg, bg) => fg.slice(0, 3).map((c, i) => c * (fg[3] ?? 1) + bg[i] * (1 - (fg[3] ?? 1)));
    const luminance = color => color.map(c => c / 255).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4)
        .reduce((v, c, i) => v + c * [.2126, .7152, .0722][i], 0);
    const root = [...document.querySelectorAll('dialog[open], [role=dialog]')]
        .find(el => el.getClientRects().length && getComputedStyle(el).visibility === 'visible') || document.body;
    const failures = [];
    for (const el of root.querySelectorAll('*')) {
        if (!(el instanceof HTMLElement) || el.closest('.sr-only, [aria-hidden=true], [disabled]') || el.hidden) continue;
        const rect = el.getBoundingClientRect();
        if (!rect.width || !rect.height || rect.bottom < 0 || rect.top > innerHeight) continue;
        const text = [...el.childNodes].some(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
        if (!text && !(el.matches('input, textarea') && el.value)) continue;
        const style = getComputedStyle(el);
        if (style.visibility !== 'visible' || style.display === 'none') continue;
        const ancestors = [];
        for (let parent = el; parent; parent = parent.parentElement) ancestors.unshift(parent);
        if (ancestors.some(parent => Number(getComputedStyle(parent).opacity) < 1)) continue;
        let bg = [255, 255, 255];
        for (const parent of ancestors) bg = over(rgb(getComputedStyle(parent).backgroundColor), bg);
        const fg = over(rgb(style.color), bg);
        const a = luminance(fg), b = luminance(bg);
        const ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
        const large = parseFloat(style.fontSize) >= 24 || (parseFloat(style.fontSize) >= 18.6667 && Number(style.fontWeight) >= 700);
        if (ratio < (large ? 3 : 4.5)) failures.push({text: (el.value || el.textContent).trim().slice(0, 65),
            id: el.id, ratio: Number(ratio.toFixed(2)), fg: style.color, bg});
    }
    return failures;
}"""


def audit(case: unittest.TestCase, page: Page, screen: str, check_focus: bool = False) -> None:
    """Inspect reflow, rendered text and unobscured focus; take the screen's screenshots.

    Args:
        case: The test reporting each check as a subtest.
        page: Browser page showing the surface under review.
        screen: Filename component identifying the workflow surface.
        check_focus: Require the current keyboard focus to remain visible.
    """
    screenshots(page, "redesign-09", screen, ":focus" if check_focus else None)
    page.add_style_tag(
        content="*, *::before, *::after { transition: none !important; }"
    )
    for theme in ("light", "dark"):
        page.evaluate(
            "theme => document.documentElement.dataset.theme = theme", theme
        )
        for width, height in SIZES:
            with case.subTest(screen=screen, theme=theme, width=width):
                page.set_viewport_size({"width": width, "height": height})
                if check_focus:
                    page.locator(":focus").scroll_into_view_if_needed()
                case.assertLessEqual(
                    page.evaluate(
                        "document.documentElement.scrollWidth - innerWidth"
                    ),
                    1,
                )
                if screen in (
                    "instructor-groups",
                    "learner-assignment",
                    "learner-guide",
                ):
                    for control in page.locator(
                        "header button:visible, header a:visible, #group-badge:visible"
                    ).all():
                        box = control.bounding_box()
                        assert box is not None
                        case.assertGreaterEqual(box["x"], -1)
                        case.assertLessEqual(
                            box["x"] + box["width"],
                            width + 1,
                            "header controls must reflow",
                        )
                for dialog in page.get_by_role("dialog").all():
                    if not dialog.is_visible():
                        continue
                    rect = dialog.bounding_box()
                    assert rect is not None
                    case.assertGreaterEqual(rect["x"], -1)
                    case.assertLessEqual(rect["x"] + rect["width"], width + 1)
                case.assertEqual(
                    page.evaluate(TEXT_CONTRAST), [], "rendered text contrast"
                )
                small_buttons = page.evaluate("""() => {
                    const root = [...document.querySelectorAll('dialog[open], [role=dialog]')]
                        .find(el => el.getClientRects().length && getComputedStyle(el).visibility === 'visible') || document.body;
                    const targets = [...root.querySelectorAll('button, a[href], input, select, summary')]
                        .filter(el => !el.disabled && !el.closest('[hidden], [aria-hidden=true]'))
                        .map(el => ({el, r: el.getBoundingClientRect()}))
                        .filter(({r}) => r.width && r.height && r.bottom > 0 && r.top < innerHeight);
                    return targets.filter(({el, r}) => el.tagName === 'BUTTON'
                        && !el.matches('.authoring-link-button') && (r.width < 24 || r.height < 24))
                        .filter(({el, r}) => {
                            const x = r.x + r.width / 2, y = r.y + r.height / 2;
                            return targets.some(({el: other, r: box}) => {
                                if (other === el || other.contains(el) || el.contains(other)) return false;
                                if (box.width < 24 || box.height < 24)
                                    return Math.hypot(x - box.x - box.width / 2, y - box.y - box.height / 2) < 24;
                                const dx = x - Math.max(box.left, Math.min(x, box.right));
                                const dy = y - Math.max(box.top, Math.min(y, box.bottom));
                                return Math.hypot(dx, dy) < 12;
                            });
                        }).map(({el}) => ({name: el.getAttribute('aria-label') || el.textContent.trim(),
                            size: [el.offsetWidth, el.offsetHeight]}));
                }""")
                case.assertEqual(
                    small_buttons,
                    [],
                    "target size or 24px-circle spacing (inline links exempt)",
                )
                focus = page.evaluate("""() => {
                    const el = document.activeElement, r = el.getBoundingClientRect();
                    if (el === document.body || el instanceof SVGElement) return null;
                    let left = Math.max(0, r.left), right = Math.min(innerWidth, r.right);
                    let top = Math.max(0, r.top), bottom = Math.min(innerHeight, r.bottom);
                    for (let parent = el.parentElement; parent; parent = parent.parentElement) {
                        const style = getComputedStyle(parent), box = parent.getBoundingClientRect();
                        if (/(auto|scroll|hidden)/.test(style.overflowX)) {
                            left = Math.max(left, box.left); right = Math.min(right, box.right);
                        }
                        if (/(auto|scroll|hidden)/.test(style.overflowY)) {
                            top = Math.max(top, box.top); bottom = Math.min(bottom, box.bottom);
                        }
                        if (style.position === 'fixed') break;
                    }
                    const x = (left + right) / 2, y = (top + bottom) / 2;
                    const hit = document.elementFromPoint(x, y), s = getComputedStyle(el);
                    return {visible: bottom > top && right > left, rect: r.toJSON(), clipped: [left, right, top, bottom], active: el.outerHTML.slice(0, 160),
                        hit: hit?.outerHTML.slice(0, 180), unobscured: el === hit || el.contains(hit), outline: s.outlineStyle !== 'none'
                            ? s.outlineStyle : getComputedStyle(el.closest('.cm-editor') || el).outlineStyle};
                }""")
                if check_focus and focus:
                    case.assertTrue(focus["visible"] and focus["unobscured"], focus)
                    case.assertNotEqual(focus["outline"], "none", focus)
    page.set_viewport_size({"width": 1280, "height": 720})
    for theme in ("light", "dark"):
        page.evaluate(
            "theme => document.documentElement.dataset.theme = theme", theme
        )
        for adjustment, css in (
            ("200% text", "html { font-size: 200% !important; }"),
            (
                "text spacing",
                "* { line-height: 1.5 !important; letter-spacing: .12em !important; word-spacing: .16em !important; } p { margin-bottom: 2em !important; }",
            ),
        ):
            with case.subTest(screen=screen, theme=theme, adjustment=adjustment):
                style = page.add_style_tag(content=css)
                try:
                    if check_focus:
                        page.locator(":focus").scroll_into_view_if_needed()
                    case.assertLessEqual(
                        page.evaluate(
                            "document.documentElement.scrollWidth - innerWidth"
                        ),
                        1,
                    )
                    case.assertEqual(
                        page.evaluate("""() => {
                        const root = [...document.querySelectorAll('dialog[open], [role=dialog]')]
                            .find(el => el.getClientRects().length && getComputedStyle(el).visibility === 'visible') || document.body;
                        return [...root.querySelectorAll('button, label, h1, h2, h3')]
                            .filter(el => el.offsetWidth && !el.closest('.sr-only, [hidden], [aria-hidden=true]'))
                            .filter(el => el.scrollWidth > el.clientWidth + 1)
                            .map(el => el.textContent.trim().slice(0, 60));
                    }"""),
                        [],
                        "text must not be clipped",
                    )
                finally:
                    style.evaluate("el => el.remove()")
    page.evaluate("document.documentElement.dataset.theme = 'light'")
