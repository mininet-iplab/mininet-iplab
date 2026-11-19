// IPv4 and IPv6 address parsing for the row editors' client-side checks. The
// server stays the authority; these only tell an Instructor early which field
// is wrong, so they follow the same rules as Python's ``ipaddress`` module.

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

function ipv4Value(text) {
    return IPV4.test(text) ? text.split('.').reduce((total, part) => total * 256n + BigInt(part), 0n) : null;
}

function ipv6Value(text) {
    if (!text.includes(':') || /[^0-9a-fA-F:.]/.test(text) || text.split('::').length > 2) return null;
    let [head, tail] = text.includes('::') ? text.split('::') : [text, null];
    const groupsOf = part => (part === '' ? [] : part.split(':'));
    const embedded = groupsOf(tail ?? head);
    const last = embedded[embedded.length - 1];
    if (last?.includes('.')) {
        const value = ipv4Value(last);
        if (value === null) return null;
        embedded.splice(-1, 1, (value >> 16n).toString(16), (value & 0xffffn).toString(16));
        if (tail === null) head = embedded.join(':');
        else tail = embedded.join(':');
    }
    const front = groupsOf(head);
    const back = tail === null ? [] : groupsOf(tail);
    const count = front.length + back.length;
    if (tail === null ? count !== 8 : count > 7) return null;
    const gap = tail === null ? [] : Array(8 - count).fill('0');
    const groups = [...front, ...gap, ...back];
    if (groups.some(group => !/^[0-9a-fA-F]{1,4}$/.test(group))) return null;
    return groups.reduce((total, group) => total * 65536n + BigInt(`0x${group}`), 0n);
}

/**
 * Parse a bare address.
 *
 * @param {string} text An IPv4 or IPv6 address without a prefix length.
 * @returns {{family: 4|6, value: bigint}|null} The address, or null when malformed.
 */
export function parseAddress(text) {
    const value = ipv4Value(text);
    if (value !== null) return { family: 4, value };
    const wide = ipv6Value(text);
    return wide === null ? null : { family: 6, value: wide };
}

/**
 * Parse a network in CIDR form; host bits are allowed, as the server allows them,
 * and a bare address is the one-address network, as Python's ``ip_network`` reads it.
 *
 * @param {string} text An address and prefix length, such as 10.0.0.0/8, or an address alone.
 * @returns {{family: 4|6, prefix: number, first: bigint, last: bigint}|null}
 *     The network's first and last address, or null when malformed.
 */
export function parseCidr(text) {
    const match = /^([^/]+)(?:\/(\d{1,3}))?$/.exec(text);
    const address = match && parseAddress(match[1]);
    if (!address) return null;
    const bits = address.family === 4 ? 32 : 128;
    const prefix = match[2] === undefined ? bits : Number(match[2]);
    if (prefix > bits) return null;
    const size = 1n << BigInt(bits - prefix);
    const first = (address.value / size) * size;
    return { family: address.family, prefix, first, last: first + size - 1n };
}
