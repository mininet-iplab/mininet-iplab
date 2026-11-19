// The tabbed Node dialog for Lab Authoring (D8, D27).

import { addressVersions, showAddressFamily, withAddressFamily } from './address-family.js';
import { parseAddress, parseCidr } from './addresses.js';
import { createZoneSpec } from './dns-zones.js';
import { closeInfoTips, initInfoTips } from './info-tip.js';
import { HOSTED_CAS, rpkiServiceError } from './rpki-services.js';
import { bindInterfaces, interfaceControlId, nodeInterfaces, renderInterfaces } from './interfaces.js';
import { interfaceChoices } from './relationships.js';
import { outsideInterfaceNames, renameRouterNatInterfaces } from './router-nat.js';
import { blankGenerator, createGlobalGenerator, generatorErrors } from './route-generators.js';
import { createRowEditor, revealRowControl, validateItems } from './row-editor.js';
import {
    ACCESS_CONTROL, DHCP_POOLS, RELAY_SERVERS, SPEAKER_NEIGHBORS, SPEAKER_ROUTES,
} from './service-lists.js';
import { bindTabs, selectTab } from './shell.js';
import { escapeHtml } from '../utils/dom.js';

export const HOST_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,7}$/;
const STARTUP_STAGES = [['before-daemons', 'Before daemons'], ['after-start', 'After start']];
const DEFAULT_STARTUP_TIMEOUT = 5;
const TYPE_LABELS = {
    host: 'Host',
    container: 'Container Host',
    nat: 'Internet Gateway',
    transit: 'Transit Internet Gateway',
    speaker: 'BGP Speaker',
    router: 'Router',
    switch: 'Switch',
};

export const NODE_TABS = {
    host: ['general', 'services', 'startup', 'interfaces'],
    container: ['general', 'startup', 'interfaces'],
    router: ['general', 'routing', 'services', 'startup', 'interfaces'],
    switch: ['general', 'interfaces'],
    nat: ['general', 'nat', 'interfaces'],
    transit: ['general', 'nat', 'interfaces'],
    speaker: ['general', 'speaker', 'startup', 'interfaces'],
};

export const TAB_LABELS = {
    general: 'General',
    routing: 'Routing',
    speaker: 'Speaker',
    nat: 'NAT',
    services: 'Services',
    startup: 'Startup',
    interfaces: 'Interfaces',
};

export const CONTROL_TAB_MAP = {
    'host-name': 'general',
    'host-x': 'general',
    'host-y': 'general',
    'container-image': 'general',
    'switch-stp': 'general',
    'switch-priority': 'general',
    'host-ipv4-gateway': 'interfaces',
    'host-ipv6-gateway': 'interfaces',
    'host-nameserver-addresses': 'general',
    'host-nameserver-choices': 'general',

    'router-protocols': 'routing',
    'router-fast-timers': 'routing',
    'router-frr-config': 'routing',
    'router-subinterfaces': 'routing',
    'router-vrfs': 'routing',
    'router-nat': 'routing',

    'speaker-local-as': 'speaker',
    'speaker-neighbors': 'speaker',
    'speaker-routes': 'speaker',

    'nat-ip': 'nat',
    'nat-subnet': 'nat',
    'nat-downstream-fields': 'nat',

    'dhcp-pools': 'services',
    'dns-zones': 'services',
    'dns-secondary-source': 'services',
    'resolver-root-addresses': 'services',
    'resolver-forwarders': 'services',
    'resolver-max-ttl': 'services',
    'resolver-max-negative-ttl': 'services',
    'resolver-access-control': 'services',
    'relay-subnets': 'services',
    'relay-servers': 'services',
    'rpki-ca-enabled': 'services',
    'rpki-ca-hostname': 'services',
    'validator-enabled': 'services',
    'validator-refresh': 'services',

    'node-startup': 'startup',
};

/**
 * Returns the tab key for a given control ID.
 *
 * @param {string} controlId
 * @returns {string}
 */
export function tabForControl(controlId) {
    if (!controlId) return 'general';
    const panel = typeof document !== 'undefined'
        ? document.getElementById(controlId)?.closest?.('[role="tabpanel"]')
        : null;
    if (panel?.id?.startsWith('node-panel-')) {
        return panel.id.replace('node-panel-', '');
    }
    if (CONTROL_TAB_MAP[controlId]) return CONTROL_TAB_MAP[controlId];
    if (controlId.startsWith('iface-')) return 'interfaces';
    if (controlId.startsWith('startup-')) return 'startup';
    if (controlId.startsWith('subinterface-')) return 'routing';
    if (controlId.startsWith('vrf-')) return 'routing';
    if (controlId.startsWith('router-nat-')) return 'routing';
    if (controlId.startsWith('resolver-')) return 'services';
    if (controlId.startsWith('relay-')) return 'services';
    if (controlId.startsWith('rpki-') || controlId.startsWith('validator-')) return 'services';
    if (controlId.startsWith('dhcp-')) return 'services';
    if (controlId.startsWith('dns-')) return 'services';
    if (controlId.startsWith('nat-')) return 'nat';
    if (controlId.startsWith('speaker-')) return 'speaker';
    return 'general';
}

/**
 * Switch to the tab containing a control and open any enclosing details.
 *
 * @param {string|HTMLElement} control
 */
export function revealControl(control) {
    const el = typeof control === 'string' ? document.getElementById(control) : control;
    if (!el) return;
    const tabKey = tabForControl(el.id);
    selectTab(`node-tab-${tabKey}`);
    revealRowControl(el);
    el.focus();
}

/**
 * Configure which tabs are shown for this Node kind and activate the first one.
 *
 * @param {string} nodeType
 * @param {string} [activeTab]
 * @returns {string} The key of the active tab.
 */
export function setVisibleTabs(nodeType, activeTab) {
    const tablist = document.getElementById('node-dialog-tabs');
    if (!tablist) return 'general';
    const allowed = NODE_TABS[nodeType] || ['general', 'interfaces'];

    for (const tab of tablist.querySelectorAll('[role="tab"]')) {
        const key = tab.id.replace('node-tab-', '');
        const visible = allowed.includes(key);
        tab.hidden = !visible;
    }

    const targetKey = activeTab && allowed.includes(activeTab) ? activeTab : allowed[0];
    selectTab(`node-tab-${targetKey}`);
    return targetKey;
}

/**
 * Initialize Node dialog tabs and bindings.
 *
 * @param {Object} options
 */
export function initNodeDialogTabs(options = {}) {
    const tablist = document.getElementById('node-dialog-tabs');
    if (tablist) {
        bindTabs(tablist, options.onTabSelect || (() => {}));
    }

    const summary = document.getElementById('node-dialog-error-summary');
    if (summary) {
        summary.addEventListener('click', event => {
            const link = event.target.closest('a[href^="#"]');
            if (!link) return;
            event.preventDefault();
            const controlId = link.getAttribute('href').slice(1);
            revealControl(controlId);
        });
    }
}

/**
 * Update error summary and tab error marks for the current Node dialog.
 *
 * @param {Array<{controlId: string, message: string}>} errors
 */
export function updateNodeDialogErrors(errors = []) {
    const summary = document.getElementById('node-dialog-error-summary');
    const heading = document.getElementById('node-dialog-error-heading');
    const list = document.getElementById('node-dialog-error-list');
    const tablist = document.getElementById('node-dialog-tabs');
    if (!summary || !tablist) return;

    // Clear existing tab error marks
    tablist.querySelectorAll('.dialog-tab-error-mark, .dialog-tab-error-text').forEach(el => el.remove());

    if (!errors.length) {
        summary.hidden = true;
        return;
    }

    const count = errors.length;
    heading.textContent = count === 1 ? '1 field needs attention' : `${count} fields need attention`;

    list.innerHTML = errors.map(err => {
        const tabKey = tabForControl(err.controlId);
        const tabLabel = TAB_LABELS[tabKey] || tabKey;
        const control = document.getElementById(err.controlId);
        const fieldLabel = control?.getAttribute('aria-label') || control?.labels?.[0]?.textContent.replace('(required)', '').replace('*', '').trim() || err.controlId;
        return `<li><a href="#${escapeHtml(err.controlId)}" data-tab="${escapeHtml(tabKey)}">${escapeHtml(tabLabel)} › ${escapeHtml(fieldLabel)}: ${escapeHtml(err.message)}</a></li>`;
    }).join('');

    summary.hidden = false;

    // Add error marks to tabs holding errors
    const tabCounts = {};
    for (const err of errors) {
        const tabKey = tabForControl(err.controlId);
        tabCounts[tabKey] = (tabCounts[tabKey] || 0) + 1;
    }

    for (const [key, n] of Object.entries(tabCounts)) {
        const tab = document.getElementById(`node-tab-${key}`);
        if (!tab) continue;
        const mark = document.createElement('span');
        mark.className = 'dialog-tab-error-mark';
        mark.setAttribute('aria-hidden', 'true');
        mark.textContent = ' ⚠';
        const srCount = document.createElement('span');
        srCount.className = 'sr-only dialog-tab-error-text';
        srCount.textContent = `, ${n} error${n > 1 ? 's' : ''}`;
        tab.append(mark, srCount);
    }
}

