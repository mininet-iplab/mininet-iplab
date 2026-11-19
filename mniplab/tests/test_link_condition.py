"""Unit tests for Link Condition — the administrative and quality state of a Link."""

import unittest
from unittest.mock import patch

from mniplab.errors import LinkError
from mniplab.link_condition import (
    LinkCondition,
    LinkConditionStore,
    UnknownLinkError,
)
from mniplab.net import mnIPLab
from mniplab.tests.labs import unstarted_lab


class LinkIdentityTests(unittest.TestCase):
    """A Link has one identity regardless of which end is named first."""

    def test_either_ordering_names_the_same_link(self):
        store = LinkConditionStore()
        store.register('r2', 'r1', delay='10ms')

        self.assertEqual(store.get('r1', 'r2').delay, '10ms')
        self.assertEqual(store.get('r2', 'r1').delay, '10ms')

    def test_endpoints_are_stored_in_canonical_order(self):
        store = LinkConditionStore()
        condition = store.register('r2', 'r1')

        self.assertEqual(condition.node1, 'r1')
        self.assertEqual(condition.node2, 'r2')
        self.assertEqual(condition.key, ('r1', 'r2'))

    def test_registering_twice_replaces_the_record(self):
        store = LinkConditionStore()
        store.register('r1', 'r2', delay='10ms')
        store.register('r1', 'r2', delay='20ms')

        self.assertEqual(len(store), 1)
        self.assertEqual(store.get('r1', 'r2').delay, '20ms')


class InitialConditionTests(unittest.TestCase):
    """A Link registers the condition it starts with."""

    def test_a_new_link_is_up_and_unimpaired(self):
        condition = LinkConditionStore().register('r1', 'r2')

        self.assertEqual(condition.status, 'up')
        self.assertFalse(condition.is_impaired)

    def test_initial_impairment_is_recorded(self):
        condition = LinkConditionStore().register(
            'r1', 'r2', delay='10ms', jitter='2ms', loss='5%', bandwidth=100
        )

        self.assertEqual(condition.delay, '10ms')
        self.assertEqual(condition.jitter, '2ms')
        self.assertEqual(condition.loss, '5%')
        self.assertEqual(condition.bandwidth, 100)
        self.assertTrue(condition.is_impaired)

    def test_scheduler_defaults_when_not_given(self):
        condition = LinkConditionStore().register('r1', 'r2')

        self.assertEqual(condition.scheduler, 'hfsc')


class StatusTests(unittest.TestCase):
    """Administrative status is a field of Link Condition."""

    def setUp(self):
        self.store = LinkConditionStore()
        self.store.register('r1', 'r2')

    def test_status_can_be_changed(self):
        self.store.set_status('r1', 'r2', 'down')

        self.assertEqual(self.store.status('r1', 'r2'), 'down')

    def test_status_change_works_in_either_direction(self):
        self.store.set_status('r2', 'r1', 'down')

        self.assertEqual(self.store.status('r1', 'r2'), 'down')

    def test_invalid_status_is_rejected(self):
        with self.assertRaises(LinkError) as ctx:
            self.store.set_status('r1', 'r2', 'flapping')

        self.assertIn('flapping', str(ctx.exception))

    def test_unknown_link_reads_as_up(self):
        self.assertEqual(self.store.status('r9', 'r8'), 'up')


class ImpairmentPersistenceTests(unittest.TestCase):
    """Impairment survives an administrative down/up cycle.

    An Instructor who sets delay, downs the Link to demonstrate reconvergence,
    then brings it back up still has that delay. A demonstration must not
    silently discard the conditions set up beforehand.
    """

    def test_impairment_survives_down_and_up(self):
        store = LinkConditionStore()
        store.register('r1', 'r2', delay='100ms', loss='5%', bandwidth=10)

        store.set_status('r1', 'r2', 'down')
        store.set_status('r1', 'r2', 'up')

        condition = store.get('r1', 'r2')
        self.assertEqual(condition.status, 'up')
        self.assertEqual(condition.delay, '100ms')
        self.assertEqual(condition.loss, '5%')
        self.assertEqual(condition.bandwidth, 10)

    def test_impairment_survives_while_down(self):
        store = LinkConditionStore()
        store.register('r1', 'r2', delay='100ms')

        store.set_status('r1', 'r2', 'down')

        self.assertEqual(store.get('r1', 'r2').delay, '100ms')

    def test_changing_impairment_leaves_status_alone(self):
        store = LinkConditionStore()
        store.register('r1', 'r2')
        store.set_status('r1', 'r2', 'down')

        store.set_impairment('r1', 'r2', delay='50ms')

        self.assertEqual(store.get('r1', 'r2').status, 'down')
        self.assertEqual(store.get('r1', 'r2').delay, '50ms')

    def test_impairment_updates_only_named_fields(self):
        store = LinkConditionStore()
        store.register('r1', 'r2', delay='10ms', loss='1%')

        store.set_impairment('r1', 'r2', delay='20ms')

        condition = store.get('r1', 'r2')
        self.assertEqual(condition.delay, '20ms')
        self.assertEqual(condition.loss, '1%')

    def test_unknown_impairment_field_is_rejected(self):
        store = LinkConditionStore()
        store.register('r1', 'r2')

        with self.assertRaises(LinkError) as ctx:
            store.set_impairment('r1', 'r2', mtu=1400)

        self.assertIn('mtu', str(ctx.exception))


