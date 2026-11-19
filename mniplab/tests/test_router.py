"""Unit tests for FRRouter file I/O error handling."""

import unittest
from unittest.mock import Mock, patch

from mniplab.router import FRRouter


class RouterFileIOTests(unittest.TestCase):
    """Validate FRRouter file operations handle OSError gracefully."""

    def _make_router(self, frr_dir='/tmp/frr-config/r1'):
        """Create a bare FRRouter instance with mocked dependencies."""
        router = object.__new__(FRRouter)
        router.name = 'r1'
        router.privateDirs = [('/etc/frr', frr_dir)]
        return router

    def test_read_frr_file_returns_none_on_oserror(self):
        """_read_frr_file should return None when open raises OSError."""
        router = self._make_router()
        with patch('mniplab.router.os.path.exists', return_value=True):
            with patch('builtins.open', side_effect=PermissionError("denied")):
                result = router._read_frr_file('frr.conf')
        self.assertIsNone(result)

    def test_write_frr_file_returns_false_on_oserror(self):
        """_write_frr_file should return False when open raises OSError."""
        router = self._make_router()
        with patch('builtins.open', side_effect=IOError("disk full")):
            result = router._write_frr_file('daemons', 'zebra=yes')
        self.assertFalse(result)

    def test_append_to_frr_file_returns_false_on_oserror(self):
        """_append_to_frr_file should return False when open raises OSError."""
        router = self._make_router()
        with patch('mniplab.router.os.path.exists', return_value=True):
            with patch('builtins.open', side_effect=IOError("read-only")):
                result = router._append_to_frr_file('frr.conf', 'new line')
        self.assertFalse(result)

    def test_read_frr_file_returns_none_when_frr_dir_unset(self):
        """_read_frr_file should short-circuit when no FRR dir is mapped."""
        router = self._make_router(frr_dir=None)
        router.privateDirs = []
        result = router._read_frr_file('frr.conf')
        self.assertIsNone(result)


class EnsureFRRAvailableTests(unittest.TestCase):
    """Validate ensure_frr_available preflight checks and bypass seam."""

    def test_raises_frr_error_when_script_missing_and_unbypassed(self) -> None:
        """Without bypass, ensure_frr_available must raise FRRError when script is missing."""
        from mniplab.errors import FRRError
        from mniplab.router import ensure_frr_available

        with patch.dict('os.environ', {}, clear=True):
            with patch('mniplab.router.os.path.exists', return_value=False):
                with self.assertRaises(FRRError):
                    ensure_frr_available()

    def test_bypasses_check_when_skip_flag_set(self) -> None:
        """When MNIPLAB_SKIP_DAEMON_PROBES=1, ensure_frr_available returns cleanly."""
        from mniplab.router import ensure_frr_available

        with patch.dict('os.environ', {'MNIPLAB_SKIP_DAEMON_PROBES': '1'}):
            with patch('mniplab.router.os.path.exists', return_value=False):
                # Should not raise even though script does not exist
                ensure_frr_available()


if __name__ == '__main__':
    unittest.main()