function syncNodeDialogErrors(dialog) {
    if (!dialog || dialog.id !== 'host-dialog') return;
    const invalids = Array.from(dialog.querySelectorAll('[aria-invalid="true"]'));
    const errors = invalids.map(ctrl => {
        const errorEl = document.getElementById(`${ctrl.id}-error`);
        return {
            controlId: ctrl.id,
            message: errorEl?.textContent || 'Needs attention',
        };
    });
    updateNodeDialogErrors(errors);
}

/**
 * Clear an inline field error and update the Node dialog error summary if needed.
 *
 * @param {HTMLElement|null} control
 */
export function clearFieldError(control) {
    if (!control) return;
    control.removeAttribute('aria-invalid');
    const error = document.getElementById(`${control.id}-error`);
    if (error) {
        error.textContent = '';
        error.hidden = true;
    }
    const dialog = control.closest?.('.authoring-dialog');
    if (dialog && dialog.id === 'host-dialog') {
        syncNodeDialogErrors(dialog);
    }
}

/**
 * Show an inline field error on a control and update the Node dialog error summary if needed.
 *
 * @param {HTMLElement|null} control
 * @param {string} message
 * @param {boolean} [focus]
 */
export function showFieldError(control, message, focus = true) {
    if (!control) return;
    const error = document.getElementById(`${control.id}-error`);
    control.setAttribute('aria-invalid', 'true');
    if (error) {
        error.textContent = message;
        error.hidden = false;
    }
    revealRowControl(control);
    const dialog = control.closest?.('.authoring-dialog');
    if (dialog && dialog.id === 'host-dialog') {
        syncNodeDialogErrors(dialog);
    }
    if (focus) {
        control.focus();
    }
}

/**
 * Clear all field errors inside a dialog.
 *
 * @param {HTMLElement} dialog
 */
export function clearDialogErrors(dialog) {
    dialog?.querySelectorAll('[aria-invalid="true"]').forEach(clearFieldError);
}

/**
 * What is wrong with a Node's name, as the dialog and the in-place rename both say it.
 *
 * @param {string} noun
 * @param {string} name
 * @param {boolean} taken
 * @returns {string|null}
 */
export function nameProblem(noun, name, taken) {
    if (!name) return `Enter a ${noun} name.`;
    if (!HOST_NAME_PATTERN.test(name)) {
        return `Use a unique ${noun} name starting with a letter, up to 8 characters.`;
    }
    return taken ? `Choose a unique ${noun} name.` : null;
}

/**
 * What is wrong with a VRF name, if anything: Linux names it as a device in
 * the Router's namespace, beside lo and the Router's own Interfaces.
 *
 * @param {Object} node
 * @param {string} name
 * @returns {string}
 */
export function vrfNameProblem(node, name) {
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,14}$/.test(name)) {
        return 'Enter up to 15 letters, digits, - or _, starting with a letter.';
    }
    if (name === 'default') return "default is FRR's own default VRF; choose another name.";
    if (name === 'lo' || new RegExp(`^${(node.name || '').replace(/[^A-Za-z0-9]/g, '\\$&')}-eth\\d+$`).test(name)) {
        return "That is one of this Router's Interfaces; choose another name.";
    }
    return '';
}

function isBlank(value) {
    return value === '' || value === null || value === undefined;
}

function hasSeenName(seenNames, lowerName) {
    if (!seenNames) return false;
    return seenNames.has(lowerName);
}

function addSeenName(seenNames, lowerName, nodeId) {
    if (!seenNames) return;
    if (seenNames instanceof Map) seenNames.set(lowerName, nodeId);
    else if (seenNames instanceof Set) seenNames.add(lowerName);
}

/**
 * Validate a Node and its attached Interfaces, returning a structured target on error or null.
 *
 * @param {Object} node
 * @param {Object} draft
 * @param {Map|Set} [seenNames]
 * @returns {{kind: 'node', id: string, controlId: string, message: string}|null}
 */
