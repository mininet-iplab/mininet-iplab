// The RPKI CA and Validator Services a Node holds in the definition. The Hosted
// CAs are a row editor's list; the checks mirror the definition validator's rules
// (mniplab/webserver/authoring.py) so a mistake is named on its row before the
// server is asked. The server stays the authority.

import { parseAddress, parseCidr } from './addresses.js';
import { validateItems } from './row-editor.js';

const MAX_ASN = 4294967295;
const ASN_MESSAGE = 'Enter an AS number from 1 to 4294967295.';
const DNS_NAME = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/;
const ROA_SHAPE = 'Write each starting ROA as prefix[-maxLength] => origin, such as 10.1.0.0/24-24 => 65002.';
const ASPA_SHAPE = 'Write each starting ASPA as ASn => ASm, ASk, such as AS65002 => AS65001.';

const asn = value => Number.isInteger(value) && value >= 1 && value <= MAX_ASN;

// An AS number written with or without its AS prefix; null when it is not one.
function asnText(text) {
    const match = /^(?:AS)?(\d+)$/i.exec(String(text).trim());
    return match && asn(Number(match[1])) ? Number(match[1]) : null;
}

// A network in CIDR form with no host bits set, as the server reads it; null when malformed.
function strictNetwork(text) {
    const network = parseCidr(text);
    const address = parseAddress(text.split('/')[0]);
    return network && address && address.value === network.first ? network : null;
}

const lines = text => text.split('\n').map(line => line.trim()).filter(Boolean);
const joinLines = value => value.join('\n');

// The networks a Hosted CA holds; a starting object must fall inside one of them.
const heldNetworks = row => row.prefixes.map(prefix => strictNetwork(prefix.trim())).filter(Boolean);

// Why a starting ROA line is refused, or null when it is fine.
function roaProblem(line, held) {
    const parts = line.split('=>');
    if (parts.length !== 2) return ROA_SHAPE;
    const [body, origin] = parts;
    const [prefixText, maxText = ''] = body.trim().split('-');
    const network = strictNetwork(prefixText.trim());
    if (!network) return `'${prefixText.trim()}' is not a prefix in CIDR notation.`;
    const bits = network.family === 4 ? 32 : 128;
    const max = maxText.trim() === '' ? network.prefix : /^\d+$/.test(maxText.trim()) ? Number(maxText) : NaN;
    if (!(max >= network.prefix && max <= bits)) {
        return `maxLength must be from ${network.prefix} to ${bits}, or empty for ${network.prefix}.`;
    }
    if (asnText(origin) === null) return ASN_MESSAGE;
    const inside = held.some(cover => cover.family === network.family
        && network.first >= cover.first && network.last <= cover.last);
    return inside ? null : `Starting ROA ${prefixText.trim()} is outside this Hosted CA's prefixes.`;
}

// Why a starting ASPA line is refused for the Hosted CA with this ASN, or null when it is fine.
function aspaProblem(line, ownAsn) {
    const parts = line.split('=>');
    if (parts.length !== 2) return ASPA_SHAPE;
    const customer = asnText(parts[0]);
    if (customer === null) return ASN_MESSAGE;
    if (customer !== ownAsn) return `Starting ASPA customer AS${customer} is not this Hosted CA's ASN.`;
    if (parts[1].trim() === '') return 'An ASPA needs at least one provider.';
    const providers = parts[1].split(',').map(item => asnText(item));
    return providers.every(provider => provider !== null) ? null : ASN_MESSAGE;
}

