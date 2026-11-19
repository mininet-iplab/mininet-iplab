"""Lifecycle provisioner for the Authoritative DNS Service.

Owns the setup of ephemeral runtime directories, configuration rendering for
Knot DNS (knotd), zone file generation, daemon lifecycle inside Node network
namespaces, and lifecycle registry hooks.

Replication is wired here too: a Primary Zone is rendered with the Secondaries
it notifies and the transfer ACL that permits them, and a Secondary Zone with
the Primary it pulls from. Start orders Primaries ahead of Secondaries and then
nudges each Secondary, but never waits for a Zone Transfer to land — an
Instructor may deliberately declare an unreachable Primary to stage that
failure, and such a Lab must still start.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from mininet.log import info, warn

from mniplab.constants import SERVICE_READY_TIMEOUT
from mniplab.dns.control_client import KnotControlClient
from mniplab.dns.models import (
    DSRecord,
    Record,
    SecondaryZone,
    Zone,
    transfer_target_addresses,
)
from mniplab.dns.service import DNSService
from mniplab.errors import ConfigurationError
from mniplab.utils import ensure_directory, wait_until


class DNSProvisioner:
    """Owns the Authoritative DNS Service lifecycle: directories, config, and knotd.

    Prepares ephemeral runtime directories on the host under the Lab directory,
    bind-mounts them into the Node's private mount namespace (/etc/knot, /run/knot,
    /var/lib/knot), renders knot.conf with mod-stats loaded, renders initial zone
    files, and starts knotd listening on 0.0.0.0@53 and ::@53.
    """

    ETC_KNOT_PATH = "/etc/knot"
    VAR_RUN_KNOT_PATH = "/run/knot"
    VAR_LIB_KNOT_PATH = "/var/lib/knot"

    CONFIG_FILENAME = "knot.conf"
    CONTROL_SOCKET_FILENAME = "knot.sock"
    LOG_FILENAME = "knot.log"
    KNOT_BINARY = "knotd"

    PERM_USER_RWX_GROUP_RX = 0o755
    READY_TIMEOUT = SERVICE_READY_TIMEOUT
    SCRATCH_KEYS_PREFIX = "mniplab-zone-keys-"

    @classmethod
    def ensure_zone_keys(
        cls, zone: Zone, node_name: Optional[str] = None, net: Optional[Any] = None
    ) -> str:
        """Ensure KSK and ZSK are generated in the KASP database for a signed zone.

        Args:
            zone: The signed Zone.
            node_name: Optional serving Node name.
            net: Optional Lab instance to resolve paths through.

        Returns:
            The host-side path to the keys directory.

        Raises:
            RuntimeError: If keymgr fails to generate keys.
        """
        target_node = node_name or zone.host_node
        if net is None:
            try:
                from mniplab.net import mnIPLab
                for candidate in reversed(getattr(mnIPLab, '_active_instances', [])):
                    if not getattr(candidate, '_stopped', False):
                        if target_node:
                            node_str = target_node.name if hasattr(target_node, 'name') else str(target_node)
                            if hasattr(candidate, 'nameToNode') and node_str in candidate.nameToNode:
                                net = candidate
                                break
                        else:
                            net = candidate
                            break
            except Exception:
                pass

        if target_node:
            keys_dir = os.path.join(cls.host_dir(target_node, "lib", net=net), "keys")
        elif zone.keys_dir:
            keys_dir = zone.keys_dir
        elif net is not None:
            # A Zone not yet bound to a Node keeps its keys at the Lab's top,
            # to be moved into the serving Node's tree when it is bound.
            keys_dir = net.dns_zone_keys_dir(zone.name)
        else:
            # A Zone with no Lab at all: a scratch directory, moved into the
            # serving Node's tree when the Zone is bound.
            keys_dir = tempfile.mkdtemp(prefix=cls.SCRATCH_KEYS_PREFIX)

        ensure_directory(keys_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
        zone.keys_dir = keys_dir

        list_cmd = ["keymgr", "-D", keys_dir, "-j", zone.name, "list"]
        list_res = subprocess.run(list_cmd, capture_output=True, text=True)
        existing_out = list_res.stdout
        has_ksk = False
        has_zsk = False
        if existing_out.strip().startswith("["):
            try:
                keys_data = json.loads(existing_out)
                for k in keys_data:
                    if k.get("ksk"):
                        has_ksk = True
                    if k.get("zsk"):
                        has_zsk = True
            except Exception:
                pass

        for is_ksk, exists in ((True, has_ksk), (False, has_zsk)):
            if not exists:
                ksk_flag = "ksk=yes" if is_ksk else "ksk=no"
                cmd = [
                    "keymgr",
                    "-D",
                    keys_dir,
                    zone.name,
                    "generate",
                    "algorithm=ecdsap256sha256",
                    ksk_flag,
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    role = "KSK" if is_ksk else "ZSK"
                    raise RuntimeError(
                        f"Failed to generate {role} for zone '{zone.name}' via keymgr: {res.stderr.strip()}"
                    )

        if zone.nsec3 and zone.nsec3_salt is not None:
            res = subprocess.run(
                ["keymgr", "-D", keys_dir, zone.name, "nsec3-salt", zone.nsec3_salt],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                raise RuntimeError(
                    f"Failed to set the NSEC3 salt for zone '{zone.name}' via keymgr: {res.stderr.strip()}"
                )

        return keys_dir

    @classmethod
    def bind_zone_keys(cls, zone: Zone, node_name: str, *, net: Any) -> None:
        """Move a Zone's keys from where they were held to the serving Node's tree.

        Args:
            zone: The Zone whose keys are being bound.
            node_name: The serving Node's name.
            net: The Lab the Node belongs to.
        """
        target_dir = os.path.join(cls.host_dir(node_name, "lib", net=net), "keys")
        if zone.keys_dir and zone.keys_dir != target_dir and os.path.exists(zone.keys_dir):
            ensure_directory(target_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
            for item in os.listdir(zone.keys_dir):
                s = os.path.join(zone.keys_dir, item)
                d = os.path.join(target_dir, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)
            if cls._is_holding_keys_dir(zone.keys_dir, net=net):
                shutil.rmtree(zone.keys_dir, ignore_errors=True)
        zone.keys_dir = target_dir

    @classmethod
    def _is_holding_keys_dir(cls, keys_dir: str, net: Any) -> bool:
        """Was this keys directory one :meth:`ensure_zone_keys` chose to hold keys until binding?

        Args:
            keys_dir: The directory the Zone's keys were generated in.
            net: The Lab the Zone is bound into.

        Returns:
            True for the Lab's own holding directory or a scratch one; False
            for a directory the caller supplied.
        """
        if os.path.basename(keys_dir).startswith(cls.SCRATCH_KEYS_PREFIX):
            return True
        return os.path.dirname(keys_dir) == os.path.join(net.lab_dir, "zone-keys")

    @classmethod
    def get_zone_ds_record(
        cls, zone: Zone, digest_type: int = 2, net: Optional[Any] = None
    ) -> DSRecord:
        """Generate and extract the KSK DS record for a signed zone.

        Args:
            zone: The signed Zone.
            digest_type: Digest algorithm number (default 2 for SHA-256).
            net: Optional Lab instance to resolve paths through.

        Returns:
            A DSRecord corresponding to the zone's provisioned KSK.

        Raises:
            RuntimeError: If keymgr execution fails or no matching DS record is found.
        """
        keys_dir = cls.ensure_zone_keys(zone, net=net)
        cmd = ["keymgr", "-D", keys_dir, zone.name, "ds"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(
                f"Failed to query DS records for zone '{zone.name}' via keymgr: {res.stderr.strip()}"
            )
        for line in res.stdout.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 6 and parts[1].upper() == "DS":
                tag = int(parts[2])
                alg = int(parts[3])
                dtype = int(parts[4])
                digest = parts[5]
                if dtype == digest_type:
                    return DSRecord(
                        name=zone.name,
                        key_tag=tag,
                        algorithm=alg,
                        digest_type=dtype,
                        digest=digest,
                        ttl=zone.default_ttl,
                    )
        raise RuntimeError(
            f"No DS record of digest_type {digest_type} found for zone '{zone.name}'"
        )

    @classmethod
    def host_dir(cls, node_name: str, subdir: str = "", *, net: Any) -> str:
        """Return the host-side directory backing one of a Node's Knot paths.

        Args:
            node_name: The serving Node's name.
            subdir: 'etc', 'run', or 'lib'; omitted for the Node's base directory.
            net: The Lab the Node belongs to.

        Returns:
            The host-side path.
        """
        return net.dns_dir(node_name, subdir)

    @classmethod
    def control_socket_host_path(cls, node_name: str, *, net: Any) -> str:
        """Return the host-side path of a Node's Knot control socket.

        Args:
            node_name: The serving Node's name.
            net: The Lab the Node belongs to.

        Returns:
            The host-side control socket path.
        """
        return net.dns_control_socket_path(node_name)

    @classmethod
    def log_host_path(cls, node_name: str, *, net: Any) -> str:
        """Return the host-side path of a Node's Knot log file.

        The log Knot already writes is what the Transfer Log is read from, so
        replication needs no log target of its own.

        Args:
            node_name: The serving Node's name.
            net: The Lab the Node belongs to.

        Returns:
            The host-side log file path.
        """
        return net.dns_log_path(node_name)

    @classmethod
    def bind_service_directories(cls, node: Any) -> None:
        """Bind-mount a Node's Knot directories into its own mount namespace.

        Args:
            node: The Node the Service is attached to.
        """
        net = node.net
        for container_path, subdir in (
            (cls.ETC_KNOT_PATH, "etc"),
            (cls.VAR_RUN_KNOT_PATH, "run"),
            (cls.VAR_LIB_KNOT_PATH, "lib"),
        ):
            host_dir = cls.host_dir(node.name, subdir, net=net)
            ensure_directory(host_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
            node.cmd(f"mkdir -p {container_path}")
            node.cmd(f"mount --bind {host_dir} {container_path}")

    @classmethod
    def render_zone_file(cls, zone: Zone) -> str:
        """Render a Zone into RFC 1035 master file syntax.

        Args:
            zone: The Zone to render.

        Returns:
            String content of the zone file.
        """
        return zone.to_zone_file()

    @staticmethod
    def _config_id(*parts: str) -> str:
        """Build a Knot configuration identifier from arbitrary name parts.

        Zone apexes carry dots and trailing dots, neither of which reads well
        as a configuration id, so every part is reduced to word characters.

        Args:
            *parts: Name fragments, typically an apex and a peer name.

        Returns:
            An underscore-joined identifier safe for knot.conf.
        """
        cleaned = [
            re.sub(r"[^A-Za-z0-9]+", "_", part.strip().strip(".")).strip("_")
            for part in parts
            if part
        ]
        return "_".join(p for p in cleaned if p)

    @classmethod
    def collect_secondaries(cls, nodes: List[Any]) -> Dict[str, List[Tuple[str, List[str]]]]:
        """Map each replicated apex to the Nodes declaring themselves Secondary.

        This is what lets a Primary's notify list and transfer ACL be derived
        rather than declared: the Secondaries name the apex, and the Primary
        serving that apex picks them up.

        Args:
            nodes: Every Node in the Lab.

        Returns:
            Apex FQDN mapped to (Node name, addresses) pairs, one per Secondary.
        """
        collected: Dict[str, List[Tuple[str, List[str]]]] = {}
        for node in nodes:
            for service in getattr(node, "services", []):
                if not isinstance(service, DNSService):
                    continue
                for apex in service.secondary_zones:
                    collected.setdefault(apex, []).append(
                        (node.name, transfer_target_addresses(node))
                    )
        return collected

    @classmethod
    def _render_replication(
        cls,
        service: DNSService,
        secondaries: Dict[str, List[Tuple[str, List[str]]]],
    ) -> Tuple[List[str], List[str], Dict[str, List[str]]]:
        """Render the remote and ACL sections this Node's Zones need.

        Args:
            service: The DNSService being configured.
            secondaries: Every declared Secondary in the Lab, keyed by apex.

        Returns:
            The ``remote:`` lines, the ``acl:`` lines, and a mapping of apex to
            the per-zone configuration lines referring to them.
        """
        remotes: List[str] = []
        acls: List[str] = []
        zone_options: Dict[str, List[str]] = {}

        for apex, zone in service.zones.items():
            if isinstance(zone, SecondaryZone):
                addresses = zone.primary_addresses()
                if not addresses:
                    # A Primary that resolves to no address is a staged failure,
                    # not a rendering error: the Zone is configured without a
                    # master and reports itself unsynchronised at runtime.
                    continue
                remote_id = cls._config_id("primary", apex)
                acl_id = cls._config_id("notify", apex)
                remotes.append(f"  - id: {remote_id}")
                remotes.append(f"    address: [ {', '.join(addresses)} ]")
                acls.append(f"  - id: {acl_id}")
                acls.append(f"    address: [ {', '.join(addresses)} ]")
                acls.append("    action: notify")
                zone_options[apex] = [
                    f"    master: [ {remote_id} ]",
                    f"    acl: [ {acl_id} ]",
                ]
                continue

            notify_ids: List[str] = []
            for secondary_name, addresses in secondaries.get(apex, []):
                if not addresses:
                    continue
                remote_id = cls._config_id("secondary", apex, secondary_name)
                remotes.append(f"  - id: {remote_id}")
                remotes.append(f"    address: [ {', '.join(addresses)} ]")
                notify_ids.append(remote_id)

            if zone.allow_transfer is not None:
                allowed = [
                    address
                    for target in zone.allow_transfer
                    for address in transfer_target_addresses(target)
                ]
            else:
                allowed = [
                    address
                    for _, addresses in secondaries.get(apex, [])
                    for address in addresses
                ]

            options: List[str] = []
            if allowed:
                acl_id = cls._config_id("transfer", apex)
                acls.append(f"  - id: {acl_id}")
                acls.append(f"    address: [ {', '.join(allowed)} ]")
                acls.append("    action: transfer")
                options.append(f"    acl: [ {acl_id} ]")
            if notify_ids:
                options.append(f"    notify: [ {', '.join(notify_ids)} ]")
            if options:
                zone_options[apex] = options

        return remotes, acls, zone_options

    @classmethod
    def render_config(
        cls,
        node_name: str,
        service: DNSService,
        secondaries: Optional[Dict[str, List[Tuple[str, List[str]]]]] = None,
        net: Optional[Any] = None,
    ) -> str:
        """Render initial knot.conf and zone files for a serving Node.

        Args:
            node_name: The serving Node's name.
            service: The DNSService being configured.
            secondaries: Every declared Secondary in the Lab keyed by apex, as
                returned by :meth:`collect_secondaries`. Omitted when the Lab
                declares no replication.
            net: Optional Lab instance to resolve paths through.

        Returns:
            The host-side path to knot.conf.
        """
        if net is None:
            node = getattr(service, "node", None)
            if node is not None:
                net = getattr(node, "net", getattr(node, "lab", None))
        etc_dir = cls.host_dir(node_name, "etc", net=net)
        lib_dir = cls.host_dir(node_name, "lib", net=net)
        run_dir = cls.host_dir(node_name, "run", net=net)
        ensure_directory(etc_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
        ensure_directory(lib_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)
        ensure_directory(run_dir, cls.PERM_USER_RWX_GROUP_RX, silent=True)

        # Bind host node on service to ensure declared zone snapshot retains node
        service.bind_node(node_name)

        # Write each Primary Zone's initial zone file into the lib directory. A
        # Secondary has none: its contents arrive by Zone Transfer.
        for zone in service.primary_zones.values():
            zone.bind_host_node(node_name)
            if getattr(zone, "dnssec", False):
                # Keys generated before the Zone had a Node -- for a DS
                # record a parent Zone carries -- move into the Node's tree.
                cls.bind_zone_keys(zone, node_name, net=net)
                cls.ensure_zone_keys(zone, node_name=node_name, net=net)
            zone_filename = f"{zone.name}zone"
            zone_path = os.path.join(lib_dir, zone_filename)
            with open(zone_path, "w", encoding="utf-8") as f:
                f.write(cls.render_zone_file(zone))

        remotes, acls, zone_options = cls._render_replication(
            service, secondaries or {}
        )

        # Build knot.conf
        conf_lines = [
            "server:",
            f'    rundir: "{cls.VAR_RUN_KNOT_PATH}"',
            "    user: root:root",
            "    listen: [ 0.0.0.0@53, ::@53 ]",
            "",
            "log:",
            "  - target: syslog",
            "    any: info",
            f'  - target: "{cls.VAR_RUN_KNOT_PATH}/{cls.LOG_FILENAME}"',
            "    any: info",
            "",
            "database:",
            f'    storage: "{cls.VAR_LIB_KNOT_PATH}"',
            "",
            "mod-stats:",
            "  - id: default",
            "    query-type: on",
        ]

        has_signed = any(
            getattr(z, "dnssec", False) for z in service.primary_zones.values()
        )
        has_nsec = any(
            getattr(z, "dnssec", False) and not getattr(z, "nsec3", False)
            for z in service.primary_zones.values()
        )
        has_nsec3 = any(
            getattr(z, "dnssec", False) and getattr(z, "nsec3", False)
            for z in service.primary_zones.values()
        )

        # Knot replaces a salt whose length is not the policy's, and re-salts
        # after 30 days, so a declared salt needs a policy of its own length
        # that never rotates.
        salt_octets = {
            name: len(z.nsec3_salt) // 2
            for name, z in service.primary_zones.items()
            if getattr(z, "dnssec", False) and getattr(z, "nsec3", False)
            and z.nsec3_salt is not None
        }
        salt_policies = {
            name: (f"nsec3-salt-{octets}", octets) for name, octets in salt_octets.items()
        }

        if has_signed:
            conf_lines.extend(["", "policy:"])
            if has_nsec or not has_nsec3:
                conf_lines.extend([
                    "  - id: default",
                    "    algorithm: ecdsap256sha256",
                ])
            if has_nsec3:
                policy_id = "nsec3" if has_nsec else "default"
                conf_lines.extend([
                    f"  - id: {policy_id}",
                    "    algorithm: ecdsap256sha256",
                    "    nsec3: on",
                ])
            for policy_id, octets in sorted(set(salt_policies.values()), key=lambda policy: policy[1]):
                conf_lines.extend([
                    f"  - id: {policy_id}",
                    "    algorithm: ecdsap256sha256",
                    "    nsec3: on",
                    f"    nsec3-salt-length: {octets}",
                    "    nsec3-salt-lifetime: 0",
                ])

        if remotes:
            conf_lines.extend(["", "remote:", *remotes])
        if acls:
            conf_lines.extend(["", "acl:", *acls])

        conf_lines.extend([
            "",
            "template:",
            "  - id: default",
            f'    storage: "{cls.VAR_LIB_KNOT_PATH}"',
            '    file: "%s.zone"',
            # The journal is what makes an incremental Zone Transfer possible;
            # without it every transfer degrades to a full copy.
            "    journal-content: changes",
            "    global-module: mod-stats/default",
        ])

        if service.secondary_zones:
            conf_lines.extend([
                "",
                "  - id: secondary",
                f'    storage: "{cls.VAR_LIB_KNOT_PATH}"',
                # A Secondary owns no zone file: Knot acquires the contents and
                # keeps them in its journal. Knot permits global-module only in
                # the default template, so mod-stats is attached per-zone.
                "    zonefile-load: none",
                "    zonefile-sync: -1",
                "    journal-content: all",
            ])

        if service.zones:
            conf_lines.append("")
            conf_lines.append("zone:")
            for zone_name, hosted in service.zones.items():
                conf_lines.append(f"  - domain: {zone_name}")
                if isinstance(hosted, SecondaryZone):
                    conf_lines.append("    template: secondary")
                elif getattr(hosted, "dnssec", False):
                    if zone_name in salt_policies:
                        policy_name = salt_policies[zone_name][0]
                    elif getattr(hosted, "nsec3", False) and has_nsec:
                        policy_name = "nsec3"
                    else:
                        policy_name = "default"
                    conf_lines.append("    dnssec-signing: on")
                    conf_lines.append(f"    dnssec-policy: {policy_name}")
                conf_lines.extend(zone_options.get(zone_name, []))
                conf_lines.append("    module: mod-stats/default")

        conf_lines.append("")
        conf_path = os.path.join(etc_dir, cls.CONFIG_FILENAME)
        with open(conf_path, "w", encoding="utf-8") as f:
            f.write("\n".join(conf_lines))

        return conf_path

    @classmethod
    def start_dns_service(
        cls,
        node: Any,
        service: DNSService,
        net: Any = None,
        secondaries: Optional[Dict[str, List[Tuple[str, List[str]]]]] = None,
    ) -> None:
        """Bind directories, render config, and start knotd inside the Node namespace.

        Args:
            node: The serving Node.
            service: The DNSService to start.
            net: Optional network orchestrator reference.
            secondaries: Every declared Secondary in the Lab, keyed by apex.
        """
        if net is None:
            net = getattr(node, "net", getattr(node, "lab", None))
        cls.bind_service_directories(node)
        cls.render_config(node.name, service, secondaries, net=net)
        if not getattr(node, "params", {}).get("nameservers"):
            # knotd looks up its own hostname as it starts. Through the
            # host's nameservers, which a Lab usually cannot reach, that
            # lookup waits out a 5 s timeout per server; on localhost it
            # fails at once, and afterwards knotd answers it itself.
            resolv_conf = net.resolv_conf_file(node.name)
            ensure_directory(os.path.dirname(resolv_conf))
            with open(resolv_conf, "w", encoding="utf-8") as f:
                f.write("nameserver 127.0.0.1\n")

        quoted_binary = shlex.quote(cls.KNOT_BINARY)
        quoted_config = shlex.quote(f"{cls.ETC_KNOT_PATH}/{cls.CONFIG_FILENAME}")
        node.cmd(f"{quoted_binary} -c {quoted_config} -d")
        cls.await_ready(node, net=net)

    @classmethod
    def await_ready(cls, node: Any, net: Optional[Any] = None) -> None:
        """Block until the Node's Knot server answers on its control socket.

        ``start_dns_service`` returns only once this has passed, so a caller
        may read Records or stats the moment the Lab has started.

        Args:
            node: The serving Node.
            net: Optional Lab instance to resolve paths through.

        Raises:
            ConfigurationError: If the server did not answer within
                :attr:`READY_TIMEOUT` seconds.
        """
        if net is None:
            net = getattr(node, "net", getattr(node, "lab", None))
        client = KnotControlClient(cls.control_socket_host_path(node.name, net=net))
        if not wait_until(client.is_running, cls.READY_TIMEOUT):
            raise ConfigurationError(
                f"Node '{node.name}': {cls.KNOT_BINARY} did not answer on its "
                f"control socket within {cls.READY_TIMEOUT:.0f}s. "
                f"Check {cls.log_host_path(node.name, net=net)}."
            )

    @classmethod
    def stop_dns_service(cls, node: Any) -> None:
        """Stop knotd inside the Node namespace and restore declared zones.

        Args:
            node: The serving Node.
        """
        node.cmd(f"pkill -x {cls.KNOT_BINARY} || true")
        net = getattr(node, "net", getattr(node, "lab", None))
        shutil.rmtree(cls.host_dir(node.name, net=net), ignore_errors=True)
        for service in getattr(node, "services", []):
            if isinstance(service, DNSService):
                service.reset_zones()

    @classmethod
    def start_node_services(
        cls,
        node: Any,
        net: Any = None,
        secondaries: Optional[Dict[str, List[Tuple[str, List[str]]]]] = None,
    ) -> None:
        """Start every DNS Service attached to node.

        Args:
            node: The Node with DNS services.
            net: Optional network orchestrator reference.
            secondaries: Every declared Secondary in the Lab, keyed by apex.
        """
        for service in getattr(node, "services", []):
            if isinstance(service, DNSService):
                cls.start_dns_service(node, service, net, secondaries)

    @classmethod
    def stop_node_services(cls, node: Any) -> None:
        """Stop every DNS Service attached to node.

        Args:
            node: The Node with DNS services.
        """
        for service in getattr(node, "services", []):
            if isinstance(service, DNSService):
                cls.stop_dns_service(node)

    @staticmethod
    def _serves_primary(node: Any) -> bool:
        """Return whether a Node is Primary for any Zone.

        Args:
            node: The Node to inspect.

        Returns:
            True when the Node declares at least one Primary Zone.
        """
        return any(
            isinstance(service, DNSService) and service.primary_zones
            for service in getattr(node, "services", [])
        )

    @classmethod
    def nudge_secondaries(cls, nodes: List[Any], net: Optional[Any] = None) -> None:
        """Ask every Secondary to check its Primary once, without waiting.

        The first Zone Transfer would otherwise wait out the SOA ``retry``
        timer whenever routing had not converged by the time the Secondary
        started. The nudge is idempotent — Knot transfers only when the Serial
        has moved — and deliberately fire-and-forget: a Lab staging an
        unreachable Primary must still finish starting.

        Args:
            nodes: Nodes to nudge, typically every DNS Node in the Lab.
            net: Optional Lab instance to resolve paths through.
        """
        for node in nodes:
            net_to_use = net or getattr(node, "net", getattr(node, "lab", None))
            for service in getattr(node, "services", []):
                if not isinstance(service, DNSService) or not service.secondary_zones:
                    continue
                socket_path = cls.control_socket_host_path(node.name, net=net_to_use)
                for apex in service.secondary_zones:
                    try:
                        with KnotControlClient(socket_path) as client:
                            client.refresh_zone(apex)
                    except Exception as exc:
                        warn(
                            f"*** Warning: could not nudge secondary zone '{apex}' on "
                            f"'{node.name}': {exc}\n"
                        )

    @classmethod
    def start_all(cls, net: Any, nodes: List[Any]) -> None:
        """Start DNS services on every served Node in nodes.

        Every Node serving a Primary starts ahead of the purely replicating
        ones, so a Secondary has something to transfer from. A Node that is
        Primary for one apex and Secondary for another counts as a Primary
        here, which is what keeps the mutual-backup topology working; the
        Secondary half is covered by the nudge that follows once the whole
        Lab's DNS is up.

        Args:
            net: Network orchestrator instance.
            nodes: Nodes to inspect and start DNS services on.
        """
        targets = [
            n for n in nodes
            if any(isinstance(s, DNSService) for s in getattr(n, "services", []))
        ]
        if not targets:
            return

        secondaries = cls.collect_secondaries(targets)
        ordered = sorted(targets, key=lambda node: not cls._serves_primary(node))

        info("\n*** Starting DNS Services\n")
        for node in ordered:
            info(node.name + " ")
            cls.start_node_services(node, net, secondaries)
        info("\n")

        cls.nudge_secondaries(ordered, net=net)

    @classmethod
    def stop_all(cls, nodes: List[Any]) -> None:
        """Stop DNS services on every served Node in nodes.

        Args:
            nodes: Nodes to inspect and stop DNS services on.
        """
        targets = [
            n for n in nodes
            if any(isinstance(s, DNSService) for s in getattr(n, "services", []))
        ]
        if not targets:
            return

        info("\n*** Stopping DNS Services\n")
        for node in targets:
            info(node.name + " ")
            cls.stop_node_services(node)
        info("\n")

    @classmethod
    def ensure_available(cls, nodes: List[Any]) -> None:
        """Verify knotd is installed when any Node in nodes runs a DNS Service.

        A missing daemon is an environment fact, not a Lab-shape diagnostic,
        so it is checked here before DNSDiagnostics reads the Lab.

        Args:
            nodes: Nodes to inspect.

        Raises:
            ConfigurationError: If knotd is not found in PATH.
        """
        served = [
            n for n in nodes
            if any(isinstance(s, DNSService) for s in getattr(n, "services", []))
        ]
        if served and shutil.which(cls.KNOT_BINARY) is None:
            raise ConfigurationError(
                f"Node '{served[0].name}': Knot DNS binary '{cls.KNOT_BINARY}' "
                f"not found in PATH. Ensure Knot DNS is installed."
            )
