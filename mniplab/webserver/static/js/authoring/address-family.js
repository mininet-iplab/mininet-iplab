// A Canvas draft's address family governs address controls throughout the page.

import { parseCidr } from './addresses.js';

/** The address versions used by the Lab. Python drafts choose through code. */
export function addressVersions(family) {
    return family === 'dual' ? [4, 6] : [family === 'ipv6' ? 6 : 4];
}

/** Hide the controls and labels for an unused family without changing values. */
export function showAddressFamily(root, family) {
    const versions = addressVersions(family);
    root.querySelectorAll('[data-address-family]').forEach(element => {
        element.hidden = !versions.includes(Number(element.dataset.addressFamily));
    });
    root.querySelectorAll('[data-address-examples]').forEach(element => {
        const cidr = element.dataset.addressExamples === 'subnets';
        element.placeholder = 'e.g., ' + versions.map(version => version === 4
            ? `192.0.2.${cidr ? '0/24' : '53'}`
            : `2001:db8::${cidr ? '/64' : '53'}`).join(', ');
    });
}

/** List the governed fields a proposed family would exclude, leaving values intact. */
export function addressFamilyConflicts(topology, family) {
    const allowed = addressVersions(family);
    const conflicts = [];
    const addressKeys = new Set(['ipv4', 'ipv6', 'ipv4_gateway', 'ipv6_gateway', 'dhcp4', 'dhcp6',
        'ip', 'subnet', 'cidr', 'start', 'end', 'router', 'dns_server', 'peer', 'local_address',
        'prefix', 'next_hop', 'address', 'pools', 'reservations', 'subnets', 'servers',
        'root_hints', 'forwarders', 'routes', 'zones', 'allow_transfer']);
    const visit = (value, label) => {
        if (Array.isArray(value)) value.forEach((item, index) => visit(item, `${label}[${index + 1}]`));
        else if (value && typeof value === 'object') {
            Object.entries(value).forEach(([key, item]) => {
                if (!addressKeys.has(key)) return;
                if (key === 'dhcp4' || key === 'dhcp6') {
                    if (item && !allowed.includes(Number(key.at(-1)))) conflicts.push(`${label}.${key}`);
                } else visit(item, `${label}.${key}`);
            });
        } else if (typeof value === 'string' && value) {
            const address = parseCidr(value);
            if (address && !allowed.includes(address.family)) conflicts.push(label);
        }
    };
    topology.nodes.forEach(node => {
        ['ipv4_gateway', 'ipv6_gateway', 'subinterfaces', 'dhcp', 'relay', 'nameservers',
            'resolver', 'dns', 'neighbors', 'routes'].forEach(key => {
            if (key in node) visit(node[key], `${node.name} ${key}`);
        });
        if (node.type === 'nat' || node.type === 'transit') {
            ['ip', 'subnet'].forEach(key => visit(node[key], `${node.name} ${key}`));
        }
    });
    topology.links.forEach(link => link.ends.forEach(end => {
        const node = topology.nodes.find(item => item.id === end.node_id);
        ['ipv4', 'ipv6', 'dhcp4', 'dhcp6'].forEach(key => {
            if (key in end) visit({ [key]: end[key] }, `${node.name}-eth${end.port}`);
        });
    }));
    return conflicts;
}

/** Apply the Lab's family to address fields in a Service or Speaker row editor. */
export function withAddressFamily(spec, family) {
    const cidrs = new Set(['cidr', 'prefix']);
    const addresses = new Set(['start', 'end', 'router', 'dns_server', 'ip', 'address',
        'peer', 'local_address', 'next_hop']);
    const fields = items => items.map(field => {
        if (!cidrs.has(field.key) && !addresses.has(field.key)) return field;
        return {
            ...field,
            check: (value, row, parent) => {
                const own = field.check?.(value, row, parent);
                if (own) return own;
                const parsed = typeof value === 'string' && parseCidr(value.trim());
                if (parsed && !addressVersions(family()).includes(parsed.family)) {
                    return `Use ${family() === 'ipv6' ? 'IPv6' : 'IPv4'}: the Lab's address family.`;
                }
                return null;
            },
            placeholder: (...args) => {
                if (family() === 'ipv6') return cidrs.has(field.key) ? 'e.g., 2001:db8::/64' : 'e.g., 2001:db8::1';
                const example = typeof field.placeholder === 'function' ? field.placeholder(...args) : field.placeholder;
                if (!example) return cidrs.has(field.key) ? 'e.g., 192.0.2.0/24' : 'e.g., 192.0.2.1';
                return example.includes(' or ') && example.includes(':') ? 'e.g., 192.0.2.1' : example;
            },
            visibleWhen: (row, parent, ui) => (field.visibleWhen ? field.visibleWhen(row, parent, ui) : true)
                && (field.key !== 'router' || addressVersions(family()).includes(4)),
        };
    });
    return {
        ...spec,
        fields: fields(spec.fields),
        ...(spec.more ? { more: fields(spec.more) } : {}),
        ...(spec.children ? { children: spec.children.map(child => withAddressFamily(child, family)) } : {}),
    };
}
