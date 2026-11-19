// The browser's half of a Packet Capture's PDML stream: one <packet>
// element in, the field tree a Learner expands out.
//
// The server forwards each packet's PDML untouched and the tree is built
// here, on arrival, because parsing on the server would cost fifty times what
// forwarding does and be paid in the process every Lab shares. Parsing on
// arrival rather than on click is also the cheaper way to hold a ring of
// packets: the tree keeps the labels and byte offsets and drops the hex, so
// it is about a third of the text it came from.

const PARSER = new DOMParser();

// PDML bookkeeping rather than a layer of the packet: `geninfo` restates the
// summary row, and the wrapper holds fields tshark could not place.
const SKIPPED_PROTOCOLS = new Set(['geninfo', 'fake-field-wrapper']);

function intAttr(el, name) {
    const raw = el.getAttribute(name);
    if (raw === null || raw === '') return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
}

// A field's label as Wireshark's detail pane prints it: `showname` when the
// dissector wrote one, otherwise the field name and its shown value.
function labelOf(el) {
    const showname = el.getAttribute('showname');
    if (showname) return showname;
    const name = el.getAttribute('name');
    const show = el.getAttribute('show');
    if (name && show !== null) return `${name}: ${show}`;
    return show || name || '';
}

function walk(el) {
    const node = {
        name: el.getAttribute('name') || '',
        label: labelOf(el),
        pos: intAttr(el, 'pos'),
        size: intAttr(el, 'size'),
        children: [],
    };
    for (const child of el.children) {
        // Wireshark hides these duplicates (eth.addr beside eth.src, and
        // the like) from its own tree; so does this one.
        if (child.getAttribute('hide') === 'yes') continue;
        node.children.push(walk(child));
    }
    return node;
}

/**
 * Parse one packet's PDML into the tree the pane renders.
 *
 * @param {string} pdml - One `<packet>` element, as the server forwards it.
 * @returns {{protocols: Array<object>}|null} One node per protocol layer,
 *   outermost first; each node carries `name`, `label`, `pos`, `size` and
 *   `children`. Null when the text is not a packet.
 */
export function parsePacket(pdml) {
    const doc = PARSER.parseFromString(pdml, 'application/xml');
    const root = doc.documentElement;
    if (!root || root.nodeName !== 'packet' || doc.getElementsByTagName('parsererror').length) {
        return null;
    }
    const protocols = [];
    for (const proto of root.children) {
        if (SKIPPED_PROTOCOLS.has(proto.getAttribute('name'))) continue;
        protocols.push(walk(proto));
    }
    return { protocols };
}

/**
 * Find the first node with a field name, depth first.
 *
 * @param {{protocols: Array<object>}} tree - What `parsePacket` returned.
 * @param {string} name - The field name, as Wireshark abbreviates it.
 * @returns {Array<object>|null} The path from the protocol layer down to the
 *   node, inclusive, or null when the packet has no such field.
 */
export function findField(tree, name) {
    const search = (node, path) => {
        const here = [...path, node];
        if (node.name === name) return here;
        for (const child of node.children) {
            const found = search(child, here);
            if (found) return found;
        }
        return null;
    };
    for (const proto of tree.protocols) {
        const found = search(proto, []);
        if (found) return found;
    }
    return null;
}
