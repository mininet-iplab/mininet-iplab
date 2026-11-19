"""Parser for Unbound's ``dump_cache`` text into Cache Entries.

Unbound dumps two caches in one stream. The RRset cache holds the records it
learned during an Iterative Walk, each preceded by a ``;rrset`` header and
written with its *remaining* TTL. The message cache holds one line per
answered question plus references into the RRset cache, and it is the only
place a negative answer exists at all: an ``NXDOMAIN`` is a message with
rcode 3 and no answer section, a ``NODATA`` is rcode 0 with no answer
section, and in both cases the SOA that bounds the entry's lifetime (RFC
2308) is a reference into the RRset cache rather than data of its own. That
correlation is what this module does, and it is why negative caching is
visible to a Learner at all.

Sample of the shape being parsed::

    START_RRSET_CACHE
    ;rrset 300 1 0 8 0
    web.example.com.	300	IN	A	10.0.5.2
    END_RRSET_CACHE
    START_MSG_CACHE
    msg nosuch.example.com. IN A 32899 1 60 0 0 1 0
    example.com. IN SOA 4
    END_MSG_CACHE
    EOF
"""

from __future__ import annotations

from typing import Dict, Iterator, List, Tuple

from mniplab.resolver.models import (
    CACHE_STATUS_NODATA,
    CACHE_STATUS_NXDOMAIN,
    CACHE_STATUS_POSITIVE,
    CacheEntry,
)

RRSET_SECTION_START = "START_RRSET_CACHE"
RRSET_SECTION_END = "END_RRSET_CACHE"
MSG_SECTION_START = "START_MSG_CACHE"
MSG_SECTION_END = "END_MSG_CACHE"

MSG_LINE_PREFIX = "msg "
RCODE_MASK = 0x000F
RCODE_NXDOMAIN = 3
RCODE_NOERROR = 0

MSG_FIELD_COUNT = 11
RRSET_FIELD_COUNT = 5
REFERENCE_FIELD_COUNT = 4

# Field positions in a message line, which reads:
# msg <qname> <qclass> <qtype> <flags> <qdcount> <ttl> <security> <an> <ns> <ar>
MSG_FIELD_QNAME = 1
MSG_FIELD_QTYPE = 3
MSG_FIELD_FLAGS = 4
MSG_FIELD_TTL = 6
MSG_FIELD_SECURITY = 7
FLAG_AD = 0x0020
SEC_STATUS_SECURE = (4, 5)
SEC_STATUS_BOGUS = (1,)


def _map_security_status(sec_val: int) -> str:
    """Map Unbound numeric security status to standard string label."""
    if sec_val in SEC_STATUS_SECURE:
        return "secure"
    if sec_val in SEC_STATUS_BOGUS:
        return "bogus"
    return "insecure"
# The three section counts are the last fields, and their sum is how many
# reference lines follow the message.
MSG_FIELD_ANSWER_COUNT = -3
MSG_SECTION_COUNTS = slice(-3, None)

# Field positions in a reference line, which reads: <name> <class> <type> <flags>
REFERENCE_FIELD_NAME = 0
REFERENCE_FIELD_TYPE = 2


def _section(lines: List[str], start: str, end: str) -> Iterator[str]:
    """Yield the lines strictly between two section markers.

    Args:
        lines: Every line of the dump.
        start: The opening marker.
        end: The closing marker.

    Yields:
        Each line inside the section, in dump order.
    """
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped == start:
            inside = True
            continue
        if stripped == end:
            return
        if inside and stripped:
            yield stripped


def parse_rrsets(dump: str) -> List[CacheEntry]:
    """Parse the positive RRsets Unbound is holding.

    Args:
        dump: The raw ``dump_cache`` response.

    Returns:
        One entry per resource record, carrying its remaining TTL, validation status,
        and authenticated data flag.
    """
    entries: List[CacheEntry] = []
    current_security = "insecure"
    current_ad = False
    for line in _section(dump.splitlines(), RRSET_SECTION_START, RRSET_SECTION_END):
        if line.startswith(";rrset"):
            parts = line.split()
            offset = 1 if len(parts) > 1 and parts[1] == "nsec_apex" else 0
            try:
                if len(parts) > 5 + offset:
                    sec_code = int(parts[5 + offset])
                    current_security = _map_security_status(sec_code)
                else:
                    current_security = "insecure"
            except (ValueError, IndexError):
                current_security = "insecure"
            current_ad = False
            continue
        if line.startswith(";"):
            continue
        fields = line.split(None, RRSET_FIELD_COUNT - 1)
        if len(fields) < RRSET_FIELD_COUNT:
            continue
        name, ttl, _rclass, rtype, data = fields
        try:
            remaining = int(ttl)
        except ValueError:
            continue
        entries.append(
            CacheEntry(
                name=name,
                rtype=rtype,
                ttl=remaining,
                data=data.strip(),
                status=CACHE_STATUS_POSITIVE,
                security=current_security,
                authenticated_data=current_ad,
            )
        )
    return entries


def _soa_rdata(rrsets: List[CacheEntry]) -> Dict[str, str]:
    """Index the cached SOA records by owner name.

    Args:
        rrsets: The parsed positive entries.

    Returns:
        Owner name mapped to the SOA's rdata, for correlating a negative
        message-cache entry with the SOA that bounds it.
    """
    return {
        entry.name: entry.data for entry in rrsets if entry.rtype.upper() == "SOA"
    }


