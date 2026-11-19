"""Unit tests for the `netns 1` fallback guard.

Mininet's makeIntfPair (mininet/util.py) computes ``netns = 1 if not node2``
and runs the veth creation through node1's shell. That command means "the
root network namespace" only while the shell lives in the root PID namespace;
a Private Namespace Node's shell resolves pid 1 to itself and the command
exits 0 while placing the interface in the wrong namespace. These tests pin
the guard that turns that silent wrong success into a loud, named failure —
and they are written so that deleting the guard turns this suite red.
"""

import os
import unittest
from unittest.mock import patch

from mininet import util as mininet_util

from mniplab.errors import LinkError, NamespaceError, NamespaceGuardError
from mniplab.namespace import (
    PRIVATE_NAMESPACES_ENV_VAR,
    guard_netns_fallback,
    install_netns_fallback_guard,
)
from mniplab.tests.fakes import FakeNode


class NetnsFallbackGuardTests(unittest.TestCase):
    """guard_netns_fallback fails closed on the ambiguous case only."""

    @staticmethod
    def _node(name, private_namespace_capable=True):
        node = FakeNode(name)
        node.private_namespace_capable = private_namespace_capable
        return node

    def _as_node_shell(self, name):
        """Pretend the current process is node ``name``'s Mininet shell."""
        argv = f'env\0PS1=\x7f\0bash\0--norc\0-is\0mininet:{name}\0'.encode()
        opener = lambda *a, **k: __import__('io').BytesIO(argv)
        return patch('mniplab.namespace.open', opener)

    def test_capable_caller_shell_fails_closed(self):
        """A capable Node's own shell reaching the fallback raises, naming the Node."""
        node = self._node('h1')
        with self._as_node_shell('h1'):
            with self.assertRaises(NamespaceGuardError) as context:
                guard_netns_fallback(node, "create veth pair (h1-eth0, h2-eth0)")

        message = str(context.exception)
        self.assertIn("'h1'", message)
        self.assertIn('netns 1', message)
        self.assertIn('h1-eth0', message)

    def test_error_is_distinguishable_from_an_ordinary_link_failure(self):
        """The guard's error is a NamespaceError, not a LinkError."""
        self.assertTrue(issubclass(NamespaceGuardError, NamespaceError))
        self.assertFalse(issubclass(NamespaceGuardError, LinkError))

    def test_host_side_call_with_no_caller_is_allowed(self):
        """Mininet's module-level calls pass no caller; they use the host's pid 1."""
        with self._as_node_shell('h1'):
            guard_netns_fallback(None, 'create veth pair (h1-eth0, h2-eth0)')

    def test_ordinary_node_caller_is_allowed(self):
        """A Node without the capability keeps the upstream fallback."""
        ordinary = self._node('h1', private_namespace_capable=False)
        with self._as_node_shell('h1'):
            guard_netns_fallback(ordinary, 'create veth pair (h1-eth0, h2-eth0)')

    def test_foreign_process_mentioning_a_capable_node_is_allowed(self):
        """Only the Node's own shell is ambiguous; a random host process is not."""
        node = self._node('h1')
        with self._as_node_shell('someone-else'):
            guard_netns_fallback(node, 'create veth pair (h1-eth0, h2-eth0)')

    def test_hatch_off_leaves_the_fallback_alone(self):
        """The guard keys off the capability helper, so the hatch silences it."""
        node = self._node('h1')
        with self._as_node_shell('h1'):
            with patch.dict(os.environ, {PRIVATE_NAMESPACES_ENV_VAR: '0'}):
                guard_netns_fallback(node, 'create veth pair (h1-eth0, h2-eth0)')


class InstalledGuardTests(unittest.TestCase):
    """The installed guard wraps Mininet's real fallback sites."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        install_netns_fallback_guard()

    @staticmethod
    def _capable_shell(name, pid=4242):
        node = FakeNode(name)
        node.private_namespace_capable = True
        node.inNamespace = True
        node.pid = pid
        argv = f'bash\0-is\0mininet:{name}\0'.encode()
        opener = lambda *a, **k: __import__('io').BytesIO(argv)
        return node, patch('mniplab.namespace.open', opener)

    def test_install_is_idempotent(self):
        """Reinstalling leaves the same wrapper objects in place."""
        guarded_pair = mininet_util.makeIntfPair
        guarded_move = mininet_util.moveIntfNoRetry
        install_netns_fallback_guard()
        self.assertIs(mininet_util.makeIntfPair, guarded_pair)
        self.assertIs(mininet_util.moveIntfNoRetry, guarded_move)

    def test_make_intf_pair_with_single_node_fails_closed(self):
        """The one-node makeIntfPair call — the ticket's case — raises."""
        node, as_shell = self._capable_shell('h1')
        with as_shell:
            with self.assertRaises(NamespaceGuardError):
                mininet_util.makeIntfPair('h1-eth0', 'h2-eth0', node1=node)

    def test_make_intf_pair_with_both_nodes_passes_through(self):
        """A normal two-node creation is untouched by the guard."""
        node, as_shell = self._capable_shell('h1')
        other = FakeNode('h2')
        other.inNamespace = True
        other.pid = 4343
        with patch.object(mininet_util, 'quietRun', return_value=''):
            with as_shell:
                mininet_util.makeIntfPair('h1-eth0', 'h2-eth0', node1=node, node2=other)

    def test_fast_path_link_creation_fails_closed_through_mininet_link(self):
        """The real fast-path call site — Link.makeIntfPair — is guarded too.

        mininet.link binds makeIntfPair at its own import time; patching only
        mininet.util would leave Link.__init__'s fast path unguarded.
        """
        import mininet.link as mininet_link

        node, as_shell = self._capable_shell('h1')
        with as_shell:
            with self.assertRaises(NamespaceGuardError):
                mininet_link.Link.makeIntfPair('h1-eth0', 'h2-eth0', node1=node)

    def test_move_intf_into_capable_node_from_its_own_shell_fails_closed(self):
        """A fast-path move into a capable Node raises instead of silently misplacing."""
        node, as_shell = self._capable_shell('h1')
        with as_shell:
            with self.assertRaises(NamespaceGuardError):
                mininet_util.moveIntfNoRetry('h1-eth0', node)

    def test_move_intf_host_side_is_untouched(self):
        """The host-side (non-fast) path keeps working: caller is the host."""
        node, _as_shell = self._capable_shell('h1')
        with patch.object(mininet_util, 'quietRun', return_value=''):
            self.assertTrue(mininet_util.moveIntfNoRetry('h1-eth0', node))

    def test_hatch_off_restores_upstream_behaviour(self):
        """With the hatch off, the installed wrappers pass everything through."""
        node, as_shell = self._capable_shell('h1')
        with patch.dict(os.environ, {PRIVATE_NAMESPACES_ENV_VAR: '0'}):
            with patch.object(mininet_util, 'quietRun', return_value=''):
                with as_shell:
                    mininet_util.makeIntfPair('h1-eth0', 'h2-eth0', node1=node)
                    self.assertTrue(mininet_util.moveIntfNoRetry('h1-eth0', node))


if __name__ == '__main__':
    unittest.main()
