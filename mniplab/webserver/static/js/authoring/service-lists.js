// The lists a Node's Services and Speaker keep in the definition, as row
// editor declarations. Each mirrors the definition validator's rules for its
// list, so a mistake is named on its row before the server is asked.

import { parseAddress, parseCidr } from './addresses.js';
import { blankGenerator, generatorFields } from './route-generators.js';

const CIDR_MESSAGE = 'Enter a Subnet CIDR, such as 10.0.0.0/8.';

/** Resolver Access Control: the most specific matching Subnet decides. */
export const ACCESS_CONTROL = {
    name: 'resolver-access-control',
    itemLabel: 'Access rule',
    blank: () => ({ subnet: '', action: 'allow' }),
    fields: [
        { key: 'subnet', label: 'Subnet', placeholder: 'e.g., 10.0.0.0/8',
            check: value => (parseCidr(String(value).trim()) ? null : CIDR_MESSAGE) },
        { key: 'action', label: 'Action',
            kind: 'select', options: [['allow', 'Allow'], ['refuse', 'Refuse'], ['deny', 'Deny']] },
    ],
};

const MAC = /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/;
const DUID = /^([0-9A-Fa-f]{2}:){3,}[0-9A-Fa-f]{2}$/;
const blank = value => value === '' || value === null || value === undefined;
const whole = value => Number.isInteger(value) && value > 0;

// A Pool's family follows its Subnet; null while the Subnet is not yet a CIDR.
const poolFamily = pool => parseCidr(String(pool?.cidr ?? '').trim())?.family ?? null;
const isFamily = family => (pool => poolFamily(pool) === family);

function addressIn(text, network, cidr, outsideMessage) {
    const address = parseAddress(text.trim());
    if (!address) return `Enter an IPv${network.family} address.`;
    if (address.family !== network.family) return `Enter an IPv${network.family} address.`;
    if (address.value < network.first || address.value > network.last) return outsideMessage;
    return null;
}

function poolMessages(pool, _parent, pools) {
    const messages = {};
    const cidr = String(pool.cidr).trim();
    const network = parseCidr(cidr);
    if (!network) messages.cidr = 'Enter a Subnet CIDR, such as 10.50.0.0/24.';
    else if (pools.slice(0, pools.indexOf(pool)).some(other => parseCidr(String(other.cidr).trim())?.first === network.first
        && parseCidr(String(other.cidr).trim())?.prefix === network.prefix)) {
        messages.cidr = `Subnet ${cidr} already has a Pool.`;
    }
    if (blank(pool.start) !== blank(pool.end)) {
        messages[blank(pool.start) ? 'start' : 'end'] = 'Enter both ends of the range, or neither.';
    } else if (network && !blank(pool.start)) {
        const outside = `Outside ${cidr}.`;
        const start = addressIn(pool.start, network, cidr, outside);
        const end = addressIn(pool.end, network, cidr, outside);
        if (start) messages.start = start;
        if (end) messages.end = end;
        if (!start && !end) {
            const first = parseAddress(pool.start.trim()).value;
            const last = parseAddress(pool.end.trim()).value;
            if (first > last) messages.end = 'Range end is before range start.';
            else if (network.family === 4 && network.prefix <= 30) {
                if (first === network.first) messages.start = 'Range start cannot be the network address.';
                else if (last === network.last) messages.end = 'Range end cannot be the broadcast address.';
            } else if (network.family === 6 && first === network.first) {
                messages.start = 'Range start cannot be the Subnet-Router anycast address.';
            }
        }
    }
    if (network && !blank(pool.dns_server)) {
        const dns = parseAddress(String(pool.dns_server).trim());
        if (!dns || dns.family !== network.family) messages.dns_server = `Enter an IPv${network.family} address, or leave it empty.`;
    }
    if (network?.family === 4 && !blank(pool.router)) {
        const router = parseAddress(String(pool.router).trim());
        if (!router || router.family !== 4) messages.router = 'Enter an IPv4 address, or leave it empty.';
    }
    if (network?.family === 6 && whole(pool.preferred_lifetime) && whole(pool.lease_time)
        && pool.preferred_lifetime > pool.lease_time) {
        messages.preferred_lifetime = `Preferred lifetime cannot exceed the lease time (${pool.lease_time} s).`;
    }
    return messages;
}

