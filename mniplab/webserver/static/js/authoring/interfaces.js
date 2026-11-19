// A Node's Interfaces: each Link end it holds, with its addresses, DHCP
// client flags, MAC (Host), VRF (Router) or port VLAN settings (Switch). The Node dialog's
// Interfaces tab and the Addresses… popover both render and bind these, so an
// address is edited the same way in either place. Addresses stay on the Link end.

import { escapeHtml } from '../utils/dom.js';
import { showAddressFamily } from './address-family.js';
import { parseCidr } from './addresses.js';

const ADDRESS_EXAMPLES = { '4': 'e.g., 192.0.2.1/24', '6': 'e.g., 2001:db8::1/64' };

/** Addresses declared in interface sections of FRR text, with their source. */
export function frrAddresses(node, config = node.frr_config || '') {
    let name = null;
    const addresses = [];
    for (const line of config.split('\n')) {
        const heading = /^interface\s+(\S+)/.exec(line);
        if (heading) name = heading[1];
        else if (line && !/^\s|^!/.test(line)) name = null;
        const address = name && /^\s+(?:ip|ipv6) address (\S+)/.exec(line);
        if (address && parseCidr(address[1])) addresses.push({
            name, address: address[1], label: `${address[1]} (from ${node.name}'s FRR config)`,
        });
    }
    return addresses;
}

/** Read-only FRR addresses; their text is never written onto a Link end. */
export function renderFrrAddresses(node, config) {
    const addresses = frrAddresses(node, config);
    return addresses.length ? `<p>FRR addresses</p><ul class="authoring-selection-list">`
        + addresses.map(item => `<li>${escapeHtml(item.name)}: ${escapeHtml(item.label)}</li>`).join('')
        + '</ul>' : '';
}

/**
 * The Interfaces of one Node, lowest port first.
 *
 * @param {object} node
 * @param {Array<object>} links
 * @param {Array<object>} nodes
 * @returns {Array<{link: object, end: object, peer: ?object, name: string}>}
 */
export function nodeInterfaces(node, links, nodes) {
    return links
        .flatMap(link => link.ends.map((end, index) => ({ link, end, other: link.ends[1 - index] })))
        .filter(({ end }) => end.node_id === node.id)
        .sort((a, b) => a.end.port - b.end.port)
        .map(({ link, end, other }) => ({
            link, end, peer: nodes.find(item => item.id === other.node_id) || null,
            name: `${node.name}-eth${end.port}`,
        }));
}

/** The id prefix of one Interface's controls; a second place showing them uses its own prefix. */
function interfaceBase(end, prefix = 'iface') {
    return `${prefix}-${end.interface_id}`;
}

/** The id of one of an Interface's controls, such as its IPv4 address field. */
export function interfaceControlId(end, field, prefix = 'iface') {
    return `${interfaceBase(end, prefix)}-${field}`;
}

function addressFields(end, id, host, compact) {
    const family = version => {
        const client = host && end[`dhcp${version}`];
        const field = `ipv${version}`;
        return `<label data-address-family="${version}" for="${id}-${field}">IPv${version} address and prefix</label>
        <input data-address-family="${version}" id="${id}-${field}" data-iface="${escapeHtml(end.interface_id)}" data-field="${field}"
            value="${escapeHtml(end[field] || '')}" autocomplete="off"
            placeholder="${client ? 'Assigned by DHCP' : ADDRESS_EXAMPLES[version]}"
            aria-describedby="${id}-${field}-error${client ? ` ${id}-dhcp-help` : ''}" ${client ? 'disabled' : ''}>
        <p id="${id}-${field}-error" class="field-error" hidden></p>`;
    };
    const iface = escapeHtml(end.interface_id);
    const hostFields = !host ? '' : `${compact ? '' : `<label for="${id}-mac">MAC address, optional</label>
        <input id="${id}-mac" data-iface="${iface}" data-field="mac" value="${escapeHtml(end.mac || '')}"
            placeholder="e.g., aa:bb:cc:dd:ee:01" autocomplete="off" aria-describedby="${id}-mac-help ${id}-mac-error">
        <p id="${id}-mac-help" class="authoring-note">Leave blank for a random address. Pin one when a DHCP
            Reservation names this client.</p>
        <p id="${id}-mac-error" class="field-error" hidden></p>`}
        <label data-address-family="4" class="authoring-choice"><input type="checkbox" id="${id}-dhcp4" data-iface="${iface}"
            data-client-family="4" aria-describedby="${id}-dhcp-help" ${end.dhcp4 ? 'checked' : ''}>
            DHCPv4 client</label>
        <label data-address-family="6" class="authoring-choice"><input type="checkbox" id="${id}-dhcp6" data-iface="${iface}"
            data-client-family="6" aria-describedby="${id}-dhcp-help" ${end.dhcp6 ? 'checked' : ''}>
            DHCPv6 client</label>
        <p id="${id}-dhcp-help" class="authoring-note">The Interface starts without that family's address;
            a Learner runs the DHCP client to get one.</p>`;
    return family('4') + family('6') + hostFields;
}

