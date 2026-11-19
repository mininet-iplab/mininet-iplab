"""Keep a developer's env file out of the test suite.

The web server's ``Settings`` reads its env file at class definition, and
``mniplab/tests/conftest.py`` imports the package while it is itself still
loading, so a per-test patch comes too late: whatever ``.env`` holds in the
checkout -- a Multi-Lab scenario, a class roster -- would decide what the
suite sees as the defaults. This file loads first, imports nothing from the
package, and points every reader (``Settings`` and the lab entry points'
``load_lab_environment``) at ``os.devnull`` instead. Shell exports are left
alone: ``MNIPLAB_PRIVATE_NAMESPACES=0 python -m pytest -m integration`` is
a supported way to run the privileged tier.
"""

import os

os.environ["MNIPLAB_ENV_FILE"] = os.devnull