export function validateNode(node, draft, seenNames = new Map()) {
    const noun = TYPE_LABELS[node.type] || 'Node';
    const target = { kind: 'node', id: node.id };

    if (node.type === 'container' && (!node.image || !/^[A-Za-z0-9][A-Za-z0-9._/:@-]*$/.test(node.image))) {
        return {
            ...target,
            controlId: 'container-image',
            message: 'Enter a Docker image reference, such as alpine:3.20.',
        };
    }
    if (node.type === 'host' || node.type === 'container') {
        if (node.ipv4_gateway && parseAddress(node.ipv4_gateway)?.family !== 4) {
            return {
                ...target,
                controlId: 'host-ipv4-gateway',
                message: 'Enter a valid IPv4 default gateway address.',
            };
        }
        if (node.ipv6_gateway && parseAddress(node.ipv6_gateway)?.family !== 6) {
            return {
                ...target,
                controlId: 'host-ipv6-gateway',
                message: 'Enter a valid IPv6 default gateway address.',
            };
        }
    }
    if (node.type === 'nat' || node.type === 'transit') {
        if (!node.ip) return { ...target, controlId: 'nat-ip', message: 'Enter a gateway IPv4 address.' };
        if (!node.subnet) return { ...target, controlId: 'nat-subnet', message: 'Enter a translated subnet.' };
        if (node.type === 'transit' && !node.downstream_ids?.length) {
            return {
                ...target,
                controlId: 'nat-downstream-fields',
                message: 'Choose at least one downstream Router.',
            };
        }
    }
    if (node.type === 'speaker') {
        if (!Number.isInteger(node.local_as) || node.local_as < 1 || node.local_as > 4294967295) {
            return {
                ...target,
                controlId: 'speaker-local-as',
                message: 'Enter an AS number from 1 to 4294967295.',
            };
        }
        if (!node.neighbors?.length) {
            return {
                ...target,
                controlId: 'speaker-neighbors',
                message: 'Add at least one Neighbor.',
            };
        }
        const globalErrors = generatorErrors(node.route_generator);
        const firstGlobal = Object.keys(globalErrors)[0];
        const speakerError = (firstGlobal && {
            controlId: `speaker-generator-${firstGlobal}`,
            message: globalErrors[firstGlobal],
        })
            || validateItems(SPEAKER_NEIGHBORS, node.neighbors, node)[0]
            || validateItems(SPEAKER_ROUTES, node.routes, node)[0];
        if (speakerError) return { ...target, ...speakerError };
    }
    if (node.relay) {
        if (!node.relay.subnets?.length) {
            return {
                ...target,
                controlId: 'relay-subnets',
                message: 'Enter at least one Subnet CIDR to relay, separated by commas.',
            };
        }
        if (!node.relay.servers?.length) {
            return {
                ...target,
                controlId: 'relay-servers',
                message: 'Add at least one DHCP server.',
            };
        }
        const serverError = validateItems(RELAY_SERVERS, node.relay.servers)[0];
        if (serverError) return { ...target, ...serverError };
    }
    const poolError = node.dhcp && validateItems(DHCP_POOLS, node.dhcp.pools)[0];
    if (poolError) return { ...target, ...poolError };
    const dnsSpec = createZoneSpec(() => draft?.topology);
    const zoneError = node.dns && validateItems(dnsSpec, node.dns.zones, node)[0];
    if (zoneError) return { ...target, ...zoneError };
    const listError = node.resolver && validateItems(ACCESS_CONTROL, node.resolver.access_control)[0];
    if (listError) return { ...target, ...listError };
    const rpkiError = draft?.topology && rpkiServiceError(node, draft.topology);
    if (rpkiError) return { ...target, ...rpkiError };

    if (node.type === 'switch' && node.priority !== undefined) {
        const limit = node.stp === 'rstp' ? 61440 : 65535;
        if (!node.stp || !Number.isInteger(node.priority) || node.priority < 0
            || node.priority > limit || (node.stp === 'rstp' && node.priority % 4096)) {
            return { ...target, controlId: 'switch-priority',
                message: node.stp === 'rstp' ? 'Enter a multiple of 4096 from 0 to 61440.'
                    : 'Enable spanning tree and enter an integer from 0 to 65535.' };
        }
    }

    const lowerName = (node.name || '').toLowerCase();
    const nameIssue = nameProblem(noun, node.name, hasSeenName(seenNames, lowerName));
    if (nameIssue) return { ...target, controlId: 'host-name', message: nameIssue };
    addSeenName(seenNames, lowerName, node.id);

    if (draft?.layout?.nodes) {
        const position = draft.layout.nodes[node.id] || {};
        for (const axis of ['x', 'y']) {
            const value = position[axis];
            if (isBlank(value)) {
                return { ...target, controlId: `host-${axis}`, message: 'Enter a canvas position.' };
            }
            if (!Number.isFinite(value)) {
                return {
                    ...target,
                    controlId: `host-${axis}`,
                    message: 'Enter a finite number.',
                };
            }
        }
    }

    for (const [index, command] of (node.startup || []).entries()) {
        if (!command.command?.trim()) {
            return { ...target, controlId: `startup-${index}-command`, message: 'Enter a shell command.' };
        }
        if (!Number.isInteger(command.timeout) || command.timeout < 1 || command.timeout > 60) {
            return { ...target, controlId: `startup-${index}-timeout`, message: 'Enter whole seconds from 1 to 60.' };
        }
    }

    const links = draft?.topology?.links || [];
    for (const [index, sub] of (node.subinterfaces || []).entries()) {
        const parents = links.flatMap(link => link.ends || [])
            .filter(end => end.node_id === node.id)
            .map(end => end.interface_id);
        if (!parents.includes(sub.parent_interface_id)) {
            return {
                ...target,
                controlId: `subinterface-${index}-parent`,
                message: 'Choose an attached parent Interface.',
            };
        }
        if (!Number.isInteger(sub.vlan) || sub.vlan < 1 || sub.vlan > 4094) {
            return {
                ...target,
                controlId: `subinterface-${index}-vlan`,
                message: 'Enter a VLAN ID from 1 to 4094.',
            };
        }
        if (node.subinterfaces.some((other, otherIndex) => otherIndex < index
            && other.parent_interface_id === sub.parent_interface_id && other.vlan === sub.vlan)) {
            return {
                ...target,
                controlId: `subinterface-${index}-vlan`,
                message: 'Choose a different VLAN ID for this parent Interface.',
            };
        }
        for (const family of ['4', '6']) {
            const addr = sub[`ipv${family}`];
            if (addr) {
                const parsed = addr.includes('/') ? parseCidr(addr) : null;
                if (!parsed || parsed.family !== Number(family)) {
                    return {
                        ...target,
                        controlId: `subinterface-${index}-ipv${family}`,
                        message: `Enter a valid IPv${family} address with a prefix length.`,
                    };
                }
            }
        }
    }

    for (const [index, vrf] of (node.vrfs || []).entries()) {
        const earlier = node.vrfs.slice(0, index);
        const vrfIssue = vrfNameProblem(node, vrf.name)
            || (earlier.some(other => other.name === vrf.name) && 'Choose a name no other VRF on this Router uses.');
        if (vrfIssue) return { ...target, controlId: `vrf-${index}-name`, message: vrfIssue };
        if (!Number.isInteger(vrf.table) || vrf.table < 1 || vrf.table > 4294967295
            || [253, 254, 255].includes(vrf.table)) {
            return {
                ...target,
                controlId: `vrf-${index}-table`,
                message: "Enter a table ID from 1 to 4294967295, other than the kernel's own tables 253, 254 and 255.",
            };
        }
        if (earlier.some(other => other.table === vrf.table)) {
            return {
                ...target,
                controlId: `vrf-${index}-table`,
                message: 'Choose a table ID no other VRF on this Router uses.',
            };
        }
    }

    const validVlan = value => Number.isInteger(value) && value >= 1 && value <= 4094;
    for (const link of links) {
        for (const end of link.ends || []) {
            if (end.node_id !== node.id) continue;
            if (end.vlan && node.type === 'switch') {
                if (end.vlan.mode === 'access' && !validVlan(end.vlan.vlan)) {
                    return {
                        ...target,
                        controlId: interfaceControlId(end, 'access-vlan'),
                        message: 'Enter an access VLAN ID from 1 to 4094.',
                    };
                }
                if (end.vlan.mode === 'trunk') {
                    const trunks = end.vlan.trunks;
                    if (!Array.isArray(trunks) || !trunks.length || trunks.some(value => !validVlan(value))
                        || new Set(trunks).size !== trunks.length) {
                        return {
                            ...target,
                            controlId: interfaceControlId(end, 'trunks'),
                            message: 'Enter distinct trunk VLAN IDs from 1 to 4094, separated by commas.',
                        };
                    }
                    if (end.vlan.native !== null && end.vlan.native !== undefined && !validVlan(end.vlan.native)) {
                        return {
                            ...target,
                            controlId: interfaceControlId(end, 'native'),
                            message: 'Enter a native VLAN ID from 1 to 4094 or leave it blank.',
                        };
                    }
                }
            }
            if (end.vrf !== undefined && !(node.vrfs || []).some(vrf => vrf.name === end.vrf)) {
                return {
                    ...target,
                    controlId: interfaceControlId(end, 'vrf'),
                    message: "Choose one of this Router's VRFs, or Default table.",
                };
            }
            if (end.mac !== undefined && end.mac !== '' && !/^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(end.mac)) {
                return {
                    ...target,
                    controlId: interfaceControlId(end, 'mac'),
                    message: 'Enter six colon-separated hex octets, such as aa:bb:cc:dd:ee:01, or leave it blank.',
                };
            }
            for (const family of ['4', '6']) {
                if (end[`dhcp${family}`] && end[`ipv${family}`]) {
                    return {
                        ...target,
                        controlId: interfaceControlId(end, `ipv${family}`),
                        message: `A DHCPv${family} client Interface has no static IPv${family} address.`,
                    };
                }
                const addr = end[`ipv${family}`];
                if (addr) {
                    const parsed = addr.includes('/') ? parseCidr(addr) : null;
                    if (!parsed || parsed.family !== Number(family)) {
                        return {
                            ...target,
                            controlId: interfaceControlId(end, `ipv${family}`),
                            message: `Enter a valid IPv${family} address with a prefix length, for example ${family === '4' ? '192.0.2.1/24' : '2001:db8::1/64'}.`,
                        };
                    }
                }
            }
        }
    }

    return null;
}

let nodeDialogDeps = null;
let speakerRoutesEditor = null;
let speakerNeighborsEditor = null;
let globalGenerator = null;
let relayServersEditor = null;
let dhcpPoolsEditor = null;
let accessControlEditor = null;
let dnsZonesEditor = null;
let rpkiHostedCasEditor = null;
const vrfMemberNames = new WeakMap();

function vrfMemberName(vrf) {
    return vrfMemberNames.has(vrf) ? vrfMemberNames.get(vrf) : vrf.name;
}

function removeVrfLabel(vrf, index) {
    return `Remove VRF ${vrf.name || index + 1}`;
}

function showFastTimers(node) {
    const group = document.getElementById('router-fast-timers-group');
    if (!group) return;
    group.hidden = node.type !== 'router'
        || !node.protocols.some(protocol => protocol === 'OSPF' || protocol === 'OSPF6');
}

