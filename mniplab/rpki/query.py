"""RPKIQueryService: reads an RPKI CA's Hosted CAs, ROAs and ASPAs as plain data.

Shared by the Lab Prompt (mniplab.cli.mnIPLabCLI.do_show_rpki) and, later, the
Web API. Every answer comes from Krill, so an edit made in a Terminal with
``krillc`` shows here at once.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from mniplab.rpki.krill_client import KrillClient
from mniplab.rpki.models import HostedCA, as_asn
from mniplab.rpki.routinator_client import RoutinatorClient
from mniplab.rpki.service import RPKICAService, ValidatorService, rpki_ca_services, validator_services
from mniplab.rpki.validator_provisioner import ValidatorProvisioner


def _instant(text: str) -> datetime:
    """Parse an RFC 3339 instant as Routinator writes it.

    Routinator writes nanoseconds, which Python reads to microseconds.

    Args:
        text: The instant, such as ``2026-11-06T13:36:00.191074841+00:00``.

    Returns:
        The instant as a timezone-aware datetime.
    """
    return datetime.fromisoformat(text)


class RPKIQueryService:
    """Query methods for an RPKI CA's Hosted CAs, ROAs and ASPAs, and a Validator's VRPs."""

    @staticmethod
    def find_ca(net: Any, node_name: Optional[str] = None) -> Tuple[Any, RPKICAService]:
        """Find the Node and its RPKI CA Service.

        Args:
            net: Mininet network instance.
            node_name: The RPKI CA's Node name; None for the Lab's one RPKI CA.

        Returns:
            Tuple of (node, RPKICAService).

        Raises:
            ValueError: If the Node is missing or has no RPKI CA Service, if
                the Lab has no RPKI CA, or if it has more than one and no
                Node is named.
        """
        if node_name is None:
            found = [
                (node, service)
                for node in net.values()
                for service in rpki_ca_services(node)
            ]
            if not found:
                raise ValueError("This Lab has no RPKI CA Service")
            if len(found) > 1:
                names = ", ".join(node.name for node, _ in found)
                raise ValueError(f"This Lab has more than one RPKI CA ({names}); name the Node")
            return found[0]

        try:
            node = net.get(node_name)
        except KeyError as exc:
            raise ValueError(f"Node not found: {node_name}") from exc
        services = rpki_ca_services(node)
        if not services:
            raise ValueError(f"{node_name} has no RPKI CA Service attached")
        return node, services[0]

    @classmethod
    def get_client(
        cls, net: Any, node_name: Optional[str] = None
    ) -> Tuple[Any, RPKICAService, KrillClient]:
        """Resolve a Node, its RPKI CA Service, and a client on its Krill socket.

        The client is constructed, not connected: each call it makes opens its
        own socket, so this lookup has no side effect to undo.

        Args:
            net: Mininet network instance.
            node_name: The RPKI CA's Node name; None for the Lab's one RPKI CA.

        Returns:
            Tuple of (node, service, client).

        Raises:
            ValueError: If the Node or Service is absent, or the Krill socket
                does not exist, which means Krill is not running.
        """
        node, service = cls.find_ca(net, node_name)
        socket_path = net.rpki_ca_socket_path(node.name)
        if not os.path.exists(socket_path):
            raise ValueError(
                f"Krill socket not found at {socket_path}. Is krill running on {node.name}?"
            )
        return node, service, KrillClient(socket_path)

    @staticmethod
    def hosted_ca(service: RPKICAService, asn: Any) -> HostedCA:
        """Return the Hosted CA for an Autonomous System.

        Args:
            service: The RPKI CA Service holding the Hosted CAs.
            asn: The ASN as an int, or text such as ``65002`` or ``AS65002``.

        Returns:
            The matching Hosted CA.

        Raises:
            ValueError: If the value is not an ASN, or no Hosted CA holds it;
                the message lists the Hosted CAs that exist.
        """
        value = as_asn(asn)
        for hosted in service.hosted_cas:
            if hosted.asn == value:
                return hosted
        listing = ", ".join(f"AS{hosted.asn}" for hosted in service.hosted_cas)
        raise ValueError(f"No Hosted CA for AS{value}. This RPKI CA's Hosted CAs are {listing}")

    @staticmethod
    def _listing(hosted: HostedCA, client: KrillClient) -> Dict[str, Any]:
        """Return one Hosted CA with its resources, ROAs and ASPAs as plain data.

        Args:
            hosted: The Hosted CA to read.
            client: The RPKI CA's Krill client.

        Returns:
            A dict with ``asn``, ``handle``, ``prefixes`` (its declared
            resources), ``roas`` (each with ``prefix``, ``max_length`` and
            ``origin``) and ``aspas`` (each with ``customer`` and ``providers``).
        """
        return {
            "asn": hosted.asn,
            "handle": hosted.handle,
            "prefixes": list(hosted.prefixes),
            "roas": [asdict(roa) for roa in client.list_roas(hosted.handle)],
            "aspas": [asdict(aspa) for aspa in client.list_aspas(hosted.handle)],
        }

    @classmethod
    def list_cas(cls, net: Any, node_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return each Hosted CA with its resources, ROAs and ASPAs.

        Args:
            net: Mininet network instance.
            node_name: The RPKI CA's Node name; None for the Lab's one RPKI CA.

        Returns:
            One dict per declared Hosted CA, as :meth:`get_hosted_ca` returns.

        Raises:
            ValueError: If the Node or Service is absent, or Krill is not running.
            KrillError: If Krill cannot be reached or refuses a read.
        """
        _node, service, client = cls.get_client(net, node_name)
        return [cls._listing(hosted, client) for hosted in service.hosted_cas]

    @classmethod
    def get_hosted_ca(cls, net: Any, node_name: Optional[str], asn: Any) -> Dict[str, Any]:
        """Return one Hosted CA with its resources, ROAs and ASPAs.

        Args:
            net: Mininet network instance.
            node_name: The RPKI CA's Node name; None for the Lab's one RPKI CA.
            asn: The Hosted CA's ASN, as an int or ``AS`` text.

        Returns:
            The Hosted CA's dict, as :meth:`list_cas` lists it.

        Raises:
            ValueError: If the Node or Service is absent, the ASN is not one
                the RPKI CA holds, or Krill is not running. Nothing is asked of
                Krill for an ASN the RPKI CA does not hold.
            KrillError: If Krill cannot be reached or refuses a read.
        """
        _node, service, client = cls.get_client(net, node_name)
        return cls._listing(cls.hosted_ca(service, asn), client)

    @staticmethod
    def find_validator(net: Any, node_name: Optional[str] = None) -> Tuple[Any, ValidatorService]:
        """Find the Node and its Validator Service.

        Args:
            net: Mininet network instance.
            node_name: The Validator's Node name; None for the Lab's one Validator.

        Returns:
            Tuple of (node, ValidatorService).

        Raises:
            ValueError: If the Node is missing or has no Validator Service, if
                the Lab has no Validator, or if it has more than one and no
                Node is named.
        """
        if node_name is None:
            found = [
                (node, service)
                for node in net.values()
                for service in validator_services(node)
            ]
            if not found:
                raise ValueError("This Lab has no Validator Service")
            if len(found) > 1:
                names = ", ".join(node.name for node, _ in found)
                raise ValueError(f"This Lab has more than one Validator ({names}); name the Node")
            return found[0]

        try:
            node = net.get(node_name)
        except KeyError as exc:
            raise ValueError(f"Node not found: {node_name}") from exc
        services = validator_services(node)
        if not services:
            raise ValueError(f"{node_name} has no Validator Service attached")
        return node, services[0]

    @classmethod
    def get_routinator(
        cls, net: Any, node_name: Optional[str] = None
    ) -> Tuple[Any, ValidatorService, RoutinatorClient]:
        """Resolve a Node, its Validator Service, and a client on its Routinator.

        The client is constructed, not run: each read curls the endpoint inside
        the Validator Node, so constructing it touches nothing.

        Args:
            net: Mininet network instance.
            node_name: The Validator's Node name; None for the Lab's one Validator.

        Returns:
            Tuple of (node, service, client).

        Raises:
            ValueError: If the Node or Service is absent.
        """
        node, service = cls.find_validator(net, node_name)
        port = ValidatorProvisioner.HTTP_PORT
        client = RoutinatorClient(
            lambda path: node.cmd(f"curl -sf --max-time 5 http://127.0.0.1:{port}{shlex.quote(path)}")
        )
        return node, service, client

    @staticmethod
    def _router_names(net: Any, addresses: List[str]) -> List[str]:
        """Name each connected Router by the Node whose interface holds its address.

        Args:
            net: Mininet network instance.
            addresses: The source address of each connected RTR client.

        Returns:
            One name per address, in order. An address no Node carries stays as
            the address.
        """
        owners: Dict[str, str] = {}
        for host in net.hosts:
            for intf in host.intfList():
                address = intf.IP()
                if address:
                    owners.setdefault(address, host.name)
        return [owners.get(address, address) for address in addresses]

    @classmethod
    def get_vrps(cls, net: Any, node_name: Optional[str] = None) -> Dict[str, Any]:
        """Return what a Validator serves: its VRPs and ASPAs, refresh times and connected Routers.

        The next refresh is the last one plus the Validator's declared interval;
        Routinator does not report it. Both times are None before the first run.

        Args:
            net: Mininet network instance.
            node_name: The Validator's Node name; None for the Lab's one Validator.

        Returns:
            A dict with ``node``, ``vrps`` (each with ``prefix``, ``max_length``
            and ``origin``), ``aspas`` (each with ``customer`` and ``providers``),
            ``refresh``, ``last_refresh`` and ``next_refresh`` (ISO 8601 or None),
            and ``routers`` (the connected Routers' names).

        Raises:
            ValueError: If the Node or Service is absent.
            RoutinatorError: If the Validator is not answering.
        """
        node, service, client = cls.get_routinator(net, node_name)
        status = client.status()
        last = _instant(status["last_refresh"]) if status["last_refresh"] else None
        nxt = last + timedelta(seconds=service.refresh) if last is not None else None
        return {
            "node": node.name,
            "vrps": [asdict(vrp) for vrp in client.vrps()],
            "aspas": client.aspas(),
            "refresh": service.refresh,
            "last_refresh": last.isoformat() if last is not None else None,
            "next_refresh": nxt.isoformat() if nxt is not None else None,
            "routers": cls._router_names(net, status["rtr_clients"]),
        }
