"""RPKI CA Service support: Hosted CAs, ROAs and ASPAs, and the Krill-backed Service.

Krill runs in testbed mode on a Node, so each Autonomous System's Hosted CA
signs only for the prefixes the Lab gives it. A Validator on another Node
reads what these CAs sign and serves it to Routers over RTR.
"""

from mniplab.rpki.models import ASPA, HostedCA, ROA
from mniplab.rpki.service import RPKICAService, ValidatorService

__all__ = ["ASPA", "HostedCA", "ROA", "RPKICAService", "ValidatorService"]
