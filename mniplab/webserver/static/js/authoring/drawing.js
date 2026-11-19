// What only a draft's script knows, drawn read-only on the canvas: a Python
// draft's Nodes and Links, or those a Canvas draft's Code section adds. The
// server records them by running the script against a stand-in network.

/**
 * Turn a recorded drawing into canvas Nodes and Links.
 *
 * A Python draft's Nodes are keyed by name, as its Layout is. A Code
 * section's Nodes are keyed ``code:<name>``, apart from the canvas's own IDs,
 * and its Links reach canvas Nodes by their names.
 *
 * @param {{nodes: Array<object>, links: Array<object>}} drawing The server's drawing.
 * @param {?Array<object>} canvasNodes A Canvas draft's own Nodes; null for a Python draft.
 * @returns {{nodes: Array<object>, links: Array<object>}}
 */
export function drawnItems(drawing, canvasNodes) {
    const fromCode = canvasNodes !== null;
    const canvasIds = new Map((canvasNodes || []).map(node => [node.name, node.id]));
    const idOf = name => canvasIds.get(name) ?? (fromCode ? `code:${name}` : name);
    const nodes = drawing.nodes.filter(node => !canvasIds.has(node.name)).map(node => ({
        id: idOf(node.name), name: node.name, type: node.kind, line: node.line,
        ...(node.nat ? { nat: node.nat } : {}), readOnly: true, fromCode,
    }));
    const links = drawing.links.map((link, index) => ({
        id: `drawn-${index}`,
        ends: link.ends.map(end => ({
            node_id: idOf(end.node), port: end.port, ...(end.outside ? { outside: true } : {}),
        })),
        line: link.line, readOnly: true, fromCode,
    }));
    return { nodes, links };
}

/**
 * Where a read-only Node or Link is made, for the Selection and announcements.
 *
 * @param {object} item A drawn Node or Link.
 * @returns {string}
 */
export function madeAt(item) {
    if (item.line === null || item.line === undefined) return 'Drawn from the running Preview.';
    return item.fromCode ? `The Code section makes it at line ${item.line}.`
        : `The script makes it at line ${item.line}.`;
}
