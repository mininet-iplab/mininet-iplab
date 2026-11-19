"""RPKI CA and Validator in a canvas definition: validated like the Python API, and declared by the export.

The export is checked by its syntax tree, so the test reads what the generated
script would call and not how it is laid out. The refusals are checked against
the Python API's own wording, with the control that names the bad row or field.
"""

from __future__ import annotations

import ast
import copy
import tempfile
import unittest
from typing import Any, Dict, List, Optional

import pytest

from mniplab.tests.authoring_fixtures import AuthoringAPIFixture, draft_from_shipped, host_node
from mniplab.tests.labs import authored_rpki_test_lab
from mniplab.tests.test_web_multi_lab import FakeLauncher
from mniplab.webserver.authoring import AuthoringRepository, DraftValidationError
from mniplab.webserver.authoring_python import generate_python


def hosted_ca(asn: int, prefixes: List[str], roas: Optional[List[str]] = None,
              aspas: Optional[List[str]] = None) -> Dict[str, Any]:
    """One Hosted CA row in the definition's shape."""
    return {"asn": asn, "prefixes": list(prefixes), "roas": list(roas or []),
            "aspas": list(aspas or [])}


def rpki_ca(*rows: Dict[str, Any], hostname: Optional[str] = None) -> Dict[str, Any]:
    """An RPKI CA declaration holding these Hosted CAs."""
    return {"hostname": hostname, "hosted_cas": list(rows)}


def ca_node(node_id: str = "ca", name: str = "ca", *rows: Dict[str, Any],
            hostname: Optional[str] = None) -> Dict[str, Any]:
    """A Host carrying an RPKI CA, with the Hosted CAs given (one by default)."""
    node = host_node(node_id, name, ipv4_gateway="10.0.14.1")
    node["rpki_ca"] = rpki_ca(*(rows or (hosted_ca(65002, ["10.2.0.0/24"]),)), hostname=hostname)
    return node


def validator_node(node_id: str = "rv", name: str = "rv", refresh: Any = 10) -> Dict[str, Any]:
    """A Host carrying a Validator."""
    node = host_node(node_id, name, ipv4_gateway="10.0.15.1")
    node["validator"] = {"refresh": refresh}
    return node


def _calls(source: str, attribute: str) -> List[ast.Call]:
    """Every call to a method of this name in the generated script."""
    return [node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == attribute]


def _service(source: str, variable: str, class_name: str) -> ast.Call:
    """The ``<class_name>(...)`` a Node's ``addService`` call attaches."""
    for call in _calls(source, "addService"):
        func = call.func
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == variable):
            [service] = call.args
            if isinstance(service, ast.Call) and getattr(service.func, "id", None) == class_name:
                return service
    raise AssertionError(f"{variable} attaches no {class_name}:\n{source}")


def _hosted_cas(service: ast.Call) -> List[ast.Call]:
    """The ``HostedCA(...)`` calls in an RPKI CA Service's first argument."""
    [hosted] = service.args[:1]
    assert isinstance(hosted, ast.List), ast.dump(hosted)
    calls = [call for call in hosted.elts if isinstance(call, ast.Call)]
    assert len(calls) == len(hosted.elts), ast.dump(hosted)
    return calls


class CanvasRPKICase(unittest.TestCase):
    """A canvas definition with the RPKI Services on its Nodes."""

    def setUp(self) -> None:
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.repository = AuthoringRepository(storage.name)

    def document(self, *nodes: Dict[str, Any]) -> Dict[str, Any]:
        """A valid IPv4 definition holding these Nodes, with no Links."""
        base = self.repository.create()
        base["address_family"] = "ipv4"
        base["catalog"] = {"id": None, "name": "RPKI lesson", "description": "desc",
                           "protocols": ["BGP"]}
        base["topology"] = {"nodes": copy.deepcopy(list(nodes)), "links": []}
        return base

    def refusal(self, *nodes: Dict[str, Any]) -> DraftValidationError:
        """The refusal a definition with these Nodes gets when validated."""
        with self.assertRaises(DraftValidationError) as caught:
            self.repository.validate(self.document(*nodes))
        return caught.exception