class UnregisteredLinkTests(unittest.TestCase):
    """A typo in a Node name must not look like it worked."""

    def test_changing_status_of_an_unknown_link_raises(self):
        with self.assertRaises(UnknownLinkError):
            LinkConditionStore().set_status('r1', 'typo', 'down')

    def test_changing_impairment_of_an_unknown_link_raises(self):
        with self.assertRaises(UnknownLinkError):
            LinkConditionStore().set_impairment('r1', 'typo', delay='10ms')

    def test_getting_an_unknown_link_raises(self):
        with self.assertRaises(UnknownLinkError):
            LinkConditionStore().get('r1', 'typo')

    def test_the_error_lists_known_links(self):
        store = LinkConditionStore()
        store.register('r1', 'r2')

        with self.assertRaises(UnknownLinkError) as ctx:
            store.set_status('r1', 'typo', 'down')

        self.assertIn('r1 <-> r2', str(ctx.exception))

    def test_unknown_link_error_is_a_link_error(self):
        self.assertTrue(issubclass(UnknownLinkError, LinkError))


class SerializationTests(unittest.TestCase):
    """The topology payload keeps the field names clients already use."""

    def test_reports_endpoints_and_status(self):
        payload = LinkCondition('r1', 'r2').to_dict()

        self.assertEqual(payload['source'], 'r1')
        self.assertEqual(payload['target'], 'r2')
        self.assertEqual(payload['status'], 'up')

    def test_omits_impairment_that_is_not_set(self):
        payload = LinkCondition('r1', 'r2').to_dict()

        self.assertNotIn('delay', payload)
        self.assertNotIn('bandwidth', payload)

    def test_includes_impairment_that_is_set(self):
        payload = LinkCondition('r1', 'r2', delay='10ms', bandwidth=100).to_dict()

        self.assertEqual(payload['delay'], '10ms')
        self.assertEqual(payload['bandwidth'], 100)
        self.assertEqual(payload['scheduler'], 'hfsc')


class _StubIntf:
    """An interface that records ifconfig calls and can refuse them."""

    def __init__(self, name):
        self.name = name
        self.calls = []
        self.failure = ''

    def ifconfig(self, *args):
        self.calls.append(' '.join(args))
        return self.failure


class _StubNode:
    """A node that knows which interfaces connect it to another node."""

    def __init__(self, name):
        self.name = name
        self.connections = {}

    def connectionsTo(self, other):
        return self.connections.get(other.name, [])


class LabIntegrationTests(unittest.TestCase):
    """The Lab's public API routes through one authoritative record."""

    def _lab(self, connected=True, register=True):
        lab = self.enterContext(unstarted_lab())

        r1, r2 = _StubNode('r1'), _StubNode('r2')
        self.intf1, self.intf2 = _StubIntf('r1-eth0'), _StubIntf('r2-eth0')
        if connected:
            pair = [(self.intf1, self.intf2)]
            r1.connections['r2'] = pair
            r2.connections['r1'] = [(self.intf2, self.intf1)]

        lab.nameToNode = {'r1': r1, 'r2': r2}
        if register:
            lab.link_conditions.register('r1', 'r2')
        return lab

    def test_config_link_status_records_intent(self):
        lab = self._lab()

        lab.configLinkStatus('r1', 'r2', 'down')

        self.assertEqual(lab.get_link_condition('r1', 'r2'), 'down')

    def test_config_link_status_applies_to_both_interfaces(self):
        lab = self._lab()

        lab.configLinkStatus('r1', 'r2', 'down')

        self.assertEqual(self.intf1.calls, ['down'])
        self.assertEqual(self.intf2.calls, ['down'])

    def test_config_link_status_on_unknown_link_raises(self):
        lab = self._lab(register=False)

        with self.assertRaises(UnknownLinkError):
            lab.configLinkStatus('r1', 'r2', 'down')

    def test_unknown_node_raises(self):
        lab = self._lab()

        with self.assertRaises(LinkError) as ctx:
            lab.configLinkStatus('r1', 'typo', 'down')

        self.assertIn('typo', str(ctx.exception))

    def test_unconnected_nodes_raise(self):
        lab = self._lab(connected=False)

        with self.assertRaises(LinkError) as ctx:
            lab.configLinkStatus('r1', 'r2', 'down')

        self.assertIn('No link', str(ctx.exception))

    def test_invalid_status_raises(self):
        lab = self._lab()

        with self.assertRaises(LinkError):
            lab.configLinkStatus('r1', 'r2', 'flapping')

    def test_get_link_condition_defaults_to_up(self):
        self.assertEqual(self._lab(register=False).get_link_condition('r1', 'r2'), 'up')

    def test_get_link_condition_reads_either_direction(self):
        lab = self._lab()
        lab.configLinkStatus('r1', 'r2', 'down')

        self.assertEqual(lab.get_link_condition('r2', 'r1'), 'down')


