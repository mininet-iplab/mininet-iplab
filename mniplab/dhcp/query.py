"""DHCPQueryService: how a query reaches a DHCP Service's Kea.

:class:`~mniplab.dhcp.leases.LeaseQueryService` and
:class:`~mniplab.dhcp.pool_service.PoolService` both need the same two
things before they can say anything to Kea — the Node's DHCP Service, and
a client on the right control socket for the family — the way
:class:`mniplab.dns.query.DNSQueryService` resolves a Node's Knot. This is
that lookup, once, so the family-to-socket rule lives in one place and a
test can stand a scripted client in at one seam
(:class:`mniplab.tests.fakes.KeaClientStubMixin`).
"""

from __future__ import annotations

from typing import Any, Tuple

from mniplab.dhcp.control_client import KeaControlClient
from mniplab.dhcp.service import DHCPService
from mniplab.provisioners import DHCPProvisioner


class DHCPQueryService:
    """Resolves a Node's DHCP Service and a client on one of its Kea servers."""

    @staticmethod
    def find_service(net: Any, node_name: str) -> Tuple[Any, DHCPService]:
        """Resolve a Node and its attached DHCP Service.

        Args:
            net: The Lab.
            node_name: The serving Node's name.

        Returns:
            Tuple of (node, service).

        Raises:
            ValueError: The Node does not exist or has no DHCP Service
                attached.
        """
        # Mininet's get raises KeyError for a name the Lab lacks; the
        # caller is promised a ValueError either way.
        try:
            node = net.get(node_name)
        except KeyError:
            node = None
        if node is None:
            raise ValueError(f"Node not found: {node_name}")
        return node, DHCPService.find_on(node)

    @classmethod
    def get_client(
        cls, net: Any, node_name: str, family: int = 4
    ) -> Tuple[Any, DHCPService, KeaControlClient]:
        """Resolve a Node, its Service, and a client on one family's control socket.

        The client is constructed, not connected: it opens a connection per
        command, so this lookup has no side effect to undo.

        Args:
            net: The Lab.
            node_name: The serving Node's name.
            family: The address family whose Kea server to reach, 4 or 6.

        Returns:
            Tuple of (node, service, client).

        Raises:
            ValueError: The Node or Service is absent, or the Service holds
                no Pool of that family, so no such server was launched.
        """
        node, service = cls.find_service(net, node_name)
        if family not in service.families:
            raise ValueError(f"{node_name} serves no DHCPv{family} Pool")
        socket_path = DHCPProvisioner.control_socket_host_path(node_name, family=family, net=net)
        return node, service, KeaControlClient(socket_path, family=family)
