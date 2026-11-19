"""PoolService: reading and updating what a DHCP Service's Kea serves.

Shared by the Web API (``mniplab.webserver.session.SessionRegistry``) and
future management surfaces, matching :class:`mniplab.dhcp.leases.LeaseQueryService`.

A Pool is Exercise Configuration: state a Learner is expected to author from the
Service panel. The Subnet it belongs to and the Interface Kea
listens on stay the Instructor's (Lab shape).

Edits are applied to running kea-dhcp4 via its control channel without a
restart, preserving any Leases held before the edit. Pre-validation and
``config-test`` guarantee that a rejected edit leaves the running server
untouched.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from mniplab.dhcp.config import KeaConfigBuilder
from mniplab.dhcp.pool import (
    Pool,
    Reservation,
    validate_reservation,
)
from mniplab.dhcp.query import DHCPQueryService
from mniplab.dhcp.service import DHCPService
from mniplab.errors import ConfigurationError
from mniplab.provisioners import DHCPProvisioner


class PoolService:
    """Manages address Pools for a Node's DHCP Service."""

    @staticmethod
    def _serialize_pool(pool: Pool, interface: str) -> Dict[str, Any]:
        """Return one Pool's serialized form, as the Web UI and CLI consume it.

        Args:
            pool: The Pool to serialize.
            interface: The Interface this Pool's CIDR resolved to, if any.

        Returns:
            A dict with ``family``, ``cidr``, ``start``, ``end``, ``interface``,
            ``lease_time``, ``router``, ``dns_server``, and ``reservations``
            (each a ``{"mac", "duid", "ip", "hostname"}`` dict — exactly one
            of ``mac``/``duid`` is non-empty).  IPv6 Pools also carry
            ``preferred_lifetime``.
        """
        data: Dict[str, Any] = {
            "family": pool.family,
            "cidr": pool.cidr,
            "start": pool.start,
            "end": pool.end,
            "interface": interface,
            "lease_time": pool.lease_time,
            "router": pool.router,
            "dns_server": pool.dns_server,
            "reservations": [
                {"mac": r.mac, "duid": r.duid, "ip": r.ip, "hostname": r.hostname}
                for r in pool.reservations
            ],
        }
        if pool.family == 6:
            data["preferred_lifetime"] = pool.preferred_lifetime
        return data

    @classmethod
    def _serialize_pools(cls, service: DHCPService) -> List[Dict[str, Any]]:
        """Return a list of serialized pool dictionaries for ``service``.

        Args:
            service: The DHCPService whose pools to serialize.

        Returns:
            One serialized dict (see :meth:`_serialize_pool`) per Pool.
        """
        return [
            cls._serialize_pool(pool, service.interface_names.get(pool.cidr, ""))
            for pool in service.pools.values()
        ]

    @staticmethod
    def _build_reservations(
        cidr: str, reservations: Optional[List[Dict[str, str]]]
    ) -> Tuple[Reservation, ...]:
        """Validate and convert raw reservation dicts into ``Reservation`` objects.

        Args:
            cidr: The Subnet these Reservations belong to.
            reservations: Raw ``{"mac", "duid", "ip", "hostname"}`` dicts as
                submitted by the Web UI; ``None`` or empty means no
                Reservations. Exactly one of ``mac``/``duid`` must be set.

        Returns:
            A tuple of :class:`~mniplab.dhcp.pool.Reservation` in the same order.

        Raises:
            ValueError: Any Reservation carries both a hardware address and
                a DUID, neither, or a malformed identifier or address.
        """
        result = []
        for raw in reservations or []:
            mac = raw.get("mac", "")
            duid = raw.get("duid", "")
            ip = raw.get("ip", "")
            hostname = raw.get("hostname", "")
            validate_reservation(cidr, mac, ip, duid)
            result.append(Reservation(mac=mac, ip=ip, duid=duid, hostname=hostname))
        return tuple(result)

    @classmethod
    def get_pools(cls, net: Any, node_name: str) -> Dict[str, Any]:
        """Return every Pool the named Node's DHCP Service currently serves.

        Args:
            net: The Lab, containing the named Node.
            node_name: The serving Node's name.

        Returns:
            ``{"success": True, "pools": [...]}`` — each with ``cidr``,
            ``start``, ``end``, and resolved ``interface``.

        Raises:
            ValueError: The Node does not exist or has no DHCP Service attached.
        """
        _, service = DHCPQueryService.find_service(net, node_name)
        return {"success": True, "pools": cls._serialize_pools(service)}

    @classmethod
    def set_pool(
        cls,
        net: Any,
        node_name: str,
        cidr: str,
        start: str,
        end: str,
        lease_time: Optional[int] = None,
        preferred_lifetime: Optional[int] = None,
        router: str = "",
        dns_server: str = "",
        reservations: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Update a Pool's editable fields on the running server.

        Validates every field in Python, verifies the prospective
        configuration via Kea's ``config-test`` control command, and applies it
        using ``config-set`` without restarting the server. Existing Leases
        survive the change. Fields omitted from the call (``lease_time``,
        ``preferred_lifetime``, ``router``, ``dns_server``, ``reservations``
        at their defaults) are cleared, not left at their previous value —
        a Learner's form submits the Pool's whole state on every Apply.

        Args:
            net: The running Lab.
            node_name: The Node offering the DHCP Service.
            cidr: The Subnet the Pool belongs to.
            start: New first address to hand out.
            end: New last address to hand out.
            lease_time: New lease time in seconds, or ``None`` to leave Kea's
                own default in place.
            preferred_lifetime: New preferred lifetime in seconds (IPv6 only),
                or ``None`` to leave Kea's default in place.
            router: New router option, or ``""`` to omit it.
            dns_server: New DNS-server option, or ``""`` to omit it.
            reservations: New Reservations as raw ``{"mac", "duid", "ip",
                "hostname"}`` dicts (exactly one of ``mac``/``duid`` set),
                replacing whatever was declared before.

        Returns:
            ``{"success": True, "pool": {...}, "pools": [...]}``.

        Raises:
            ValueError: The Node or Service was not found, the Subnet is not
                served by this Service, any field is invalid, or Kea rejected
                the prospective configuration.
        """
        node, service = DHCPQueryService.find_service(net, node_name)

        if not cidr and len(service.pools) == 1:
            cidr = next(iter(service.pools.keys()))

        if cidr not in service.pools:
            raise ValueError(f"Subnet {cidr} is not served by {node_name}")

        family = service.pools[cidr].family
        reservation_tuple = cls._build_reservations(cidr, reservations)

        prospective_pool = Pool(
            cidr=cidr,
            start=start,
            end=end,
            lease_time=lease_time,
            preferred_lifetime=preferred_lifetime,
            router=router,
            dns_server=dns_server,
            reservations=reservation_tuple,
        )
        prospective_pools: Dict[str, Pool] = {
            c: p for c, p in service.pools.items() if p.family == family
        }
        prospective_pools[cidr] = prospective_pool

        control_socket_path = os.path.join(
            DHCPProvisioner.VAR_RUN_KEA_PATH,
            DHCPProvisioner.CONTROL_SOCKET_FILENAMES[family],
        )
        lease_database_path = os.path.join(
            DHCPProvisioner.VAR_LIB_KEA_PATH,
            DHCPProvisioner.LEASE_DATABASE_FILENAMES[family],
        )
        if node is not None and net is not None:
            interface_names = DHCPProvisioner.listening_interfaces(
                node, service, family=family, net=net
            )
        else:
            interface_names = list(dict.fromkeys(
                service.interface_names[c]
                for c in prospective_pools
                if c in service.interface_names and service.interface_names[c]
            ))
        subnet_interfaces = (
            {c: service.interface_names[c] for c in prospective_pools if c in service.interface_names}
            if family == 6
            else None
        )
        config = KeaConfigBuilder.build(
            pools=prospective_pools.values(),
            interface_names=interface_names,
            control_socket_path=control_socket_path,
            lease_database_path=lease_database_path,
            family=family,
            subnet_interfaces=subnet_interfaces,
        )

        _, _, client = DHCPQueryService.get_client(net, node_name, family)

        # Pre-test prospective configuration to guarantee an invalid edit
        # leaves the running server exactly as it was.
        try:
            client.test_config(config)
            client.set_config(config)
        except ConfigurationError as exc:
            raise ValueError(str(exc)) from exc

        # Update in-memory state and on-disk config file
        service.pools[cidr] = prospective_pool
        host_config_path = os.path.join(
            DHCPProvisioner.host_dir(node_name, 'etc', net=net),
            DHCPProvisioner.CONFIG_FILENAMES[family],
        )
        if os.path.exists(os.path.dirname(host_config_path)):
            try:
                with open(host_config_path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2)
            except OSError:
                pass

        return {
            "success": True,
            "pool": cls._serialize_pool(prospective_pool, service.interface_names.get(cidr, "")),
            "pools": cls._serialize_pools(service),
        }
