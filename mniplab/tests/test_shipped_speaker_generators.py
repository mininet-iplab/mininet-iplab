"""Lossless Speaker conversion at the generated and shipped Python boundaries."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pytest

from mniplab.tests.authoring_speakers import announcements
from mniplab.tests.authoring_fixtures import draft_from_shipped
from mniplab.webserver.authoring import AuthoringRepository

# Counts and SHA-256 fingerprints of every BGPRoute field in the pre-conversion
# stored rows, sorted canonically (including duplicates). No runtime data.
ORIGINAL = {
    "bgp-ispixp-lab": {
        "tr1->10.0.5.1": (
            24,
            "3905e71180113652d03cfbbf7ca95d67f499acba1b01ae9dbeaa7660d27a9f12",
        ),
        "tr1->10.0.6.1": (
            24,
            "b7545649f4e497aa57d70bac790ae54d0cd25e6e505760a53f8f0948ef144654",
        ),
        "tr2->10.0.7.1": (
            22,
            "481e769e118915114bafd8b5cbe1ba55ae3d33c479ef5b0f47440159628ad60a",
        ),
        "tr2->10.0.8.1": (
            22,
            "902ff544279f20d99960ec3f212637e1b79216190d0356fa828b8b3df8127bfa",
        ),
    },
    "bgp-isp-lab": {
        "tr1->203.0.113.1": (
            24,
            "701d5c1c80dbca5d0b439a4c176762a8667f7990097dec5939351d3350fc0e05",
        ),
        "tr2->203.0.113.5": (
            22,
            "eda43f483b759c0140e28d4f83374c6e015fee443a0584b5e80a98263bc71d2a",
        ),
    },

}


@pytest.mark.unit
class ShippedSpeakerGeneratorTests(unittest.TestCase):
    """Converted definitions equal both their stored rows and running-script intent."""

    def test_shipped_speakers_store_generators_instead_of_copied_rows(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repository = AuthoringRepository(root)
            for example in ORIGINAL:
                draft = draft_from_shipped(repository, example)
                for node in draft["topology"]["nodes"]:
                    if node["type"] != "speaker":
                        continue
                    self.assertEqual(node["routes"], [])
                    for neighbor in node["neighbors"]:
                        self.assertEqual(neighbor["routes"], [])
                        self.assertTrue(
                            node.get("route_generator")
                            or neighbor.get("route_generator")
                        )

    def test_generated_and_shipped_scripts_equal_every_original_announcement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            repository = AuthoringRepository(root)
            for example, fingerprints in ORIGINAL.items():
                with self.subTest(example):
                    draft = draft_from_shipped(repository, example)
                    path = Path(root) / "lab.py"
                    repository.write_builder(draft, str(path))
                    generated = announcements(path.read_text(), path)
                    original = (
                        Path(__file__).resolve().parents[2]
                        / "examples"
                        / f"{example}.py"
                    )
                    shipped = announcements(original.read_text(), original)
                    self.assertEqual(generated, shipped)
                    self.assertEqual(set(generated), set(fingerprints))
                    for key, rows in generated.items():
                        canonical = json.dumps(
                            sorted(
                                rows, key=lambda row: json.dumps(row, sort_keys=True)
                            ),
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                        count, digest = fingerprints[key]
                        self.assertEqual(len(rows), count)
                        self.assertEqual(hashlib.sha256(canonical).hexdigest(), digest)
