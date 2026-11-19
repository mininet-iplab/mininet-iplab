"""The Service role: a network service a Node offers to the Lab.

A Service is distinct from a Protocol: a Protocol is a routing daemon a
Router runs to forward traffic (OSPF, BGP, ...), while a Service is a role a
Node plays for the rest of the Lab — DHCP first, DNS intended next.

The attachment point (:class:`ServiceMixin`) lives in its own module, mixed
into both :class:`mniplab.host.mnIPLabHost` and :class:`mniplab.router.FRRouter`,
because a Router serving DHCP for its own Subnets is the common real-world
case — putting it on Host alone would make that case unbuildable.
"""

from __future__ import annotations

from typing import Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from mininet.node import Node
    from mniplab.dhcp.relay import Relay


class Service:
    """Base class for a network service a Node offers to the Lab.

    Carries no behaviour of its own — each concrete Service (``DHCPService``
    today, a DNS Service later) is a plain data holder, and the provisioner
    that owns its kind (``DHCPProvisioner``, mirroring the FRR/ExaBGP
    provisioners) is what starts and stops the process behind it. Keeping
    Service data-only means a new kind of Service needs a new provisioner,
    never a change here.

    Attributes:
        name: The canonical identifier for this Service (e.g. ``'dhcp'``).
        node: The Node this Service is attached to. ``None`` until
            :meth:`ServiceMixin.addService` sets it.
    """

    name: str = ""
    node: Any = None

    @property
    def badge_text(self) -> str:
        """The badge label for this Service.

        Returns:
            The badge text string, by default the uppercase service name.
        """
        return self.name.upper()

    @property
    def families(self) -> List[int]:
        """Address families served by this Service.

        Returns:
            List of address family integers (e.g. [4], [6], or [4, 6]).
        """
        return []


class ServiceMixin:
    """Gives a Node the ability to have a Service attached to it.

    Mixed into :class:`mniplab.host.mnIPLabHost` and
    :class:`mniplab.router.FRRouter`, ahead of Mininet's ``Node`` in the MRO.
    A Service does not change what kind of Node this is — it stays a Host or
    a Router in the Topology — so this mixin adds an attachment point and
    nothing else.
    """

    def addService(self, service: Service) -> Service:
        """Attach a Service to this Node.

        Args:
            service: The Service to attach — a ``DHCPService`` carrying one
                Pool per Subnet it serves.

        Returns:
            The same Service, so a Lab Example can chain straight off the
            constructor call.
        """
        service.node = self
        self._attached_services().append(service)
        return service

    @property
    def services(self) -> List[Service]:
        """Every Service attached to this Node, in attachment order."""
        return list(self._attached_services())

    def _attached_services(self) -> List[Service]:
        """Return this Node's backing service list, creating it on first use.

        A lazy-initialized list rather than one set in ``__init__`` — this
        mixin sits ahead of Mininet's ``Node`` in the MRO for classes whose
        ``__init__`` chain it must not have to participate in.
        """
        if not hasattr(self, '_services'):
            self._services: List[Service] = []
        return self._services

    def addRelay(self, relay: Relay) -> Relay:
        """Attach a Relay to this Node.

        Args:
            relay: The Relay to attach — a ``Relay`` covering one or more
                Subnets and forwarding to a Service.

        Returns:
            The same Relay, so a Lab Example can chain straight off the
            constructor call.
        """
        from mniplab.errors import ConfigurationError
        if self._attached_relays():
            name = getattr(self, 'name', 'Node')
            raise ConfigurationError(
                f"{name}: already has a Relay attached. "
                "Only one Relay per Node is permitted."
            )
        relay.node = self
        self._attached_relays().append(relay)
        return relay

    @property
    def relays(self) -> List[Relay]:
        """Every Relay attached to this Node, in attachment order."""
        return list(self._attached_relays())

    def _attached_relays(self) -> List[Relay]:
        """Return this Node's backing relay list, creating it on first use."""
        if not hasattr(self, '_relays'):
            self._relays: List[Relay] = []
        return self._relays