function renderStartupList(node) {
    const list = document.getElementById('node-startup-list');
    if (!list) return;
    list.innerHTML = (node.startup || []).map((command, index) => {
        const id = `startup-${index}`;
        const stages = STARTUP_STAGES.map(([value, label]) =>
            `<option value="${value}" ${command.stage === value ? 'selected' : ''}>${label}</option>`).join('');
        return `<li><fieldset class="authoring-startup-command"><legend>Startup command ${index + 1}</legend>
            <label for="${id}-stage">Stage</label>
            <select id="${id}-stage" data-index="${index}" data-field="stage">${stages}</select>
            <label for="${id}-command">Command <span class="required-marker" aria-hidden="true">*</span></label>
            <input id="${id}-command" class="authoring-code" data-index="${index}" data-field="command"
                value="${escapeHtml(command.command)}" maxlength="2000" autocomplete="off" spellcheck="false"
                aria-required="true" aria-describedby="${id}-command-error">
            <p id="${id}-command-error" class="field-error" hidden></p>
            <label for="${id}-timeout">Timeout in seconds</label>
            <input id="${id}-timeout" type="number" min="1" max="60" step="1" data-index="${index}"
                data-field="timeout" value="${escapeHtml(command.timeout)}" aria-describedby="${id}-timeout-error">
            <p id="${id}-timeout-error" class="field-error" hidden></p>
            <label class="authoring-choice"><input type="checkbox" id="${id}-may-fail" data-index="${index}"
                data-field="may_fail" ${command.may_fail ? 'checked' : ''}> May fail: report a warning instead of stopping the start</label>
            <button type="button" class="btn-danger" data-remove-startup="${index}">Remove Startup command ${index + 1}</button>
        </fieldset></li>`;
    }).join('');
}

function renderSubinterfaces(node, draft) {
    const parents = draft.topology.links.flatMap(link => link.ends)
        .filter(end => end.node_id === node.id);
    document.getElementById('router-add-subinterface').disabled = !parents.length;
    const list = document.getElementById('router-subinterface-list');
    list.innerHTML = (node.subinterfaces || [])
        .map((sub, index) => {
            const id = `subinterface-${index}`;
            const options = parents.map(end => `<option value="${escapeHtml(end.interface_id)}"
                ${end.interface_id === sub.parent_interface_id ? 'selected' : ''}>
                ${escapeHtml(node.name)}-eth${end.port}</option>`).join('');
            return `<fieldset class="authoring-subinterface"><legend>Subinterface ${index + 1}</legend>
                <label for="${id}-parent">Parent Interface</label>
                <select id="${id}-parent" data-subinterface="${index}" data-field="parent_interface_id">${options}</select>
                <label for="${id}-vlan">VLAN ID</label>
                <input id="${id}-vlan" type="number" min="1" max="4094" step="1"
                    data-subinterface="${index}" data-field="vlan" value="${escapeHtml(sub.vlan)}"
                    aria-describedby="${id}-vlan-error">
                <p id="${id}-vlan-error" class="field-error" hidden></p>
                <label data-address-family="4" for="${id}-ipv4">IPv4 address and prefix</label>
                <input data-address-family="4" id="${id}-ipv4" data-subinterface="${index}" data-field="ipv4"
                    value="${escapeHtml(sub.ipv4)}" placeholder="e.g., 192.0.2.1/24"
                    aria-describedby="${id}-ipv4-error">
                <p id="${id}-ipv4-error" class="field-error" hidden></p>
                <label data-address-family="6" for="${id}-ipv6">IPv6 address and prefix</label>
                <input data-address-family="6" id="${id}-ipv6" data-subinterface="${index}" data-field="ipv6"
                    value="${escapeHtml(sub.ipv6)}" placeholder="e.g., 2001:db8::1/64"
                    aria-describedby="${id}-ipv6-error">
                <p id="${id}-ipv6-error" class="field-error" hidden></p>
                <button type="button" class="btn-danger" data-remove-subinterface="${index}">
                    Remove subinterface ${index + 1}</button>
            </fieldset>`;
        }).join('');
    showAddressFamily(list, draft.address_family);
}

function renderVrfs(node) {
    document.getElementById('router-vrf-list').innerHTML = (node.vrfs || []).map((vrf, index) => {
        const id = `vrf-${index}`;
        return `<fieldset class="authoring-vrf"><legend>VRF ${index + 1}</legend>
            <label for="${id}-name">VRF name</label>
            <input id="${id}-name" data-vrf="${index}" data-field="name" value="${escapeHtml(vrf.name)}"
                maxlength="15" placeholder="e.g., vrf-ext" autocomplete="off" spellcheck="false"
                aria-describedby="${id}-name-error">
            <p id="${id}-name-error" class="field-error" hidden></p>
            <label for="${id}-table">Table ID</label>
            <input id="${id}-table" type="number" min="1" max="4294967295" step="1"
                data-vrf="${index}" data-field="table" value="${escapeHtml(vrf.table)}"
                aria-describedby="${id}-table-error">
            <p id="${id}-table-error" class="field-error" hidden></p>
            <button type="button" class="btn-danger" data-remove-vrf="${index}">${escapeHtml(removeVrfLabel(vrf, index))}</button>
        </fieldset>`;
    }).join('');
}

const NAT_KINDS = [['port_forward', 'Port forward'], ['static', 'Static 1:1 mapping']];
const NAT_PROTOCOLS = ['tcp', 'udp'];

/**
 * A NAT entry of one kind, carrying over what the previous entry had that the kind uses.
 *
 * @param {string} kind
 * @param {Object} previous The entry being changed, or {} for a new one.
 * @returns {Object}
 */
function natEntryOfKind(kind, previous) {
    const entry = kind === 'static'
        ? { kind, inside: previous.inside ?? '', outside: previous.outside ?? '' }
        : {
            kind, protocol: previous.protocol ?? 'tcp', outside_port: previous.outside_port ?? 8080,
            inside: previous.inside ?? '', inside_port: previous.inside_port ?? 80,
            hairpin: previous.hairpin ?? false,
        };
    if (previous.interface !== undefined) entry.interface = previous.interface;
    return entry;
}

function natText(id, index, field, label, value, placeholder) {
    return `<label for="${id}-${field}">${label}</label>
        <input id="${id}-${field}" data-nat="${index}" data-field="${field}" value="${escapeHtml(value)}"
            placeholder="${placeholder}" autocomplete="off" spellcheck="false" aria-required="true" aria-describedby="${id}-${field}-error">
        <p id="${id}-${field}-error" class="field-error" hidden></p>`;
}

function natNumber(id, index, field, label, value) {
    return `<label for="${id}-${field}">${label}</label>
        <input id="${id}-${field}" type="number" min="1" max="65535" step="1" data-nat="${index}"
            data-field="${field}" value="${escapeHtml(value)}" aria-required="true" aria-describedby="${id}-${field}-error">
        <p id="${id}-${field}-error" class="field-error" hidden></p>`;
}

function renderRouterNat(node, draft) {
    const list = document.getElementById('router-nat-list');
    const outside = outsideInterfaceNames(node, draft.topology.links);
    list.innerHTML = (node.nat || []).map((entry, index) => {
        const id = `router-nat-${index}`;
        const chosen = entry.interface ?? '';
        const names = [...outside, ...(chosen && !outside.includes(chosen) ? [chosen] : [])];
        const interfaceOptions = [
            `<option value="" ${chosen ? '' : 'selected'}>Only one outside Interface</option>`,
            ...names.map(name => `<option value="${escapeHtml(name)}" ${name === chosen ? 'selected' : ''}>${escapeHtml(name)}</option>`),
        ].join('');
        const kindOptions = NAT_KINDS.map(([value, label]) =>
            `<option value="${value}" ${entry.kind === value ? 'selected' : ''}>${label}</option>`).join('');
        const fields = entry.kind === 'static'
            ? natText(id, index, 'inside', 'Inside address', entry.inside, 'e.g., 192.168.1.10')
                + natText(id, index, 'outside', 'Outside address', entry.outside, 'e.g., 203.0.113.5')
            : `<label for="${id}-protocol">Protocol</label>
                <select id="${id}-protocol" data-nat="${index}" data-field="protocol">
                    ${NAT_PROTOCOLS.map(protocol => `<option value="${protocol}" ${entry.protocol === protocol ? 'selected' : ''}>${protocol.toUpperCase()}</option>`).join('')}
                </select>
                ${natNumber(id, index, 'outside_port', 'Outside port', entry.outside_port)}
                ${natText(id, index, 'inside', 'Inside address', entry.inside, 'e.g., 192.168.1.10')}
                ${natNumber(id, index, 'inside_port', 'Inside port', entry.inside_port)}
                <label class="authoring-choice"><input type="checkbox" id="${id}-hairpin" data-nat="${index}"
                    data-field="hairpin" ${entry.hairpin ? 'checked' : ''}><span>Hairpin: inside clients also reach this forward through the outside address</span></label>`;
        return `<fieldset class="authoring-nat-entry"><legend>NAT entry ${index + 1}</legend>
            <label for="${id}-kind">Kind</label>
            <select id="${id}-kind" data-nat="${index}" data-field="kind">${kindOptions}</select>
            ${fields}
            <label for="${id}-interface">Outside Interface</label>
            <select id="${id}-interface" data-nat="${index}" data-field="interface"
                aria-describedby="${id}-interface-error">${interfaceOptions}</select>
            <p id="${id}-interface-error" class="field-error" hidden></p>
            <button type="button" class="btn-danger" data-remove-nat="${index}">Remove NAT entry ${index + 1}</button>
        </fieldset>`;
    }).join('');
}

