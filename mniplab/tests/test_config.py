"""Unit tests for ConfigBuilder error handling."""

import os
import tempfile
import unittest
from unittest.mock import Mock, patch, mock_open

from mniplab.config_utils import ConfigBuilder
from mniplab.errors import ConfigurationError
from mniplab.frrouter.config import FRRConfigBuilder


class DummyConfigBuilder(ConfigBuilder):
    """Concrete subclass for testing abstract ConfigBuilder."""

    REQUIRED_VARS = {'name': str}

    def _get_template_dir(self):
        return '/tmp/templates'


class RenderTemplateErrorTests(unittest.TestCase):
    """Validate render_template converts specific errors to ConfigurationError."""

    def setUp(self):
        self.builder = DummyConfigBuilder()

    @patch('mniplab.config_utils.os.path.exists', return_value=True)
    @patch('builtins.open', mock_open(read_data='${undefined_var}'))
    def test_keyerror_raised_as_configuration_error(self, _mock_exists):
        """Template KeyError should be wrapped in ConfigurationError."""
        with self.assertRaises(ConfigurationError):
            self.builder.render_template('/tmp/templates/test.mako', {'name': 'r1'})

    @patch('mniplab.config_utils.os.path.exists', return_value=True)
    @patch('builtins.open', mock_open(read_data='${"bad" + 123}'))
    def test_typeerror_raised_as_configuration_error(self, _mock_exists):
        """Template TypeError should be wrapped in ConfigurationError."""
        with self.assertRaises(ConfigurationError):
            self.builder.render_template('/tmp/templates/test.mako', {'name': 'r1'})

    @patch('mniplab.config_utils.os.path.exists', return_value=True)
    @patch('builtins.open', mock_open(read_data='${int("abc")}'))
    def test_valueerror_raised_as_configuration_error(self, _mock_exists):
        """Template ValueError should be wrapped in ConfigurationError."""
        with self.assertRaises(ConfigurationError):
            self.builder.render_template('/tmp/templates/test.mako', {'name': 'r1'})

    @patch('mniplab.config_utils.os.path.exists', return_value=True)
    @patch('builtins.open', mock_open(read_data='${missing_func()}'))
    def test_nameerror_raised_as_configuration_error(self, _mock_exists):
        """Template NameError should be wrapped in ConfigurationError."""
        with self.assertRaises(ConfigurationError):
            self.builder.render_template('/tmp/templates/test.mako', {'name': 'r1'})


class FRRConfigBuilderDaemonsTests(unittest.TestCase):
    """Validate FRRConfigBuilder daemons configuration rendering."""

    def setUp(self):
        self.builder = FRRConfigBuilder()

    def test_setup_config_dir_renders_pim6d_enabled(self):
        """pim6d should render as 'yes' with daemon options when in daemons list."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.builder.setup_config_dir('r1', tmp_dir, daemons=['pim6d'])
            daemons_path = os.path.join(tmp_dir, 'daemons')
            self.assertTrue(os.path.exists(daemons_path))
            with open(daemons_path, 'r', encoding='utf-8') as f:
                content = f.read()

            self.assertIn('pim6d=yes', content)
            self.assertIn('pimd=no', content)
            self.assertIn('pim6d_options="  --daemon -A ::1"', content)

    def test_setup_config_dir_renders_pim6d_disabled_when_omitted(self):
        """pim6d should render as 'no' when omitted from daemons list."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            self.builder.setup_config_dir('r1', tmp_dir, daemons=['ospfd'])
            daemons_path = os.path.join(tmp_dir, 'daemons')
            self.assertTrue(os.path.exists(daemons_path))
            with open(daemons_path, 'r', encoding='utf-8') as f:
                content = f.read()

            self.assertIn('pim6d=no', content)
            self.assertIn('ospfd=yes', content)
            self.assertIn('pim6d_options="  --daemon -A ::1"', content)


if __name__ == '__main__':
    unittest.main()

