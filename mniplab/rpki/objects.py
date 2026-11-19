"""RPKIObjectService: ROA and ASPA changes at one Hosted CA, for the Lab Prompt and REST.

Each change names the Hosted CA it is made at. The text is parsed and the
Hosted CA found before Krill hears of it, so a malformed prefix, maxLength,
ASN or customer is refused with nothing written. Krill then has the last word:
a prefix the Hosted CA does not hold comes back as RPKINotHeldError, and any
other refusal as RPKIObjectError, both naming the Hosted CA.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Sequence, Tuple, Union

from mniplab.errors import KrillError, RPKINotHeldError, RPKIObjectError
from mniplab.rpki.krill_client import KrillClient
from mniplab.rpki.models import ASPA, ROA, HostedCA, as_asn
from mniplab.rpki.query import RPKIQueryService

#: Krill's ``delta_error`` categories other than ``notheld``, with what each means.
_ROA_REFUSALS = (
    ("unknowns", "is not signed here, so it cannot be removed"),
    ("duplicates", "is already signed here"),
    ("invalid_length", "has a maxLength outside the range its prefix allows"),
)


def _as_roa(roa: Union[ROA, str]) -> ROA:
    """Return a ROA model, parsing Krill-grammar text when given text.

    Args:
        roa: A ROA model, or text such as ``10.1.0.0/24-24 => 65002``.

    Returns:
        The ROA as a model.

    Raises:
        ValueError: If the text does not parse or the ROA is invalid.
    """
    return roa if isinstance(roa, ROA) else ROA.parse(roa)


def _as_aspa(aspa: Union[ASPA, str]) -> ASPA:
    """Return an ASPA model, parsing text when given text.

    Args:
        aspa: An ASPA model, or text such as ``AS65002 => AS65001``.

    Returns:
        The ASPA as a model.

    Raises:
        ValueError: If the text does not parse or the ASPA is invalid.
    """
    return aspa if isinstance(aspa, ASPA) else ASPA.parse(aspa)


def _roa_refusal(asn: int, exc: KrillError) -> RPKIObjectError:
    """Turn Krill's refusal of a ROA delta into an error naming the Hosted CA.

    Args:
        asn: The Hosted CA's Autonomous System number.
        exc: The error Krill answered with.

    Returns:
        An RPKINotHeldError when Krill refused a prefix the Hosted CA does not
        hold, otherwise an RPKIObjectError in plain words.
    """
    body = exc.body if isinstance(exc.body, dict) else {}
    delta = body.get("delta_error") or {}
    notheld = [str(entry.get("prefix")) for entry in delta.get("notheld", [])]
    if notheld:
        pronoun = "it" if len(notheld) == 1 else "them"
        return RPKINotHeldError(
            f"Hosted CA AS{asn} does not hold {', '.join(notheld)}, "
            f"so it cannot sign a ROA for {pronoun}",
            hosted_ca=asn,
            prefixes=notheld,
        )
    reasons = [
        f"{entry.get('prefix')}-{entry.get('max_length')} => {entry.get('asn')} {why}"
        for key, why in _ROA_REFUSALS
        for entry in delta.get(key, [])
    ]
    detail = "; ".join(reasons) or " ".join(exc.message.split())
    return RPKIObjectError(f"Hosted CA AS{asn} refused the ROA change: {detail}")


def _aspa_refusal(asn: int, exc: KrillError) -> RPKIObjectError:
    """Turn Krill's refusal of an ASPA change into an error naming the Hosted CA.

    Args:
        asn: The Hosted CA's Autonomous System number.
        exc: The error Krill answered with.

    Returns:
        An RPKIObjectError carrying Krill's message on one line.
    """
    return RPKIObjectError(
        f"Hosted CA AS{asn} refused the ASPA change: {' '.join(exc.message.split())}"
    )


class RPKIObjectService:
    """Adds, replaces and removes ROAs and ASPAs at one Hosted CA on an RPKI CA Node."""

    @staticmethod
    def _client_and_hosted(net: Any, node_name: str, asn: Any) -> Tuple[KrillClient, HostedCA]:
        """Resolve the RPKI CA's Krill client and the Hosted CA a change names.

        Args:
            net: Mininet network instance.
            node_name: The RPKI CA's Node name.
            asn: The Hosted CA's ASN, as an int or ``AS`` text.

        Returns:
            Tuple of (client, Hosted CA). Nothing has been sent to Krill.

        Raises:
            ValueError: If the Node or Service is absent, Krill is not running,
                or no Hosted CA holds the ASN.
        """
        _node, service, client = RPKIQueryService.get_client(net, node_name)
        return client, RPKIQueryService.hosted_ca(service, asn)

    @staticmethod
    def _own_customer(hosted: HostedCA, customer: int) -> None:
        """Check an ASPA's customer is the Hosted CA's own ASN.

        Args:
            hosted: The Hosted CA the ASPA is changed at.
            customer: The ASPA's customer Autonomous System.

        Raises:
            ValueError: If the customer is another Autonomous System.
        """
        if customer != hosted.asn:
            raise ValueError(
                f"Hosted CA AS{hosted.asn} signs ASPAs only for its own ASN, not AS{customer}"
            )

    @staticmethod
    def _send_roas(
        client: KrillClient,
        hosted: HostedCA,
        added: Sequence[ROA],
        removed: Sequence[ROA],
    ) -> None:
        """Send one ROA delta to a Hosted CA, typing Krill's refusals.

        Args:
            client: The RPKI CA's Krill client.
            hosted: The Hosted CA the delta is for.
            added: ROAs to sign.
            removed: ROAs to withdraw.

        Raises:
            RPKINotHeldError: If Krill refuses a prefix the Hosted CA does not hold.
            RPKIObjectError: If Krill refuses the delta for another reason.
            KrillError: If Krill cannot be reached.
        """
        try:
            client.update_roas(hosted.handle, added=added, removed=removed)
        except KrillError as exc:
            if exc.status is None:
                raise
            raise _roa_refusal(hosted.asn, exc) from exc

    @staticmethod
    def _send_aspas(
        client: KrillClient,
        hosted: HostedCA,
        add_or_replace: Sequence[ASPA],
        remove: Sequence[int],
    ) -> None:
        """Send one ASPA change to a Hosted CA, typing Krill's refusals.

        Args:
            client: The RPKI CA's Krill client.
            hosted: The Hosted CA the change is for.
            add_or_replace: ASPAs to sign.
            remove: Customer ASNs whose ASPAs are withdrawn.

        Raises:
            RPKIObjectError: If Krill refuses the change.
            KrillError: If Krill cannot be reached.
        """
        try:
            client.update_aspas(hosted.handle, add_or_replace=add_or_replace, remove=remove)
        except KrillError as exc:
            if exc.status is None:
                raise
            raise _aspa_refusal(hosted.asn, exc) from exc

    @classmethod
    def add_roa(cls, net: Any, node_name: str, asn: Any, roa: Union[ROA, str]) -> Dict[str, Any]:
        """Sign a ROA at a Hosted CA.

        Args:
            net: Mininet network instance.
            node_name: The RPKI CA's Node name.
            asn: The Hosted CA's ASN, as an int or ``AS`` text.
            roa: The ROA, as a model or ``<prefix>[-<maxLength>] => <origin>``.
                The origin may be any ASN, including one outside the Hosted CA.

        Returns:
            Dict with ``node``, the Hosted CA's ``asn`` and the ``roa`` added.

        Raises:
            ValueError: If the ROA or ASN is invalid, or no Hosted CA holds the ASN.
            RPKINotHeldError: If the Hosted CA does not hold the prefix.
            RPKIObjectError: If Krill refuses the ROA for another reason.
            KrillError: If Krill cannot be reached.
        """
        model = _as_roa(roa)
        client, hosted = cls._client_and_hosted(net, node_name, asn)
        cls._send_roas(client, hosted, added=[model], removed=[])
        return {"node": node_name, "asn": hosted.asn, "roa": asdict(model)}

    @classmethod
    def remove_roa(cls, net: Any, node_name: str, asn: Any, roa: Union[ROA, str]) -> Dict[str, Any]:
        """Withdraw a ROA from a Hosted CA.

        Args:
            net: Mininet network instance.
            node_name: The RPKI CA's Node name.
            asn: The Hosted CA's ASN, as an int or ``AS`` text.
            roa: The ROA to withdraw, matched by prefix, maxLength and origin.

        Returns:
            Dict with ``node``, the Hosted CA's ``asn`` and the ``roa`` removed.

        Raises:
            ValueError: If the ROA or ASN is invalid, or no Hosted CA holds the ASN.
            RPKIObjectError: If Krill has no such ROA to withdraw.
            KrillError: If Krill cannot be reached.
        """
        model = _as_roa(roa)
        client, hosted = cls._client_and_hosted(net, node_name, asn)
        cls._send_roas(client, hosted, added=[], removed=[model])
        return {"node": node_name, "asn": hosted.asn, "roa": asdict(model)}

    @classmethod
    def replace_roa(
        cls,
        net: Any,
        node_name: str,
        asn: Any,
        old: Union[ROA, str],
        new: Union[ROA, str],
    ) -> Dict[str, Any]:
        """Change a ROA at a Hosted CA, such as its maxLength, in one delta.

        Both ROAs are checked before anything is sent, and Krill applies the
        withdrawal and the signing together.

        Args:
            net: Mininet network instance.
            node_name: The RPKI CA's Node name.
            asn: The Hosted CA's ASN, as an int or ``AS`` text.
            old: The ROA to withdraw.
            new: The ROA to sign in its place.

        Returns:
            Dict with ``node``, the Hosted CA's ``asn``, and the ``old`` and
            ``new`` ROAs.

        Raises:
            ValueError: If either ROA or the ASN is invalid, or no Hosted CA
                holds the ASN.
            RPKINotHeldError: If the Hosted CA does not hold the new prefix.
            RPKIObjectError: If Krill refuses the delta for another reason.
            KrillError: If Krill cannot be reached.
        """
        old_model, new_model = _as_roa(old), _as_roa(new)
        client, hosted = cls._client_and_hosted(net, node_name, asn)
        cls._send_roas(client, hosted, added=[new_model], removed=[old_model])
        return {
            "node": node_name,
            "asn": hosted.asn,
            "old": asdict(old_model),
            "new": asdict(new_model),
        }

    @classmethod
    def add_aspa(cls, net: Any, node_name: str, asn: Any, aspa: Union[ASPA, str]) -> Dict[str, Any]:
        """Sign an ASPA at a Hosted CA, replacing any it already has for the customer.

        Args:
            net: Mininet network instance.
            node_name: The RPKI CA's Node name.
            asn: The Hosted CA's ASN, as an int or ``AS`` text.
            aspa: The ASPA, as a model or ``<customer> => <provider>[, ...]``.
                Its customer must be the Hosted CA's own ASN.

        Returns:
            Dict with ``node``, the Hosted CA's ``asn`` and the ``aspa`` signed.

        Raises:
            ValueError: If the ASPA or ASN is invalid, the customer is not the
                Hosted CA's own ASN, or no Hosted CA holds the ASN.
            RPKIObjectError: If Krill refuses the ASPA.
            KrillError: If Krill cannot be reached.
        """
        model = _as_aspa(aspa)
        client, hosted = cls._client_and_hosted(net, node_name, asn)
        cls._own_customer(hosted, model.customer)
        cls._send_aspas(client, hosted, add_or_replace=[model], remove=[])
        return {"node": node_name, "asn": hosted.asn, "aspa": asdict(model)}

    @classmethod
    def remove_aspa(cls, net: Any, node_name: str, asn: Any, customer: Any) -> Dict[str, Any]:
        """Withdraw a customer's ASPA from a Hosted CA.

        Args:
            net: Mininet network instance.
            node_name: The RPKI CA's Node name.
            asn: The Hosted CA's ASN, as an int or ``AS`` text.
            customer: The customer whose ASPA is withdrawn; must be the Hosted
                CA's own ASN.

        Returns:
            Dict with ``node``, the Hosted CA's ``asn`` and the ``customer``.

        Raises:
            ValueError: If the customer or ASN is invalid, the customer is not
                the Hosted CA's own ASN, or no Hosted CA holds the ASN.
            RPKIObjectError: If Krill has no ASPA for the customer.
            KrillError: If Krill cannot be reached.
        """
        customer_asn = as_asn(customer)
        client, hosted = cls._client_and_hosted(net, node_name, asn)
        cls._own_customer(hosted, customer_asn)
        cls._send_aspas(client, hosted, add_or_replace=[], remove=[customer_asn])
        return {"node": node_name, "asn": hosted.asn, "customer": customer_asn}
