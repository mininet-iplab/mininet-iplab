"""Unit tests for mnIPLab's capability-based Link creation policy."""

import unittest
from unittest.mock import Mock, patch

from mininet.link import Link
from mininet.net import Mininet

from mniplab.container import ContainerHost, ContainerLink
from mniplab.errors import LinkError
from mniplab.tests.fakes import FakeNode
from mniplab.tests.labs import unstarted_lab


class AddLinkNamespacePolicyTests(unittest.TestCase):
    """mnIPLab.addLink selects a safe creation path from Node capability."""

    def setUp(self):
        """Build a Lab whose default Link class is Mininet's own."""
        self.lab = self.enterContext(unstarted_lab())
        self.lab.link = Link

    @staticmethod
    def _node(name, private_namespace_capable=False, in_namespace=True):
        """Return a minimal Node-shaped object for the addLink seam."""
        node = FakeNode(name)
        node.private_namespace_capable = private_namespace_capable
        node.inNamespace = in_namespace
        return node

    def _add_link(self, node1, node2, cls=None, **params):
        """Call addLink while isolating actual interface creation and provisioning."""
        link = Mock()
        with patch.object(Mininet, 'addLink', return_value=link) as parent_add_link:
            result = self.lab.addLink(node1, node2, cls=cls, **params)
        return result, parent_add_link

    def test_capable_endpoint_uses_non_fast_creation_path(self):
        """A capable endpoint forces a normal Link to be built host-side."""
        capable = self._node('h1', private_namespace_capable=True)
        ordinary = self._node('h2')

        result, parent_add_link = self._add_link(capable, ordinary, fast=True)

        self.assertIs(result, parent_add_link.return_value)
        self.assertFalse(parent_add_link.call_args.kwargs['fast'])

    def test_capability_on_second_endpoint_also_uses_non_fast_path(self):
        """The policy applies when only the second endpoint is capable."""
        ordinary = self._node('h1')
        capable = self._node('h2', private_namespace_capable=True)

        _, parent_add_link = self._add_link(ordinary, capable)

        self.assertFalse(parent_add_link.call_args.kwargs['fast'])

    def test_ordinary_nodes_keep_mininet_default_path(self):
        """Two ordinary Nodes do not receive a new fast-path override."""
        node1 = self._node('h1')
        node2 = self._node('h2')

        _, parent_add_link = self._add_link(node1, node2)

        self.assertNotIn('fast', parent_add_link.call_args.kwargs)

    def test_explicit_specialized_link_class_is_preserved(self):
        """Capability policy preserves an explicitly selected Link class."""
        class SpecializedLink(Link):
            """A caller-selected Mininet-compatible Link class."""

        capable = self._node('h1', private_namespace_capable=True)
        ordinary = self._node('h2')

        _, parent_add_link = self._add_link(capable, ordinary, cls=SpecializedLink)

        self.assertIs(parent_add_link.call_args.args[4], SpecializedLink)
        self.assertFalse(parent_add_link.call_args.kwargs['fast'])

    def test_container_link_keeps_specialized_host_side_path(self):
        """ContainerLink keeps its own host-side implementation and class selection."""
        container = object.__new__(ContainerHost)
        container.name = 'c1'
        ordinary = self._node('h1')

        _, parent_add_link = self._add_link(container, ordinary)

        self.assertIs(parent_add_link.call_args.args[4], ContainerLink)
        self.assertNotIn('fast', parent_add_link.call_args.kwargs)

    def test_configured_container_link_keeps_specialized_host_side_path(self):
        """A configured ContainerLink is not given the normal Link policy."""
        self.lab.link = ContainerLink
        capable = self._node('h1', private_namespace_capable=True)
        ordinary = self._node('h2')

        _, parent_add_link = self._add_link(capable, ordinary)

        self.assertIsNone(parent_add_link.call_args.args[4])
        self.assertNotIn('fast', parent_add_link.call_args.kwargs)

    def test_root_only_nodes_keep_explicit_fast_path(self):
        """Root-only endpoints are not changed by the capability policy."""
        root1 = self._node('nat1', in_namespace=False)
        root2 = self._node('s1', in_namespace=False)

        _, parent_add_link = self._add_link(root1, root2, fast=True)

        self.assertTrue(parent_add_link.call_args.kwargs['fast'])

    def test_host_side_failure_is_a_link_error_without_retry(self):
        """Host-side creation failures name the endpoints and are not retried."""
        capable = self._node('h1', private_namespace_capable=True)
        ordinary = self._node('h2')

        with patch.object(
            Mininet, 'addLink', side_effect=RuntimeError('veth failed')
        ) as parent_add_link:
            with self.assertRaises(LinkError) as context:
                self.lab.addLink(capable, ordinary)

        self.assertIn('h1', str(context.exception))
        self.assertIn('h2', str(context.exception))
        self.assertIn('veth failed', str(context.exception))
        self.assertEqual(parent_add_link.call_count, 1)

    def test_host_side_movement_exit_is_a_link_error(self):
        """Mininet's movement retry exit is translated to the link contract."""
        capable = self._node('h1', private_namespace_capable=True)
        ordinary = self._node('h2')

        with patch.object(Mininet, 'addLink', side_effect=SystemExit(1)):
            with self.assertRaises(LinkError):
                self.lab.addLink(capable, ordinary)


if __name__ == '__main__':
    unittest.main()