class WriteThroughTests(unittest.TestCase):
    """A condition is recorded only once it has actually been applied.

    The failure this prevents: an Instructor sets a condition, the command
    fails, and the UI keeps reporting a value that was never in effect while
    the Learner measures something else entirely.
    """

    def _lab(self):
        lab = self.enterContext(unstarted_lab())

        r1, r2 = _StubNode('r1'), _StubNode('r2')
        self.intf1, self.intf2 = _StubIntf('r1-eth0'), _StubIntf('r2-eth0')
        r1.connections['r2'] = [(self.intf1, self.intf2)]
        lab.nameToNode = {'r1': r1, 'r2': r2}
        lab.link_conditions.register('r1', 'r2')
        return lab

    def test_failed_apply_raises(self):
        lab = self._lab()
        self.intf1.failure = 'SIOCSIFFLAGS: Operation not permitted'

        with self.assertRaises(LinkError) as ctx:
            lab.configLinkStatus('r1', 'r2', 'down')

        self.assertIn('r1-eth0', str(ctx.exception))
        self.assertIn('Operation not permitted', str(ctx.exception))

    def test_failed_apply_records_nothing(self):
        lab = self._lab()
        self.intf1.failure = 'SIOCSIFFLAGS: Operation not permitted'

        with self.assertRaises(LinkError):
            lab.configLinkStatus('r1', 'r2', 'down')

        self.assertEqual(lab.get_link_condition('r1', 'r2'), 'up')

    def test_failure_on_the_far_interface_also_records_nothing(self):
        lab = self._lab()
        self.intf2.failure = 'SIOCSIFFLAGS: Operation not permitted'

        with self.assertRaises(LinkError):
            lab.configLinkStatus('r1', 'r2', 'down')

        self.assertEqual(lab.get_link_condition('r1', 'r2'), 'up')

    def test_previous_condition_survives_a_failed_change(self):
        lab = self._lab()
        lab.configLinkStatus('r1', 'r2', 'down')
        self.intf1.failure = 'SIOCSIFFLAGS: Operation not permitted'

        with self.assertRaises(LinkError):
            lab.configLinkStatus('r1', 'r2', 'up')

        self.assertEqual(lab.get_link_condition('r1', 'r2'), 'down')

    def test_unregistered_link_is_rejected_before_anything_is_applied(self):
        lab = self._lab()
        lab.link_conditions = LinkConditionStore()

        with self.assertRaises(UnknownLinkError):
            lab.configLinkStatus('r1', 'r2', 'down')

        self.assertEqual(self.intf1.calls, [])
        self.assertEqual(self.intf2.calls, [])


class InitialImpairmentCollectionTests(unittest.TestCase):
    """Link creation records impairment declared at either level."""

    def test_collects_link_level_parameters(self):
        collected = mnIPLab._initial_impairment(
            {'delay': '10ms', 'bw': 100}, {}, {}
        )

        self.assertEqual(collected['delay'], '10ms')
        self.assertEqual(collected['bandwidth'], 100)

    def test_collects_per_interface_parameters(self):
        collected = mnIPLab._initial_impairment({}, {'loss': '5%'}, {'jitter': '2ms'})

        self.assertEqual(collected['loss'], '5%')
        self.assertEqual(collected['jitter'], '2ms')

    def test_per_interface_values_win_over_link_level(self):
        collected = mnIPLab._initial_impairment(
            {'delay': '10ms'}, {'delay': '50ms'}, {}
        )

        self.assertEqual(collected['delay'], '50ms')

    def test_no_impairment_collects_nothing(self):
        self.assertEqual(mnIPLab._initial_impairment({}, {}, {}), {})

    def test_ignores_unrelated_link_parameters(self):
        collected = mnIPLab._initial_impairment({'ip6': '2001:db8::1/64'}, {}, {})

        self.assertEqual(collected, {})


