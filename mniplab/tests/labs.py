"""Lab harness shared by the privileged tests, beside ``browser.py``.

The unit suite crosses it too, through :func:`unstarted_lab`: a real
``mnIPLab`` that is never started, for tests of the Lab's own methods.

A privileged test inherits :class:`LabCase`, boots a **Test Lab** — one per
family it proves, built by :func:`build_dns_test_lab` or
:func:`build_bgp_test_lab`, or described as a definition by
:func:`authored_dhcp_test_lab` or :func:`authored_nat_test_lab` — and
asserts one mechanism. The builder declares what no test varies; the test
attaches the Service it is testing before the Lab starts. A definition
starts through :meth:`LabCase.start_draft`, from the script it generates.

Every boot is the same: ``start()`` is called, and ``stop()`` is registered
as a cleanup. A ``stop()`` that raises is followed by the emergency sweep so
a half-stopped Lab never poisons the next test. A Lab Example is only ever
started by the suite's start check; :func:`built_example` builds one
without starting it, for tests about the topology an Example declares
rather than what it does once running.

DHCP Test Lab — ``r1`` serves, ``r2`` relays
============================================

===== ========================= ============= ==========================
Node  Role                      Subnet        Link (r1 / r2 side)
===== ========================= ============= ==========================
r1    DHCP Service per test     —             —
h1    v4 client (MAC ``H1_MAC``) 10.50.0.0/24 r1-eth0 10.50.0.1
h2    v4 client, second Subnet  10.51.0.0/24  r1-eth1 10.51.0.1
h3    client no Pool covers     10.60.0.0/24  r1-eth2 10.60.0.1
h6    v6 client (RA per test)   2001:db8:1::/64 r1-eth3 2001:db8:1::1
r2    Relay per test            core 10.0.0.0/30 · 2001:db8:0::/64  r1-eth4 .1 / r2-eth0 .2
hr    client behind the Relay   10.70.0.0/24 · 2001:db8:70::/64  r2-eth1 .1
===== ========================= ============= ==========================

DNS Test Lab — ``r1`` hub, every Node on its own Subnet
========================================================

======= ============================== ========== ==================
Node    Role                           v4         v6 (``ipv6=True``)
======= ============================== ========== ==================
root1   ``.``                          10.0.1.2   2001:db8:1::2
tld1    ``com.``                       10.0.2.2   2001:db8:2::2
auth1   ``example.com.`` Primary       10.0.3.2   2001:db8:3::2
res1    Resolver per test              10.0.4.2   2001:db8:4::2
client1 nameservers → res1             10.0.5.2   2001:db8:5::2
ns2     Secondary per test             10.0.6.2   2001:db8:6::2
======= ============================== ========== ==================

``r1`` holds ``.1`` of each Subnet; ``example.com.`` answers ``web`` with
``client1``'s address and ``ns`` with ``auth1``'s. IPv6 is opt-in because
AAAA glue in the Root Hints turns a Resolver's walk dual-stack.

BGP Test Lab — ``sp1`` plays the outside ASes to ``r1`` and ``r2`` of AS 65000
==============================================================================

===== ===== ============================ ============== ===============================
Node  AS    IPv4 Links                   IPv6 Link      Declared route
===== ===== ============================ ============== ===============================
sp1   65001 10.0.1.2/30 (r1),            2001:db8:1::2  ``SPEAKER_V4_ROUTE`` to r1 as
            10.0.2.2/30 (r2)                            ``65001``, to r2 as ``65001
                                                        65002``; ``SPEAKER_V6_ROUTE``
                                                        to the v6 Neighbor
r1    65000 10.0.1.1/30 (sp1),           2001:db8:1::1  ``SPEAKER_R1_NETWORK``
            10.0.3.1/30 (r2)
r2    65000 10.0.2.1/30 (sp1),           —              ``SPEAKER_R2_NETWORK``
            10.0.3.2/30 (r1)
===== ===== ============================ ============== ===============================

``sp1`` is passive; ``r1`` and ``r2`` dial their Neighbors, accept their
routes with ``no bgp ebgp-requires-policy``, and negotiate a three second
hold time so a session that loses its Link reads as down within a test's
patience. ``r1`` and ``r2`` peer over iBGP with ``next-hop-self``, so each
can use a route the other learned. With no policy, both prefer the shorter
path through ``r1``. Each of ``sp1``'s Neighbors carries one family and ships
exactly one declared route, so a test can tell what it announced from what
the Lab shipped. The Links in order: sp1–r1 IPv4 (port 0 of each), sp1–r1
IPv6 (port 1 of each), sp1–r2 (``sp1-eth2``, ``r2-eth0``) and r1–r2
(``r1-eth2``, ``r2-eth1``).

NAT Test Lab — one branch per kind of NAT Gateway
=================================================

===== ============================ ================ ====================
Node  Role                         Address          Link (Router side)
===== ============================ ================ ====================
nat1  plain NAT Gateway            100.64.0.1/24    r1-eth0 100.64.0.2
h1    inside r1                    100.64.1.2/24    r1-eth1 100.64.1.1
h2    inside r1                    100.64.2.2/24    r1-eth2 100.64.2.1
nat2  Transit NAT Gateway, r2 down 100.64.50.1/30   sp1-eth0 100.64.50.2
sp1   Speaker nat2 is behind       100.64.51.1/30   r2-eth0 100.64.51.2
h3    inside r2                    100.64.3.2/24    r2-eth1 100.64.3.1
===== ============================ ================ ====================

``r1`` translates on ``r1-eth0``, toward the hidden ``nat1``; ``r2``
translates on ``r2-eth0``, toward ``sp1``, and routes out through it.
``sp1`` holds one passive Neighbor that ``r2`` never dials: it is there to
carry the Transit NAT Gateway, not to peer.

RPKI Test Lab — ``ca`` holds the RPKI CA, ``rv`` the Validator
==============================================================

===== ===== ================================ ===========================
Node  AS    Role                             Link (Router side)
===== ===== ================================ ===========================
r1    65001 the Learner's Router             r1-eth0 10.0.12.1/30 (r2)
                                             r1-eth1 10.0.13.1/30 (sp1)
                                             r1-eth2 10.0.14.1/30 (ca)
                                             r1-eth3 10.0.15.1/30 (rv)
r2    65002 origin of ``10.1.0.0/24``        10.0.12.2/30
sp1   65003 Speaker; announces 10.1.0.0/24   10.0.13.2/30
ca    —     RPKI CA, Hosted CAs 65002/65003  10.0.14.2/30
rv    —     Validator, Routinator           10.0.15.2/30
===== ===== ================================ ===========================

``r1`` dials ``r2`` and ``sp1`` over eBGP and accepts their routes with
``no bgp ebgp-requires-policy``. ``r2`` originates ``10.1.0.0/24``;
``sp1`` announces the same prefix with its own origin, AS 65003, which is the
hijack. ``ca`` declares AS 65002's Hosted CA holding ``10.1.0.0/24`` with
one starting ROA, and AS 65003's Hosted CA holding only ``10.3.0.0/24``, so
the hijacker cannot sign for the prefix it announces. ``rv`` runs Routinator,
which fetches the Lab's TAL from ``ca`` and serves VRPs over RTR on port 3323.
``r1`` polls it every 10 seconds, so once a validation run has finished the
hijack reads ``invalid`` and r2's path ``valid``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
import shlex
from contextlib import contextmanager
from types import ModuleType
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

import pytest
from mininet.node import Node

from mniplab import mnIPLab
from mniplab.dns import AAAARecord, ARecord, DNSService, NSRecord, Zone
from mniplab.exabgp import BGPNeighbor, BGPRoute
from mniplab.rpki import HostedCA, RPKICAService, ValidatorService
from mniplab.runner import load_lab_example
from mniplab.lab_key import DEFAULT_ALLOCATOR

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")
PACKET_ANALYSIS_JS = os.path.join(
    REPO_ROOT, "mniplab", "webserver", "static", "js", "capture", "packet-analysis.js"
)

H1_MAC = "aa:bb:cc:dd:ee:01"
"""The DHCP Test Lab's ``h1`` hardware address, so a Reservation can name it."""

