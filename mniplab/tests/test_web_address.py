"""Which address the web server binds: a flag, else MNIPLAB_HOST/PORT."""

import sys
import unittest
from unittest.mock import patch

from mniplab.__main__ import main
from mniplab.webserver import config, registry


class _Bound(Exception):
    """Raised in place of binding, carrying the address asked for."""


def _refuse_bind(_config, host, port):
    raise _Bound(host, port)


class ServeAddressTests(unittest.TestCase):
    """`python -m mniplab serve` binds where the env file says unless told."""

    def _serve(self, *flags: str) -> tuple:
        self.addCleanup(config.settings.use_login_store, config.settings.login_store)
        with patch.object(config.settings, 'host', '127.0.0.9'), \
                patch.object(config.settings, 'port', 9123), \
                patch('mniplab.webserver._bind_server_socket', side_effect=_refuse_bind), \
                patch('mniplab.logging.setup_logging'), \
                patch.object(registry, 'settings_store', None), \
                patch('mniplab.webserver.SettingsStore.load'), \
                patch.object(sys, 'argv', ['mniplab', 'serve', *flags]):
            with self.assertRaises(_Bound) as caught:
                main()
        return caught.exception.args

    def test_env_file_address_without_flags(self):
        self.assertEqual(self._serve(), ('127.0.0.9', 9123))

    def test_flags_override_env_file(self):
        self.assertEqual(
            self._serve('--web-host', '127.0.0.1', '--web-port', '8800'),
            ('127.0.0.1', 8800),
        )


if __name__ == '__main__':
    unittest.main()