def _parse_messages(dump: str) -> Iterator[Tuple[List[str], List[List[str]]]]:
    """Yield each cached message with the RRset references that follow it.

    Args:
        dump: The raw ``dump_cache`` response.

    Yields:
        Tuples of (message fields, reference field lists). A message declares
        how many references follow it, so the reference lines are consumed by
        count rather than by looking for the next ``msg`` line.
    """
    lines = list(_section(dump.splitlines(), MSG_SECTION_START, MSG_SECTION_END))
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.startswith(MSG_LINE_PREFIX):
            continue
        fields = line.split()
        if len(fields) < MSG_FIELD_COUNT:
            continue
        try:
            reference_count = sum(
                int(count) for count in fields[MSG_SECTION_COUNTS]
            )
        except ValueError:
            continue
        references: List[List[str]] = []
        for _ in range(reference_count):
            if index >= len(lines):
                break
            reference = lines[index].split()
            index += 1
            if len(reference) >= REFERENCE_FIELD_COUNT:
                references.append(reference)
        yield fields, references


def parse_negative_entries(dump: str, rrsets: List[CacheEntry]) -> List[CacheEntry]:
    """Parse the cached negative answers, correlating each with its SOA.

    Args:
        dump: The raw ``dump_cache`` response.
        rrsets: The parsed positive entries, searched for the bounding SOA.

    Returns:
        One entry per cached ``NXDOMAIN`` or ``NODATA``, filed under the
        queried name and type so a Learner finds it where they looked for it,
        with the SOA's owner and rdata attached.
    """
    soa_by_owner = _soa_rdata(rrsets)
    entries: List[CacheEntry] = []

    for fields, references in _parse_messages(dump):
        qname = fields[MSG_FIELD_QNAME]
        qtype = fields[MSG_FIELD_QTYPE]
        try:
            answer_count = int(fields[MSG_FIELD_ANSWER_COUNT])
            ttl = int(fields[MSG_FIELD_TTL])
            rcode = int(fields[MSG_FIELD_FLAGS]) & RCODE_MASK
        except ValueError:
            continue

        if answer_count:
            continue

        soa_owner = next(
            (
                reference[REFERENCE_FIELD_NAME]
                for reference in references
                if reference[REFERENCE_FIELD_TYPE].upper() == "SOA"
            ),
            "",
        )

        if rcode == RCODE_NXDOMAIN:
            status = CACHE_STATUS_NXDOMAIN
        elif rcode == RCODE_NOERROR and soa_owner:
            # RFC 2308 defines NODATA by the SOA in the authority section, and
            # that requirement is load-bearing here: a cached referral is also
            # NOERROR with an empty answer, and without the SOA test it would
            # show a Learner a phantom NODATA for a name that resolves fine.
            status = CACHE_STATUS_NODATA
        else:
            continue

        sec_tag = "insecure"
        has_ad = False
        try:
            flags_int = int(fields[MSG_FIELD_FLAGS])
            has_ad = bool(flags_int & FLAG_AD)
            if len(fields) > MSG_FIELD_SECURITY:
                sec_val = int(fields[MSG_FIELD_SECURITY])
                sec_tag = _map_security_status(sec_val)
        except (ValueError, IndexError):
            pass
        # A validated denial is served with AD set, the same as a validated
        # answer; see the note in parse_cache_dump.
        has_ad = has_ad or sec_tag == "secure"

        entries.append(
            CacheEntry(
                name=qname,
                rtype=qtype,
                ttl=ttl,
                data=soa_by_owner.get(soa_owner, ""),
                status=status,
                soa=soa_owner,
                security=sec_tag,
                authenticated_data=has_ad,
            )
        )
    return entries


def parse_cache_dump(dump: str) -> List[CacheEntry]:
    """Parse a full ``dump_cache`` response into Cache Entries.

    Args:
        dump: The raw ``dump_cache`` response.

    Returns:
        Every positive RRset and every cached negative answer, sorted so the
        rows hold still between refreshes while their TTLs tick down.
    """
    rrsets = parse_rrsets(dump)

    # Correlate positive RRsets with message cache flags
    ad_by_name_type: Dict[Tuple[str, str], bool] = {}
    sec_by_name_type: Dict[Tuple[str, str], str] = {}
    for fields, references in _parse_messages(dump):
        try:
            flags = int(fields[MSG_FIELD_FLAGS])
            has_ad = bool(flags & FLAG_AD)
            sec_val = int(fields[MSG_FIELD_SECURITY]) if len(fields) > MSG_FIELD_SECURITY else 0
            sec_str = _map_security_status(sec_val)
        except (ValueError, IndexError):
            continue

        qname = fields[MSG_FIELD_QNAME].rstrip(".").lower() + "."
        qtype = fields[MSG_FIELD_QTYPE].upper()
        if has_ad:
            ad_by_name_type[(qname, qtype)] = True
        if sec_str != "insecure":
            sec_by_name_type[(qname, qtype)] = sec_str

        for ref in references:
            ref_name = ref[REFERENCE_FIELD_NAME].rstrip(".").lower() + "."
            ref_type = ref[REFERENCE_FIELD_TYPE].upper()
            if has_ad:
                ad_by_name_type[(ref_name, ref_type)] = True
            if sec_str != "insecure":
                sec_by_name_type[(ref_name, ref_type)] = sec_str

    for entry in rrsets:
        key = (entry.name.rstrip(".").lower() + ".", entry.rtype.upper())
        if key in ad_by_name_type and ad_by_name_type[key]:
            entry.authenticated_data = True
        if key in sec_by_name_type and entry.security == "insecure":
            entry.security = sec_by_name_type[key]
        # An RRset Unbound validated is one it will answer with AD set. The
        # cached message's own flags word never carries the bit — Unbound
        # computes AD at answer time from exactly this status — so the status
        # is the only place the flag can be read back from.
        if entry.security == "secure":
            entry.authenticated_data = True

    negatives = parse_negative_entries(dump, rrsets)
    return sorted(rrsets + negatives, key=lambda entry: entry.sort_key)