/** Hosted CAs: one row per Autonomous System, with the prefixes it holds and its starting ROAs and ASPAs. */
export const HOSTED_CAS = {
    name: 'rpki-ca-hosted-cas',
    itemLabel: 'Hosted CA',
    blank: () => ({ asn: null, prefixes: [], roas: [], aspas: [] }),
    summary: row => {
        const count = (noun, items) => `${items.length} ${noun}${items.length === 1 ? '' : 's'}`;
        return `AS${row.asn ?? '?'} · ${count('prefix', row.prefixes)} · ${count('ROA', row.roas)} · ${count('ASPA', row.aspas)}`;
    },
    // The later of two rows naming one ASN is the one refused, as the server refuses it.
    validate: (row, _parent, rows) => (asn(row.asn)
        && rows.slice(0, rows.indexOf(row)).some(other => other.asn === row.asn)
        ? { asn: `Hosted CA AS${row.asn} is declared more than once.` } : {}),
    fields: [
        { key: 'asn', label: 'ASN', required: true, kind: 'number', invalid: ASN_MESSAGE,
            check: value => (asn(value) ? null : ASN_MESSAGE) },
        { key: 'prefixes', label: 'Prefixes', kind: 'lines', required: true,
            placeholder: 'e.g., 10.1.0.0/24, one per line',
            parse: text => text.split(/[\n,]/).map(item => item.trim()).filter(Boolean),
            format: joinLines,
            check: value => (value.length && value.every(item => strictNetwork(item.trim()))
                ? null : 'Enter Subnet CIDRs, such as 10.1.0.0/24.') },
        { key: 'roas', label: 'Starting ROAs', kind: 'lines', placeholder: 'e.g., 10.1.0.0/24-24 => 65002',
            parse: lines, format: joinLines,
            check: (value, row) => {
                const held = heldNetworks(row);
                for (const [index, line] of value.entries()) {
                    const problem = roaProblem(line, held);
                    if (problem) return `Line ${index + 1}: ${problem}`;
                }
                return null;
            } },
        { key: 'aspas', label: 'Starting ASPAs', kind: 'lines', placeholder: 'e.g., AS65002 => AS65001, AS65005',
            parse: lines, format: joinLines,
            check: (value, row) => {
                for (const [index, line] of value.entries()) {
                    const problem = aspaProblem(line, row.asn);
                    if (problem) return `Line ${index + 1}: ${problem}`;
                }
                return null;
            } },
    ],
};

/**
 * The first refusal among a Node's RPKI Services, as a control and its message, or null.
 *
 * Covers the Hosted CA rows, the hostname a Node's name stands in for, the
 * Validator's refresh, and the Lab-wide rules: one RPKI CA per Lab, and a
 * Validator only where the Lab has an RPKI CA.
 *
 * @param {object} node The Node being checked.
 * @param {{nodes: object[]}} topology The draft's Topology.
 * @returns {{controlId: string, message: string}|null}
 */
export function rpkiServiceError(node, topology) {
    const authorities = topology.nodes.filter(item => item.rpki_ca);
    if (node.rpki_ca) {
        if (!node.rpki_ca.hosted_cas.length) {
            return { controlId: 'rpki-ca-hosted-cas', message: 'Add at least one Hosted CA.' };
        }
        const rowError = validateItems(HOSTED_CAS, node.rpki_ca.hosted_cas)[0];
        if (rowError) return rowError;
        const hostname = node.rpki_ca.hostname ?? node.name;
        if (hostname.length > 253 || !DNS_NAME.test(hostname)) {
            return { controlId: 'rpki-ca-hostname', message: `Node '${node.name}' RPKI CA: '${hostname}' is not a DNS hostname (letters, digits and hyphens).` };
        }
        if (authorities.indexOf(node) > 0) {
            return { controlId: 'rpki-ca-enabled', message: `A Lab holds at most one RPKI CA, but ${authorities.length} are declared (${authorities.map(item => item.name).join(', ')}).` };
        }
    }
    if (node.validator) {
        const refresh = node.validator.refresh;
        if (!Number.isInteger(refresh) || refresh <= 0) {
            return { controlId: 'validator-refresh', message: 'Enter whole seconds greater than 0.' };
        }
        if (!authorities.length) {
            return { controlId: 'validator-enabled', message: `Node '${node.name}': a Validator needs an RPKI CA in the Lab, to take its TAL and trust from, but this Lab declares none.` };
        }
    }
    return null;
}