function reservationMessages(reservation, pool) {
    const messages = {};
    const network = parseCidr(String(pool.cidr).trim());
    if (!network) return messages;
    const ip = addressIn(String(reservation.ip), network, pool.cidr, `Outside ${String(pool.cidr).trim()}.`);
    if (ip) messages.ip = ip;
    const { mac, duid } = reservation;
    if (network.family === 4) {
        if (blank(mac)) messages.identifier = 'Enter a MAC address, such as aa:bb:cc:dd:ee:ff.';
        else if (!MAC.test(mac)) messages.identifier = 'Enter a MAC address, such as aa:bb:cc:dd:ee:ff.';
    } else if (blank(mac) === blank(duid)) {
        messages.identifier = 'Name exactly one client: a MAC address or a DUID.';
    } else if (!blank(mac) && !MAC.test(mac)) {
        messages.identifier = 'Enter a MAC address, such as aa:bb:cc:dd:ee:ff.';
    } else if (!blank(duid) && !DUID.test(duid)) {
        messages.identifier = 'Enter a DUID as colon-separated hex octets, such as 00:01:00:01:aa:bb:cc:dd.';
    }
    return messages;
}

const hostPart = address => String(address).split(/[.:]/).pop();

/** DHCP Pools, each with its Reservations. The Subnet sets which fields apply. */
export const DHCP_POOLS = {
    name: 'dhcp-pools',
    itemLabel: 'Pool',
    blank: () => ({ cidr: '', start: '', end: '', lease_time: null, preferred_lifetime: null,
        router: '', dns_server: '', reservations: [] }),
    summary: pool => {
        const count = pool.reservations.length;
        const range = blank(pool.start) && blank(pool.end) ? 'no range' : `${hostPart(pool.start)}–${hostPart(pool.end)}`;
        return `${String(pool.cidr).trim() || 'New Pool'} · ${range} · ${count} Reservation${count === 1 ? '' : 's'}`;
    },
    validate: poolMessages,
    fields: [
        { key: 'cidr', label: 'Subnet', placeholder: 'e.g., 10.50.0.0/24' },
        { key: 'start', label: 'Range start' },
        { key: 'end', label: 'Range end' },
        { key: 'lease_time', label: 'Lease time (seconds)', kind: 'number', placeholder: 'Kea default',
            invalid: 'Enter whole seconds, or leave it empty.',
            check: value => (value === null || whole(value) ? null : 'Enter whole seconds greater than 0.') },
        { key: 'preferred_lifetime', label: 'Preferred lifetime (seconds)', kind: 'number',
            invalid: 'Enter whole seconds, or leave it empty.',
            placeholder: 'Kea default', visibleWhen: isFamily(6), hiddenValue: null,
            check: value => (value === null || whole(value) ? null : 'Enter whole seconds greater than 0.') },
        { key: 'router', label: 'Router', placeholder: 'none', visibleWhen: pool => poolFamily(pool) !== 6,
            hiddenValue: '' },
        { key: 'dns_server', label: 'DNS server', placeholder: 'none' },
    ],
    children: [{
        key: 'reservations',
        itemLabel: 'Reservation',
        blank: () => ({ ip: '', mac: '', duid: '', hostname: '' }),
        validate: reservationMessages,
        fields: [
            { key: 'ip', label: 'Address' },
            { key: 'identified_by', label: 'Identified by', kind: 'select', state: true,
                options: [['mac', 'MAC address'], ['duid', 'DUID']], initial: row => (row.duid ? 'duid' : 'mac'),
                after: row => { row.mac = ''; row.duid = ''; },
                visibleWhen: (_row, pool) => poolFamily(pool) === 6, hiddenValue: 'mac' },
            { key: 'identifier',
                label: (row, pool, ui) => (row ? (ui.identified_by === 'duid' ? 'DUID' : 'MAC')
                    : (poolFamily(pool) === 6 ? 'MAC or DUID' : 'MAC')),
                placeholder: (_row, _pool, ui) => (ui.identified_by === 'duid'
                    ? 'e.g., 00:01:00:01:aa:bb:cc:dd' : 'e.g., aa:bb:cc:dd:ee:ff'),
                read: (row, ui) => (ui.identified_by === 'duid' ? row.duid : row.mac),
                write: (row, text, ui) => {
                    row[ui.identified_by === 'duid' ? 'duid' : 'mac'] = text;
                    row[ui.identified_by === 'duid' ? 'mac' : 'duid'] = '';
                } },
            { key: 'hostname', label: 'Hostname', placeholder: 'optional' },
        ],
    }],
};

