"""Unit tests for the one grammar every Speaker surface accepts.

``parse_route`` turns the CLI's ExaBGP-words line into a ``BGPRoute``;
``build_route`` turns the REST body's fields into the same object; both
refuse the same things naming the offending word or field, so CLI, REST and
the panel accept exactly the same input. ``route_command``
renders a route back into the words ExaBGP's pipe takes.
"""

from __future__ import annotations

import unittest

from mniplab.exabgp.grammar import (
    GenerateOptions,
    build_generate,
    build_route,
    parse_generate,
    parse_route,
    route_command,
)
from mniplab.exabgp.models import BGPRoute

FULL_LINE = (
    "203.0.113.0/24 next-hop 10.0.1.2 as-path [ 65001 65002 ] "
    "community [ 65001:100 no-export ] large-community [ 65001:1:2 ] med 50"
)

FULL_ROUTE = BGPRoute(
    "203.0.113.0/24", "10.0.1.2",
    as_path=[65001, 65002],
    community=["65001:100", "no-export"],
    large_community=["65001:1:2"],
    med=50,
)


class ParseRouteTests(unittest.TestCase):
    """The CLI's words, in ExaBGP's grammar."""

    def test_a_prefix_and_next_hop_are_enough(self) -> None:
        self.assertEqual(
            parse_route("203.0.113.0/24 next-hop 10.0.1.2".split()),
            BGPRoute("203.0.113.0/24", "10.0.1.2"),
        )

    def test_every_supported_attribute_round_trips(self) -> None:
        route = parse_route(FULL_LINE.split())
        self.assertEqual(route, FULL_ROUTE)
        # What the parser read is what the pipe is told, word for word.
        self.assertEqual(route_command(route), f"announce route {FULL_LINE}")
        self.assertEqual(parse_route(route_command(route).split()[2:]), route)

    def test_brackets_are_optional_and_may_be_glued(self) -> None:
        """ExaBGP writes ``as-path [ 65001 65002 ]``; a Learner may type it
        without the brackets, or without the spaces inside them."""
        for line in (
            "203.0.113.0/24 next-hop 10.0.1.2 as-path 65001 65002 community 65001:100 no-export",
            "203.0.113.0/24 next-hop 10.0.1.2 as-path [65001 65002] community [65001:100 no-export]",
        ):
            route = parse_route(line.split())
            self.assertEqual(route.as_path, [65001, 65002], line)
            self.assertEqual(route.community, ["65001:100", "no-export"], line)

    def test_well_known_community_names_are_accepted_in_any_case(self) -> None:
        route = parse_route(
            "203.0.113.0/24 next-hop 10.0.1.2 community NO-EXPORT no-advertise blackhole no-peer".split()
        )
        self.assertEqual(route.community, ["no-export", "no-advertise", "blackhole", "no-peer"])

    def test_an_unknown_token_is_refused_by_name(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_route("203.0.113.0/24 next-hop 10.0.1.2 local-preference 200".split())
        self.assertIn("'local-preference'", str(ctx.exception))

    def test_a_missing_next_hop_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_route("203.0.113.0/24 as-path 65001".split())
        self.assertIn("next-hop", str(ctx.exception))

    def test_a_malformed_community_is_refused_by_name(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_route("203.0.113.0/24 next-hop 10.0.1.2 community 65001-100".split())
        self.assertIn("'65001-100'", str(ctx.exception))
        self.assertIn("community", str(ctx.exception))

    def test_a_malformed_large_community_and_as_path_are_refused_by_name(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_route("203.0.113.0/24 next-hop 10.0.1.2 large-community 65001:1".split())
        self.assertIn("'65001:1'", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            parse_route("203.0.113.0/24 next-hop 10.0.1.2 as-path 65001 AS65002".split())
        self.assertIn("'AS65002'", str(ctx.exception))

    def test_an_attribute_without_a_value_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_route("203.0.113.0/24 next-hop 10.0.1.2 med".split())
        self.assertIn("med", str(ctx.exception))

    def test_an_empty_line_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_route([])
        self.assertIn("prefix", str(ctx.exception))


class BuildRouteTests(unittest.TestCase):
    """The REST body's fields, deserialised to the same object."""

    def test_the_cli_line_and_the_rest_body_produce_equal_routes(self) -> None:
        body = {
            "prefix": "203.0.113.0/24", "next_hop": "10.0.1.2",
            "as_path": [65001, 65002], "community": ["65001:100", "no-export"],
            "large_community": ["65001:1:2"], "med": 50,
        }
        self.assertEqual(build_route(**body), parse_route(FULL_LINE.split()))  # type: ignore[arg-type]

    def test_absent_attributes_default_to_empty(self) -> None:
        self.assertEqual(
            build_route(prefix="203.0.113.0/24", next_hop="10.0.1.2"),
            BGPRoute("203.0.113.0/24", "10.0.1.2"),
        )

    def test_a_missing_prefix_is_refused_naming_the_field(self) -> None:
        for prefix in (None, "", "  "):
            with self.assertRaises(ValueError) as ctx:
                build_route(prefix=prefix, next_hop="10.0.1.2")
            self.assertEqual(str(ctx.exception), "prefix is required")

    def test_a_prefix_without_a_length_is_refused_naming_the_field(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_route(prefix="203.0.113.0", next_hop="10.0.1.2")
        self.assertIn("prefix", str(ctx.exception))
        self.assertIn("203.0.113.0", str(ctx.exception))

    def test_a_next_hop_that_is_not_an_address_is_refused_naming_the_field(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_route(prefix="203.0.113.0/24", next_hop="r1")
        self.assertIn("next-hop", str(ctx.exception))
        self.assertIn("'r1'", str(ctx.exception))

    def test_an_ipv6_prefix_with_an_ipv4_next_hop_is_refused_naming_both_families(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_route(prefix="2001:db8:200::/48", next_hop="10.0.1.2")
        message = str(ctx.exception)
        self.assertIn("next-hop", message)
        self.assertIn("IPv4", message)
        self.assertIn("IPv6", message)

    def test_an_ipv4_prefix_with_an_ipv6_next_hop_is_refused_naming_both_families(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_route(prefix="203.0.113.0/24", next_hop="2001:db8:1::2")
        message = str(ctx.exception)
        self.assertIn("IPv4", message)
        self.assertIn("IPv6", message)

    def test_a_med_that_is_not_a_number_is_refused_naming_the_field(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_route(prefix="203.0.113.0/24", next_hop="10.0.1.2", med="high")
        self.assertIn("med", str(ctx.exception))
        self.assertIn("'high'", str(ctx.exception))


class RouteCommandTests(unittest.TestCase):
    """What the pipe is told."""

    def test_a_route_with_no_attributes_is_prefix_and_next_hop_only(self) -> None:
        self.assertEqual(
            route_command(BGPRoute("203.0.113.0/24", "10.0.1.2")),
            "announce route 203.0.113.0/24 next-hop 10.0.1.2",
        )

    def test_a_med_of_zero_is_rendered(self) -> None:
        self.assertEqual(
            route_command(BGPRoute("203.0.113.0/24", "10.0.1.2", med=0)),
            "announce route 203.0.113.0/24 next-hop 10.0.1.2 med 0",
        )


class ParseGenerateTests(unittest.TestCase):
    """``generate <count> [sub-prefixes N] [prefix-seed S] [as-path-seed S]``."""

    def test_a_count_alone_is_enough(self) -> None:
        self.assertEqual(parse_generate(["20"]), GenerateOptions(count=20))

    def test_every_option_round_trips_in_any_order(self) -> None:
        options = parse_generate("5 as-path-seed 7 sub-prefixes 3 prefix-seed 42".split())
        self.assertEqual(options, GenerateOptions(count=5, sub_prefixes=3, prefix_seed=42, as_path_seed=7))

    def test_the_cli_words_and_the_rest_body_produce_equal_options(self) -> None:
        self.assertEqual(
            parse_generate("5 sub-prefixes 3 prefix-seed 42 as-path-seed 7".split()),
            build_generate(count=5, sub_prefixes=3, prefix_seed=42, as_path_seed=7),
        )
        self.assertEqual(
            build_generate(count="5", sub_prefixes="3", prefix_seed="42", as_path_seed="7"),
            GenerateOptions(count=5, sub_prefixes=3, prefix_seed=42, as_path_seed=7),
        )

    def test_a_missing_count_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_generate([])
        self.assertIn("count", str(ctx.exception))

    def test_a_missing_count_over_rest_is_refused_naming_the_field(self) -> None:
        for count in (None, ""):
            with self.assertRaises(ValueError) as ctx:
                build_generate(count=count)
            self.assertEqual(str(ctx.exception), "count is required")

    def test_a_count_that_is_not_a_number_is_refused_naming_the_field(self) -> None:
        for words in (["twenty"], ["0"], ["-1"]):
            with self.assertRaises(ValueError) as ctx:
                parse_generate(words)
            self.assertIn("count", str(ctx.exception))
            self.assertIn(f"'{words[0]}'", str(ctx.exception))

    def test_an_unknown_option_is_refused_by_name(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_generate("5 seed 3".split())
        self.assertIn("'seed'", str(ctx.exception))

    def test_an_option_without_a_value_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_generate("5 prefix-seed".split())
        self.assertIn("prefix-seed", str(ctx.exception))

    def test_a_seed_that_is_not_a_number_is_refused_naming_the_field(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_generate(count=5, prefix_seed="lucky")
        self.assertIn("prefix-seed 'lucky'", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            build_generate(count=5, sub_prefixes=0)
        self.assertIn("sub-prefixes '0'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
