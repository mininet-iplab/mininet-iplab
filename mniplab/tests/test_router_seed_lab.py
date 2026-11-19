"""A Router starts from a Seed that holds only ``frr.conf``.

Everything else FRR needs is the tool's: rendered from the Router's declared
Protocols on every start. Before, a Seed folder without ``daemons`` failed
the start (``/etc/frr/daemons does not exist``), and a ``daemons`` edited by
hand quietly ran daemons the Router never declared.
"""

import os
import tempfile

from mniplab.tests.labs import LabCase


class RouterSeedTests(LabCase):
    """Daemons follow the Protocols, whatever the Seed folder holds."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.seed_template = os.path.join(tmp.name, '%(name)s')
        self.seed_dir = os.path.join(tmp.name, 'r1')
        os.makedirs(self.seed_dir)
        with open(os.path.join(self.seed_dir, 'frr.conf'), 'w') as f:
            f.write('router ospf\n ospf router-id 1.1.1.1\n')

    def _running_daemons(self) -> str:
        net = self.lab()
        r1 = net.addRouter('r1', proto='OSPF', frr_dir=self.seed_template)
        self.start(net)
        return r1.cmd('vtysh -c "show daemons"')

    def test_a_seed_holding_only_frr_conf_starts_the_declared_daemons(self) -> None:
        daemons = self._running_daemons()

        self.assertIn('ospfd', daemons)
        self.assertNotIn('bfdd', daemons)
