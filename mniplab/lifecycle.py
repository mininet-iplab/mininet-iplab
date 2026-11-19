"""Node lifecycle registry for mnIPLab.

Replaces hard-coded ``isinstance()`` loops in :meth:`mnIPLab.start` and
:meth:`mnIPLab.stop` with a registration pattern.  Adding a new node category
no longer requires editing the network orchestrator.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Type, Union

from mininet.log import info


@dataclass
class LifecycleHandler:
    """A single entry in the lifecycle registry."""
    node_type: Union[Type, tuple]
    start_callback: Optional[Callable[[List[Any]], None]] = None
    stop_callback: Optional[Callable[[List[Any]], None]] = None
    cleanup_callback: Optional[Callable[[List[Any]], None]] = None
    post_ipv6_callback: Optional[Callable[[List[Any]], None]] = None


class NodeLifecycleRegistry:
    """Registry of node-type lifecycle callbacks.

    Each entry matches nodes via ``isinstance(node, handler.node_type)`` and
    invokes the corresponding callback during ``start()``, ``stop()``, or
    early ``cleanup()``.
    """

    def __init__(self) -> None:
        self._handlers: List[LifecycleHandler] = []

    def register(
        self,
        node_type: Union[Type, tuple],
        start_callback: Optional[Callable[[List[Any]], None]] = None,
        stop_callback: Optional[Callable[[List[Any]], None]] = None,
        cleanup_callback: Optional[Callable[[List[Any]], None]] = None,
        post_ipv6_callback: Optional[Callable[[List[Any]], None]] = None,
    ) -> None:
        """Register lifecycle callbacks for a node type.

        Args:
            node_type: A class or tuple of classes (same semantics as
                :func:`isinstance`).
            start_callback: Called with matching nodes during network start.
            stop_callback: Called with matching nodes during network stop.
            cleanup_callback: Called with matching nodes during early cleanup
                (before Mininet terminates nodes).
            post_ipv6_callback: Called with matching nodes after IPv6 address
                reconciliation during network start.
        """
        self._handlers.append(
            LifecycleHandler(
                node_type=node_type,
                start_callback=start_callback,
                stop_callback=stop_callback,
                cleanup_callback=cleanup_callback,
                post_ipv6_callback=post_ipv6_callback,
            )
        )

    @staticmethod
    def _grouped_action(label: str, nodes: List[Any],
                        action: Optional[Callable[[Any], None]]) -> None:
        """Print a Mininet-style grouped header and invoke *action* per node."""
        info(f'\n*** {label}\n')
        for node in nodes:
            info(node.name + ' ')
            if action:
                action(node)
        info('\n')

    def start_all(self, hosts: List[Any]) -> None:
        """Run all registered start callbacks."""
        for handler in self._handlers:
            nodes = [h for h in hosts if isinstance(h, handler.node_type)]
            if nodes and handler.start_callback:
                handler.start_callback(nodes)

    def start_post_ipv6_all(self, hosts: List[Any]) -> None:
        """Run all registered post-IPv6 start callbacks."""
        for handler in self._handlers:
            nodes = [h for h in hosts if isinstance(h, handler.node_type)]
            if nodes and handler.post_ipv6_callback:
                handler.post_ipv6_callback(nodes)

    def cleanup_all(self, hosts: List[Any]) -> None:
        """Run all registered cleanup callbacks."""
        for handler in self._handlers:
            nodes = [h for h in hosts if isinstance(h, handler.node_type)]
            if nodes and handler.cleanup_callback:
                handler.cleanup_callback(nodes)

    def stop_all(self, hosts: List[Any]) -> None:
        """Run all registered stop callbacks."""
        for handler in self._handlers:
            nodes = [h for h in hosts if isinstance(h, handler.node_type)]
            if nodes and handler.stop_callback:
                handler.stop_callback(nodes)