// A Router's outside mark: its NAT entries apply to the Interfaces marked so.
function outsideField(end, id) {
    return `<label class="authoring-choice"><input type="checkbox" id="${id}-outside"
        data-iface="${escapeHtml(end.interface_id)}" data-outside aria-describedby="${id}-outside-help ${id}-outside-error"
        ${end.outside ? 'checked' : ''}><span>Outside (NAT)</span></label>
        <p id="${id}-outside-help" class="authoring-note">The Router masquerades traffic that leaves through this
            Interface. Its Router NAT entries apply to it.</p>
        <p id="${id}-outside-error" class="field-error" hidden></p>`;
}

// Shown once the Router has a VRF to choose; a VRF the Router no longer has
// stays listed, so the field shows what is stored until it is changed.
function vrfField(node, end, id) {
    const names = (node.vrfs || []).map(vrf => vrf.name);
    if (end.vrf && !names.includes(end.vrf)) names.push(end.vrf);
    if (!names.length) return '';
    const options = names.map(name => `<option value="${escapeHtml(name)}"
        ${end.vrf === name ? 'selected' : ''}>${escapeHtml(name)}</option>`).join('');
    return `<label for="${id}-vrf">VRF</label>
        <select id="${id}-vrf" data-iface="${escapeHtml(end.interface_id)}" data-field="vrf"
            aria-describedby="${id}-vrf-error">
            <option value="" ${end.vrf ? '' : 'selected'}>Default table</option>${options}
        </select>
        <p id="${id}-vrf-error" class="field-error" hidden></p>`;
}

function vlanFields(end, id) {
    const iface = escapeHtml(end.interface_id);
    const mode = end.vlan?.mode;
    return `<label for="${id}-vlan-mode">VLAN mode</label>
        <select id="${id}-vlan-mode" data-iface="${iface}" data-vlan-field="mode">
            <option value="none" ${!end.vlan ? 'selected' : ''}>None</option>
            <option value="access" ${mode === 'access' ? 'selected' : ''}>Access</option>
            <option value="trunk" ${mode === 'trunk' ? 'selected' : ''}>Trunk</option>
        </select>
        <div id="${id}-access-fields" ${mode === 'access' ? '' : 'hidden'}>
            <label for="${id}-access-vlan">Access VLAN ID</label>
            <input id="${id}-access-vlan" type="number" min="1" max="4094" step="1"
                data-iface="${iface}" data-vlan-field="vlan" value="${escapeHtml(end.vlan?.vlan ?? '')}"
                aria-describedby="${id}-access-vlan-error">
            <p id="${id}-access-vlan-error" class="field-error" hidden></p>
        </div>
        <div id="${id}-trunk-fields" ${mode === 'trunk' ? '' : 'hidden'}>
            <label for="${id}-trunks">Allowed VLAN IDs, comma separated</label>
            <input id="${id}-trunks" data-iface="${iface}" data-vlan-field="trunks"
                value="${escapeHtml(end.vlan?.trunks?.join(', ') || '')}"
                placeholder="e.g., 10, 20" aria-describedby="${id}-trunks-error">
            <p id="${id}-trunks-error" class="field-error" hidden></p>
            <label for="${id}-native">Native VLAN ID, optional</label>
            <input id="${id}-native" type="number" min="1" max="4094" step="1"
                data-iface="${iface}" data-vlan-field="native"
                value="${escapeHtml(end.vlan?.native ?? '')}" aria-describedby="${id}-native-error">
            <p id="${id}-native-error" class="field-error" hidden></p>
        </div>`;
}