function renderNodeInterfaces(node, draft) {
    const interfaces = nodeInterfaces(node, draft.topology.links, draft.topology.nodes);
    const list = document.getElementById('node-interfaces-list');
    list.innerHTML = renderInterfaces(node, interfaces);
    document.getElementById('node-interfaces-empty').hidden = interfaces.length > 0;
    showAddressFamily(list, draft.address_family);
    bindInterfaces(list, {
        endOf: interfaceId => interfaces.find(item => item.end.interface_id === interfaceId)?.end,
        clearError: clearFieldError,
        changed: () => {
            nodeDialogDeps?.updateDirtyState();
            if (node.type === 'router') renderRouterNat(node, draft);
        },
    });
    list.querySelectorAll('[data-edit-link]').forEach(button => button.addEventListener('click', () => {
        nodeDialogDeps?.closeDialogs();
        nodeDialogDeps?.openLinkDialog(button.dataset.editLink);
    }));
}

/**
 * Open the tabbed Node dialog for a given Node ID.
 *
 * @param {string} nodeId
 * @param {boolean} [hasError]
 */
export function openNodeDialog(nodeId, hasError = false) {
    if (!nodeDialogDeps) return;
    const {
        getDraft, nodeById, typeLabel, nodeLabel, getDialogItem, setDialogItem,
        clearPendingNode, selectNode, closeDialogs, getRouterProtocols, getRefusalFix,
    } = nodeDialogDeps;
    const draft = getDraft();
    const node = nodeById(nodeId);
    if (!node || !draft) return;
    const dialog = document.getElementById('host-dialog');
    if (dialog.open && getDialogItem()?.id === nodeId) return;
    closeDialogs();
    closeInfoTips();
    clearPendingNode();
    setDialogItem({ kind: 'host', id: nodeId });
    selectNode(nodeId);
    const position = draft.layout.nodes[nodeId] || {};
    document.getElementById('host-dialog-title').textContent = `${typeLabel(node)} ${nodeLabel(node)}`;
    document.getElementById('host-name').value = node.name;
    document.getElementById('host-x').value = position.x ?? '';
    document.getElementById('host-y').value = position.y ?? '';
    const activeTab = setVisibleTabs(node.type);
    renderNodeInterfaces(node, draft);
    const others = draft.topology.nodes.filter(other => other.id !== nodeId);
    const isRouter = node.type === 'router';
    document.getElementById('host-gateways').hidden = !['host', 'container'].includes(node.type);
    for (const id of ['container-image-label', 'container-image']) {
        document.getElementById(id).hidden = node.type !== 'container';
    }
    document.getElementById('container-image').required = node.type === 'container';
    document.getElementById('switch-spanning-tree').hidden = node.type !== 'switch';
    if (node.type === 'switch') {
        document.getElementById('switch-stp').value = node.stp === 'rstp' ? 'rstp' : node.stp ? 'stp' : 'off';
        document.getElementById('switch-priority').value = node.priority ?? '';
        document.getElementById('switch-priority').disabled = !node.stp;
        document.getElementById('switch-priority').max = node.stp === 'rstp' ? '61440' : '65535';
        document.getElementById('switch-priority').step = node.stp === 'rstp' ? '4096' : '1';
    }
    document.getElementById('node-startup').hidden = ['switch', 'nat', 'transit'].includes(node.type);
    const isNat = node.type === 'nat' || node.type === 'transit';
    const isSpeaker = node.type === 'speaker';
    document.getElementById('speaker-fields').hidden = !isSpeaker;
    if (isSpeaker) {
        document.getElementById('speaker-local-as').value = node.local_as;
        speakerNeighborsEditor.setRows(node.neighbors, node);
        speakerRoutesEditor.setRows(node.routes, node);
        globalGenerator.setOwner(node);
        document.querySelector(`#speaker-generator-scope input[value="${node.route_generator ? 'global' : 'neighbor'}"]`).checked = true;
        document.getElementById('speaker-routes-more').open = false;
    }
    document.getElementById('dhcp-enabled').checked = Boolean(node.dhcp);
    document.getElementById('dhcp-pool-fields').hidden = !node.dhcp;
    if (node.dhcp) dhcpPoolsEditor.setRows(node.dhcp.pools);
    const canServeDns = ['host', 'router'].includes(node.type);
    document.getElementById('dns-enabled').checked = Boolean(node.dns);
    document.getElementById('dns-zone-fields').hidden = !node.dns;
    if (node.dns) dnsZonesEditor.setRows(node.dns.zones, node);
    const primarySources = others.flatMap(other => (other.dns?.zones || [])
        .filter(zone => zone.kind === 'primary').map(zone => ({ node: other, zone })));
    const secondarySource = document.getElementById('dns-secondary-source');
    secondarySource.replaceChildren(...primarySources.map(source =>
        new Option(`${source.node.name} · ${source.zone.name}`,
            JSON.stringify({ node_id: source.node.id, name: source.zone.name }))));
    secondarySource.disabled = !primarySources.length;
    document.getElementById('dns-add-secondary').disabled = !primarySources.length;
    document.getElementById('resolver-enabled').checked = Boolean(node.resolver);
    document.getElementById('resolver-settings').hidden = !node.resolver;
    if (canServeDns) {
        const hints = node.resolver?.root_hints || [];
        document.getElementById('resolver-root-choices').replaceChildren(...others.map(other => {
            const label = document.createElement('label');
            label.className = 'authoring-choice';
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.value = other.id;
            box.checked = hints.some(item => item.node_id === other.id);
            label.append(box, ` ${other.name}`);
            return label;
        }));
        const anchors = node.resolver?.trust_anchors || [];
        document.getElementById('resolver-trust-choices').replaceChildren(...others
            .filter(other => (other.dns?.zones || []).some(zone => zone.kind === 'primary'
                && zone.name === '.' && zone.dnssec === true))
            .map(other => {
                const label = document.createElement('label');
                label.className = 'authoring-choice';
                const box = document.createElement('input');
                box.type = 'checkbox';
                box.value = other.id;
                box.checked = anchors.some(item => item.node_id === other.id);
                label.append(box, ` ${other.name}`);
                return label;
            }));
        document.getElementById('resolver-root-addresses').value = hints
            .filter(item => item.address).map(item => item.address).join(', ');
        document.getElementById('resolver-forwarders').value = (node.resolver?.forwarders || [])
            .filter(item => item.address).map(item => item.address).join(', ');
        document.getElementById('resolver-max-ttl').value = node.resolver?.max_ttl ?? 300;
        document.getElementById('resolver-max-negative-ttl').value = node.resolver?.max_negative_ttl ?? 60;
        if (node.resolver) accessControlEditor.setRows(node.resolver.access_control);
    }
    document.getElementById('host-nameservers').hidden = node.type !== 'host';
    if (node.type === 'host') {
        const declarations = node.nameservers || [];
        document.getElementById('host-nameserver-choices').replaceChildren(...others.map(other => {
            const label = document.createElement('label');
            label.className = 'authoring-choice';
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.value = other.id;
            box.checked = declarations.some(item => item.node_id === other.id);
            label.append(box, ` ${other.name}`);
            return label;
        }));
        document.getElementById('host-nameserver-addresses').value = declarations
            .filter(item => item.address).map(item => item.address).join(', ');
    }
    document.getElementById('relay-enabled').checked = Boolean(node.relay);
    document.getElementById('relay-group').hidden = !node.relay;
    document.getElementById('relay-subnets').value = node.relay?.subnets.join(', ') ?? '';
    if (node.relay) relayServersEditor.setRows(node.relay.servers);
    document.getElementById('rpki-ca-enabled').checked = Boolean(node.rpki_ca);
    document.getElementById('rpki-ca-fields').hidden = !node.rpki_ca;
    document.getElementById('rpki-ca-hostname').value = node.rpki_ca?.hostname ?? '';
    if (node.rpki_ca) rpkiHostedCasEditor.setRows(node.rpki_ca.hosted_cas);
    document.getElementById('validator-enabled').checked = Boolean(node.validator);
    document.getElementById('validator-fields').hidden = !node.validator;
    document.getElementById('validator-refresh').value = node.validator?.refresh ?? '';
    document.getElementById('nat-fields').hidden = !isNat;
    document.getElementById('nat-downstream-fields').hidden = node.type !== 'transit';
    if (isNat) {
        document.getElementById('nat-ip').value = node.ip;
        document.getElementById('nat-subnet').value = node.subnet;
        if (node.type === 'transit') {
            document.getElementById('nat-downstream-choices').replaceChildren(...draft.topology.nodes
                .filter(other => other.type === 'router').map(router => {
                    const label = document.createElement('label');
                    label.className = 'authoring-choice';
                    const box = document.createElement('input');
                    box.type = 'checkbox';
                    box.value = router.id;
                    box.checked = node.downstream_ids.includes(router.id);
                    label.append(box, ` ${router.name}`);
                    return label;
                }));
        }
    }
    document.getElementById('router-subinterfaces').hidden = !isRouter;
    document.getElementById('router-vrfs').hidden = !isRouter;
    showFastTimers(node);
    ['router-protocols', 'router-frr-config-heading', 'router-frr-config'].forEach(id => {
        document.getElementById(id).hidden = !isRouter;
    });
    document.getElementById('host-remove').textContent = `Remove ${typeLabel(node)}`;
    if (isRouter) {
        const routerProtocols = getRouterProtocols();
        document.getElementById('router-protocol-choices').replaceChildren(...routerProtocols.map(protocol => {
            const label = document.createElement('label');
            label.className = 'authoring-choice';
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.value = protocol;
            box.checked = node.protocols.includes(protocol);
            label.append(box, ` ${protocol}`);
            return label;
        }));
        document.getElementById('router-fast-timers').checked = node.fast_timers;
        document.getElementById('router-frr-config').value = node.frr_config;
        renderSubinterfaces(node, draft);
        renderVrfs(node);
        renderRouterNat(node, draft);
    } else if (node.type === 'host' || node.type === 'container') {
        document.getElementById('host-ipv4-gateway').value = node.ipv4_gateway;
        document.getElementById('host-ipv6-gateway').value = node.ipv6_gateway;
        if (node.type === 'container') document.getElementById('container-image').value = node.image;
    }
    if (node.startup) renderStartupList(node);
    showAddressFamily(dialog, draft.address_family);
    clearDialogErrors(dialog);
    updateNodeDialogErrors([]);
    const refusalFix = getRefusalFix?.();
    if (!hasError && (refusalFix?.kind === 'host' || refusalFix?.kind === 'node') && refusalFix.id === nodeId) {
        hasError = true;
        const control = refusalFix.control || (refusalFix.controlId ? document.getElementById(refusalFix.controlId) : null);
        if (control) {
            showFieldError(control, refusalFix.message, false);
        }
    }
    dialog.showModal();
    if (hasError) {
        const summary = document.getElementById('node-dialog-error-summary');
        if (!summary.hidden) summary.focus();
        else document.getElementById('node-tab-' + activeTab)?.focus();
    } else {
        document.getElementById('node-tab-' + activeTab)?.focus();
    }
}

