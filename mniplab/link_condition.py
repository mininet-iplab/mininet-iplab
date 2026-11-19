"""Link Condition — the administrative and quality state of a Link.

A Link Condition is what an Instructor changes to make a protocol react:
whether the Link is up or down, plus its delay, jitter, loss, bandwidth and
queueing discipline.  The store here is authoritative for *intent* — what was
asked for — which the emulator cannot report on its own: a traffic-control
query says what is configured now, never what the Instructor requested, and
after a failed apply or an external change the two differ.

Note the naming: this is deliberately not "link state", which names the
OSPF/IS-IS protocol family this project exists to teach.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterator, List, Optional, Tuple

from mniplab.errors import LinkError

# Impairment fields, as distinct from administrative status. Kept together so
# that "reset the impairment" and "list the impairment" cannot drift apart.
IMPAIRMENT_FIELDS = ('delay', 'jitter', 'loss', 'bandwidth', 'scheduler')

STATUS_UP = 'up'
STATUS_DOWN = 'down'
VALID_STATUSES = (STATUS_UP, STATUS_DOWN)

DEFAULT_SCHEDULER = 'hfsc'


class UnknownLinkError(LinkError):
    """Raised when an operation names a Link that was never registered."""


@dataclass
class LinkCondition:
    """The condition of one Link, identified by its two endpoints.

    Attributes:
        node1: First endpoint name, in canonical (sorted) order.
        node2: Second endpoint name, in canonical (sorted) order.
        status: 'up' or 'down' — the administrative state.
        delay: Delay with units, e.g. '10ms'.
        jitter: Jitter with units, e.g. '2ms'.
        loss: Loss percentage, e.g. '5%'.
        bandwidth: Bandwidth in Mbps.
        scheduler: Queueing discipline ('hfsc', 'htb', 'tbf').
    """

    node1: str
    node2: str
    status: str = STATUS_UP
    delay: Optional[str] = None
    jitter: Optional[str] = None
    loss: Optional[str] = None
    bandwidth: Optional[float] = None
    scheduler: str = DEFAULT_SCHEDULER

    @property
    def key(self) -> Tuple[str, str]:
        """Return this Link's canonical identity.

        Returns:
            The two endpoint names in sorted order.
        """
        return (self.node1, self.node2)

    @property
    def is_impaired(self) -> bool:
        """Return True if any quality impairment is set.

        Returns:
            True when delay, jitter, loss, or bandwidth is present.
        """
        return any(
            getattr(self, name) is not None
            for name in IMPAIRMENT_FIELDS
            if name != 'scheduler'
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for the topology payload.

        The administrative state is reported as ``status`` — the field name
        clients already use.

        Returns:
            A dict carrying the endpoints, the status, and any impairment set.
        """
        payload: Dict[str, Any] = {
            'source': self.node1,
            'target': self.node2,
            'status': self.status,
        }
        if self.delay:
            payload['delay'] = self.delay
        if self.jitter:
            payload['jitter'] = self.jitter
        if self.loss:
            payload['loss'] = self.loss
        if self.bandwidth:
            payload['bandwidth'] = self.bandwidth
            payload['scheduler'] = self.scheduler
        return payload


class LinkConditionStore:
    """Authoritative record of the Link Condition an Instructor asked for.

    Reads of an unregistered Link fall back to a default, because the topology
    view queries every Link and one unknown Link should not break the whole
    view.  *Mutations* of an unregistered Link raise: a typo in a Node name
    must not look like it worked.
    """

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._conditions: Dict[Tuple[str, str], LinkCondition] = {}

    @staticmethod
    def key(node1: str, node2: str) -> Tuple[str, str]:
        """Return the canonical identity for a Link between two Nodes.

        A Link has one identity regardless of which end is named first.

        Args:
            node1: One endpoint name.
            node2: The other endpoint name.

        Returns:
            The two names in sorted order.
        """
        first, second = sorted([node1, node2])
        return (first, second)

    def register(self, node1: str, node2: str, **params: Any) -> LinkCondition:
        """Record a newly created Link and any impairment it starts with.

        Args:
            node1: One endpoint name.
            node2: The other endpoint name.
            **params: Optional initial status and impairment values.

        Returns:
            The stored condition.
        """
        first, second = self.key(node1, node2)
        condition = LinkCondition(
            node1=first,
            node2=second,
            status=params.get('status', STATUS_UP),
            delay=params.get('delay'),
            jitter=params.get('jitter'),
            loss=params.get('loss'),
            bandwidth=params.get('bandwidth'),
            scheduler=params.get('scheduler') or DEFAULT_SCHEDULER,
        )
        self._conditions[condition.key] = condition
        return condition

    def knows(self, node1: str, node2: str) -> bool:
        """Return whether a Link has been registered.

        Args:
            node1: One endpoint name.
            node2: The other endpoint name.

        Returns:
            True if the Link is known to the store.
        """
        return self.key(node1, node2) in self._conditions

    def get(self, node1: str, node2: str) -> LinkCondition:
        """Return the recorded condition for a Link.

        Args:
            node1: One endpoint name.
            node2: The other endpoint name.

        Returns:
            The stored condition.

        Raises:
            UnknownLinkError: If the Link was never registered.
        """
        try:
            return self._conditions[self.key(node1, node2)]
        except KeyError:
            raise UnknownLinkError(
                f"No link condition recorded for {node1} <-> {node2}\n"
                f"Known links: {self._describe_known()}"
            ) from None

    def status(self, node1: str, node2: str) -> str:
        """Return a Link's administrative status, defaulting to 'up'.

        This is a read, so an unregistered Link answers with the default
        rather than raising — the topology view asks about every Link.

        Args:
            node1: One endpoint name.
            node2: The other endpoint name.

        Returns:
            'up' or 'down'.
        """
        condition = self._conditions.get(self.key(node1, node2))
        return condition.status if condition else STATUS_UP

    def set_status(self, node1: str, node2: str, status: str) -> LinkCondition:
        """Record a Link's new administrative status.

        Impairment is untouched, so a Link brought down and back up keeps the
        delay, jitter, loss and bandwidth it had beforehand.

        Args:
            node1: One endpoint name.
            node2: The other endpoint name.
            status: 'up' or 'down'.

        Returns:
            The updated condition.

        Raises:
            UnknownLinkError: If the Link was never registered.
            LinkError: If the status is not 'up' or 'down'.
        """
        if status not in VALID_STATUSES:
            raise LinkError(
                f"Invalid link condition status '{status}'\n"
                f"Valid values: {', '.join(VALID_STATUSES)}"
            )

        condition = self.get(node1, node2)
        updated = replace(condition, status=status)
        self._conditions[updated.key] = updated
        return updated

    def set_impairment(self, node1: str, node2: str, **params: Any) -> LinkCondition:
        """Record new impairment values for a Link.

        Only the fields present in ``params`` change; the administrative status
        is never touched here.

        Args:
            node1: One endpoint name.
            node2: The other endpoint name.
            **params: Any of delay, jitter, loss, bandwidth, scheduler.

        Returns:
            The updated condition.

        Raises:
            UnknownLinkError: If the Link was never registered.
            LinkError: If an unrecognised field is supplied.
        """
        unknown = set(params) - set(IMPAIRMENT_FIELDS)
        if unknown:
            raise LinkError(
                f"Unknown link condition field(s): {', '.join(sorted(unknown))}\n"
                f"Valid fields: {', '.join(IMPAIRMENT_FIELDS)}"
            )

        condition = self.get(node1, node2)
        updated = replace(condition, **params)
        self._conditions[updated.key] = updated
        return updated

    def all(self) -> List[LinkCondition]:
        """Return every recorded condition.

        Returns:
            Conditions in registration order.
        """
        return list(self._conditions.values())

    def __len__(self) -> int:
        """Return the number of registered Links."""
        return len(self._conditions)

    def __iter__(self) -> Iterator[LinkCondition]:
        """Iterate over registered conditions."""
        return iter(self._conditions.values())

    def _describe_known(self) -> str:
        """Return a readable list of known Links for error messages."""
        if not self._conditions:
            return '(none registered)'
        return ', '.join(f'{n1} <-> {n2}' for n1, n2 in sorted(self._conditions))


