// Link dialog for Lab Authoring (D9, D14, D29).

import { escapeHtml } from '../utils/dom.js';

/**
 * Returns the first port number for a given node (1 for switches, 0 for hosts/routers/etc).
 *
 * @param {Object} node
 * @returns {number}
 */
export function firstPort(node) {
    return node?.type === 'switch' ? 1 : 0;
}

/**
 * Returns an array of available port numbers for a given node.
 * Includes the currently selected port plus the next free ports.
 *
 * @param {Object} node
 * @param {number|null} currentPort
 * @param {Array} allLinks
 * @param {string} currentLinkId
 * @returns {Array<number>}
 */
export function freePortsForNode(node, currentPort, allLinks, currentLinkId) {
    if (!node) return [currentPort ?? 0];
    const base = firstPort(node);
    const usedPorts = new Set(
        (allLinks || [])
            .filter(link => link.id !== currentLinkId)
            .flatMap(link => link.ends || [])
            .filter(end => end.node_id === node.id)
            .map(end => end.port)
    );
    const ports = [];
    if (currentPort !== null && currentPort !== undefined) {
        ports.push(currentPort);
    }
    let p = base;
    while (ports.length < 16 || p < (currentPort ?? base) + 5) {
        if (!usedPorts.has(p) && !ports.includes(p)) {
            ports.push(p);
        }
        p++;
        if (p > 64) break;
    }
    return ports.sort((a, b) => a - b);
}

/**
 * Safely re-points a link end to another node without orphaning router VLAN subinterfaces.
 *
 * @param {Object} link
 * @param {number} endIndex
 * @param {string} newNodeId
 * @param {Object} draft
 */
export function repointLinkEnd(link, endIndex, newNodeId, draft) {
    const end = link.ends[endIndex];
    const oldNodeId = end.node_id;
    if (oldNodeId === newNodeId) return;

    const oldNode = draft.topology.nodes.find(n => n.id === oldNodeId);
    const newNode = draft.topology.nodes.find(n => n.id === newNodeId);
    if (!newNode) return;

    // Guard against orphaning router VLAN subinterfaces
    if (oldNode?.type === 'router' && Array.isArray(oldNode.subinterfaces)) {
        const remainingParents = draft.topology.links
            .filter(l => l.id !== link.id)
            .flatMap(l => l.ends || [])
            .filter(e => e.node_id === oldNodeId)
            .map(e => e.interface_id);

        if (remainingParents.length > 0) {
            for (const sub of oldNode.subinterfaces) {
                if (sub.parent_interface_id === end.interface_id) {
                    sub.parent_interface_id = remainingParents[0];
                }
            }
        } else {
            oldNode.subinterfaces = oldNode.subinterfaces.filter(sub => sub.parent_interface_id !== end.interface_id);
        }
    }

    end.node_id = newNodeId;

    // Pick first free port on the new node
    const free = freePortsForNode(newNode, null, draft.topology.links, link.id);
    end.port = free[0] ?? firstPort(newNode);

    // Clean up node-type-specific fields
    if (newNode.type !== 'host') {
        delete end.mac;
        delete end.dhcp4;
        delete end.dhcp6;
    }
    if (newNode.type !== 'switch') {
        delete end.vlan;
    } else {
        end.ipv4 = '';
        end.ipv6 = '';
    }
}

/**
 * Renders the HTML markup for one Link End column.
 *
 * @param {Object} link
 * @param {number} endIndex
 * @param {Array} nodes
 * @param {Array} allLinks
 * @param {Object} helpers ({ typeLabel, nodeLabel })
 * @returns {string}
 */
export function renderLinkEnd(link, endIndex, nodes, allLinks, helpers) {
    const end = link.ends[endIndex];
    const id = `link-end-${endIndex}`;
    const node = nodes.find(n => n.id === end.node_id);
    const otherEnd = link.ends[1 - endIndex];
    const label = node ? `${helpers.typeLabel(node)} ${helpers.nodeLabel(node)}` : end.node_id;

    const availableNodes = nodes.filter(n => n.id !== otherEnd.node_id);
    const nodeOptions = availableNodes.map(n =>
        `<option value="${escapeHtml(n.id)}" ${n.id === end.node_id ? 'selected' : ''}>${escapeHtml(helpers.typeLabel(n))} ${escapeHtml(helpers.nodeLabel(n))}</option>`
    ).join('');

    const freePorts = freePortsForNode(node, end.port, allLinks, link.id);
    const portOptions = freePorts.map(p =>
        `<option value="${p}" ${p === end.port ? 'selected' : ''}>${escapeHtml(node?.name || 'eth')}-eth${p}</option>`
    ).join('');

    return `<fieldset class="authoring-link-end-fields"><legend class="sr-only">Interface ${endIndex + 1}</legend>
        <div class="authoring-link-end-head">
            <h3 id="${id}-heading">${escapeHtml(label)}</h3>
            <button type="button" class="btn-secondary btn-small" id="${id}-change" aria-expanded="false" aria-controls="${id}-change-container" aria-label="Change node for Interface ${endIndex + 1}">Change…</button>
        </div>
        <div id="${id}-change-container" class="authoring-field-group" hidden>
            <label for="${id}-change-node">Select Node</label>
            <select id="${id}-change-node" data-end="${endIndex}">
                ${nodeOptions}
            </select>
        </div>
        <label for="${id}-port">Interface <span class="required-marker" aria-hidden="true">*</span></label>
        <select id="${id}-port" data-end="${endIndex}" data-field="port"
            aria-required="true" required aria-describedby="${id}-port-error">
            ${portOptions}
        </select>
        <p id="${id}-port-error" class="field-error" hidden></p>
    </fieldset>`;
}