/** A Relay's upstream DHCP servers: at least one and at most one per address family. */
export const RELAY_SERVERS = {
    name: 'relay-servers',
    itemLabel: 'DHCP server',
    max: 2,
    itemToRow: address => ({ address }),
    rowToItem: row => String(row.address).trim(),
    blank: () => ({ address: '' }),
    fields: [
        { key: 'address', label: 'Address', kind: 'address', placeholder: 'e.g., 10.0.0.1 or 2001:db8::1' },
    ],
    validate: (row, _parent, rows) => {
        const address = parseAddress(String(row.address).trim());
        if (!address) return { address: 'Enter an IPv4 or IPv6 address.' };
        const earlier = rows.slice(0, rows.indexOf(row));
        if (earlier.some(other => parseAddress(String(other.address).trim())?.family === address.family)) {
            return { address: 'Only one server per address family.' };
        }
        return {};
    },
};

const ASN_MESSAGE = 'Enter an AS number from 1 to 4294967295.';
const asn = value => Number.isInteger(value) && value >= 1 && value <= 4294967295;
const asnCheck = value => (asn(value) ? null : ASN_MESSAGE);
const COMMUNITY = /^[0-9]+(:[0-9]+){1,2}$/;
const tokens = text => text.split(/[\s,]+/).filter(Boolean);
const sameAddress = (a, b) => {
    const left = parseAddress(String(a).trim());
    const right = parseAddress(String(b).trim());
    return Boolean(left && right && left.family === right.family && left.value === right.value);
};
const peerFamily = neighbor => parseAddress(String(neighbor?.peer ?? '').trim())?.family ?? null;

// A Route is judged against the Neighbor it belongs to, or, for a Global Route,
// against every Neighbor that has no Routes of its own, which is who it applies to.
function routeMessages(route, owner, isGlobal) {
    const messages = {};
    const text = String(route.prefix).trim();
    const prefix = parseCidr(text);
    const hop = parseAddress(String(route.next_hop).trim());
    if (!prefix || parseAddress(text.split('/')[0]).value !== prefix.first) {
        messages.prefix = 'Enter a network such as 203.0.113.0/24, with no host bits set.';
    }
    if (!hop) messages.next_hop = 'Enter an IPv4 or IPv6 address.';
    else if (prefix && hop.family !== prefix.family) {
        messages.next_hop = `Use an IPv${prefix.family} next hop to match the prefix.`;
    }
    if (prefix && !messages.prefix) {
        if (isGlobal) {
            const unserved = (owner?.neighbors || []).find(neighbor => !neighbor.routes.length
                && peerFamily(neighbor) && peerFamily(neighbor) !== prefix.family);
            if (unserved) {
                messages.prefix = `An IPv${prefix.family} Route cannot apply to Neighbor ${unserved.peer}, `
                    + 'which has no Routes of its own.';
            }
        } else if (peerFamily(owner) && peerFamily(owner) !== prefix.family) {
            messages.prefix = `Use an IPv${peerFamily(owner)} prefix: this Neighbor is IPv${peerFamily(owner)}.`;
        }
    }
    return messages;
}

const routeFields = [
    { key: 'prefix', label: 'Prefix', placeholder: 'e.g., 203.0.113.0/24' },
    { key: 'next_hop', label: 'Next hop', placeholder: 'e.g., 10.0.0.2' },
];
// A definition may leave a Route's optional attributes out, as the Lab does:
// an absent one reads as empty.
const routeMore = [
    { key: 'community', label: 'Communities', placeholder: 'e.g., 65000:100, 65000:200',
        read: route => route.community ?? [], parse: tokens, format: value => (Array.isArray(value) ? value.join(', ') : String(value)),
        check: value => (value.every(item => COMMUNITY.test(item))
            ? null : 'Enter communities such as 65000:100, separated by commas.') },
    { key: 'large_community', label: 'Large communities', placeholder: 'e.g., 65000:1:2',
        read: route => route.large_community ?? [], parse: tokens, format: value => (Array.isArray(value) ? value.join(', ') : String(value)),
        check: value => (value.every(item => COMMUNITY.test(item))
            ? null : 'Enter large communities such as 65000:1:2, separated by commas.') },
    { key: 'as_path', label: 'AS path', placeholder: 'e.g., 65001 64512',
        read: route => route.as_path ?? [], parse: text => tokens(text).map(item => (/^\d+$/.test(item) ? Number(item) : item)),
        format: value => (Array.isArray(value) ? value.join(' ') : String(value)),
        check: value => (value.every(asn) ? null : 'Enter AS numbers from 1 to 4294967295, separated by spaces.') },
    { key: 'med', label: 'MED', kind: 'number', placeholder: 'none',
        read: route => route.med ?? null, invalid: 'Enter a whole number from 0 to 4294967295, or leave it empty.',
        check: value => (value === null || (Number.isInteger(value) && value >= 0 && value <= 4294967295)
            ? null : 'Enter a whole number from 0 to 4294967295, or leave it empty.') },
];
const blankRoute = () => ({ prefix: '', next_hop: '', community: [], as_path: [], large_community: [], med: null });

