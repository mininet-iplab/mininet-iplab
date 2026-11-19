// Relationships between Nodes that the definition states outside Links: a
// Relay's DHCP servers, a Speaker's peers, a Secondary Zone's Primary and a
// parent Zone's DS delegations. Relay servers and Speaker peers are stored as
// addresses, so they relate to whichever Node has that Interface address.

// One spelling per address: no prefix length, and IPv6 written out in full.
// An embedded IPv4 part, as in ::ffff:192.0.2.1, is kept as written.
function canonical(address) {
    const bare = String(address).split('/')[0].trim().toLowerCase();
    if (!bare.includes(':')) return bare;
    const [head, tail] = bare.split('::');
    const groups = head ? head.split(':') : [];
    const rest = tail === undefined ? [] : (tail ? tail.split(':') : []);
    const missing = tail === undefined ? 0 : 8 - groups.length - rest.length;
    return [...groups, ...Array(Math.max(missing, 0)).fill('0'), ...rest]
        .map(group => (group.includes('.') ? group : parseInt(group, 16).toString(16))).join(':');
}

function interfaceAddresses(topology, node) {
    const ends = topology.links.flatMap(link => link.ends).filter(end => end.node_id === node.id);
    return [...ends, ...(node.subinterfaces || [])]
        .flatMap(item => [item.ipv4, item.ipv6]).filter(Boolean).map(canonical);
}

// Relationships are listed kind by kind, in this order.
const KINDS = ['relay', 'peer', 'secondary', 'ds'];

// Every relationship in the draft, each from the Node that states it.
function allRelationships(topology) {
    const owners = address => topology.nodes.filter(node =>
        interfaceAddresses(topology, node).includes(canonical(address)));
    const byAddress = (from, kind, address) => {
        const found = owners(address).filter(node => node.id !== from.id);
        return found.length ? found.map(to => ({ kind, from, to, address })) : [{ kind, from, to: null, address }];
    };
    const nodeById = id => topology.nodes.find(node => node.id === id);
    const found = [];
    for (const node of topology.nodes) {
        for (const server of node.relay?.servers || []) found.push(...byAddress(node, 'relay', server));
        if (node.type === 'speaker') {
            for (const neighbor of node.neighbors || []) found.push(...byAddress(node, 'peer', neighbor.peer));
        }
        for (const zone of node.dns?.zones || []) {
            if (zone.kind === 'secondary') {
                found.push({ kind: 'secondary', from: node, to: nodeById(zone.primary_node_id), zone: zone.name });
            }
            for (const delegation of zone.ds_delegations || []) {
                found.push({ kind: 'ds', from: node, to: nodeById(delegation.node_id),
                    zone: zone.name, child: delegation.zone });
            }
        }
    }
    // A reference to a Node no longer in the draft relates to nothing.
    return found.filter(item => item.to !== undefined)
        .sort((a, b) => KINDS.indexOf(a.kind) - KINDS.indexOf(b.kind));
}

// How each relationship reads from either of its Nodes.
const WORDING = {
    relay: {
        from: item => (item.to ? `DHCP Relay to ${item.to.name} at ${item.address}`
            : `DHCP Relay to ${item.address}: no Node has this address`),
        to: item => `DHCP relayed from ${item.from.name}`,
    },
    peer: {
        from: item => (item.to ? `BGP peer ${item.to.name} at ${item.address}`
            : `BGP peer ${item.address}: no Node has this address`),
        to: item => `BGP peer ${item.from.name}, a Speaker`,
    },
    secondary: {
        from: item => `Secondary Zone ${item.zone} from Primary ${item.to.name}`,
        to: item => `Primary of ${item.zone} for Secondary ${item.from.name}`,
    },
    ds: {
        from: item => `DS delegation of ${item.child} to ${item.to.name}`,
        to: item => `DS parent Zone ${item.zone} on ${item.from.name}`,
    },
};

/**
 * The relationships of one Node, read from its side.
 *
 * @param {{nodes: Array<object>, links: Array<object>}} topology The draft's Topology.
 * @param {string} nodeId The Node whose relationships are wanted.
 * @returns {Array<{text: string, nodeId: ?string}>} Each relationship's
 *     wording and the other Node, or null when an address matches no Node.
 */
export function relationshipsOf(topology, nodeId) {
    const seen = new Set();
    return allRelationships(topology)
        .flatMap(item => [
            item.from.id === nodeId && { text: WORDING[item.kind].from(item), nodeId: item.to?.id ?? null },
            item.to?.id === nodeId && { text: WORDING[item.kind].to(item), nodeId: item.from.id },
        ])
        .filter(item => item && !seen.has(item.text) && seen.add(item.text));
}

/**
 * The Interfaces of every Node but one, as choices for an address picker.
 *
 * @param {{nodes: Array<object>, links: Array<object>}} topology The draft's Topology.
 * @param {string} exceptNodeId The Node being edited, whose own addresses are no use to it.
 * @returns {Array<{address: string, label: string}>} One per address, labelled
 *     "Node · Interface · address", in Node order.
 */
export function interfaceChoices(topology, exceptNodeId) {
    const ends = topology.links.flatMap(link => link.ends);
    const bare = address => String(address).split('/')[0];
    return topology.nodes.filter(node => node.id !== exceptNodeId).flatMap(node => {
        const named = ends.filter(end => end.node_id === node.id)
            .map(end => ({ name: `${node.name}-eth${end.port}`, item: end }));
        const subinterfaces = (node.subinterfaces || []).flatMap(sub => {
            const parent = named.find(entry => entry.item.interface_id === sub.parent_interface_id);
            return parent ? [{ name: `${parent.name}.${sub.vlan}`, item: sub }] : [];
        });
        return [...named, ...subinterfaces].flatMap(({ name, item }) => [item.ipv4, item.ipv6]
            .filter(Boolean).map(address => ({ address: bare(address),
                label: `${node.name} · ${name} · ${bare(address)}` })));
    });
}
