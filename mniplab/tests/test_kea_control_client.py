"""Unit tests for mniplab.dhcp.control_client.KeaControlClient.

The real OS resource (an AF_UNIX socket) is stood in for by
:class:`mniplab.tests.fakes.FakeUnixSocket`, handed to the adapter through
its ``socket_factory`` seam, so these tests never touch a real socket or a
real kea-dhcp4/kea-dhcp6.
"""

import json
import unittest

from mniplab.dhcp.control_client import KeaControlClient
from mniplab.errors import ConfigurationError
from mniplab.tests.fakes import FakeUnixSocket

SOCKET_PATHS = {
    4: '/tmp/does-not-matter/kea4-ctrl-socket',
    6: '/tmp/does-not-matter/kea6-ctrl-socket',
}


def make_client(sock: FakeUnixSocket, family: int = 4) -> KeaControlClient:
    """Return a client whose socket factory hands out ``sock``."""
    return KeaControlClient(
        SOCKET_PATHS[family], family=family, socket_factory=lambda: sock
    )


class KeaControlClientSendCommandTests(unittest.TestCase):
    """send_command() writes one JSON request and parses the JSON response."""

    def test_sends_command_as_json(self):
        fake_socket = FakeUnixSocket(b'{"result": 0}')
        client = make_client(fake_socket)
        client.send_command('list-commands')

        sent = json.loads(fake_socket.sent.decode('utf-8'))
        self.assertEqual(sent, {"command": "list-commands"})

    def test_sends_arguments_and_service_when_given(self):
        fake_socket = FakeUnixSocket(b'{"result": 0}')
        client = make_client(fake_socket)
        client.send_command(
            'config-set', arguments={"Dhcp4": {}}, service=["dhcp4"]
        )

        sent = json.loads(fake_socket.sent.decode('utf-8'))
        self.assertEqual(sent["arguments"], {"Dhcp4": {}})
        self.assertEqual(sent["service"], ["dhcp4"])

    def test_returns_parsed_response(self):
        fake_socket = FakeUnixSocket(b'{"result": 0, "text": "ok"}')
        client = make_client(fake_socket)
        response = client.send_command('list-commands')

        self.assertEqual(response, {"result": 0, "text": "ok"})

    def test_assembles_response_split_across_chunks(self):
        fake_socket = FakeUnixSocket(b'{"result": 0, "text": "ok"}', chunk_size=5)
        client = make_client(fake_socket)
        response = client.send_command('list-commands')

        self.assertEqual(response, {"result": 0, "text": "ok"})

    def test_connects_to_the_socket_path_with_the_timeout_and_closes_after(self):
        fake_socket = FakeUnixSocket(b'{"result": 0}')
        client = make_client(fake_socket)
        client.send_command('list-commands')

        self.assertEqual(fake_socket.connected_to, SOCKET_PATHS[4])
        self.assertEqual(fake_socket.timeout, KeaControlClient.TIMEOUT_SECONDS)
        self.assertTrue(fake_socket.closed)

    def test_connect_failure_raises_configuration_error_naming_kea(self):
        client = make_client(
            FakeUnixSocket(connect_error=FileNotFoundError('no such file')),
        )
        with self.assertRaises(ConfigurationError) as ctx:
            client.send_command('list-commands')

        self.assertIn(client.socket_path, str(ctx.exception))
        self.assertIn('kea-dhcp4', str(ctx.exception))

    def test_send_failure_raises_configuration_error(self):
        fake_socket = FakeUnixSocket()
        fake_socket.sendall = lambda data: (_ for _ in ()).throw(
            BrokenPipeError('broken pipe')
        )
        client = make_client(fake_socket)
        with self.assertRaises(ConfigurationError):
            client.send_command('list-commands')

    def test_timeout_before_any_response_raises_configuration_error(self):
        fake_socket = FakeUnixSocket(timeout_on_recv=True)
        client = make_client(fake_socket)
        with self.assertRaises(ConfigurationError) as ctx:
            client.send_command('list-commands')

        self.assertIn('Timed out', str(ctx.exception))

    def test_connection_closed_with_no_data_raises_configuration_error(self):
        fake_socket = FakeUnixSocket()
        client = make_client(fake_socket)
        with self.assertRaises(ConfigurationError) as ctx:
            client.send_command('list-commands')

        self.assertIn('without sending a response', str(ctx.exception))

    def test_unparseable_response_raises_configuration_error(self):
        fake_socket = FakeUnixSocket(b'not json')
        client = make_client(fake_socket)
        with self.assertRaises(ConfigurationError) as ctx:
            client.send_command('list-commands')

        self.assertIn('not valid JSON', str(ctx.exception))