/**
 * Initialize the Node dialog, its row editors, and all event bindings.
 *
 * @param {Object} deps
 */
export function initNodeDialog(deps) {
    nodeDialogDeps = deps;
    const {
        getDraft, nodeById, typeLabel, nodeLabel, getDialogItem,
        updateDirtyState, renderCanvas, removeNode, bindDialogClosing,
        randomUuid, announce,
    } = deps;

    const rowEditorHooks = {
        onChange: () => updateDirtyState(),
        showError: (control, message) => showFieldError(control, message, false),
        clearError: control => clearFieldError(control),
        addressChoices: () => {
            const draft = getDraft();
            if (!draft) return [];
            return interfaceChoices(draft.topology, getDialogItem()?.id)
                .filter(choice => addressVersions(draft.address_family).includes(choice.address.includes(':') ? 6 : 4));
        },
    };
    speakerRoutesEditor = createRowEditor(
        document.getElementById('speaker-routes-rows'),
        withAddressFamily(SPEAKER_ROUTES, () => getDraft()?.address_family),
        rowEditorHooks,
    );
    speakerNeighborsEditor = createRowEditor(
        document.getElementById('speaker-neighbors-rows'),
        withAddressFamily(SPEAKER_NEIGHBORS, () => getDraft()?.address_family),
        { ...rowEditorHooks, onChange: () => { speakerRoutesEditor.rejudge(); updateDirtyState(); } },
    );
    globalGenerator = createGlobalGenerator(document.getElementById('speaker-generator'), rowEditorHooks);
    relayServersEditor = createRowEditor(
        document.getElementById('relay-servers-rows'),
        withAddressFamily(RELAY_SERVERS, () => getDraft()?.address_family),
        rowEditorHooks,
    );
    initInfoTips();
    dhcpPoolsEditor = createRowEditor(
        document.getElementById('dhcp-pools-rows'),
        withAddressFamily(DHCP_POOLS, () => getDraft()?.address_family),
        rowEditorHooks,
    );
    accessControlEditor = createRowEditor(
        document.getElementById('resolver-access-control-rows'),
        ACCESS_CONTROL,
        rowEditorHooks,
    );
    const dnsZonesSpec = withAddressFamily(createZoneSpec(() => getDraft()?.topology), () => getDraft()?.address_family);
    dnsZonesEditor = createRowEditor(
        document.getElementById('dns-zones-rows'),
        dnsZonesSpec,
        rowEditorHooks,
    );
    rpkiHostedCasEditor = createRowEditor(
        document.getElementById('rpki-ca-hosted-cas-rows'),
        HOSTED_CAS,
        rowEditorHooks,
    );

    const dialog = document.getElementById('host-dialog');
    if (!dialog) return;
    const current = () => nodeById(getDialogItem()?.id);

    document.getElementById('speaker-generator-scope').addEventListener('change', event => {
        const draft = getDraft();
        const node = draft?.topology.nodes.find(item => item.id === getDialogItem()?.id);
        if (node?.type !== 'speaker') return;
        if (event.target.value === 'global') {
            node.route_generator = node.neighbors.find(neighbor => neighbor.route_generator)?.route_generator || blankGenerator();
            node.neighbors.forEach(neighbor => { delete neighbor.route_generator; });
        } else {
            node.neighbors.forEach(neighbor => {
                neighbor.route_generator ||= node.route_generator
                    ? { ...node.route_generator } : blankGenerator();
            });
            delete node.route_generator;
        }
        globalGenerator.setOwner(node);
        speakerNeighborsEditor.setRows(node.neighbors, node);
        updateDirtyState();
    });

    document.getElementById('switch-stp').addEventListener('change', event => {
        const node = current();
        if (node?.type !== 'switch') return;
        node.stp = event.target.value === 'rstp' ? 'rstp' : event.target.value === 'stp';
        const priority = document.getElementById('switch-priority');
        priority.disabled = !node.stp;
        priority.max = node.stp === 'rstp' ? '61440' : '65535';
        priority.step = node.stp === 'rstp' ? '4096' : '1';
        if (!node.stp) { delete node.priority; priority.value = ''; }
        clearFieldError(event.target);
        updateDirtyState();
    });
    document.getElementById('switch-priority').addEventListener('input', event => {
        const node = current();
        if (node?.type !== 'switch') return;
        if (event.target.value === '') delete node.priority;
        else node.priority = Number(event.target.value);
        clearFieldError(event.target);
        updateDirtyState();
    });

    document.getElementById('host-name').addEventListener('input', event => {
        const node = current();
        if (!node) return;
        clearFieldError(event.target);
        const previousName = node.name;
        node.name = event.target.value;
        renameRouterNatInterfaces(node, previousName);
        document.getElementById('host-dialog-title').textContent = `${typeLabel(node)} ${nodeLabel(node)}`;
        updateDirtyState();
        renderCanvas();
    });
    ['x', 'y'].forEach(axis => document.getElementById(`host-${axis}`).addEventListener('input', event => {
        const node = current();
        const draft = getDraft();
        if (!node || !draft) return;
        clearFieldError(event.target);
        const layout = draft.layout.nodes[node.id] || (draft.layout.nodes[node.id] = {});
        layout[axis] = event.target.value === '' ? '' : Number(event.target.value);
        updateDirtyState();
        renderCanvas();
    }));
    ['ipv4_gateway', 'ipv6_gateway'].forEach(field => {
        const control = document.querySelector(`[data-node-field="${field}"]`);
        control.addEventListener('input', () => {
            const node = current();
            if (!node) return;
            clearFieldError(control);
            node[field] = control.value.trim();
            updateDirtyState();
        });
    });
    document.getElementById('container-image').addEventListener('input', event => {
        const node = current();
        if (!node || node.type !== 'container') return;
        clearFieldError(event.target);
        node.image = event.target.value.trim();
        updateDirtyState();
    });
    for (const [id, field] of [['nat-ip', 'ip'], ['nat-subnet', 'subnet']]) {
        document.getElementById(id).addEventListener('input', event => {
            const node = current();
            if (!node || !['nat', 'transit'].includes(node.type)) return;
            clearFieldError(event.target);
            node[field] = event.target.value.trim();
            updateDirtyState();
        });
    }
    document.getElementById('nat-downstream-choices').addEventListener('change', () => {
        const node = current();
        if (!node || node.type !== 'transit') return;
        node.downstream_ids = [...document.querySelectorAll('#nat-downstream-choices input:checked')]
            .map(box => box.value);
        updateDirtyState();
    });
    document.getElementById('speaker-local-as').addEventListener('input', event => {
        const node = current();
        if (!node || node.type !== 'speaker') return;
        clearFieldError(event.target);
        node.local_as = event.target.value === '' ? '' : Number(event.target.value);
        updateDirtyState();
    });
    document.getElementById('dhcp-enabled').addEventListener('change', event => {
        const node = current();
        if (!node) return;
        if (event.target.checked) node.dhcp = { pools: [] };
        else delete node.dhcp;
        clearFieldError(document.getElementById('dhcp-pools'));
        if (node.dhcp) dhcpPoolsEditor.setRows(node.dhcp.pools);
        document.getElementById('dhcp-pool-fields').hidden = !node.dhcp;
        updateDirtyState();
    });
    function updateNameservers() {
        const node = current();
        if (node?.type !== 'host') return;
        const references = [...document.querySelectorAll('#host-nameserver-choices input:checked')]
            .map(box => ({ node_id: box.value }));
        const addresses = document.getElementById('host-nameserver-addresses').value
            .split(',').map(value => value.trim()).filter(Boolean).map(address => ({ address }));
        if (references.length || addresses.length) node.nameservers = [...references, ...addresses];
        else delete node.nameservers;
        updateDirtyState();
    }
    document.getElementById('host-nameserver-choices').addEventListener('change', updateNameservers);
    document.getElementById('host-nameserver-addresses').addEventListener('input', event => {
        clearFieldError(event.target);
        updateNameservers();
    });
    document.getElementById('dns-enabled').addEventListener('change', event => {
        const node = current();
        if (!node) return;
        if (event.target.checked) node.dns = { zones: [] };
        else delete node.dns;
        const zones = document.getElementById('dns-zones');
        clearFieldError(zones);
        if (node.dns) dnsZonesEditor.setRows(node.dns.zones, node);
        document.getElementById('dns-zone-fields').hidden = !node.dns;
        updateDirtyState();
    });
    const zonesControl = document.getElementById('dns-zones');
    document.getElementById('dns-add-secondary').addEventListener('click', () => {
        const node = current();
        const source = document.getElementById('dns-secondary-source');
        if (!node?.dns || !source.value) return;
        const selected = JSON.parse(source.value);
        if (node.dns.zones.some(zone => zone.name === selected.name)) {
            showFieldError(zonesControl, `This Node already hosts Zone ${selected.name}.`);
            return;
        }
        node.dns.zones.push({
            kind: 'secondary', name: selected.name,
            primary_node_id: selected.node_id,
        });
        dnsZonesEditor.setRows(node.dns.zones, node);
        clearFieldError(zonesControl);
        updateDirtyState();
    });
    document.getElementById('resolver-enabled').addEventListener('change', event => {
        const node = current();
        if (!node) return;
        if (event.target.checked) node.resolver = {
            root_hints: [], forwarders: [], max_ttl: 300, max_negative_ttl: 60,
            access_control: [{ subnet: '0.0.0.0/0', action: 'allow' },
                { subnet: '::/0', action: 'allow' }], trust_anchors: [],
        };
        else delete node.resolver;
        document.getElementById('resolver-settings').hidden = !node.resolver;
        if (node.resolver) accessControlEditor.setRows(node.resolver.access_control);
        updateDirtyState();
    });
    function updateResolverReferences() {
        const node = current();
        if (!node?.resolver) return;
        const references = [...document.querySelectorAll('#resolver-root-choices input:checked')]
            .map(box => ({ node_id: box.value }));
        const addresses = document.getElementById('resolver-root-addresses').value
            .split(',').map(value => value.trim()).filter(Boolean).map(address => ({ address }));
        node.resolver.root_hints = [...references, ...addresses];
        node.resolver.trust_anchors = [...document.querySelectorAll('#resolver-trust-choices input:checked')]
            .map(box => ({ node_id: box.value }));
        node.resolver.forwarders = document.getElementById('resolver-forwarders').value
            .split(',').map(value => value.trim()).filter(Boolean).map(address => ({ address }));
        updateDirtyState();
    }
    document.getElementById('resolver-root-choices').addEventListener('change', updateResolverReferences);
    document.getElementById('resolver-trust-choices').addEventListener('change', updateResolverReferences);
    for (const id of ['resolver-root-addresses', 'resolver-forwarders']) {
        document.getElementById(id).addEventListener('input', event => {
            clearFieldError(event.target);
            updateResolverReferences();
        });
    }
    for (const [id, field] of [['resolver-max-ttl', 'max_ttl'],
        ['resolver-max-negative-ttl', 'max_negative_ttl']]) {
        document.getElementById(id).addEventListener('input', event => {
            const node = current();
            if (!node?.resolver) return;
            node.resolver[field] = event.target.value === '' ? null : Number(event.target.value);
            clearFieldError(event.target);
            updateDirtyState();
        });
    }
    document.getElementById('rpki-ca-enabled').addEventListener('change', event => {
        const node = current();
        if (!node) return;
        if (event.target.checked) node.rpki_ca = { hostname: null, hosted_cas: [] };
        else delete node.rpki_ca;
        clearFieldError(document.getElementById('rpki-ca-hosted-cas'));
        document.getElementById('rpki-ca-fields').hidden = !node.rpki_ca;
        if (node.rpki_ca) rpkiHostedCasEditor.setRows(node.rpki_ca.hosted_cas);
        updateDirtyState();
    });
    document.getElementById('rpki-ca-hostname').addEventListener('input', event => {
        const node = current();
        if (!node?.rpki_ca) return;
        node.rpki_ca.hostname = event.target.value === '' ? null : event.target.value;
        clearFieldError(event.target);
        updateDirtyState();
    });
    document.getElementById('validator-enabled').addEventListener('change', event => {
        const node = current();
        if (!node) return;
        if (event.target.checked) node.validator = { refresh: 10 };
        else delete node.validator;
        clearFieldError(document.getElementById('validator-refresh'));
        document.getElementById('validator-fields').hidden = !node.validator;
        document.getElementById('validator-refresh').value = node.validator?.refresh ?? '';
        updateDirtyState();
    });
    document.getElementById('validator-refresh').addEventListener('input', event => {
        const node = current();
        if (!node?.validator) return;
        node.validator.refresh = event.target.value === '' ? null : Number(event.target.value);
        clearFieldError(event.target);
        updateDirtyState();
    });
    document.getElementById('relay-enabled').addEventListener('change', event => {
        const node = current();
        if (!node) return;
        if (event.target.checked) node.relay = { subnets: [], servers: [] };
        else delete node.relay;
        const subnets = document.getElementById('relay-subnets');
        clearFieldError(subnets);
        subnets.value = '';
        clearFieldError(document.getElementById('relay-servers'));
        if (node.relay) relayServersEditor.setRows(node.relay.servers);
        document.getElementById('relay-group').hidden = !node.relay;
        updateDirtyState();
    });
    const relaySubnets = document.getElementById('relay-subnets');
    relaySubnets.addEventListener('input', () => {
        const node = current();
        if (!node?.relay) return;
        clearFieldError(relaySubnets);
        node.relay.subnets = relaySubnets.value.split(',').map(item => item.trim()).filter(Boolean);
        updateDirtyState();
    });
    document.getElementById('router-protocol-choices').addEventListener('change', () => {
        const node = current();
        if (!node) return;
        clearFieldError(document.getElementById('router-protocols'));
        node.protocols = [...document.querySelectorAll('#router-protocol-choices input:checked')]
            .map(box => box.value);
        showFastTimers(node);
        updateDirtyState();
    });
    document.getElementById('router-fast-timers').addEventListener('change', event => {
        const node = current();
        if (!node) return;
        node.fast_timers = event.target.checked;
        updateDirtyState();
    });
    document.getElementById('router-frr-config').addEventListener('input', event => {
        const node = current();
        if (!node) return;
        clearFieldError(event.target);
        node.frr_config = event.target.value;
        updateDirtyState();
    });
    document.getElementById('router-add-subinterface').addEventListener('click', () => {
        const node = current();
        const draft = getDraft();
        if (!node || !draft) return;
        const parent = draft.topology.links.flatMap(link => link.ends)
            .find(end => end.node_id === node.id);
        if (!parent) return;
        node.subinterfaces ||= [];
        node.subinterfaces.push({
            id: randomUuid(), parent_interface_id: parent.interface_id,
            vlan: 1, ipv4: '', ipv6: '',
        });
        updateDirtyState();
        renderSubinterfaces(node, draft);
        document.getElementById(`subinterface-${node.subinterfaces.length - 1}-vlan`).focus();
    });
    const subinterfaceList = document.getElementById('router-subinterface-list');
    const editSubinterface = event => {
        const node = current();
        const control = event.target;
        if (!node || !control.dataset.field) return;
        clearFieldError(control);
        node.subinterfaces[Number(control.dataset.subinterface)][control.dataset.field] =
            control.dataset.field === 'vlan' ? (control.value === '' ? '' : Number(control.value))
                : control.value.trim();
        updateDirtyState();
    };
    subinterfaceList.addEventListener('input', editSubinterface);
    subinterfaceList.addEventListener('change', editSubinterface);
    subinterfaceList.addEventListener('click', event => {
        const button = event.target.closest('[data-remove-subinterface]');
        const node = current();
        const draft = getDraft();
        if (!node || !draft || !button) return;
        node.subinterfaces.splice(Number(button.dataset.removeSubinterface), 1);
        updateDirtyState();
        renderSubinterfaces(node, draft);
        document.getElementById('router-add-subinterface').focus();
    });
    document.getElementById('router-add-vrf').addEventListener('click', () => {
        const node = current();
        if (!node) return;
        node.vrfs ||= [];
        let table = 10;
        while (node.vrfs.some(vrf => vrf.table === table)) table += 1;
        node.vrfs.push({ name: '', table });
        updateDirtyState();
        renderVrfs(node);
        document.getElementById(`vrf-${node.vrfs.length - 1}-name`).focus();
    });
    const vrfList = document.getElementById('router-vrf-list');
    vrfList.addEventListener('input', event => {
        const node = current();
        const draft = getDraft();
        const control = event.target;
        if (!node || !draft || !control.dataset.field) return;
        clearFieldError(control);
        const index = Number(control.dataset.vrf);
        const vrf = node.vrfs[index];
        if (control.dataset.field === 'table') {
            vrf.table = control.value === '' ? '' : Number(control.value);
        } else {
            const previous = vrfMemberName(vrf);
            if (previous) vrfMemberNames.set(vrf, previous);
            vrf.name = control.value.trim();
            if (vrf.name && previous && !node.vrfs.some(other => other !== vrf && other.name === previous)) {
                nodeInterfaces(node, draft.topology.links, draft.topology.nodes)
                    .filter(({ end }) => end.vrf === previous)
                    .forEach(({ end }) => { end.vrf = vrf.name; });
            }
            if (vrf.name) vrfMemberNames.set(vrf, vrf.name);
            vrfList.querySelector(`[data-remove-vrf="${index}"]`).textContent = removeVrfLabel(vrf, index);
            renderNodeInterfaces(node, draft);
        }
        updateDirtyState();
    });
    vrfList.addEventListener('click', event => {
        const button = event.target.closest('[data-remove-vrf]');
        const node = current();
        const draft = getDraft();
        if (!node || !draft || !button) return;
        const index = Number(button.dataset.removeVrf);
        const vrf = node.vrfs[index];
        const members = nodeInterfaces(node, draft.topology.links, draft.topology.nodes)
            .filter(({ end }) => vrfMemberName(vrf) && end.vrf === vrfMemberName(vrf)).map(({ name }) => name);
        if (members.length) {
            showFieldError(document.getElementById(`vrf-${index}-name`),
                `${members.join(', ')} ${members.length === 1 ? 'is' : 'are'} in this VRF. `
                + `Move ${members.length === 1 ? 'it' : 'them'} to another VRF on the Interfaces tab first.`);
            return;
        }
        node.vrfs.splice(index, 1);
        updateDirtyState();
        renderVrfs(node);
        renderNodeInterfaces(node, draft);
        document.getElementById('router-add-vrf').focus();
    });
    const natList = document.getElementById('router-nat-list');
    const editNat = event => {
        const node = current();
        const control = event.target;
        if (!node || !control.dataset.field) return;
        const index = Number(control.dataset.nat);
        const entry = node.nat?.[index];
        if (!entry) return;
        clearFieldError(control);
        const field = control.dataset.field;
        if (field === 'kind') {
            // A kind has fields of its own, so the row is redrawn once it is chosen.
            if (event.type !== 'change') return;
            node.nat[index] = natEntryOfKind(control.value, entry);
            updateDirtyState();
            renderRouterNat(node, getDraft());
            document.getElementById(`router-nat-${index}-kind`).focus();
            return;
        }
        if (field === 'hairpin') entry.hairpin = control.checked;
        else if (field === 'interface') {
            if (control.value) entry.interface = control.value;
            else delete entry.interface;
        } else if (field === 'outside_port' || field === 'inside_port') {
            entry[field] = control.value === '' ? '' : Number(control.value);
        } else entry[field] = control.value.trim();
        updateDirtyState();
    };
    natList.addEventListener('input', editNat);
    natList.addEventListener('change', editNat);
    natList.addEventListener('click', event => {
        const button = event.target.closest('[data-remove-nat]');
        const node = current();
        const draft = getDraft();
        if (!node || !draft || !button) return;
        node.nat.splice(Number(button.dataset.removeNat), 1);
        if (!node.nat.length) delete node.nat;
        updateDirtyState();
        renderRouterNat(node, draft);
        document.getElementById('router-add-nat').focus();
    });
    document.getElementById('router-add-nat').addEventListener('click', () => {
        const node = current();
        const draft = getDraft();
        if (!node || !draft) return;
        node.nat ||= [];
        node.nat.push(natEntryOfKind('port_forward', {}));
        updateDirtyState();
        renderRouterNat(node, draft);
        document.getElementById(`router-nat-${node.nat.length - 1}-kind`).focus();
    });
    document.getElementById('node-add-startup').addEventListener('click', () => {
        const node = current();
        if (!node) return;
        node.startup.push({
            stage: node.type === 'router' ? 'before-daemons' : 'after-start',
            command: '', timeout: DEFAULT_STARTUP_TIMEOUT, may_fail: false,
        });
        updateDirtyState();
        renderStartupList(node);
        document.getElementById(`startup-${node.startup.length - 1}-command`).focus();
    });
    const startupList = document.getElementById('node-startup-list');
    const editStartup = event => {
        const node = current();
        const control = event.target;
        if (!node || !control.dataset.field) return;
        clearFieldError(control);
        clearFieldError(document.getElementById('node-startup'));
        const command = node.startup[Number(control.dataset.index)];
        const field = control.dataset.field;
        command[field] = field === 'may_fail' ? control.checked
            : field === 'timeout' ? (control.value === '' ? '' : Number(control.value))
            : control.value;
        updateDirtyState();
    };
    startupList.addEventListener('input', editStartup);
    startupList.addEventListener('change', editStartup);
    startupList.addEventListener('click', event => {
        const node = current();
        const button = event.target.closest('[data-remove-startup]');
        if (!node || !button) return;
        const index = Number(button.dataset.removeStartup);
        node.startup.splice(index, 1);
        updateDirtyState();
        renderStartupList(node);
        const next = document.getElementById(`startup-${Math.min(index, node.startup.length - 1)}-command`);
        (next || document.getElementById('node-add-startup')).focus();
        announce(`Removed Startup command ${index + 1}.`);
    });
    initNodeDialogTabs();
    document.getElementById('host-remove').addEventListener('click', () => {
        if (current()) removeNode(current().id);
    });
    bindDialogClosing(dialog);
}

