"""Unit tests for deepened authoring frontend modules and structured validation."""

import json
import os
import subprocess
import unittest

import pytest


AUTHORING_JS_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "webserver",
        "static",
        "js",
        "authoring",
    )
)


@pytest.mark.unit
class AuthoringStaticModulesTests(unittest.TestCase):
    """Verify deepened dialog modules, structured client validation, and regex removal."""

    def _read_js(self, filename: str) -> str:
        path = os.path.join(AUTHORING_JS_DIR, filename)
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_deepened_dialog_and_interface_exports_and_validate_functions(self) -> None:
        script = f"""
globalThis.document = {{
  addEventListener() {{}},
  getElementById() {{ return null; }},
}};
const nodeDialog = await import({json.dumps(os.path.join(AUTHORING_JS_DIR, "node-dialog.js"))});
const linkDialog = await import({json.dumps(os.path.join(AUTHORING_JS_DIR, "link-dialog.js"))});
const interfaces = await import({json.dumps(os.path.join(AUTHORING_JS_DIR, "interfaces.js"))});

const assert = (cond, msg) => {{
  if (!cond) throw new Error(msg);
}};

for (const name of ["initNodeDialog", "openNodeDialog", "validateNode", "showFieldError", "clearFieldError"]) {{
  assert(typeof nodeDialog[name] === "function", `node-dialog.js missing export ${{name}}`);
}}
for (const name of ["initLinkDialog", "openLinkDialog", "validateLink"]) {{
  assert(typeof linkDialog[name] === "function", `link-dialog.js missing export ${{name}}`);
}}
for (const name of ["initAddressesDialog", "openAddressesDialog", "closeAddressesDialog"]) {{
  assert(typeof interfaces[name] === "function", `interfaces.js missing export ${{name}}`);
}}

const draft = {{
  address_family: "dual",
  topology: {{
    nodes: [
      {{ id: "r", name: "r1", type: "router", protocols: ["OSPF"], frr_config: "", fast_timers: true, startup: [] }},
      {{ id: "h", name: "h1", type: "host", ipv4_gateway: "", ipv6_gateway: "", startup: [] }},
    ],
    links: [
      {{
        id: "link-1",
        ends: [
          {{ node_id: "r", interface_id: "if-000000000001", port: 0, ipv4: "10.0.0.1/24", ipv6: "" }},
          {{ node_id: "h", interface_id: "if-000000000002", port: 0, ipv4: "10.0.0.2/24", ipv6: "" }},
        ],
      }},
    ],
  }},
}};

const seen = new Map([["r1", "r"]]);
const dupErr = nodeDialog.validateNode({{ ...draft.topology.nodes[1], name: "r1" }}, draft, seen);
assert(dupErr && dupErr.kind === "node" && dupErr.id === "h" && dupErr.controlId === "host-name",
  `unexpected dupErr: ${{JSON.stringify(dupErr)}}`);

const gwErr = nodeDialog.validateNode({{ ...draft.topology.nodes[1], ipv4_gateway: "not-ip" }}, draft, new Map());
assert(gwErr && gwErr.kind === "node" && gwErr.id === "h" && gwErr.controlId === "host-ipv4-gateway",
  `unexpected gwErr: ${{JSON.stringify(gwErr)}}`);

const ifaceErr = nodeDialog.validateNode(
  draft.topology.nodes[0],
  {{
    ...draft,
    topology: {{
      ...draft.topology,
      links: [{{
        id: "link-1",
        ends: [
          {{ node_id: "r", interface_id: "if-000000000001", port: 0, ipv4: "10.0.0.1", ipv6: "" }},
          {{ node_id: "h", interface_id: "if-000000000002", port: 0, ipv4: "10.0.0.2/24", ipv6: "" }},
        ],
      }}],
    }},
  }},
  new Map(),
);
assert(ifaceErr && ifaceErr.kind === "node" && ifaceErr.id === "r" && ifaceErr.controlId === "iface-if-000000000001-ipv4",
  `unexpected ifaceErr: ${{JSON.stringify(ifaceErr)}}`);

const occupied = new Map([["r", new Set([0])]]);
const portErr = linkDialog.validateLink(draft.topology.links[0], draft, occupied);
assert(portErr && portErr.kind === "link" && portErr.id === "link-1" && portErr.controlId === "link-end-0-port",
  `unexpected portErr: ${{JSON.stringify(portErr)}}`);

const condErr = linkDialog.validateLink(
  {{
    ...draft.topology.links[0],
    condition: {{ status: "up", delay: "bad", jitter: null, loss: null, bandwidth: null, scheduler: "hfsc" }},
  }},
  draft,
  new Map(),
);
assert(condErr && condErr.kind === "link" && condErr.id === "link-1" && condErr.controlId === "link-condition-delay",
  `unexpected condErr: ${{JSON.stringify(condErr)}}`);
"""
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_regex_error_guessing_is_removed_from_frontend_modules(self) -> None:
        authoring_src = self._read_js("authoring.js")
        service_lists_src = self._read_js("service-lists.js")
        dns_zones_src = self._read_js("dns-zones.js")

        for forbidden in ("serverValidationTarget", "rowHoldingValue"):
            self.assertNotIn(forbidden, authoring_src, f"authoring.js still contains {forbidden}")

        for forbidden in ("POOL_VALUES", "RESERVATION_VALUES", "reservationValue", "firstCapture", "locate("):
            self.assertNotIn(forbidden, service_lists_src, f"service-lists.js still contains {forbidden}")

        self.assertNotIn("locate(", dns_zones_src, "dns-zones.js still contains locate(")