_DELAY_RE = re.compile(r'\bdelay\s+([\d.]+)(us|ms|s)?')
_LOSS_RE = re.compile(r'\bloss\s+([\d.]+)%')

_DURATION_SCALE = {'us': 0.001, 'ms': 1.0, 's': 1000.0}


def build_tc_show_command(intf_name: str) -> str:
    """Build the command that reads an interface's applied traffic control.

    Args:
        intf_name: Interface name.

    Returns:
        The shell command to run on the owning node.
    """
    return f'tc qdisc show dev {shlex.quote(intf_name)}'


def parse_duration_ms(value: Optional[str]) -> Optional[float]:
    """Parse a delay or jitter string into milliseconds.

    Args:
        value: A duration such as '10ms', '10.0ms', '1s', or a bare number.

    Returns:
        The duration in milliseconds, or None if it could not be read.
    """
    if value is None:
        return None
    match = re.fullmatch(r'\s*([\d.]+)\s*(us|ms|s)?\s*', str(value))
    if not match:
        return None
    amount, unit = match.groups()
    try:
        return float(amount) * _DURATION_SCALE.get(unit or 'ms', 1.0)
    except ValueError:
        return None


def parse_percentage(value: Optional[Any]) -> Optional[float]:
    """Parse a loss value into a percentage.

    Args:
        value: A percentage such as '5%', '5', or 5.0.

    Returns:
        The percentage as a float, or None if it could not be read.
    """
    if value is None:
        return None
    try:
        return float(str(value).replace('%', '').strip())
    except ValueError:
        return None


def parse_applied_impairment(tc_output: str) -> Dict[str, Optional[float]]:
    """Extract the impairment traffic control is actually enforcing.

    Args:
        tc_output: Output of a ``tc qdisc show`` command.

    Returns:
        A dict with 'delay' in milliseconds and 'loss' as a percentage; either
        may be None when the qdisc does not set it.
    """
    delay: Optional[float] = None
    loss: Optional[float] = None

    delay_match = _DELAY_RE.search(tc_output or '')
    if delay_match:
        amount, unit = delay_match.groups()
        try:
            delay = float(amount) * _DURATION_SCALE.get(unit or 'ms', 1.0)
        except ValueError:
            delay = None

    loss_match = _LOSS_RE.search(tc_output or '')
    if loss_match:
        loss = parse_percentage(loss_match.group(1))

    return {'delay': delay, 'loss': loss}


def impairment_matches(condition: LinkCondition,
                       applied: Dict[str, Optional[float]],
                       tolerance: float = 0.5) -> bool:
    """Return whether the applied impairment matches what was asked for.

    Compares numerically so that '10ms' and '10.0ms' agree, and allows a small
    tolerance because traffic control rounds to its own resolution.

    Args:
        condition: The recorded intent.
        applied: Impairment read back from traffic control.
        tolerance: Permitted absolute difference.

    Returns:
        True when intent and reality agree.
    """
    for field_name, parse in (('delay', parse_duration_ms), ('loss', parse_percentage)):
        intended = parse(getattr(condition, field_name))
        actual = applied.get(field_name)

        if intended is None and actual is None:
            continue
        if intended is None or actual is None:
            return False
        if abs(intended - actual) > tolerance:
            return False

    return True
