"""The Switch panel names the root Switch from this Lab's bridges only."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

import pytest

from mniplab.labhandle import LocalLabHandle
from mniplab.switch import parse_bridge_id
from mniplab.tests.test_switch import FakeSwitch

ROOT_ID = "1.000.0000000000aa"
OTHER_ID = "8.000.0000000000bb"


def status_map(bridge_id: str, root_id: str) -> list:
    return ["map", [["rstp_bridge_id", bridge_id], ["rstp_root_id", root_id]]]


def bridge_table(rows: dict) -> str:
    return json.dumps({
        "headings": ["name", "stp_enable", "rstp_enable", "status", "rstp_status"],
        "data": [[name, False, True, ["map", []], ["map", [["rstp_bridge_id", bid]]]]
                 for name, bid in rows.items()]})


def make_handle(bridges: dict, own_id: str, root_id: str):
    s0, s1 = FakeSwitch("s0"), FakeSwitch("s1")
    s0.bridge_name, s1.bridge_name = "k7-s0", "k7-s1"
    for sw in (s0, s1):
        sw.recorder.respond_to("list Port", "")
        sw.recorder.respond_to("list-ports", "")
        sw.recorder.respond_to("--columns=name,stp_enable,rstp_enable,status,rstp_status",
                               bridge_table(bridges))
        sw.recorder.respond_to("--columns=stp_enable", json.dumps({
            "headings": ["stp_enable", "rstp_enable", "status", "rstp_status"],
            "data": [[False, True, ["map", []], status_map(own_id, root_id)]]}))
    net = SimpleNamespace(switches=[s0, s1], hosts=[], lab_key="k7")
    return LocalLabHandle(net, key="k7"), s1


@pytest.mark.unit
class BridgeIdTests(unittest.TestCase):
    def test_parse_both_formats_and_garbage(self) -> None:
        self.assertEqual(parse_bridge_id("1.000.0000000000AA"), (4096, "00:00:00:00:00:aa"))
        self.assertEqual(parse_bridge_id("8000.0000000000aa"), (32768, "00:00:00:00:00:aa"))
        self.assertEqual(parse_bridge_id(None), (None, None))
        self.assertEqual(parse_bridge_id("nonsense"), (None, None))


@pytest.mark.unit
class RootNameTests(unittest.TestCase):
    def test_names_root_and_marks_non_root(self) -> None:
        handle, s1 = make_handle({"k7-s0": ROOT_ID, "k7-s1": OTHER_ID}, OTHER_ID, ROOT_ID)
        tree = handle.get_switch_spanning_tree("s1")
        self.assertEqual((tree["root_name"], tree["is_root"]), ("s0", False))
        self.assertEqual((tree["priority"], tree["root_priority"]), (32768, 4096))
        lookups = [c for c in s1.recorder.commands if "list Bridge" in c and "name" in c]
        self.assertEqual(len(lookups), 1)

    def test_root_switch_is_root(self) -> None:
        handle, _ = make_handle({"k7-s0": OTHER_ID, "k7-s1": ROOT_ID}, ROOT_ID, ROOT_ID)
        tree = handle.get_switch_spanning_tree("s1")
        self.assertEqual((tree["root_name"], tree["is_root"]), ("s1", True))

    def test_never_names_a_switch_of_another_lab(self) -> None:
        handle, _ = make_handle({"k7-s0": OTHER_ID, "k7-s1": OTHER_ID, "zz-s9": ROOT_ID},
                                OTHER_ID, ROOT_ID)
        tree = handle.get_switch_spanning_tree("s1")
        self.assertIsNone(tree["root_name"])

    def test_unreadable_bridge_list_leaves_root_unnamed(self) -> None:
        handle, s1 = make_handle({}, OTHER_ID, ROOT_ID)
        s1.recorder.respond_to("--columns=name,stp_enable", "garbage")
        self.assertIsNone(handle.get_switch_spanning_tree("s1")["root_name"])