SPEAKER_V4_ROUTE = "203.0.113.0/24"
"""The BGP Test Lab's declared IPv4 route, shipped to the IPv4 Neighbors only."""

SPEAKER_V6_ROUTE = "2001:db8:100::/48"
"""The BGP Test Lab's declared IPv6 route, shipped to the IPv6 Neighbor only."""

SPEAKER_R1_NETWORK = "198.51.100.0/24"
"""The prefix ``r1`` originates towards ``sp1``, so a received route exists."""

SPEAKER_R2_NETWORK = "192.0.2.0/24"
"""The prefix ``r2`` originates, which ``r1`` passes on to ``sp1`` over iBGP."""

RPKI_LEGIT_PREFIX = "10.1.0.0/24"
"""The prefix AS 65002 originates and AS 65003 announces as its own, the hijack."""

RPKI_SPEAKER_PREFIX = "10.3.0.0/24"
"""The prefix AS 65003 holds, so its Hosted CA can sign for it."""

DNS_EXAMPLE_APEX = "example.com."


def catalogue() -> list:
    """Return every entry of ``examples/labs.json``."""
    with open(os.path.join(EXAMPLES_DIR, "labs.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def load_example(lab_id: str) -> ModuleType:
    """Import a catalogued Lab Example by its id.

    Args:
        lab_id: The ``id`` of an entry in ``examples/labs.json``.

    Returns:
        The imported module, exposing ``build_network()``.

    Raises:
        KeyError: If no catalogue entry has that id.
    """
    for entry in catalogue():
        if entry.get("id") == lab_id:
            return load_lab_example(os.path.join(EXAMPLES_DIR, entry.get("file", f"{lab_id}.py")))
    raise KeyError(f"{lab_id} is not catalogued in examples/labs.json")


def load_builder(path: str) -> ModuleType:
    """Import a generated builder module from its path.

    Args:
        path: The script :meth:`AuthoringRepository.write_builder` wrote.

    Returns:
        The imported module, exposing ``build_network()``.
    """
    spec = importlib.util.spec_from_file_location("authored_lab", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def full_sweep() -> None:
    """Perform a host-wide full sweep: removes the runtime root and stops every Lab on the host."""
    from mniplab.cleanup import full_cleanup

    full_cleanup()


def _stop_or_sweep(net: Any) -> None:
    """Stop a Lab cleanly; if stopping fails, perform an emergency sweep.

    A full sweep stops every Lab on the host before re-raising if the Lab has no key.
    """
    try:
        net.stop()
    except BaseException:
        from mniplab.cleanup import cleanup_mininet_leftovers, emergency_cleanup

        emergency_cleanup(net=net)
        if not getattr(net, 'lab_key', None):
            cleanup_mininet_leftovers(verbose=False)
        raise


def discard_unstarted_lab(net: mnIPLab) -> None:
    """Take a never-started Lab apart, as ``stop()`` would, without stopping it.

    A Node the test added did get its shell, so every real Node's shell is
    terminated; the Lab directory is removed, the key released and the Lab
    forgotten, so nothing of it lingers for the next test to find.

    Args:
        net: The unstarted Lab.
    """
    for node in net.hosts:
        if isinstance(node, Node):
            node.terminate()
    net._cleanup_ephemeral_config_dirs()
    DEFAULT_ALLOCATOR.release(net.lab_key)
    if net in mnIPLab._active_instances:
        mnIPLab._active_instances.remove(net)


@contextmanager
def unstarted_lab(lab_key: Optional[str] = None) -> Iterator[mnIPLab]:
    """Yield a real, empty Lab that is never started.

    For unit tests of ``mnIPLab``'s own methods — ``addLink``,
    ``addContainer``, the IPv6 reconciliation — which a fake Lab cannot
    stand in for. Constructing a Lab needs root and FRR but starts nothing,
    and ``stop()`` is never run: :func:`discard_unstarted_lab` takes it
    apart on exit.

    Args:
        lab_key: The Lab key; allocated when omitted.

    Yields:
        The unstarted Lab.
    """
    net = mnIPLab(topo=None, controller=None, lab_key=lab_key)
    try:
        yield net
    finally:
        discard_unstarted_lab(net)


def build_example(case: unittest.TestCase, lab_id: str) -> Any:
    """Build a catalogued Lab Example for a test case; stop it when the case ends.

    The context-manager form is :func:`built_example`; this one suits a
    ``setUp`` or a body that reads more clearly without the indent.

    Args:
        case: The test case, which stops the Lab in its cleanup.
        lab_id: The catalogue id of the Lab Example.

    Returns:
        The built, unstarted Lab.
    """
    net = load_example(lab_id).build_network()
    case.addCleanup(_stop_or_sweep, net)
    return net


@contextmanager
def built_example(lab_id: str) -> Iterator[Any]:
    """Build a catalogued Lab Example without starting it; stop it on exit.

    Building alone already needs root: every Node gets its shell and every
    Link its veth pair as they are declared.

    Args:
        lab_id: The catalogue id of the Lab Example.

    Yields:
        The built, unstarted Lab.
    """
    net = load_example(lab_id).build_network()
    try:
        yield net
    finally:
        _stop_or_sweep(net)


class LabCase(unittest.TestCase):
    """Base class for tests that boot a Test Lab and assert one mechanism.

    Subclasses inherit the ``integration`` and ``requires_root`` marks.
    """

    pytestmark = [pytest.mark.integration, pytest.mark.requires_root]

    def lab(self) -> mnIPLab:
        """Return a fresh, unstarted Lab."""
        return mnIPLab(topo=None, controller=None)

    def start(self, net: Any) -> Any:
        """Start a Lab and register its stop, even if starting fails.

        Args:
            net: The Lab to start.

        Returns:
            The same Lab, started.
        """
        self.addCleanup(_stop_or_sweep, net)
        net.start()
        return net

    def start_draft(self, topology: Dict[str, Any], address_family: str = "dual",
                    **settings: Any) -> Any:
        """Save ``topology`` as an authored draft, generate its script and start it.

        The draft lives in a temporary directory whose removal is registered
        before the Lab's stop, so it outlasts the running Lab.

        Args:
            topology: The draft's ``topology``.
            address_family: The draft's Address Family.
            **settings: Other Lab-wide draft fields, such as
                ``persistent_config``. A dict merges into the draft's own,
                so ``catalog={"name": ...}`` names the Lab and keeps the
                rest of its catalog entry.

        Returns:
            The started Lab.
        """
        from mniplab.webserver.authoring import AuthoringRepository

        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        repository = AuthoringRepository(root)
        draft = repository.create()
        draft["address_family"] = address_family
        for field, value in settings.items():
            if isinstance(draft.get(field), dict):
                draft[field].update(value)
            else:
                draft[field] = value
        draft["topology"] = topology
        saved = repository.save(draft["id"], draft["revision"], draft)
        path = os.path.join(root, "published", "lab.py")
        repository.write_builder(saved, path)
        return self.start(load_builder(path).build_network())


def authored_dhcp_test_lab() -> Dict[str, Any]:
    """The DHCP Test Lab as an authored definition's ``topology``; no Service.

    The Nodes, ports and addresses in the table above; a test attaches the
    Service or Relay it is testing to a Node's definition before it starts
    the Lab.

    Returns:
        A fresh ``topology`` with Nodes keyed in order r1, r2, h1, h2, h3, h6, hr.
    """
    def router(name: str) -> Dict[str, Any]:
        return {"id": name, "name": name, "type": "router", "protocols": [],
                "frr_config": "", "fast_timers": False,
                "startup": []}

    def host(name: str) -> Dict[str, Any]:
        return {"id": name, "name": name, "type": "host", "ipv4_gateway": "",
                "ipv6_gateway": "", "startup": []}

    counter = iter(range(1, 100))

    def end(node: str, port: int, ipv4: str = "", ipv6: str = "", **extra: Any) -> Dict[str, Any]:
        return {"node_id": node, "interface_id": f"if-{next(counter):012x}", "port": port,
                "ipv4": ipv4, "ipv6": ipv6, **extra}

    links = [
        (end("r1", 0, "10.50.0.1/24"), end("h1", 0, dhcp4=True, mac=H1_MAC)),
        (end("r1", 1, "10.51.0.1/24"), end("h2", 0, dhcp4=True)),
        (end("r1", 2, "10.60.0.1/24"), end("h3", 0, dhcp4=True)),
        (end("r1", 3, ipv6="2001:db8:1::1/64"), end("h6", 0, dhcp6=True)),
        (end("r1", 4, "10.0.0.1/30", "2001:db8:0::1/64"),
         end("r2", 0, "10.0.0.2/30", "2001:db8:0::2/64")),
        (end("r2", 1, "10.70.0.1/24", "2001:db8:70::1/64"), end("hr", 0, dhcp4=True, dhcp6=True)),
    ]
    return {
        "nodes": [router("r1"), router("r2"),
                  *(host(name) for name in ("h1", "h2", "h3", "h6", "hr"))],
        "links": [{"id": f"link-{index}", "ends": list(pair)}
                  for index, pair in enumerate(links)],
    }


def authored_nat_test_lab() -> Dict[str, Any]:
    """The NAT Test Lab as an authored definition's ``topology``.

    Both kinds of NAT Gateway, each on its own branch, with the Nodes and
    addresses in the table above.

    Returns:
        A fresh ``topology`` with Nodes keyed in order nat1, nat2, sp1, r1,
        r2, h1, h2, h3.
    """
    def router(name: str) -> Dict[str, Any]:
        return {"id": name, "name": name, "type": "router", "protocols": [],
                "frr_config": "", "fast_timers": False, "startup": []}

    def host(name: str, gateway: str) -> Dict[str, Any]:
        return {"id": name, "name": name, "type": "host", "ipv4_gateway": gateway,
                "ipv6_gateway": "", "startup": []}

    counter = iter(range(1, 100))

    def end(node: str, port: int, ipv4: str = "") -> Dict[str, Any]:
        return {"node_id": node, "interface_id": f"if-{next(counter):012x}", "port": port,
                "ipv4": ipv4, "ipv6": ""}

    links = [
        (end("nat1", 0), end("r1", 0, "100.64.0.2/24")),
        (end("r1", 1, "100.64.1.1/24"), end("h1", 0, "100.64.1.2/24")),
        (end("r1", 2, "100.64.2.1/24"), end("h2", 0, "100.64.2.2/24")),
        (end("nat2", 0), end("sp1", 0, "100.64.50.2/30")),
        (end("sp1", 1, "100.64.51.1/30"), end("r2", 0, "100.64.51.2/30")),
        (end("r2", 1, "100.64.3.1/24"), end("h3", 0, "100.64.3.2/24")),
    ]
    return {
        "nodes": [
            {"id": "nat1", "name": "nat1", "type": "nat",
             "ip": "100.64.0.1/24", "subnet": "100.64.0.0/24"},
            {"id": "nat2", "name": "nat2", "type": "transit",
             "ip": "100.64.50.1/30", "subnet": "100.64.50.0/30", "downstream_ids": ["r2"]},
            {"id": "sp1", "name": "sp1", "type": "speaker", "local_as": 65001, "routes": [],
             "neighbors": [{"peer": "100.64.51.2", "local_address": "100.64.51.1",
                            "local_as": 65001, "peer_as": 65000, "hold_time": None,
                            "routes": []}],
             "startup": []},
            router("r1"), router("r2"),
            host("h1", "100.64.1.1"), host("h2", "100.64.2.1"), host("h3", "100.64.3.1"),
        ],
        "links": [{"id": f"link-{index}", "ends": list(pair)}
                  for index, pair in enumerate(links)],
    }


def authored_router_nat_test_lab() -> Dict[str, Any]:
    """The Router NAT Test Lab as an authored definition's ``topology``.

    r1 is a home Router: h1, h2 and srv each on a Link of their own, and an
    outside Link to isp, which carries server on 198.51.100.0/24. r1 masquerades
    out of its outside Interface, forwards 203.0.113.2:8080 to srv:80 with
    hairpin and 203.0.113.2:9090 to srv:80 without it, and maps h2 to
    203.0.113.5 both ways. isp routes the mapped address back through r1 and
    has no route to the private ranges.

    Returns:
        A fresh ``topology`` with Nodes keyed in order r1, isp, h1, h2, srv, server.
    """
    def router(name: str, startup: List[Dict[str, Any]], nat: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"id": name, "name": name, "type": "router", "protocols": [],
                "frr_config": "", "fast_timers": False, "startup": startup, "nat": nat}

    def host(name: str, gateway: str) -> Dict[str, Any]:
        return {"id": name, "name": name, "type": "host", "ipv4_gateway": gateway,
                "ipv6_gateway": "", "startup": []}

    def route(command: str) -> Dict[str, Any]:
        return {"stage": "after-start", "command": command, "timeout": 5, "may_fail": False}

    counter = iter(range(1, 100))

    def end(node: str, port: int, ipv4: str = "", **extra: Any) -> Dict[str, Any]:
        return {"node_id": node, "interface_id": f"if-{next(counter):012x}", "port": port,
                "ipv4": ipv4, "ipv6": "", **extra}

    links = [
        (end("r1", 0, "192.168.1.1/24"), end("h1", 0, "192.168.1.10/24")),
        (end("r1", 1, "192.168.2.1/24"), end("h2", 0, "192.168.2.10/24")),
        (end("r1", 2, "192.168.3.1/24"), end("srv", 0, "192.168.3.10/24")),
        (end("r1", 3, "203.0.113.2/30", outside=True), end("isp", 0, "203.0.113.1/30")),
        (end("isp", 1, "198.51.100.1/24"), end("server", 0, "198.51.100.10/24")),
    ]
    return {
        "nodes": [
            router("r1", [route("ip route add default via 203.0.113.1")], [
                {"kind": "port_forward", "protocol": "tcp", "outside_port": 8080,
                 "inside": "192.168.3.10", "inside_port": 80, "hairpin": True},
                {"kind": "port_forward", "protocol": "tcp", "outside_port": 9090,
                 "inside": "192.168.3.10", "inside_port": 80, "hairpin": False},
                {"kind": "static", "inside": "192.168.2.10", "outside": "203.0.113.5"},
            ]),
            router("isp", [route("ip route add 203.0.113.5/32 via 203.0.113.2")], []),
            host("h1", "192.168.1.1"), host("h2", "192.168.2.1"),
            host("srv", "192.168.3.1"), host("server", "198.51.100.1"),
        ],
        "links": [{"id": f"link-{index}", "ends": list(pair)}
                  for index, pair in enumerate(links)],
    }


async def collect(subscriber: Any, want: int, predicate: Any) -> List[Dict[str, Any]]:
    """Read a Capture's feed until ``want`` packets satisfy ``predicate``.

    Args:
        subscriber: The viewer :meth:`CaptureService.subscribe` returned.
        want: How many matching packets to wait for.
        predicate: Which packets count.

    Returns:
        Every packet message read, matching or not, within 20 seconds.
    """
    packets: List[Dict[str, Any]] = []
    deadline = time.monotonic() + 20.0
    while sum(1 for p in packets if predicate(p)) < want:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            batch = await asyncio.wait_for(subscriber.get(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        packets += [m for m in batch if m["type"] == "packet"]
    return packets


async def quiet_for(subscriber: Any, seconds: float) -> List[Dict[str, Any]]:
    """Every packet a Capture shows over ``seconds``."""
    packets: List[Dict[str, Any]] = []
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            batch = await asyncio.wait_for(subscriber.get(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        packets += [m for m in batch if m["type"] == "packet"]
    return packets


def preset(name: str) -> str:
    """The capture filter a protocol preset expands to in the pane.

    Read from the pane's own preset list, so a test captures with exactly
    what a Learner who picks the preset gets.

    Args:
        name: The preset's name as the pane shows it, e.g. ``DHCP``.

    Returns:
        The BPF expression the preset stands for.
    """
    with open(PACKET_ANALYSIS_JS, encoding="utf-8") as f:
        match = re.search(rf"\{{ name: '{re.escape(name)}', filter: '([^']*)' \}}", f.read())
    assert match, f"the pane has no {name} preset"
    return match.group(1)


def labels_under(element: ET.Element) -> Dict[str, List[str]]:
    """Return every field under a PDML element, by name, as the pane labels it.

    Args:
        element: A packet, or one protocol layer of it.

    Returns:
        Each field name mapped to its labels, in the order they appear.
    """
    shown: Dict[str, List[str]] = {}
    for node in element.iter():
        if "name" in node.attrib and "showname" in node.attrib:
            shown.setdefault(node.attrib["name"], []).append(node.attrib["showname"])
    return shown


def fields(packet: Dict[str, Any]) -> Dict[str, List[str]]:
    """Return every field of a streamed packet, by name, as the pane labels it.

    Args:
        packet: One ``packet`` message from a Capture's feed.

    Returns:
        Each field name mapped to its labels, in the order the packet has them.
    """
    return labels_under(ET.fromstring(packet["pdml"]))


def first(packet: Dict[str, Any], name: str) -> str:
    """Return the label of a packet's first field with this name.

    Args:
        packet: One ``packet`` message from a Capture's feed.
        name: The field name, e.g. ``dhcp.ip.your``.

    Returns:
        The label, or ``''`` if the packet has no such field.
    """
    return fields(packet).get(name, [""])[0]


class LabCapture:
    """One Capture on one Interface of a started Lab, driven as the pane drives it."""

    def __init__(self, net: Any, node: str, interface: str, capture_filter: str) -> None:
        self.net = net
        self.node = node
        self.interface = interface
        self.capture_filter = capture_filter

    def watch(self, command_node: str, command: str, want: int,
              predicate: Callable[[Dict[str, Any]], bool],
              settle: float = 0.0) -> List[Dict[str, Any]]:
        """Open the Capture, run ``command`` on ``command_node``, and read the feed.

        Args:
            command_node: The Node whose Terminal the command is typed in.
            command: What is typed there.
            want: How many packets must match ``predicate``.
            predicate: Which packets the test is about.
            settle: Seconds to keep reading once ``want`` matched, for the
                rest of an exchange that has no single last packet.

        Returns:
            Every packet the Capture showed until ``want`` of them matched,
            and through ``settle``.
        """
        from mniplab.labhandle import LocalLabHandle
        from mniplab.webserver.capture import CaptureService

        async def scenario() -> List[Dict[str, Any]]:
            service = CaptureService(grace_seconds=1)
            try:
                viewer = await service.subscribe(
                    self.net.lab_key, self.net.lab_dir,
                    LocalLabHandle(self.net).node_attachment(self.node), self.interface,
                    capture_filter=self.capture_filter,
                )
                # The pane is open before the command is typed: let tshark come up.
                await asyncio.sleep(2)
                await asyncio.get_running_loop().run_in_executor(
                    None, self.net.get(command_node).cmd, command
                )
                packets = await collect(viewer, want, predicate)
                return packets + await quiet_for(viewer, settle)
            finally:
                await service.stop_all("test over")

        return asyncio.run(scenario())


def dhcp_ra_config(intf_name: str, prefix: str) -> str:
    """The FRR lines that make DHCPv6 the only way to get an address on a link.

    Order matters: the prefix is marked ``no-autoconfig`` *before* RAs are
    un-suppressed, or FRR emits an autoconfig-enabled RA first and the host
    SLAACs an address.

    Args:
        intf_name: The Router's Interface on the link.
        prefix: The link's global prefix.

    Returns:
        FRR configuration text for ``add_frr_config``.
    """
    return (
        f"interface {intf_name}\n"
        f" ipv6 nd prefix {prefix} no-autoconfig\n"
        f" ipv6 nd managed-config-flag\n"
        f" no ipv6 nd suppress-ra"
    )


def dns_example_zone(*extra: Any, signed: bool = False) -> Zone:
    """The DNS Test Lab's ``example.com.`` Primary Zone, as ``auth1`` hosts it.

    Args:
        *extra: Records to add beyond the NS, its glue, and ``web``.
        signed: Whether the Zone is DNSSEC-signed.

    Returns:
        The Zone, bound to ``auth1``.
    """
    return Zone(
        DNS_EXAMPLE_APEX,
        [
            NSRecord("@", "ns.example.com."),
            ARecord("ns", "10.0.3.2"),
            ARecord("web", "10.0.5.2"),
            AAAARecord("web", "2001:db8:5::2"),
            *extra,
        ],
        host_node="auth1",
        dnssec=signed,
    )


def authored_dns_test_lab() -> Dict[str, Any]:
    """Describe the DNS Test Lab's fixed hierarchy as an authored topology.

    Returns:
        The same Nodes, addresses and Primary Zones as ``build_dns_test_lab``.
        Resolver and Secondary roles can be added by the test using them.
    """
    def record(name: str, kind: str, data: str) -> Dict[str, Any]:
        return {"name": name, "type": kind, "rdata": data, "ttl": 300}

    def primary(name: str, records: list[Dict[str, Any]]) -> Dict[str, Any]:
        return {"kind": "primary", "name": name, "default_ttl": 300,
                "records": records}

    router = {"id": "r1", "name": "r1", "type": "router", "protocols": [],
              "frr_config": "", "fast_timers": False,
              "startup": []}
    hosts: list[Dict[str, Any]] = [
        {"id": name, "name": name, "type": "host",
         "ipv4_gateway": f"10.0.{index}.1", "ipv6_gateway": "",
         "startup": []}
        for index, name in enumerate(("root1", "tld1", "auth1", "res1", "client1", "ns2"), start=1)
    ]
    by_name = {host["name"]: host for host in hosts}
    by_name["client1"]["nameservers"] = [{"node_id": "res1"}]
    by_name["root1"]["dns"] = {"zones": [primary(".", [
        record("@", "NS", "root1."), record("root1", "A", "10.0.1.2"),
        record("com", "NS", "ns.com."), record("ns.com.", "A", "10.0.2.2"),
    ])]}
    by_name["tld1"]["dns"] = {"zones": [primary("com.", [
        record("@", "NS", "ns.com."), record("ns", "A", "10.0.2.2"),
        record("example", "NS", "ns.example.com."),
        record("ns.example.com.", "A", "10.0.3.2"),
    ])]}
    by_name["auth1"]["dns"] = {"zones": [primary("example.com.", [
        record("@", "NS", "ns.example.com."), record("ns", "A", "10.0.3.2"),
        record("web", "A", "10.0.5.2"), record("web", "AAAA", "2001:db8:5::2"),
    ])]}
    links = []
    for index, host in enumerate(hosts, start=1):
        links.append({"id": f"link-{index}", "ends": [
            {"node_id": host["id"], "interface_id": f"if-{index * 2 - 1:012x}",
             "port": 0, "ipv4": f"10.0.{index}.2/24", "ipv6": ""},
            {"node_id": "r1", "interface_id": f"if-{index * 2:012x}",
             "port": index - 1, "ipv4": f"10.0.{index}.1/24", "ipv6": ""},
        ]})
    return {"nodes": [router, *hosts], "links": links}


def build_dns_test_lab(
    net: Any,
    signed: bool = False,
    ipv6: bool = False,
    auth_zones: Optional[Iterable[Zone]] = None,
) -> Dict[str, Any]:
    """Build the DNS Test Lab into ``net`` with its authoritative hierarchy.

    ``root1``, ``tld1`` and ``auth1`` serve ``.`` → ``com.`` →
    ``example.com.`` with the glue an Iterative Walk needs; the Resolver on
    ``res1`` and the Secondary on ``ns2`` are the test's to attach.

    Args:
        net: An unstarted Lab.
        signed: Sign every Zone of the hierarchy with DNSSEC.
        ipv6: Give every Subnet its ``2001:db8:<n>::/64`` as well. Off by
            default: Root Hints then carry AAAA glue and a Resolver walks
            the hierarchy dual-stack, which is its own mechanism and not the
            one most tests are about.
        auth_zones: Zones for ``auth1`` to host in place of the default
            ``example.com.`` (see :func:`dns_example_zone`).

    Returns:
        The Lab's Nodes, keyed by name.
    """
    r1 = net.addRouter("r1")
    nodes: Dict[str, Any] = {"r1": r1}
    for index, name in enumerate(("root1", "tld1", "auth1", "res1", "client1", "ns2"), start=1):
        node = net.addHost(
            name,
            defaultRoute=f"via 10.0.{index}.1",
            **({"defaultRoute6": f"via 2001:db8:{index}::1"} if ipv6 else {}),
            **({"nameservers": ["10.0.4.2"]} if name == "client1" else {}),
        )
        params1: Dict[str, str] = {"ip": f"10.0.{index}.2/24"}
        params2: Dict[str, str] = {"ip": f"10.0.{index}.1/24"}
        if ipv6:
            params1["ip6"] = f"2001:db8:{index}::2/64"
            params2["ip6"] = f"2001:db8:{index}::1/64"
        net.addLink(node, r1, params1=params1, params2=params2)
        nodes[name] = node

    nodes["root1"].addService(DNSService(Zone(
        ".",
        [
            NSRecord("@", "root1."),
            ARecord("root1", "10.0.1.2"),
            NSRecord("com", "ns.com."),
            ARecord("ns.com.", "10.0.2.2"),
        ],
        host_node="root1",
        dnssec=signed,
    )))
    nodes["tld1"].addService(DNSService(Zone(
        "com.",
        [
            NSRecord("@", "ns.com."),
            ARecord("ns", "10.0.2.2"),
            NSRecord("example", "ns.example.com."),
            ARecord("ns.example.com.", "10.0.3.2"),
        ],
        host_node="tld1",
        dnssec=signed,
    )))
    nodes["auth1"].addService(DNSService(
        *(auth_zones if auth_zones is not None else [dns_example_zone(signed=signed)])
    ))

    return nodes


def build_bgp_test_lab(net: Any) -> Dict[str, Any]:
    """Build the BGP Test Lab into ``net``: ``sp1`` peering with ``r1`` and ``r2``.

    Two IPv4 Neighbors and one IPv6 Neighbor, each with one declared route;
    ``r1`` and ``r2`` open every session, peer with each other over iBGP,
    and each originate one network so the Speaker has received routes to
    show.

    Args:
        net: An unstarted Lab.

    Returns:
        The Lab's Nodes, keyed by name.
    """
    sp1 = net.addExaBGP(
        "sp1",
        local_as=65001,
        neighbors=[
            BGPNeighbor(
                peer="10.0.1.1", local_address="10.0.1.2", local_as=65001, peer_as=65000,
                routes=[BGPRoute(SPEAKER_V4_ROUTE, "10.0.1.2", as_path=[65001])],
            ),
            BGPNeighbor(
                peer="2001:db8:1::1", local_address="2001:db8:1::2", local_as=65001, peer_as=65000,
                routes=[BGPRoute(SPEAKER_V6_ROUTE, "2001:db8:1::2", as_path=[65001])],
            ),
            BGPNeighbor(
                peer="10.0.2.1", local_address="10.0.2.2", local_as=65001, peer_as=65000,
                routes=[BGPRoute(SPEAKER_V4_ROUTE, "10.0.2.2", as_path=[65001, 65002])],
            ),
        ],
    )
    r1 = net.addRouter("r1", proto="BGP")
    r1.add_frr_config(
        "router bgp 65000\n"
        " bgp router-id 10.0.1.1\n"
        " no bgp ebgp-requires-policy\n"
        " no bgp network import-check\n"
        " neighbor 10.0.1.2 remote-as 65001\n"
        " neighbor 10.0.1.2 timers 1 3\n"
        " neighbor 10.0.1.2 timers connect 1\n"
        " neighbor 2001:db8:1::2 remote-as 65001\n"
        " neighbor 2001:db8:1::2 timers 1 3\n"
        " neighbor 2001:db8:1::2 timers connect 1\n"
        " neighbor 10.0.3.2 remote-as 65000\n"
        " neighbor 10.0.3.2 timers 1 3\n"
        " neighbor 10.0.3.2 timers connect 1\n"
        " address-family ipv4 unicast\n"
        f"  network {SPEAKER_R1_NETWORK}\n"
        "  neighbor 10.0.1.2 activate\n"
        "  no neighbor 2001:db8:1::2 activate\n"
        "  neighbor 10.0.3.2 next-hop-self\n"
        " exit-address-family\n"
        " address-family ipv6 unicast\n"
        "  neighbor 2001:db8:1::2 activate\n"
        " exit-address-family"
    )
    r2 = net.addRouter("r2", proto="BGP")
    r2.add_frr_config(
        "router bgp 65000\n"
        " bgp router-id 10.0.2.1\n"
        " no bgp ebgp-requires-policy\n"
        " no bgp network import-check\n"
        " neighbor 10.0.2.2 remote-as 65001\n"
        " neighbor 10.0.2.2 timers 1 3\n"
        " neighbor 10.0.2.2 timers connect 1\n"
        " neighbor 10.0.3.1 remote-as 65000\n"
        " neighbor 10.0.3.1 timers 1 3\n"
        " neighbor 10.0.3.1 timers connect 1\n"
        " address-family ipv4 unicast\n"
        f"  network {SPEAKER_R2_NETWORK}\n"
        "  neighbor 10.0.3.1 next-hop-self\n"
        " exit-address-family"
    )
    net.addLink(
        sp1, r1,
        params1={"ip": "10.0.1.2/30"}, params2={"ip": "10.0.1.1/30"},
    )
    net.addLink(
        sp1, r1,
        params1={"ip6": "2001:db8:1::2/64"}, params2={"ip6": "2001:db8:1::1/64"},
    )
    net.addLink(
        sp1, r2,
        params1={"ip": "10.0.2.2/30"}, params2={"ip": "10.0.2.1/30"},
    )
    net.addLink(
        r1, r2,
        params1={"ip": "10.0.3.1/30"}, params2={"ip": "10.0.3.2/30"},
    )
    return {"sp1": sp1, "r1": r1, "r2": r2}


def build_rpki_test_lab(net: Any, *, aspas: Iterable[str] = ()) -> Dict[str, Any]:
    """Build the RPKI Test Lab into ``net``: an RPKI CA on ``ca``, beside a small eBGP lab.

    ``r1`` dials ``r2`` (AS 65002, origin of ``10.1.0.0/24``) and ``sp1``
    (AS 65003, which announces the same prefix as a hijack). ``ca`` runs the
    RPKI CA with one Hosted CA per AS, declared as in the table above, and
    ``rv`` runs the Validator that ``r1`` takes its VRPs from.

    Args:
        net: An unstarted Lab.
        aspas: Starting ASPAs for AS 65002's Hosted CA, as ``AS65002 => ...``
            text. None by default, so the Lab declares no ASPA.

    Returns:
        The Lab's Nodes, keyed by name.
    """
    r1 = net.addRouter("r1", proto="BGP")
    r2 = net.addRouter("r2", proto="BGP")
    sp1 = net.addExaBGP(
        "sp1",
        local_as=65003,
        neighbors=[
            BGPNeighbor(
                peer="10.0.13.1", local_address="10.0.13.2", local_as=65003, peer_as=65001,
                routes=[BGPRoute(RPKI_LEGIT_PREFIX, "10.0.13.2", as_path=[65003])],
            ),
        ],
    )
    ca = net.addHost("ca", defaultRoute="via 10.0.14.1")
    ca.addService(RPKICAService([
        HostedCA(65002, [RPKI_LEGIT_PREFIX], roas=[f"{RPKI_LEGIT_PREFIX}-24 => 65002"], aspas=aspas),
        HostedCA(65003, [RPKI_SPEAKER_PREFIX]),
    ]))
    rv = net.addHost("rv", defaultRoute="via 10.0.15.1")
    rv.addService(ValidatorService())
    r1.add_frr_config(
        "rpki\n"
        " rpki polling_period 10\n"
        " rpki retry_interval 10\n"
        " rpki expire_interval 600\n"
        " rpki cache tcp 10.0.15.2 3323 preference 1\n"
        "exit\n"
        "router bgp 65001\n"
        " bgp router-id 10.0.12.1\n"
        " no bgp ebgp-requires-policy\n"
        " neighbor 10.0.12.2 remote-as 65002\n"
        " neighbor 10.0.12.2 timers 1 3\n"
        " neighbor 10.0.12.2 timers connect 1\n"
        " neighbor 10.0.13.2 remote-as 65003\n"
        " neighbor 10.0.13.2 timers 1 3\n"
        " neighbor 10.0.13.2 timers connect 1"
    )
    r2.add_frr_config(
        "router bgp 65002\n"
        " bgp router-id 10.0.12.2\n"
        " no bgp ebgp-requires-policy\n"
        " no bgp network import-check\n"
        " neighbor 10.0.12.1 remote-as 65001\n"
        " neighbor 10.0.12.1 timers 1 3\n"
        " neighbor 10.0.12.1 timers connect 1\n"
        " address-family ipv4 unicast\n"
        f"  network {RPKI_LEGIT_PREFIX}\n"
        " exit-address-family"
    )
    net.addLink(r1, r2, params1={"ip": "10.0.12.1/30"}, params2={"ip": "10.0.12.2/30"})
    net.addLink(r1, sp1, params1={"ip": "10.0.13.1/30"}, params2={"ip": "10.0.13.2/30"})
    net.addLink(r1, ca, params1={"ip": "10.0.14.1/30"}, params2={"ip": "10.0.14.2/30"})
    net.addLink(r1, rv, params1={"ip": "10.0.15.1/30"}, params2={"ip": "10.0.15.2/30"})
    return {"r1": r1, "r2": r2, "sp1": sp1, "ca": ca, "rv": rv}


def authored_rpki_test_lab() -> Dict[str, Any]:
    """Describe the RPKI Test Lab as an authored definition's ``topology``.

    The Nodes, addresses, BGP configurations, Speaker route, RPKI CA and
    Validator of :func:`build_rpki_test_lab`, with no starting ROA or ASPA
    beyond what that builder declares by default.

    Returns:
        A fresh ``topology``, Nodes keyed in order r1, r2, sp1, ca, rv.
    """
    r1_config = (
        "rpki\n"
        " rpki polling_period 10\n"
        " rpki retry_interval 10\n"
        " rpki expire_interval 600\n"
        " rpki cache tcp 10.0.15.2 3323 preference 1\n"
        "exit\n"
        "router bgp 65001\n"
        " bgp router-id 10.0.12.1\n"
        " no bgp ebgp-requires-policy\n"
        " neighbor 10.0.12.2 remote-as 65002\n"
        " neighbor 10.0.12.2 timers 1 3\n"
        " neighbor 10.0.12.2 timers connect 1\n"
        " neighbor 10.0.13.2 remote-as 65003\n"
        " neighbor 10.0.13.2 timers 1 3\n"
        " neighbor 10.0.13.2 timers connect 1"
    )
    r2_config = (
        "router bgp 65002\n"
        " bgp router-id 10.0.12.2\n"
        " no bgp ebgp-requires-policy\n"
        " no bgp network import-check\n"
        " neighbor 10.0.12.1 remote-as 65001\n"
        " neighbor 10.0.12.1 timers 1 3\n"
        " neighbor 10.0.12.1 timers connect 1\n"
        " address-family ipv4 unicast\n"
        f"  network {RPKI_LEGIT_PREFIX}\n"
        " exit-address-family"
    )
    r1 = {"id": "r1", "name": "r1", "type": "router", "protocols": ["BGP"],
          "frr_config": r1_config, "fast_timers": False, "startup": []}
    r2 = {"id": "r2", "name": "r2", "type": "router", "protocols": ["BGP"],
          "frr_config": r2_config, "fast_timers": False, "startup": []}
    sp1 = {"id": "sp1", "name": "sp1", "type": "speaker", "local_as": 65003, "startup": [],
           "routes": [],
           "neighbors": [{"peer": "10.0.13.1", "local_address": "10.0.13.2", "local_as": 65003,
                          "peer_as": 65001, "hold_time": None, "routes": [{
                              "prefix": RPKI_LEGIT_PREFIX, "next_hop": "10.0.13.2",
                              "as_path": [65003], "community": [], "large_community": [],
                              "med": None}]}]}
    ca = {"id": "ca", "name": "ca", "type": "host", "ipv4_gateway": "10.0.14.1",
          "ipv6_gateway": "", "startup": [], "rpki_ca": {"hostname": None, "hosted_cas": [
              {"asn": 65002, "prefixes": [RPKI_LEGIT_PREFIX],
               "roas": [f"{RPKI_LEGIT_PREFIX}-24 => 65002"], "aspas": []},
              {"asn": 65003, "prefixes": [RPKI_SPEAKER_PREFIX], "roas": [], "aspas": []},
          ]}}
    rv = {"id": "rv", "name": "rv", "type": "host", "ipv4_gateway": "10.0.15.1",
          "ipv6_gateway": "", "startup": [], "validator": {"refresh": 10}}
    # Links in the builder's order, so r1's Interfaces number the same way.
    links = [
        ("r1", 0, "10.0.12.1/30", "r2", 0, "10.0.12.2/30"),
        ("r1", 1, "10.0.13.1/30", "sp1", 0, "10.0.13.2/30"),
        ("r1", 2, "10.0.14.1/30", "ca", 0, "10.0.14.2/30"),
        ("r1", 3, "10.0.15.1/30", "rv", 0, "10.0.15.2/30"),
    ]
    return {"nodes": [r1, r2, sp1, ca, rv], "links": [
        {"id": f"link-{index}", "ends": [
            {"node_id": first, "interface_id": f"if-{index * 2 - 1:012x}", "port": first_port,
             "ipv4": first_ip, "ipv6": ""},
            {"node_id": second, "interface_id": f"if-{index * 2:012x}", "port": second_port,
             "ipv4": second_ip, "ipv6": ""},
        ]}
        for index, (first, first_port, first_ip, second, second_port, second_ip)
        in enumerate(links, start=1)
    ]}


_PATH_HEADER = re.compile(r"^ {2}(\S.*)$")
_AS_PATH = re.compile(r"^\d+(?: \d+)*$")
_VALIDATION_STATE = re.compile(r"rpki validation-state: (.+?)\s*$")
_PREFIX_COUNT = re.compile(r"Number of IPv[46] Prefixes: (\d+)")
_NO_CACHE_CONNECTION = "No connection to RPKI cache server."


def parse_rpki_states(output: str) -> Dict[int, str]:
    """Read each path's RPKI validation state from ``show bgp ... <prefix>`` output.

    A path's origin is the last AS of its AS path, the line FRR prints at two
    spaces of indent; its state is on a later line, at the end of which FRR
    prints ``rpki validation-state: <state>``.

    Args:
        output: The text ``vtysh`` printed for one prefix.

    Returns:
        Each origin AS mapped to the validation state of its path.
    """
    states: Dict[int, str] = {}
    origin: Optional[int] = None
    for line in output.splitlines():
        header = _PATH_HEADER.match(line)
        if header:
            text = header.group(1).strip()
            origin = int(text.split()[-1]) if _AS_PATH.match(text) else None
            continue
        state = _VALIDATION_STATE.search(line)
        if state and origin is not None:
            states[origin] = state.group(1)
    return states


def parse_rpki_prefix_count(output: str) -> int:
    """Count the VRPs in ``show rpki prefix-table`` output.

    Before a Router's RTR session to its Validator is up, FRR prints that it
    has no connection to the cache and no prefix table: the Router holds no
    VRPs, so that reads as zero.

    Args:
        output: The text ``vtysh`` printed for the prefix table.

    Returns:
        The IPv4 and IPv6 prefix counts added together, or zero when the
        Router has no connection to its cache.

    Raises:
        ValueError: If the output carries neither a prefix count nor the
            no-connection message, so a failed command is never read as an
            empty table.
    """
    counts = [int(count) for count in _PREFIX_COUNT.findall(output)]
    if counts:
        return sum(counts)
    if _NO_CACHE_CONNECTION in output:
        return 0
    raise ValueError(f"no prefix count in: {output.strip()!r}")


def rpki_states(router: Any, prefix: str) -> Dict[int, str]:
    """Return the RPKI validation state of each path a Router holds for a prefix.

    Args:
        router: The Router to ask.
        prefix: The prefix, as ``show bgp ipv4 unicast`` takes it.

    Returns:
        Each origin AS mapped to its path's validation state.
    """
    return parse_rpki_states(router.cmd("vtysh -c " + shlex.quote(f"show bgp ipv4 unicast {prefix}")))


def rpki_vrp_count(router: Any) -> int:
    """Return how many VRPs a Router holds from its Validator.

    Args:
        router: The Router to ask.

    Returns:
        The number of IPv4 and IPv6 VRPs in the Router's RPKI prefix table;
        zero while the Router has no connection to its Validator yet.
    """
    return parse_rpki_prefix_count(router.cmd("vtysh -c " + shlex.quote("show rpki prefix-table")))