function isPortOccupied(occupiedPorts, nodeId, port) {
    if (!occupiedPorts) return false;
    if (occupiedPorts instanceof Map) {
        return Boolean(occupiedPorts.get(nodeId)?.has(port));
    }
    if (occupiedPorts instanceof Set) {
        return occupiedPorts.has(`${nodeId}:${port}`);
    }
    return false;
}

function markPortOccupied(occupiedPorts, nodeId, port) {
    if (!occupiedPorts) return;
    if (occupiedPorts instanceof Map) {
        let ports = occupiedPorts.get(nodeId);
        if (!ports) {
            ports = new Set();
            occupiedPorts.set(nodeId, ports);
        }
        ports.add(port);
    } else if (occupiedPorts instanceof Set) {
        occupiedPorts.add(`${nodeId}:${port}`);
    }
}

/**
 * Validate a Link and its ends/condition, returning a structured target on error or null.
 *
 * @param {Object} link
 * @param {Object} draft
 * @param {Map|Set} [occupiedPorts]
 * @returns {{kind: 'link', id: string, controlId: string, message: string}|null}
 */
export function validateLink(link, draft, occupiedPorts = new Map()) {
    const nodes = draft?.topology?.nodes || [];
    const condition = link.condition;
    if (condition) {
        for (const field of ['delay', 'jitter']) {
            if (condition[field] !== null && condition[field] !== undefined && !/^\d+(?:\.\d+)?(?:us|ms|s)$/.test(condition[field])) {
                return {
                    kind: 'link',
                    id: link.id,
                    controlId: `link-condition-${field}`,
                    message: 'Enter a duration with units, such as 10ms.',
                };
            }
        }
        if (condition.jitter && !condition.delay) {
            return {
                kind: 'link',
                id: link.id,
                controlId: 'link-condition-jitter',
                message: 'Jitter requires delay.',
            };
        }
        if (condition.loss !== null && condition.loss !== undefined && (!/^\d+(?:\.\d+)?%$/.test(condition.loss)
            || Number(condition.loss.slice(0, -1)) > 100)) {
            return {
                kind: 'link',
                id: link.id,
                controlId: 'link-condition-loss',
                message: 'Enter packet loss from 0% to 100%.',
            };
        }
        if (condition.bandwidth !== null && condition.bandwidth !== undefined && !(condition.bandwidth > 0)) {
            return {
                kind: 'link',
                id: link.id,
                controlId: 'link-condition-bandwidth',
                message: 'Enter positive Mbps.',
            };
        }
        if (condition.bandwidth === null && condition.scheduler && condition.scheduler !== 'hfsc') {
            return {
                kind: 'link',
                id: link.id,
                controlId: 'link-condition-bandwidth',
                message: 'Enter bandwidth before choosing another queue scheduler.',
            };
        }
    }
    if (link.ends[0].node_id === link.ends[1].node_id) {
        return {
            kind: 'link',
            id: link.id,
            controlId: 'link-end-1-change',
            message: 'Choose a different Node for each end of a Link.',
        };
    }
    for (const [index, end] of link.ends.entries()) {
        const node = nodes.find(n => n.id === end.node_id);
        if (end.vlan && node && node.type !== 'switch') {
            return {
                kind: 'link',
                id: link.id,
                controlId: `link-end-${index}-port`,
                message: 'VLAN settings require a Switch end.',
            };
        }
        const target = { kind: 'link', id: link.id, controlId: `link-end-${index}-port` };
        if (end.port === '' || end.port === null || end.port === undefined) {
            return { ...target, message: 'Enter an Interface port number.' };
        }
        const lowest = firstPort(node);
        if (!Number.isInteger(end.port) || end.port < lowest || end.port > 255) {
            return { ...target, message: `Enter a number from ${lowest} to 255.` };
        }
        if (isPortOccupied(occupiedPorts, end.node_id, end.port)) {
            return { ...target, message: 'Choose a port not already used on this Node.' };
        }
        markPortOccupied(occupiedPorts, end.node_id, end.port);
    }
    return null;
}

let linkDialogDeps = null;

/**
 * Open the Link dialog for a Link ID.
 *
 * @param {string} linkId
 * @param {boolean} [force]
 * @param {number|null} [focusEndIndex]
 */