function neighborMessages(neighbor, _speaker, neighbors) {
    const messages = {};
    const peer = parseAddress(String(neighbor.peer).trim());
    const local = parseAddress(String(neighbor.local_address).trim());
    if (!peer) messages.peer = 'Enter an IPv4 or IPv6 address.';
    if (!local) messages.local_address = 'Enter an IPv4 or IPv6 address.';
    if (peer && local) {
        if (peer.family !== local.family) {
            messages.local_address = `Use an IPv${peer.family} local address to match the peer.`;
        } else if (peer.value === local.value) messages.local_address = 'The local address must differ from the peer.';
    }
    const earlier = neighbors.slice(0, neighbors.indexOf(neighbor));
    if (peer && earlier.some(other => sameAddress(other.peer, neighbor.peer))) {
        messages.peer = 'Each Neighbor needs its own peer address.';
    }
    if (local && !messages.local_address && earlier.some(other => sameAddress(other.local_address, neighbor.local_address))) {
        messages.local_address = 'Each Neighbor needs its own local address.';
    }
    return messages;
}

/** Speaker Neighbors, each with the Routes it announces in place of the Global Routes. */
export const SPEAKER_NEIGHBORS = {
    name: 'speaker-neighbors',
    itemLabel: 'Neighbor',
    max: 32,
    blank: speaker => ({ peer: '', local_address: '', local_as: asn(speaker?.local_as) ? speaker.local_as : null,
        peer_as: null, hold_time: null, routes: [],
        ...(!speaker?.route_generator ? { route_generator: blankGenerator() } : {}) }),
    summary: neighbor => {
        const count = neighbor.routes.length;
        return `${String(neighbor.peer).trim() || 'New Neighbor'} · AS ${neighbor.local_as ?? '?'} → `
            + `${neighbor.peer_as ?? '?'} · ${neighbor.route_generator ? 'Generated + ' : ''}${count} hand-written Route${count === 1 ? '' : 's'}`;
    },
    validate: neighborMessages,
    fields: [
        { key: 'peer', label: 'Peer', kind: 'address', placeholder: 'e.g., 10.0.0.1' },
        { key: 'local_address', label: 'Local address', placeholder: 'e.g., 10.0.0.2' },
        { key: 'local_as', label: 'Local AS', kind: 'number', invalid: ASN_MESSAGE, check: asnCheck },
        { key: 'peer_as', label: 'Peer AS', kind: 'number', invalid: ASN_MESSAGE, check: asnCheck },
        { key: 'hold_time', label: 'Hold time (seconds)', kind: 'number', placeholder: 'default',
            invalid: 'Enter whole seconds from 3 to 65535, or leave it empty.',
            check: value => (value === null || (Number.isInteger(value) && value >= 3 && value <= 65535)
                ? null : 'Enter whole seconds from 3 to 65535, or leave it empty.') },
        ...generatorFields,
    ],
    children: [{
        key: 'routes',
        behindMore: true,
        itemLabel: 'Route',
        max: 2000,
        blank: blankRoute,
        fields: routeFields,
        more: routeMore,
        validate: (route, neighbor) => routeMessages(route, neighbor, false),
    }],
};

/** Global Routes: announced to every Neighbor that has no Routes of its own. */
export const SPEAKER_ROUTES = {
    name: 'speaker-routes',
    itemLabel: 'Global Route',
    max: 2000,
    blank: blankRoute,
    fields: routeFields,
    more: routeMore,
    validate: (route, speaker) => routeMessages(route, speaker, true),
};

