// The sidebar's Selection section: a read-only summary of the selected Node or
// Link, with its actions. Selecting never switches the sidebar's tab.
import { escapeHtml } from '../utils/dom.js';
import { renderFrrAddresses } from './interfaces.js';
import { madeAt } from './drawing.js';
import { describeRouterNat, outsideInterfaceNames } from './router-nat.js';

const SERVICES = [['dhcp', 'DHCP'], ['dns', 'Authoritative DNS'], ['resolver', 'Resolver'], ['relay', 'DHCP Relay']];

let context = null;

// A Link end's addresses as a reader needs them, or that it has none.
function addressesOf(end) {
    const addresses = [end.ipv4, end.ipv6].filter(Boolean);
    if (end.dhcp4) addresses.push('DHCPv4 client');
    if (end.dhcp6) addresses.push('DHCPv6 client');
    return addresses.join(', ') || 'no address yet';
}

// A Link end's outside mark, named for a reader of the Selection.
function outsideNote(end) {
    return end.outside ? ' · outside (NAT)' : '';
}

function labelledList(id, label, items) {
    return `<p class="authoring-selection-label" id="${id}">${label}</p>
        <ul class="authoring-selection-list" aria-labelledby="${id}">${items.join('')}</ul>`;
}

// A Router's outside Interfaces and NAT entries, as the Selection reads them. A
// drawn Router's addresses are not known here, so only these two lists are shown.
function natDetails(node) {
    if (node.type !== 'router') return '';
    const outside = outsideInterfaceNames(node, context.links())
        .map(name => `<li>${escapeHtml(name)}</li>`);
    return [
        outside.length ? labelledList('selection-outside', 'Outside Interfaces', outside) : '',
        node.nat?.length
            ? labelledList('selection-nat', 'Router NAT', node.nat.map(entry => `<li>${escapeHtml(describeRouterNat(entry))}</li>`))
            : '',
    ].join('');
}

function nodeDetails(node) {
    const interfaces = context.links()
        .flatMap(link => link.ends.map((end, index) => ({ link, end, peer: link.ends[1 - index] })))
        .filter(({ end }) => end.node_id === node.id)
        .sort((a, b) => a.end.port - b.end.port)
        .map(({ link, end, peer }) => {
            const name = `${context.interfaceName(end)} to ${context.nodeLabel(context.nodeById(peer.node_id))}`;
            return `<li><button type="button" class="authoring-link-button" data-link-id="${escapeHtml(link.id)}">`
                + `${escapeHtml(name)}</button> <span class="authoring-selection-addresses">`
                + `${escapeHtml(addressesOf(end))}${escapeHtml(outsideNote(end))}</span></li>`;
        });
    const services = SERVICES.filter(([field]) => node[field]).map(([, label]) => label);
    const related = context.relationships(node.id).map(item => `<li>${escapeHtml(item.text)}</li>`);
    return [
        interfaces.length ? labelledList('selection-interfaces', 'Interfaces', interfaces)
            : '<p class="authoring-hint">No Links yet.</p>',
        services.length ? `<p>Services: ${services.join(', ')}</p>` : '',
        natDetails(node),
        related.length ? labelledList('selection-relationships', 'Relationships', related) : '',
    ].join('');
}

function linkDetails(link) {
    return labelledList('selection-ends', 'Ends', link.ends.map(end =>
        `<li>${escapeHtml(context.interfaceName(end))}: ${escapeHtml(addressesOf(end))}${escapeHtml(outsideNote(end))}</li>`));
}

/** Show the selected Node or Link, or hide the section when nothing is selected. */
export function renderSelection() {
    const section = document.getElementById('selection');
    const { nodeId, linkId } = context.selection();
    const node = nodeId && context.nodeById(nodeId);
    const link = linkId && context.linkById(linkId);
    section.hidden = !node && !link;
    if (section.hidden) return;
    document.getElementById('selection-title').textContent = node
        ? context.describeNode(node) : context.linkTitle(link);
    const item = node || link;
    // A drawn item is only moved here; its script is where it changes.
    const edit = document.getElementById('selection-edit');
    if (item.readOnly) {
        document.getElementById('selection-details').innerHTML =
            `<p>Read-only. ${escapeHtml(madeAt(item))}</p>${node ? natDetails(node) : ''}`;
        edit.textContent = item.fromCode ? 'Show in Code section' : 'Show in script';
        edit.removeAttribute('aria-haspopup');
    } else {
        document.getElementById('selection-details').innerHTML = node ? nodeDetails(node) : linkDetails(link);
        edit.textContent = 'Edit…';
        edit.setAttribute('aria-haspopup', 'dialog');
    }
    document.getElementById('selection-addresses').hidden = !node || (node.readOnly && !context.frr(node.id)) || node.type === 'switch';
    if (node) {
        document.getElementById('selection-details').insertAdjacentHTML('beforeend',
            renderFrrAddresses(node, context.frr(node.id)));
    }
    const changes = node ? context.changes(node.id) : 0;
    document.getElementById('selection-keep').hidden = !changes;
    document.getElementById('selection-keep-count').textContent =
        `${changes} change${changes === 1 ? '' : 's'} in the Preview · `;
    document.getElementById('selection-connect').hidden = !node || node.readOnly;
    document.getElementById('selection-remove').hidden = !node || node.readOnly;
}

/**
 * Bind the Selection section's actions.
 *
 * @param {object} options
 * @param {() => {nodeId: ?string, linkId: ?string}} options.selection What is selected.
 * @param {() => Array<object>} options.links The draft's Links.
 * @param {(id: string) => ?object} options.nodeById
 * @param {(id: string) => ?object} options.linkById
 * @param {(node: ?object) => string} options.nodeLabel A Node's name.
 * @param {(node: object) => string} options.describeNode Kind and name, such as "Router r1".
 * @param {(link: object) => string} options.linkTitle Such as "Link between h1 and r1".
 * @param {(end: object) => string} options.interfaceName Such as "r1-eth0".
 * @param {(nodeId: string) => Array<{text: string}>} options.relationships A Node's relationships.
 * @param {(kind: string, id: string, trigger: Element) => void} options.edit Open the item's dialog.
 * @param {(nodeId: string, trigger: Element) => void} options.addresses Open the Node's Addresses… popover.
 * @param {(nodeId: string, trigger: Element) => void} options.connect Start linking this Node.
 * @param {(nodeId: string) => void} options.remove Remove this Node.
 * @param {(linkId: string) => void} options.focusLink Focus a Link on the canvas.
 */
export function initSelection(options) {
    context = options;
    document.getElementById('selection-keep-action').addEventListener('click', event =>
        context.keep(context.selection().nodeId, event.currentTarget));
    const current = () => {
        const { nodeId, linkId } = context.selection();
        return nodeId ? { kind: 'host', id: nodeId } : { kind: 'link', id: linkId };
    };
    document.getElementById('selection-edit').addEventListener('click', event => {
        const { kind, id } = current();
        context.edit(kind, id, event.currentTarget);
    });
    document.getElementById('selection-addresses').addEventListener('click', event =>
        context.addresses(current().id, event.currentTarget));
    document.getElementById('selection-connect').addEventListener('click', event =>
        context.connect(current().id, event.currentTarget));
    document.getElementById('selection-remove').addEventListener('click', () => context.remove(current().id));
    document.getElementById('selection-details').addEventListener('click', event => {
        const button = event.target.closest('button[data-link-id]');
        if (button) context.focusLink(button.dataset.linkId);
    });
}
