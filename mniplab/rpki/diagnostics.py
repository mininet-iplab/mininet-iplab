"""Start-time configuration diagnostics for the RPKI CA Service.

A Lab holds at most one RPKI CA, every RPKI CA needs a hostname Krill can be
reached by, and a Validator needs an RPKI CA to take its TAL from. All are
fatal (``ConfigurationError``): the Lab never starts.
"""

from __future__ import annotations

from typing import Any, List, Tuple

from mniplab.errors import ConfigurationError
from mniplab.rpki.service import RPKICAService, rpki_ca_services, validator_services


def multiple_rpki_cas_message(count: int, names: str) -> str:
    """The refusal for a Lab that declares more than one RPKI CA.

    Args:
        count: How many RPKI CAs the Lab declares.
        names: Their Node names, comma-separated.

    Returns:
        The message, shared by the Python API and the canvas validator.
    """
    return f"A Lab holds at most one RPKI CA, but {count} are declared ({names})."


def validator_without_rpki_ca_message(name: str) -> str:
    """The refusal for a Validator in a Lab that declares no RPKI CA.

    Args:
        name: The Validator's Node name.

    Returns:
        The message, shared by the Python API and the canvas validator.
    """
    return (
        f"Node '{name}': a Validator needs an RPKI CA in the Lab, "
        f"to take its TAL and trust from, but this Lab declares none."
    )


class RPKIDiagnostics:
    """Start-time diagnostic analyzer for RPKI CA and Validator Service configurations."""

    @staticmethod
    def validate_network(net: Any) -> None:
        """Refuse a Lab whose RPKI Services cannot start together.

        Args:
            net: The network orchestrator holding the Lab's Nodes.

        Raises:
            ConfigurationError: If the Lab declares a second RPKI CA, an RPKI
                CA's hostname is not a DNS name, or a Validator is declared
                where the Lab has no RPKI CA.
        """
        hosts = getattr(net, "hosts", [])
        declared: List[Tuple[Any, RPKICAService]] = [
            (host, service)
            for host in hosts
            for service in rpki_ca_services(host)
        ]
        if len(declared) > 1:
            names = ", ".join(host.name for host, _service in declared)
            raise ConfigurationError(multiple_rpki_cas_message(len(declared), names))
        for host, service in declared:
            try:
                service.resolved_hostname
            except ValueError as exc:
                raise ConfigurationError(f"Node '{host.name}': RPKI CA {exc}") from exc
        validators = [host for host in hosts if validator_services(host)]
        if validators and not declared:
            raise ConfigurationError(validator_without_rpki_ca_message(validators[0].name))