class KeaControlClientGetAllLeasesTests(unittest.TestCase):
    """get_all_leases() interprets lease4-get-all's result code."""

    def _respond(self, response_dict):
        return FakeUnixSocket(json.dumps(response_dict).encode('utf-8'))

    def test_returns_leases_on_success(self):
        leases = [
            {"ip-address": "10.50.0.100", "hw-address": "aa:bb:cc:dd:ee:ff"},
            {"ip-address": "10.50.0.101", "hw-address": "11:22:33:44:55:66"},
        ]
        fake_socket = self._respond({"result": 0, "arguments": {"leases": leases}})
        client = make_client(fake_socket)
        result = client.get_all_leases()

        self.assertEqual(result, leases)

    def test_sends_the_lease4_get_all_command(self):
        fake_socket = self._respond({"result": 3, "text": "0 leases found"})
        client = make_client(fake_socket)
        client.get_all_leases()

        sent = json.loads(fake_socket.sent.decode('utf-8'))
        self.assertEqual(sent["command"], "lease4-get-all")

    def test_empty_result_returns_no_leases_not_an_error(self):
        """result=3 (CONTROL_RESULT_EMPTY) means 'no Leases yet', not a failure."""
        fake_socket = self._respond({"result": 3, "text": "0 IPv4 lease(s) found."})
        client = make_client(fake_socket)
        result = client.get_all_leases()

        self.assertEqual(result, [])

    def test_command_not_supported_raises_configuration_error(self):
        """Reproduces a missing lease-commands hook."""
        fake_socket = self._respond(
            {"result": 2, "text": "'lease4-get-all' command not supported."}
        )
        client = make_client(fake_socket)
        with self.assertRaises(ConfigurationError) as ctx:
            client.get_all_leases()

        self.assertIn('command not supported', str(ctx.exception))

    def test_generic_error_result_raises_configuration_error(self):
        fake_socket = self._respond({"result": 1, "text": "something went wrong"})
        client = make_client(fake_socket)
        with self.assertRaises(ConfigurationError) as ctx:
            client.get_all_leases()

        self.assertIn('something went wrong', str(ctx.exception))


class KeaControlClientGetAllLeasesV6Tests(unittest.TestCase):
    """get_all_leases() with family=6 interprets lease6-get-all's result code."""

    def _respond(self, response_dict):
        return FakeUnixSocket(json.dumps(response_dict).encode('utf-8'))

    def test_server_name_is_kea_dhcp6(self):
        self.assertEqual(make_client(FakeUnixSocket(), family=6).server_name, 'kea-dhcp6')

    def test_sends_the_lease6_get_all_command(self):
        fake_socket = self._respond({"result": 3, "text": "0 IPv6 lease(s) found."})
        client = make_client(fake_socket, family=6)
        client.get_all_leases()

        sent = json.loads(fake_socket.sent.decode('utf-8'))
        self.assertEqual(sent["command"], "lease6-get-all")

    def test_returns_v6_leases_with_duid_and_lifetimes_on_success(self):
        leases = [
            {
                "cltt": 1788667183,
                "duid": "00:01:00:01:32:2f:93:c0:a6:9c:36:56:d7:d9",
                "iaid": 2234782639,
                "ip-address": "2001:db8:1::101",
                "preferred-lft": 4500,
                "state": 0,
                "subnet-id": 1,
                "type": "IA_NA",
                "valid-lft": 7200,
            }
        ]
        fake_socket = self._respond({"result": 0, "arguments": {"leases": leases}})
        client = make_client(fake_socket, family=6)
        result = client.get_all_leases()

        self.assertEqual(result, leases)

    def test_empty_result_returns_no_leases(self):
        fake_socket = self._respond({"result": 3, "text": "0 IPv6 lease(s) found."})
        client = make_client(fake_socket, family=6)
        result = client.get_all_leases()

        self.assertEqual(result, [])

    def test_command_not_supported_raises_configuration_error_naming_hook(self):
        fake_socket = self._respond(
            {"result": 2, "text": "'lease6-get-all' command not supported."}
        )
        client = make_client(fake_socket, family=6)
        with self.assertRaises(ConfigurationError) as ctx:
            client.get_all_leases()

        self.assertIn('command not supported', str(ctx.exception))
        self.assertIn('lease6-get-all', str(ctx.exception))

    def test_connect_failure_names_kea_dhcp6(self):
        client = make_client(
            FakeUnixSocket(connect_error=FileNotFoundError('no such file')), family=6
        )
        with self.assertRaises(ConfigurationError) as ctx:
            client.send_command('list-commands')

        self.assertIn('kea-dhcp6', str(ctx.exception))


