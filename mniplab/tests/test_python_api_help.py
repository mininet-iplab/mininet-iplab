"""Keep the editor's compact API signatures aligned with the Python library."""

import inspect
import re
from pathlib import Path

import pytest

from mniplab.net import mnIPLab


API_HELP = (
    Path(__file__).resolve().parents[1]
    / "webserver/static/js/authoring/python-api-help.js"
).read_text(encoding="utf-8")
METHODS = re.findall(r"label: '([^']+)',\s*signature: '([^']+)'", API_HELP)


@pytest.mark.unit
@pytest.mark.parametrize("name,shown", METHODS)
def test_api_help_signature_matches_library(name: str, shown: str) -> None:
    """Check names, parameter kinds, defaults and return types via introspection."""
    signature = inspect.signature(getattr(mnIPLab, name))
    # The UI omits parameter annotations and self to keep the call readable.
    parameters = [
        parameter.replace(annotation=inspect.Signature.empty)
        for parameter in signature.parameters.values()
        if parameter.name != "self"
    ]
    compact = signature.replace(
        parameters=parameters, return_annotation=inspect.Signature.empty
    )
    returns = signature.return_annotation.strip("'\"")
    assert shown == f"{name}{compact} → {returns}"


@pytest.mark.unit
def test_api_help_catalog_includes_nat_and_checks_every_signature() -> None:
    """The NAT method is covered, and no signature escapes introspection."""
    names = [name for name, _shown in METHODS]
    assert "addNAT" in names
    assert len(names) == len(set(names))
    assert len(METHODS) == len(re.findall(r"\bsignature:", API_HELP))