class _StubTCIntf:
    """A traffic-control interface that can report a tc failure."""

    def __init__(self, name):
        self.name = name
        self.applied = []
        self.tc_failure = None

    def config(self, **params):
        self.applied.append(params)
        return {'tcoutputs': [self.tc_failure] if self.tc_failure else ['', '']}


class _StubLink:
    def __init__(self, intf1, intf2):
        self.intf1 = intf1
        self.intf2 = intf2


class ImpairmentWriteThroughTests(unittest.TestCase):
    """Impairment is recorded only once traffic control accepted it."""

    def _lab(self):
        lab = self.enterContext(unstarted_lab())
        lab.link_conditions.register('r1', 'r2')

        n1, n2 = _StubNode('r1'), _StubNode('r2')
        self.intf1 = _StubTCIntf('r1-eth0')
        self.intf2 = _StubTCIntf('r2-eth0')
        self.intf1.node, self.intf2.node = n1, n2
        lab.links = [_StubLink(self.intf1, self.intf2)]
        lab.nameToNode = {'r1': n1, 'r2': n2}
        return lab

    def _configure(self, lab, **kwargs):
        from mniplab.netutils import NetworkTestService
        return NetworkTestService.configure_link(lab, 'r1', 'r2', **kwargs)

    def test_successful_change_is_recorded(self):
        lab = self._lab()

        self._configure(lab, delay='10ms', loss='5')

        condition = lab.link_conditions.get('r1', 'r2')
        self.assertEqual(condition.delay, '10ms')
        self.assertEqual(condition.loss, '5%')

    def test_bandwidth_and_scheduler_are_recorded(self):
        lab = self._lab()

        self._configure(lab, bandwidth='100', scheduler='htb')

        condition = lab.link_conditions.get('r1', 'r2')
        self.assertEqual(condition.bandwidth, 100.0)
        self.assertEqual(condition.scheduler, 'htb')

    def test_failed_apply_raises(self):
        lab = self._lab()
        self.intf1.tc_failure = 'RTNETLINK answers: Invalid argument'

        with self.assertRaises(ValueError) as ctx:
            self._configure(lab, delay='10ms')

        self.assertIn('Invalid argument', str(ctx.exception))

    def test_failed_apply_records_nothing(self):
        lab = self._lab()
        self.intf1.tc_failure = 'RTNETLINK answers: Invalid argument'

        with self.assertRaises(ValueError):
            self._configure(lab, delay='10ms')

        self.assertIsNone(lab.link_conditions.get('r1', 'r2').delay)

    def test_previous_impairment_survives_a_failed_change(self):
        lab = self._lab()
        self._configure(lab, delay='10ms')
        self.intf2.tc_failure = 'RTNETLINK answers: Invalid argument'

        with self.assertRaises(ValueError):
            self._configure(lab, delay='50ms')

        self.assertEqual(lab.link_conditions.get('r1', 'r2').delay, '10ms')

    def test_impairment_does_not_disturb_status(self):
        lab = self._lab()
        lab.link_conditions.set_status('r1', 'r2', 'down')

        self._configure(lab, delay='10ms')

        self.assertEqual(lab.link_conditions.get('r1', 'r2').status, 'down')


class ImpairmentParsingTests(unittest.TestCase):
    """Applied impairment is read from traffic control output."""

    def test_reads_delay_and_loss_from_netem(self):
        from mniplab.link_condition import parse_applied_impairment
        applied = parse_applied_impairment(
            'qdisc netem 10: root refcnt 2 limit 1000 delay 10.0ms loss 5%'
        )
        self.assertEqual(applied['delay'], 10.0)
        self.assertEqual(applied['loss'], 5.0)

    def test_reports_none_for_an_unimpaired_qdisc(self):
        from mniplab.link_condition import parse_applied_impairment
        applied = parse_applied_impairment('qdisc noqueue 0: root refcnt 2')
        self.assertIsNone(applied['delay'])
        self.assertIsNone(applied['loss'])

    def test_handles_seconds_and_microseconds(self):
        from mniplab.link_condition import parse_applied_impairment
        self.assertEqual(parse_applied_impairment('delay 1s')['delay'], 1000.0)
        self.assertEqual(parse_applied_impairment('delay 500us')['delay'], 0.5)

    def test_handles_empty_output(self):
        from mniplab.link_condition import parse_applied_impairment
        self.assertEqual(parse_applied_impairment(''), {'delay': None, 'loss': None})

    def test_duration_parsing_accepts_written_forms(self):
        from mniplab.link_condition import parse_duration_ms
        self.assertEqual(parse_duration_ms('10ms'), 10.0)
        self.assertEqual(parse_duration_ms('10.0ms'), 10.0)
        self.assertEqual(parse_duration_ms('10'), 10.0)
        self.assertIsNone(parse_duration_ms(None))
        self.assertIsNone(parse_duration_ms('soon'))