class KeaControlClientConfigTests(unittest.TestCase):
    """test_config and set_config drive Kea's config validation and application."""

    def setUp(self):
        self.sample_config = {"Dhcp4": {"subnet4": []}}

    def _respond(self, response_dict):
        return FakeUnixSocket(json.dumps(response_dict).encode('utf-8'))

    def test_test_config_sends_config_test_command(self):
        fake_socket = self._respond({"result": 0, "text": "Configuration test successful."})
        client = make_client(fake_socket)
        result = client.test_config(self.sample_config)

        sent = json.loads(fake_socket.sent.decode('utf-8'))
        self.assertEqual(sent["command"], "config-test")
        self.assertEqual(sent["arguments"], self.sample_config)
        self.assertEqual(result["result"], 0)

    def test_test_config_raises_configuration_error_on_failure(self):
        fake_socket = self._respond({"result": 1, "text": "subnet configuration failed: bad pool"})
        client = make_client(fake_socket)
        with self.assertRaises(ConfigurationError) as ctx:
            client.test_config(self.sample_config)

        self.assertIn("Kea rejected configuration", str(ctx.exception))
        self.assertIn("bad pool", str(ctx.exception))

    def test_set_config_sends_config_set_command(self):
        fake_socket = self._respond({"result": 0, "text": "Configuration successful."})
        client = make_client(fake_socket)
        result = client.set_config(self.sample_config)

        sent = json.loads(fake_socket.sent.decode('utf-8'))
        self.assertEqual(sent["command"], "config-set")
        self.assertEqual(sent["arguments"], self.sample_config)
        self.assertEqual(result["result"], 0)

    def test_set_config_raises_configuration_error_on_failure(self):
        fake_socket = self._respond({"result": 1, "text": "subnet configuration failed: bad pool"})
        client = make_client(fake_socket)
        with self.assertRaises(ConfigurationError) as ctx:
            client.set_config(self.sample_config)

        self.assertIn("Kea rejected configuration", str(ctx.exception))
        self.assertIn("bad pool", str(ctx.exception))


if __name__ == '__main__':
    unittest.main()


class KeaControlClientIsRunningTests(unittest.TestCase):
    """is_running() is a status-get round-trip, never an exception."""

    def test_is_running_when_status_get_answers(self):
        fake_socket = FakeUnixSocket(b'{"result": 0, "arguments": {"pid": 42}}')
        client = make_client(fake_socket)

        self.assertTrue(client.is_running())
        self.assertEqual(
            json.loads(fake_socket.sent.decode('utf-8')), {"command": "status-get"}
        )

    def test_not_running_when_the_socket_is_unreachable(self):
        client = make_client(FakeUnixSocket(connect_error=FileNotFoundError()))

        self.assertFalse(client.is_running())

    def test_not_running_when_the_server_reports_an_error(self):
        client = make_client(FakeUnixSocket(b'{"result": 1, "text": "not ready"}'))

        self.assertFalse(client.is_running())

