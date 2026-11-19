// Router NAT on the canvas: the outside Interfaces a NAT entry can apply to, a
// plain sentence for each entry, and the rename that keeps an entry naming the
// Interface it named before its Router was renamed.

/**
 * The names of a Router's outside Interfaces, lowest port first, as the Lab names them.
 *
 * @param {object} node A Router.
 * @param {object[]} links The Links, whose ends carry the outside marks.
 * @returns {string[]}
 */
export function outsideInterfaceNames(node, links) {
    return links.flatMap(link => link.ends)
        .filter(end => end.node_id === node.id && end.outside)
        .sort((a, b) => a.port - b.port)
        .map(end => `${node.name}-eth${end.port}`);
}

/**
 * One NAT entry as a sentence a reader can check against the Router's rules.
 *
 * @param {object} entry A port forward or a static mapping.
 * @returns {string}
 */
export function describeRouterNat(entry) {
    const via = entry.interface ? ` on ${entry.interface}` : '';
    if (entry.kind === 'static') return `Static 1:1: ${entry.inside} is ${entry.outside}${via}`;
    const hairpin = entry.hairpin ? ', hairpin' : '';
    return `Port forward ${entry.protocol} ${entry.outside_port} to ${entry.inside}:${entry.inside_port}${hairpin}${via}`;
}

/**
 * Point the NAT entries that named a Router's Interfaces at its new name.
 *
 * @param {object} node The Router, already renamed.
 * @param {string} previousName The name it had before this change.
 */
export function renameRouterNatInterfaces(node, previousName) {
    if (!node.nat || previousName === node.name) return;
    const pattern = new RegExp(`^${previousName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}-eth(\\d+)$`);
    for (const entry of node.nat) {
        if (entry.interface && pattern.test(entry.interface)) {
            entry.interface = entry.interface.replace(pattern, `${node.name}-eth$1`);
        }
    }
}