class ImpairmentComparisonTests(unittest.TestCase):
    """Intent and reality are compared numerically, not textually."""

    def test_equivalent_spellings_agree(self):
        from mniplab.link_condition import impairment_matches
        condition = LinkCondition('r1', 'r2', delay='10ms')
        self.assertTrue(impairment_matches(condition, {'delay': 10.0, 'loss': None}))

    def test_unimpaired_link_matches_empty_qdisc(self):
        from mniplab.link_condition import impairment_matches
        condition = LinkCondition('r1', 'r2')
        self.assertTrue(impairment_matches(condition, {'delay': None, 'loss': None}))

    def test_different_delay_is_drift(self):
        from mniplab.link_condition import impairment_matches
        condition = LinkCondition('r1', 'r2', delay='10ms')
        self.assertFalse(impairment_matches(condition, {'delay': 50.0, 'loss': None}))

    def test_missing_applied_delay_is_drift(self):
        from mniplab.link_condition import impairment_matches
        condition = LinkCondition('r1', 'r2', delay='10ms')
        self.assertFalse(impairment_matches(condition, {'delay': None, 'loss': None}))

    def test_unrequested_applied_delay_is_drift(self):
        from mniplab.link_condition import impairment_matches
        condition = LinkCondition('r1', 'r2')
        self.assertFalse(impairment_matches(condition, {'delay': 10.0, 'loss': None}))

    def test_small_rounding_is_tolerated(self):
        from mniplab.link_condition import impairment_matches
        condition = LinkCondition('r1', 'r2', delay='10ms')
        self.assertTrue(impairment_matches(condition, {'delay': 10.2, 'loss': None}))


class DriftReportingTests(unittest.TestCase):
    """The Lab reports links whose applied condition diverged from intent."""

    def _lab(self, tc_output, delay=None):
        from mniplab.tests.fakes import FakeNode

        lab = self.enterContext(unstarted_lab())
        lab.link_conditions.register('r1', 'r2', delay=delay)

        n1, n2 = FakeNode('r1'), FakeNode('r2')
        n1.recorder.respond_to('tc qdisc show', tc_output)
        intf1, intf2 = _StubIntf('r1-eth0'), _StubIntf('r2-eth0')
        intf1.node, intf2.node = n1, n2
        lab.links = [_StubLink(intf1, intf2)]
        self.node = n1
        return lab

    def test_healthy_lab_reports_no_drift(self):
        lab = self._lab('qdisc netem 10: root limit 1000 delay 10.0ms', delay='10ms')
        self.assertEqual(lab.link_condition_drift(), [])

    def test_externally_changed_condition_is_drift(self):
        lab = self._lab('qdisc netem 10: root limit 1000 delay 90.0ms', delay='10ms')

        drift = lab.link_condition_drift()

        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]['source'], 'r1')
        self.assertEqual(drift[0]['target'], 'r2')
        self.assertEqual(drift[0]['intended']['delay'], '10ms')
        self.assertEqual(drift[0]['applied']['delay'], 90.0)

    def test_cleared_qdisc_is_drift(self):
        lab = self._lab('qdisc noqueue 0: root refcnt 2', delay='10ms')
        self.assertEqual(len(lab.link_condition_drift()), 1)

    def test_unimpaired_link_reports_no_drift(self):
        lab = self._lab('qdisc noqueue 0: root refcnt 2')
        self.assertEqual(lab.link_condition_drift(), [])

    def test_drift_reads_traffic_control_not_the_store(self):
        lab = self._lab('qdisc netem 10: root delay 10.0ms', delay='10ms')
        lab.link_condition_drift()
        self.assertTrue(self.node.recorder.issued_any('tc qdisc show'))

    def test_unregistered_links_are_skipped(self):
        lab = self._lab('qdisc netem 10: root delay 90.0ms', delay='10ms')
        from mniplab.link_condition import LinkConditionStore
        lab.link_conditions = LinkConditionStore()

        self.assertEqual(lab.link_condition_drift(), [])
