"""Unit tests for mniplab.lifecycle — node lifecycle registry."""

import unittest
from unittest.mock import Mock

from mniplab.lifecycle import LifecycleHandler, NodeLifecycleRegistry


class NodeLifecycleRegistryTests(unittest.TestCase):
    """NodeLifecycleRegistry dispatches start/stop/cleanup by node type."""

    def _make_nodes(self):
        """Return mixed node mocks with names."""
        host = Mock()
        host.name = 'h1'
        router = Mock()
        router.name = 'r1'
        switch = Mock()
        switch.name = 's1'
        return host, router, switch

    def test_register_adds_handler(self):
        registry = NodeLifecycleRegistry()
        callback = Mock()
        registry.register(str, start_callback=callback)
        self.assertEqual(len(registry._handlers), 1)
        self.assertIs(registry._handlers[0].start_callback, callback)

    def test_start_all_filters_by_type(self):
        registry = NodeLifecycleRegistry()
        router_callback = Mock()
        host_callback = Mock()

        host, router, switch = self._make_nodes()
        nodes = [host, router, switch]

        registry.register(type(router), start_callback=router_callback)
        registry.register(type(host), start_callback=host_callback)

        registry.start_all(nodes)

        router_callback.assert_called_once_with([router])
        host_callback.assert_called_once_with([host])

    def test_start_post_ipv6_all_filters_by_type(self):
        registry = NodeLifecycleRegistry()
        relay_callback = Mock()

        host, router, switch = self._make_nodes()
        nodes = [host, router, switch]

        registry.register(type(router), post_ipv6_callback=relay_callback)
        registry.start_post_ipv6_all(nodes)

        relay_callback.assert_called_once_with([router])

    def test_stop_all_filters_by_type(self):
        registry = NodeLifecycleRegistry()
        stop_cb = Mock()
        host, router, _ = self._make_nodes()

        registry.register(type(router), stop_callback=stop_cb)
        registry.stop_all([host, router])

        stop_cb.assert_called_once_with([router])

    def test_cleanup_all_filters_by_type(self):
        registry = NodeLifecycleRegistry()
        cleanup_cb = Mock()
        host, router, _ = self._make_nodes()

        registry.register(type(router), cleanup_callback=cleanup_cb)
        registry.cleanup_all([host, router])

        cleanup_cb.assert_called_once_with([router])

    def test_no_callback_skips_invocation(self):
        registry = NodeLifecycleRegistry()
        host, _, _ = self._make_nodes()

        # Register with only start_callback
        registry.register(type(host), start_callback=Mock())
        # stop_all should not crash even though stop_callback is None
        registry.stop_all([host])
        registry.cleanup_all([host])

    def test_tuple_node_type_matches_subclasses(self):
        """Handler with tuple node_type should match any of the types."""
        registry = NodeLifecycleRegistry()
        callback = Mock()

        host, router, switch = self._make_nodes()
        nodes = [host, router, switch]

        registry.register((type(host), type(router)), start_callback=callback)
        registry.start_all(nodes)

        # callback called once with host+router, not with switch
        self.assertEqual(callback.call_count, 1)
        called_with = callback.call_args[0][0]
        self.assertIn(host, called_with)
        self.assertIn(router, called_with)
        self.assertNotIn(switch, called_with)

    def test_empty_node_list_no_crash(self):
        registry = NodeLifecycleRegistry()
        callback = Mock()
        registry.register(str, start_callback=callback)
        registry.start_all([])
        callback.assert_not_called()

    def test_multiple_handlers_same_type(self):
        registry = NodeLifecycleRegistry()
        cb1 = Mock()
        cb2 = Mock()
        host, _, _ = self._make_nodes()

        registry.register(type(host), start_callback=cb1)
        registry.register(type(host), start_callback=cb2)
        registry.start_all([host])

        cb1.assert_called_once_with([host])
        cb2.assert_called_once_with([host])


class LifecycleHandlerDataclassTests(unittest.TestCase):
    """LifecycleHandler stores type and optional callbacks."""

    def test_defaults(self):
        h = LifecycleHandler(node_type=str)
        self.assertIsNone(h.start_callback)
        self.assertIsNone(h.stop_callback)
        self.assertIsNone(h.cleanup_callback)
        self.assertIsNone(h.post_ipv6_callback)

    def test_all_callbacks(self):
        start = Mock()
        stop = Mock()
        cleanup = Mock()
        post_ipv6 = Mock()
        h = LifecycleHandler(node_type=int, start_callback=start,
                             stop_callback=stop, cleanup_callback=cleanup,
                             post_ipv6_callback=post_ipv6)
        self.assertIs(h.start_callback, start)
        self.assertIs(h.stop_callback, stop)
        self.assertIs(h.cleanup_callback, cleanup)
        self.assertIs(h.post_ipv6_callback, post_ipv6)


if __name__ == '__main__':
    unittest.main()