@pytest.mark.unit
class CanvasRPKIValidationTests(CanvasRPKICase):
    """Each refusal carries the Python API's message and the control that names it."""

    def test_a_ca_and_a_validator_validate(self) -> None:
        ca = ca_node("ca", "ca", hosted_ca(65002, ["10.2.0.0/24"],
                                           roas=["10.2.0.0/24-24 => 65002"],
                                           aspas=["AS65002 => AS65001, AS65005"]))
        self.repository.validate(self.document(ca, validator_node("rv", "rv", refresh=20)))

    def test_a_roa_outside_the_hosted_cas_resources_is_named_on_its_row(self) -> None:
        error = self.refusal(ca_node("ca", "ca", hosted_ca(65001, ["10.1.0.0/24"],
                                                           roas=["10.9.0.0/24-24 => 65001"])))
        self.assertEqual(error.target["controlId"], "rpki-ca-hosted-cas-0-roas")
        self.assertEqual(error.target["id"], "ca")
        self.assertIn("starting ROA 10.9.0.0/24 is outside its resources (10.1.0.0/24)",
                      str(error))

    def test_an_aspa_for_another_customer_is_named_on_its_row(self) -> None:
        error = self.refusal(ca_node("ca", "ca", hosted_ca(65001, ["10.1.0.0/24"],
                                                           aspas=["AS65002 => AS65001"])))
        self.assertEqual(error.target["controlId"], "rpki-ca-hosted-cas-0-aspas")
        self.assertIn("starting ASPA customer AS65002 is not its own ASN", str(error))

    def test_a_duplicate_asn_is_named_on_the_second_row(self) -> None:
        error = self.refusal(ca_node("ca", "ca", hosted_ca(65001, ["10.1.0.0/24"]),
                                     hosted_ca(65001, ["10.2.0.0/24"])))
        self.assertEqual(error.target["controlId"], "rpki-ca-hosted-cas-1-asn")
        self.assertIn("Hosted CA AS65001 is declared more than once", str(error))

    def test_a_ca_without_hosted_cas_names_the_list(self) -> None:
        node = ca_node("ca", "ca")
        node["rpki_ca"]["hosted_cas"] = []
        error = self.refusal(node)
        self.assertEqual(error.target["controlId"], "rpki-ca-hosted-cas")
        self.assertIn("An RPKI CA needs at least one Hosted CA", str(error))

    def test_a_bad_refresh_is_named_on_the_refresh_field(self) -> None:
        error = self.refusal(validator_node("rv", "rv", refresh=0))
        self.assertEqual(error.target["controlId"], "validator-refresh")
        self.assertIn("refresh must be greater than zero seconds", str(error))

    def test_a_refresh_that_is_not_a_whole_number_is_refused(self) -> None:
        error = self.refusal(validator_node("rv", "rv", refresh="10"))
        self.assertEqual(error.target["controlId"], "validator-refresh")
        self.assertIn("refresh must be a whole number of seconds", str(error))

    def test_a_second_rpki_ca_is_refused_on_the_later_node(self) -> None:
        error = self.refusal(ca_node("ca1", "ca1"), ca_node("ca2", "ca2"))
        self.assertEqual(error.target["controlId"], "rpki-ca-enabled")
        self.assertEqual(error.target["id"], "ca2")
        self.assertIn("A Lab holds at most one RPKI CA, but 2 are declared (ca1, ca2).",
                      str(error))

    def test_a_validator_without_an_rpki_ca_is_refused(self) -> None:
        error = self.refusal(validator_node("rv", "rv"), host_node("h1", "h1"))
        self.assertEqual(error.target["controlId"], "validator-enabled")
        self.assertEqual(error.target["id"], "rv")
        self.assertIn("Node 'rv': a Validator needs an RPKI CA in the Lab, to take its TAL "
                      "and trust from, but this Lab declares none.", str(error))

    def test_a_ca_named_by_a_non_dns_name_needs_a_hostname(self) -> None:
        error = self.refusal(ca_node("ca", "ca_1"))
        self.assertEqual(error.target["controlId"], "rpki-ca-hostname")
        self.assertIn("'ca_1' is not a DNS hostname", str(error))

    def test_a_declared_hostname_stands_in_for_the_node_name(self) -> None:
        self.repository.validate(self.document(ca_node("ca", "ca_1", hostname="ca1")))

    def test_a_declared_hostname_must_itself_be_a_dns_name(self) -> None:
        error = self.refusal(ca_node("ca", "ca", hostname="not a name"))
        self.assertEqual(error.target["controlId"], "rpki-ca-hostname")


