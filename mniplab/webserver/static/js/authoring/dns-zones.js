import { parseAddress } from './addresses.js';

// Authoritative DNS declarations edited by the shared row editor. The objects
// remain the definition's own Zones and Records, including absent optional keys.

const primary = zone => zone?.kind === 'primary';
const positiveSeconds = value => (Number.isInteger(value) && value > 0
    ? null : 'Enter whole seconds greater than 0.');
const RECORD_TYPES = ['A', 'AAAA', 'CNAME', 'PTR', 'TXT', 'MX', 'NS', 'SOA'];

/**
 * Primary Zones with Records and policy child lists, using the current Topology's Node identities.
 *
 * @param {() => object} getTopology The draft's current Topology, including edited Zones.
 * @returns {object} The shared row editor's Zone list specification.
 */
export function createZoneSpec(getTopology) {
    const ownerOf = zone => getTopology().nodes.find(node => node.dns?.zones.includes(zone));
    const signedChildren = zone => getTopology().nodes.filter(node => node !== ownerOf(zone)).flatMap(node =>
        (node.dns?.zones || []).filter(child => primary(child) && child.dnssec)
            .map(child => [node.id + '|' + child.name, `${node.name} · ${child.name}`]));
    const transferMode = zone => zone.allow_transfer == null ? 'default'
        : zone.allow_transfer.length ? 'listed' : 'none';
    return {
        name: 'dns-zones',
        itemLabel: 'Zone',
        blank: () => ({ kind: 'primary', name: '', default_ttl: 300, records: [] }),
        summary: zone => {
            if (!primary(zone)) return `${zone.name} · Secondary · Primary `
                + (getTopology().nodes.find(node => node.id === zone.primary_node_id)?.name || 'unavailable');
            const count = zone.records.length;
            return `${zone.name || 'New Zone'} · TTL ${zone.default_ttl} s · `
                + `${count} Record${count === 1 ? '' : 's'}${zone.dnssec ? ' · signed' : ''}`;
        },
        fields: [
            { key: 'name', label: 'Apex', required: true, placeholder: 'e.g., example.com.', visibleWhen: primary,
                check: value => (typeof value === 'string' && value.endsWith('.')
                    ? null : 'Enter a Zone apex ending in a dot, such as example.com.') },
            { key: 'default_ttl', label: 'Default TTL (seconds)', required: true, kind: 'number', visibleWhen: primary,
                check: positiveSeconds },
            { key: 'dnssec', label: 'DNSSEC', kind: 'checkbox', visibleWhen: primary },
        ],
        validate: zone => ({ nsec3: zone.nsec3 && !zone.dnssec ? 'NSEC3 requires DNSSEC signing.' : null }),
        more: [
            { key: 'transfer', label: 'Transfer permission', kind: 'select', visibleWhen: primary,
                options: [['default', 'Default (declared Secondaries)'], ['none', 'None (refused)'], ['listed', 'Listed']],
                read: (row, ui) => ui.transfer ?? transferMode(row),
                write: (row, value, ui) => {
                    ui.transfer = value;
                    if (value === 'default') delete row.allow_transfer;
                    else if (value === 'none' || !Array.isArray(row.allow_transfer)) row.allow_transfer = [];
                },
                check: (value, row) => (value === 'listed' && !row.allow_transfer.length
                    ? 'Add at least one transfer target, or choose None.' : null) },
            { key: 'nsec3', label: 'NSEC3', kind: 'checkbox', visibleWhen: primary },
            { key: 'nsec3_salt', label: 'NSEC3 salt', visibleWhen: primary, placeholder: 'e.g., aabb (leave blank for no salt)',
                check: value => (value == null || /^(?:[0-9a-fA-F]{2})*$/.test(value)
                    ? null : 'Use pairs of hexadecimal digits, or leave the salt empty.') },
        ],
        children: [{
            key: 'records',
            itemLabel: 'Record',
            visibleWhen: (_record, zone) => primary(zone),
            blank: zone => ({ name: '', type: 'A', rdata: '', ttl: zone.default_ttl }),
            fields: [
                { key: 'name', label: 'Owner', required: true, placeholder: 'e.g., web or @',
                    check: value => (typeof value === 'string' && value.length ? null : 'Enter a Record owner.') },
                { key: 'type', label: 'Type', required: true, kind: 'select', options: RECORD_TYPES.map(type => [type, type]) },
                { key: 'rdata', label: 'Data', required: true,
                    check: value => (typeof value === 'string' && value.length ? null : 'Enter Record data.') },
            ],
            more: [{ key: 'ttl', label: 'TTL (seconds)', required: true, kind: 'number', check: positiveSeconds }],
        }, {
            key: 'ds_delegations', itemLabel: 'DS delegation', behindMore: true,
            visibleWhen: (_delegation, zone) => primary(zone),
            blank: () => ({ node_id: '', zone: '' }),
            fields: [{ key: 'child', label: 'Child Primary Zone', required: true, kind: 'select',
                options: (_row, zone) => [['', 'Choose a signed Primary Zone…'], ...signedChildren(zone)],
                read: row => row.node_id ? row.node_id + '|' + row.zone : '',
                write: (row, value) => { [row.node_id, row.zone] = value.split('|'); },
            }],
            validate: (row, parent) => {
                if (!signedChildren(parent).some(([value]) => value === row.node_id + '|' + row.zone)) {
                    return { child: 'Choose a signed Primary Zone on another Node.' };
                }
                if (!parent.dnssec) return { child: 'DS delegation requires signed parent and child Zones.' };
                if (parent.name !== '.' && !row.zone.endsWith('.' + parent.name)) {
                    return { child: 'DS delegation must point to a child Zone.' };
                }
                return {};
            },
        }, {
            key: 'allow_transfer', itemLabel: 'Transfer target', behindMore: true,
            summary: row => row.address ?? getTopology().nodes.find(node => node.id === row.node_id)?.name ?? 'Choose a Node',
            visibleWhen: (_target, zone, ui) => primary(zone) && (ui.transfer ?? transferMode(zone)) === 'listed',
            blank: () => ({ node_id: '' }),
            fields: [
                { key: 'identified_by', label: 'Identified by', kind: 'select',
                    options: [['node', 'Node'], ['address', 'Address']],
                    read: row => 'address' in row ? 'address' : 'node',
                    write: (row, value) => {
                        if (value === ('address' in row ? 'address' : 'node')) return;
                        if (value === 'address') { delete row.node_id; row.address = ''; }
                        else { delete row.address; row.node_id = ''; }
                    } },
                { key: 'node_id', label: 'Node', required: true, kind: 'select', visibleWhen: row => !('address' in row),
                    options: () => [['', 'Choose a Node…'], ...getTopology().nodes.map(node => [node.id, node.name])],
                    check: value => getTopology().nodes.some(node => node.id === value) ? null : 'Choose a Node.' },
                { key: 'address', label: 'Address', required: true, kind: 'address', visibleWhen: row => 'address' in row,
                    check: value => parseAddress(String(value)) ? null : 'Enter an IPv4 or IPv6 address.' },
            ],
        }],
    };
}