/**
 * Markup for the Interfaces of a Node: one group per Interface.
 *
 * @param {object} node
 * @param {Array<{link: object, end: object, peer: ?object, name: string}>} interfaces
 * @param {object} [options]
 * @param {boolean} [options.compact] Addresses and DHCP only, with no Link button, MAC, VRF or VLAN.
 * @param {string} [options.prefix] Id prefix for the controls.
 * @returns {string}
 */
export function renderInterfaces(node, interfaces, { compact = false, prefix = 'iface' } = {}) {
    const host = node.type === 'host';
    const layer2 = node.type === 'switch';
    return interfaces.map(({ link, end, peer, name }) => {
        const id = interfaceBase(end, prefix);
        const title = `${name} to ${peer ? peer.name : 'nothing'}`;
        const edit = compact ? '' : `<button type="button" class="btn-secondary btn-small"
            data-edit-link="${escapeHtml(link.id)}" aria-label="Edit Link ${escapeHtml(name)}">Edit Link…</button>`;
        return `<fieldset class="authoring-interface" data-interface="${escapeHtml(end.interface_id)}">
            <legend>${escapeHtml(title)}</legend>
            ${layer2 ? (compact ? '' : vlanFields(end, id)) : addressFields(end, id, host, compact)}
            ${node.type === 'router' && !compact ? vrfField(node, end, id) : ''}
            ${node.type === 'router' && !compact ? outsideField(end, id) : ''}
            ${edit}
        </fieldset>`;
    }).join('');
}

// Make an Interface a DHCP client of one family, or a static Interface again.
// A client's address field is disabled and described by the DHCP help, since
// a screen reader may not read a disabled field's placeholder.
function setDhcpClient(end, family, client, clearError, prefix) {
    const id = interfaceBase(end, prefix);
    const address = document.getElementById(`${id}-ipv${family}`);
    if (client) {
        end[`dhcp${family}`] = true;
        end[`ipv${family}`] = '';
        address.value = '';
        clearError(address);
    } else delete end[`dhcp${family}`];
    address.disabled = client;
    address.placeholder = client ? 'Assigned by DHCP' : ADDRESS_EXAMPLES[family];
    address.setAttribute('aria-describedby', `${id}-ipv${family}-error` + (client ? ` ${id}-dhcp-help` : ''));
}

/**
 * Write each control's value onto its Link end as the Instructor edits.
 *
 * @param {Element} root Holds the rendered Interfaces.
 * @param {object} hooks
 * @param {(interfaceId: string) => ?object} hooks.endOf The Link end an Interface id names.
 * @param {(control: Element) => void} hooks.clearError
 * @param {() => void} hooks.changed Called after every edit.
 * @param {string} [hooks.prefix] The id prefix the Interfaces were rendered with.
 */
export function bindInterfaces(root, { endOf, clearError, changed, prefix = 'iface' }) {
    root.querySelectorAll('[data-iface][data-field]').forEach(control => {
        control.addEventListener('input', () => {
            clearError(control);
            const end = endOf(control.dataset.iface);
            const key = control.dataset.field;
            if (key === 'mac' || key === 'vrf') {
                const value = control.value.trim();
                if (value) end[key] = value;
                else delete end[key];
            } else end[key] = control.value;
            changed();
        });
    });
    root.querySelectorAll('[data-iface][data-outside]').forEach(control => {
        control.addEventListener('change', () => {
            clearError(control);
            const end = endOf(control.dataset.iface);
            if (control.checked) end.outside = true;
            else delete end.outside;
            changed();
        });
    });
    root.querySelectorAll('[data-iface][data-client-family]').forEach(control => {
        control.addEventListener('change', () => {
            setDhcpClient(endOf(control.dataset.iface), control.dataset.clientFamily, control.checked, clearError, prefix);
            changed();
        });
    });
    root.querySelectorAll('[data-iface][data-vlan-field]').forEach(control => {
        const eventName = control.tagName === 'SELECT' ? 'change' : 'input';
        control.addEventListener(eventName, () => {
            clearError(control);
            const end = endOf(control.dataset.iface);
            const id = interfaceBase(end, prefix);
            const field = control.dataset.vlanField;
            if (field === 'mode') {
                if (control.value === 'none') delete end.vlan;
                else if (control.value === 'access') end.vlan = { mode: 'access', vlan: 1 };
                else end.vlan = { mode: 'trunk', trunks: [1], native: null };
                document.getElementById(`${id}-access-fields`).hidden = control.value !== 'access';
                document.getElementById(`${id}-trunk-fields`).hidden = control.value !== 'trunk';
                document.getElementById(`${id}-access-vlan`).value = end.vlan?.vlan ?? '';
                document.getElementById(`${id}-trunks`).value = end.vlan?.trunks?.join(', ') ?? '';
                document.getElementById(`${id}-native`).value = end.vlan?.native ?? '';
            } else if (end.vlan) {
                if (field === 'trunks') end.vlan.trunks = control.value.split(',').map(value =>
                    value.trim() === '' ? '' : Number(value.trim()));
                else if (field === 'native') end.vlan.native = control.value === '' ? null : Number(control.value);
                else end.vlan.vlan = control.value === '' ? '' : Number(control.value);
            }
            changed();
        });
    });
}

