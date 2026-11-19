"""Browser tests for the DNSSEC surfaces of the DNS and Resolver panels.

Follows the seam in ``test_web_content_rendering.py``: the panel modules are
imported into a live page and their render entry points called with fixture
payloads, so what is asserted is the DOM a Learner would see rather than the
shape of the JavaScript behind it.
"""

from __future__ import annotations

import pytest

from mniplab.tests.browser import LiveServerCase

pytestmark = pytest.mark.browser_services


class DNSSECPanelRenderingTests(LiveServerCase):
    """Drives the DNS and Resolver panel modules in a real browser.

    Follows the seam in ``test_web_content_rendering.py``: the panel modules
    are imported into a live page and their render entry points called with
    fixture payloads, so what is asserted is the DOM a Learner would see rather
    than the shape of the JavaScript behind it.
    """

    def test_signed_zone_renders_badge_keys_and_copyable_ds(self) -> None:
        with self.signed_in_page(wait_for_topology=False) as page:
            rendered = page.evaluate("""async () => {
                const panel = await import('/static/js/dns/dns-panel.js');
                panel.renderDnssec({
                    dnssec: true,
                    nsec3: false,
                    keys: [
                        { tag: 31589, ksk: true, zsk: false, algorithm: 13, active: true },
                        { tag: 52814, ksk: false, zsk: true, algorithm: 13, active: false },
                    ],
                    ds_records: [{
                        owner: 'example.com.', key_tag: 31589,
                        algorithm: 13, digest_type: 2, digest: 'ab'.repeat(32),
                    }],
                });
                return {
                    badgeHidden: document.getElementById('dns-zone-signed-badge').hidden,
                    sectionHidden: document.getElementById('dns-dnssec-section').hidden,
                    toggleHidden: document.getElementById('dns-dnssec-records-toggle').hidden,
                    denial: document.getElementById('dns-dnssec-denial').textContent,
                    keys: document.getElementById('dns-dnssec-keys-tbody').innerText,
                    ds: document.getElementById('dns-dnssec-ds-list').innerText,
                    copyLabels: Array.from(
                        document.querySelectorAll('.dns-dnssec-copy-btn')
                    ).map((b) => b.textContent),
                };
            }""")

            self.assertFalse(rendered["badgeHidden"], "Signed badge must show")
            self.assertFalse(rendered["sectionHidden"])
            self.assertFalse(rendered["toggleHidden"])
            self.assertEqual(rendered["denial"], "NSEC")
            self.assertIn("31589", rendered["keys"])
            self.assertIn("KSK", rendered["keys"])
            self.assertIn("ZSK", rendered["keys"])
            self.assertIn("ECDSAP256SHA256 (13)", rendered["keys"])
            self.assertIn("31589 13 2 " + "ab" * 32, rendered["ds"])
            self.assertEqual(rendered["copyLabels"], ["Copy DS"])

    def test_unsigned_zone_hides_every_dnssec_surface(self) -> None:
        with self.signed_in_page(wait_for_topology=False) as page:
            hidden = page.evaluate("""async () => {
                const panel = await import('/static/js/dns/dns-panel.js');
                panel.renderDnssec({ dnssec: false, keys: [], ds_records: [] });
                return {
                    badge: document.getElementById('dns-zone-signed-badge').hidden,
                    section: document.getElementById('dns-dnssec-section').hidden,
                    toggle: document.getElementById('dns-dnssec-records-toggle').hidden,
                    keys: document.getElementById('dns-dnssec-keys-tbody').innerText.trim(),
                };
            }""")

            self.assertTrue(hidden["badge"])
            self.assertTrue(hidden["section"])
            self.assertTrue(hidden["toggle"])
            self.assertEqual(hidden["keys"], "")

    def test_a_clipboard_message_survives_the_next_poll(self) -> None:
        """A message the Learner's own action produced outlives a re-render.

        The panel re-renders on its poll timer, and the clipboard refusal is
        the one message a Learner has to act on — it tells them to copy the DS
        by hand — so a render must not wipe it.
        """
        with self.signed_in_page(wait_for_topology=False) as page:
            statuses = page.evaluate("""async () => {
                const panel = await import('/static/js/dns/dns-panel.js');
                const details = { dnssec: true, nsec3: false, keys: [], ds_records: [{
                    owner: 'example.com.', key_tag: 31589,
                    algorithm: 13, digest_type: 2, digest: 'ab'.repeat(32),
                }]};
                panel.renderDnssec(details);
                // Refuse the clipboard the way a page served over plain HTTP does.
                Object.defineProperty(navigator, 'clipboard', {
                    configurable: true,
                    value: { writeText: () => Promise.reject(new Error('denied')) },
                });
                document.querySelector('.dns-dnssec-copy-btn').click();
                await new Promise((resolve) => setTimeout(resolve, 50));
                const afterCopy = document.getElementById('dns-dnssec-status').textContent;
                panel.renderDnssec(details);
                return { afterCopy, afterPoll:
                    document.getElementById('dns-dnssec-status').textContent };
            }""")

            self.assertIn("refused clipboard access", statuses["afterCopy"])
            self.assertEqual(statuses["afterPoll"], statuses["afterCopy"])

    def test_signature_records_are_hidden_until_asked_for(self) -> None:
        with self.signed_in_page(wait_for_topology=False) as page:
            result = page.evaluate("""async () => {
                const panel = await import('/static/js/dns/dns-panel.js');
                const records = [
                    { owner: 'web.example.com.', type: 'A', data: '10.0.6.100', ttl: 120 },
                    { owner: 'web.example.com.', type: 'RRSIG', data: 'A 13 3 120 ...', ttl: 120 },
                    { owner: 'web.example.com.', type: 'NSEC', data: 'ns1.example.com. A', ttl: 120 },
                    { owner: 'example.com.', type: 'NSEC3PARAM', data: '1 0 0 -', ttl: 120 },
                ];
                return {
                    hidden: panel.visibleRecords(records, false).map((r) => r.type),
                    shown: panel.visibleRecords(records, true).map((r) => r.type),
                };
            }""")

            self.assertEqual(result["hidden"], ["A"])
            self.assertEqual(result["shown"], ["A", "RRSIG", "NSEC", "NSEC3PARAM"])

    def test_ds_rdata_is_validated_before_it_is_sent(self) -> None:
        with self.signed_in_page(wait_for_topology=False) as page:
            verdicts = page.evaluate("""async () => {
                const panel = await import('/static/js/dns/dns-panel.js');
                return {
                    good: panel.validateDsData('31589 13 2 ' + 'ab'.repeat(32)),
                    shortDigest: panel.validateDsData('31589 13 2 abcd'),
                    badDigestType: panel.validateDsData('31589 13 9 ' + 'ab'.repeat(32)),
                    notHex: panel.validateDsData('31589 13 2 ' + 'zz'.repeat(32)),
                    tooFewFields: panel.validateDsData('31589 13 2'),
                };
            }""")

            self.assertIsNone(verdicts["good"])
            self.assertIn("64-character digest", verdicts["shortDigest"])
            self.assertIn("Digest type", verdicts["badDigestType"])
            self.assertIn("hexadecimal", verdicts["notHex"])
            self.assertIn("key-tag algorithm digest-type digest", verdicts["tooFewFields"])

    def test_resolver_validation_badges_name_each_verdict(self) -> None:
        with self.signed_in_page(wait_for_topology=False) as page:
            badges = page.evaluate("""async () => {
                const panel = await import('/static/js/resolver/resolver-panel.js');
                const of = (security) => panel.validationBadge({ security });
                return {
                    secure: of('secure'),
                    bogus: of('bogus'),
                    insecure: of('insecure'),
                    missing: panel.validationBadge({}),
                };
            }""")

            self.assertEqual(badges["secure"]["label"], "SECURE")
            self.assertIn("resolver-validation-secure", badges["secure"]["className"])
            self.assertEqual(badges["bogus"]["label"], "BOGUS")
            self.assertIn("resolver-validation-bogus", badges["bogus"]["className"])
            self.assertEqual(badges["insecure"]["label"], "INSECURE")
            self.assertIn("resolver-validation-insecure", badges["insecure"]["className"])
            # An entry the dump said nothing about is unsigned, not a failure.
            self.assertEqual(badges["missing"]["label"], "INSECURE")