export function openLinkDialog(linkId, force = false, focusEndIndex = null) {
    if (!linkDialogDeps) return;
    const {
        getDraft, linkById, linkTitle, typeLabel, nodeLabel,
        getDialogItem, setDialogItem, closeDialogs, clearFieldError,
        updateDirtyState, renderCanvas,
    } = linkDialogDeps;
    const draft = getDraft();
    const link = linkById(linkId);
    if (!link || !draft) return;
    const dialog = document.getElementById('link-dialog');
    if (!force && dialog.open && getDialogItem()?.id === linkId) return;
    const alreadyOpen = dialog.open;
    if (!alreadyOpen) {
        closeDialogs();
        setDialogItem({ kind: 'link', id: linkId });
    }
    document.getElementById('link-dialog-title').textContent = linkTitle(link);
    const ends = document.getElementById('link-dialog-ends');
    ends.innerHTML = renderLinkEnd(link, 0, draft.topology.nodes, draft.topology.links, { typeLabel, nodeLabel })
        + renderLinkEnd(link, 1, draft.topology.nodes, draft.topology.links, { typeLabel, nodeLabel });

    const disclosure = document.getElementById('link-condition-disclosure');
    const hasCondition = Boolean(link.condition);
    document.getElementById('link-condition-enabled').checked = hasCondition;
    document.getElementById('link-condition-controls').hidden = !hasCondition;
    if (disclosure) disclosure.open = hasCondition;

    for (const field of ['status', 'delay', 'jitter', 'loss', 'bandwidth', 'scheduler']) {
        document.getElementById(`link-condition-${field}`).value = link.condition?.[field] ??
            (field === 'status' ? 'up' : field === 'scheduler' ? 'hfsc' : '');
    }

    [0, 1].forEach(index => {
        const changeBtn = document.getElementById(`link-end-${index}-change`);
        const changeContainer = document.getElementById(`link-end-${index}-change-container`);
        const changeSelect = document.getElementById(`link-end-${index}-change-node`);
        if (changeBtn && changeContainer) {
            changeBtn.addEventListener('click', () => {
                const willShow = changeContainer.hidden;
                changeContainer.hidden = !willShow;
                changeBtn.setAttribute('aria-expanded', String(willShow));
                if (willShow) changeSelect?.focus();
            });
        }
        if (changeSelect) {
            changeSelect.addEventListener('change', () => {
                const newId = changeSelect.value;
                if (!newId || newId === link.ends[index].node_id) return;
                repointLinkEnd(link, index, newId, draft);
                updateDirtyState();
                renderCanvas();
                openLinkDialog(link.id, true, index);
            });
        }
    });

    ends.querySelectorAll('[data-end][data-field]').forEach(control => {
        control.addEventListener('change', () => {
            clearFieldError(control);
            link.ends[Number(control.dataset.end)][control.dataset.field] = control.value !== '' ? Number(control.value) : control.value;
            document.getElementById('link-dialog-title').textContent = linkTitle(link);
            updateDirtyState();
            renderCanvas();
        });
    });
    if (!alreadyOpen) {
        dialog.showModal();
        (document.getElementById('link-end-0-port') || document.getElementById('link-end-0-change'))?.focus();
    } else if (focusEndIndex !== null) {
        (document.getElementById(`link-end-${focusEndIndex}-change`) || document.getElementById('link-close'))?.focus();
    }
}

/**
 * Initialize the Link dialog and bind its controls.
 *
 * @param {Object} deps
 */
export function initLinkDialog(deps) {
    linkDialogDeps = deps;
    const dialog = document.getElementById('link-dialog');
    if (!dialog) return;
    const { linkById, getDialogItem, clearFieldError, updateDirtyState, removeLink, closeDialogToCanvas, bindDialogClosing } = deps;

    document.getElementById('link-condition-enabled')?.addEventListener('change', event => {
        const link = linkById(getDialogItem()?.id);
        if (!link) return;
        if (event.target.checked) {
            link.condition = {
                status: 'up', delay: null, jitter: null, loss: null,
                bandwidth: null, scheduler: 'hfsc',
            };
            const disclosure = document.getElementById('link-condition-disclosure');
            if (disclosure) disclosure.open = true;
        } else delete link.condition;
        document.getElementById('link-condition-controls').hidden = !event.target.checked;
        updateDirtyState();
    });
    for (const field of ['status', 'delay', 'jitter', 'loss', 'bandwidth', 'scheduler']) {
        const control = document.getElementById(`link-condition-${field}`);
        if (!control) continue;
        control.addEventListener(control.tagName === 'SELECT' ? 'change' : 'input', () => {
            const link = linkById(getDialogItem()?.id);
            if (!link?.condition) return;
            clearFieldError(control);
            link.condition[field] = field === 'bandwidth'
                ? (control.value === '' ? null : Number(control.value))
                : (['delay', 'jitter', 'loss'].includes(field) && !control.value
                    ? null : control.value.trim());
            updateDirtyState();
        });
    }
    document.getElementById('link-remove')?.addEventListener('click', () => {
        const item = getDialogItem();
        if (!item?.id) return;
        removeLink(item.id);
        closeDialogToCanvas(dialog);
    });
    bindDialogClosing(dialog);
}