@pytest.mark.unit
class CanvasRPKIExportTests(CanvasRPKICase):
    """The generated script declares both Services, with only the options that differ from defaults."""

    def test_the_ca_declares_each_hosted_ca_with_its_starting_objects(self) -> None:
        document = self.repository.validate(self.document(ca_node("ca", "ca",
            hosted_ca(65001, ["10.1.0.0/24"]),
            hosted_ca(65002, ["10.2.0.0/24"], roas=["10.2.0.0/24-24 => 65002"],
                      aspas=["AS65002 => AS65001"]))))
        source = generate_python(document)

        service = _service(source, "ca", "RPKICAService")
        self.assertEqual(
            [(ast.literal_eval(call.args[0]), ast.literal_eval(call.args[1]),
              {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords})
             for call in _hosted_cas(service)],
            [(65001, ["10.1.0.0/24"], {}),
             (65002, ["10.2.0.0/24"], {"roas": ["10.2.0.0/24-24 => 65002"],
                                       "aspas": ["AS65002 => AS65001"]})])
        self.assertEqual(service.keywords, [])

    def test_the_hostname_is_declared_only_when_set(self) -> None:
        source = generate_python(self.repository.validate(self.document(
            ca_node("ca", "ca", hostname="ca.lab"))))
        service = _service(source, "ca", "RPKICAService")
        self.assertEqual([(kw.arg, ast.literal_eval(kw.value)) for kw in service.keywords],
                         [("hostname", "ca.lab")])

    def test_the_validator_declares_its_refresh_only_when_it_differs_from_the_default(self) -> None:
        default = generate_python(self.repository.validate(self.document(
            ca_node("ca", "ca"), validator_node("rv", "rv", refresh=10))))
        self.assertEqual(_service(default, "rv", "ValidatorService").keywords, [])

        changed = generate_python(self.repository.validate(self.document(
            ca_node("ca", "ca"), validator_node("rv", "rv", refresh=20))))
        self.assertEqual([(kw.arg, ast.literal_eval(kw.value))
                          for kw in _service(changed, "rv", "ValidatorService").keywords],
                         [("refresh", 20)])

    def test_both_services_are_imported_from_the_package(self) -> None:
        source = generate_python(self.repository.validate(self.document(
            ca_node("ca", "ca"), validator_node("rv", "rv"))))

        [imported] = [node for node in ast.walk(ast.parse(source))
                      if isinstance(node, ast.ImportFrom) and node.module == "mniplab.rpki"]
        self.assertEqual({alias.name for alias in imported.names},
                         {"HostedCA", "RPKICAService", "ValidatorService"})

    def test_the_generated_script_compiles(self) -> None:
        source = generate_python(self.repository.validate(self.document(
            ca_node("ca", "ca", hosted_ca(65002, ["10.2.0.0/24"], roas=["10.2.0.0/24-24 => 65002"])),
            validator_node("rv", "rv"))))

        compile(source, "generated.py", "exec")

    def test_a_node_named_like_an_rpki_class_does_not_shadow_it(self) -> None:
        source = generate_python(self.repository.validate(self.document(
            ca_node("ca", "HostedCA"))))

        self.assertIn("RPKICAService", source)
        self.assertNotIn("HostedCA = ", source)

    def test_the_shipped_secure_bgp_lab_declares_its_ca_and_validator(self) -> None:
        draft = draft_from_shipped(self.repository, "secure-bgp-lab")
        source = generate_python(draft)

        service = _service(source, "ca", "RPKICAService")
        self.assertEqual([ast.literal_eval(call.args[0]) for call in _hosted_cas(service)],
                         [65001, 65002, 65003, 65004])
        self.assertEqual(_service(source, "rv", "ValidatorService").keywords, [])

    def test_the_authored_rpki_test_lab_validates_and_declares_both_services(self) -> None:
        document = self.repository.validate(self.document(*authored_rpki_test_lab()["nodes"]))
        source = generate_python(document)

        ca = _service(source, "ca", "RPKICAService")
        self.assertEqual([ast.literal_eval(call.args[0]) for call in _hosted_cas(ca)],
                         [65002, 65003])
        self.assertEqual(_service(source, "rv", "ValidatorService").keywords, [])

    def test_a_python_draft_runs_its_script_as_written(self) -> None:
        python = (
            "from mniplab import mnIPLab\n"
            "from mniplab.rpki import HostedCA, RPKICAService\n\n"
            "def build_network():\n"
            "    net = mnIPLab(topo=None)\n"
            "    ca = net.addHost('ca')\n"
            "    ca.addService(RPKICAService([HostedCA(65001, ['10.1.0.0/24'])]))\n"
            "    return net\n"
        )
        draft = self.repository.create_python(
            {"name": "Python RPKI", "description": "", "protocols": [], "area": "DNS"},
            python, {}, "", self.repository.create()["layout"])

        self.assertEqual(draft["python"], python)
        self.assertEqual(self.repository.validate(draft)["python"], python)


@pytest.mark.unit
class RPKIDefinitionAPITests(AuthoringAPIFixture):
    """A saved RPKI definition reopens unchanged, and a refusal reaches the Hosted CA's row."""

    def test_the_rpki_test_lab_round_trips_and_a_bad_roa_is_refused_on_its_row(self) -> None:
        self._launch_with("multi", FakeLauncher())

        async def exercise(client: Any) -> None:
            draft = (await client.post("/api/authoring/drafts", json={})).json()
            draft["catalog"]["name"] = "RPKI lesson"
            draft["catalog"]["area"] = "DNS"
            draft["address_family"] = "ipv4"
            draft["topology"] = authored_rpki_test_lab()
            url = f"/api/authoring/drafts/{draft['id']}"
            saved = await client.put(url, json={"revision": draft["revision"], "document": draft})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual((await client.get(url)).json()["topology"], draft["topology"])
            preview = await client.post(f"{url}/preview", json={"revision": saved.json()["revision"]})
            self.assertEqual(preview.status_code, 200, preview.text)

            altered = copy.deepcopy(saved.json())
            ca = next(node for node in altered["topology"]["nodes"] if node["id"] == "ca")
            ca["rpki_ca"]["hosted_cas"][0]["roas"] = ["10.9.0.0/24-24 => 65002"]
            refused = await client.put(url, json={"revision": altered["revision"], "document": altered})
            self.assertEqual(refused.status_code, 422, refused.text)
            self.assertEqual(refused.json()["target"]["controlId"], "rpki-ca-hosted-cas-0-roas")

        self._as("instructor", exercise)