let addressesDeps = null;
let addressesApply = () => {};
let addressesTrigger = null;
let addressesNodeId = null;

/**
 * Open the Addresses… popover dialog for a Node.
 *
 * @param {string} nodeId
 * @param {HTMLElement} [trigger]
 */
export function openAddressesDialog(nodeId, trigger) {
    if (!addressesDeps) return;
    const { getDraft, nodeById, endConnect, closeDialogs, previewFrr, clearFieldError, updateDirtyState } = addressesDeps;
    const draft = getDraft();
    const node = nodeById(nodeId);
    if (!node || !draft) return;
    endConnect();
    closeDialogs();
    const dialog = document.getElementById('addresses-dialog');
    const interfaces = (node.readOnly ? [] : nodeInterfaces(node, draft.topology.links, draft.topology.nodes))
        .map(item => ({ ...item, end: structuredClone(item.end) }));
    document.getElementById('addresses-title').textContent = `Addresses of ${node.name}`;
    const list = document.getElementById('addresses-list');
    list.innerHTML = renderInterfaces(node, interfaces, { compact: true, prefix: 'addr' })
        + renderFrrAddresses(node, previewFrr(nodeId));
    document.getElementById('addresses-empty').hidden = interfaces.length > 0
        || Boolean(renderFrrAddresses(node, previewFrr(nodeId)));
    document.getElementById('addresses-apply').hidden = !interfaces.length;
    showAddressFamily(list, draft.address_family);
    bindInterfaces(list, {
        endOf: interfaceId => interfaces.find(item => item.end.interface_id === interfaceId)?.end,
        clearError: clearFieldError,
        changed: () => {},
        prefix: 'addr',
    });
    addressesApply = () => {
        for (const { link, end } of interfaces) {
            const real = link.ends.find(item => item.interface_id === end.interface_id);
            for (const key of ['ipv4', 'ipv6']) real[key] = end[key];
            for (const key of ['dhcp4', 'dhcp6']) {
                if (end[key]) real[key] = true;
                else delete real[key];
            }
        }
        updateDirtyState();
    };
    addressesTrigger = trigger || null;
    addressesNodeId = nodeId;
    dialog.showModal();
    (list.querySelector('input:not([disabled])') || dialog.querySelector('[data-close]'))?.focus();
}

/**
 * Close the Addresses… popover dialog, optionally applying edits.
 *
 * @param {boolean} apply
 */
export function closeAddressesDialog(apply) {
    const dialog = document.getElementById('addresses-dialog');
    if (apply) addressesApply();
    dialog?.close();
    const canvasItem = addressesDeps?.canvasItem;
    ((canvasItem && canvasItem('host', addressesNodeId)) || addressesTrigger || document.getElementById('palette-host'))?.focus();
}

/**
 * Initialize the Addresses… popover dialog and bind its events.
 *
 * @param {object} deps
 */
export function initAddressesDialog(deps) {
    addressesDeps = deps;
    const dialog = document.getElementById('addresses-dialog');
    if (!dialog) return;
    dialog.querySelectorAll('[data-close]').forEach(button =>
        button.addEventListener('click', () => closeAddressesDialog(false)));
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        closeAddressesDialog(false);
    });
    dialog.querySelector('form')?.addEventListener('submit', event => {
        event.preventDefault();
        closeAddressesDialog(true);
    });
}

