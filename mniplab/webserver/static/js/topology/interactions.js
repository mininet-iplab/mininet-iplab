import { appState } from '../state/app-state.js';
import { getLinkNodeId, getLinkNodeLabel, isInternetLink } from './renderer.js';
import { pinRelayPath, unpinRelayPath, clearPinnedRelayPath } from './relay-paths.js';
import { openTerminal } from '../ui/terminal-tabs.js';
import { openDnsPanel } from '../dns/dns-panel.js';
import { openResolverPanel } from '../resolver/resolver-panel.js';
import { openSpeakerPanel } from '../speaker/speaker-panel.js';
import { openSwitchPanel } from '../switch/switch-panel.js';
import { CAPTURABLE_TYPES } from '../capture/packet-analysis.js';
import { isTranslator } from '../capture/nat-table.js';

let activeNodeContextId = null;

export function setSelectedLinkInputs(node1, node2) {
    const node1Input = document.getElementById('link-node1');
    const node2Input = document.getElementById('link-node2');
    if (node1Input) node1Input.value = node1 || '';
    if (node2Input) node2Input.value = node2 || '';
}

// A Link to the Internet offers no Link Condition, Disconnect or Reconnect:
// each would change the Host machine's own network. Its Packet Capture stays,
// on the Router's end of the Link.
export function setInternetLinkActions(link) {
    const cloudLink = isInternetLink(link);
    const configButton = document.getElementById('link-config-btn');
    if (configButton) {
        configButton.classList.toggle('hidden', cloudLink);
        configButton.setAttribute('aria-hidden', String(cloudLink));
    }
    if (!cloudLink) return;
    for (const id of ['link-disconnect-btn', 'link-reconnect-btn']) {
        const button = document.getElementById(id);
        if (button) {
            button.classList.add('hidden');
            button.setAttribute('aria-hidden', 'true');
        }
    }
}

export function setLinkActionButtons(linkStatus) {
    const disconnectBtn = document.getElementById('link-disconnect-btn');
    const reconnectBtn = document.getElementById('link-reconnect-btn');
    if (!disconnectBtn || !reconnectBtn) return;

    if (linkStatus === 'down') {
        disconnectBtn.classList.add('hidden');
        reconnectBtn.classList.remove('hidden');
    } else {
        disconnectBtn.classList.remove('hidden');
        reconnectBtn.classList.add('hidden');
    }
}

export function hideLinkContextMenu(clearInputs = false) {
    const menu = document.getElementById('link-context-menu');
    if (!menu) return;

    if (appState.linkElements) appState.linkElements.classed('selected', false);

    menu.classList.add('hidden');
    menu.setAttribute('aria-hidden', 'true');

    if (clearInputs) {
        setSelectedLinkInputs('', '');
    }
}

function nodeHasService(node, name) {
    const services = Array.isArray(node?.service_details) && node.service_details.length > 0
        ? node.service_details
        : (node?.services || []);
    return services.some(service =>
        typeof service === 'string' ? service === name : service?.name === name
    );
}

/** Open a Node's panel from its context menu entry.
 *
 * The entry is hidden along with the menu the moment it is pressed, so a
 * panel that captured it as the element to return focus to would have
 * nothing to return to and strand focus on the body. Focus goes to the Node
 * first; the panel's trap then captures the Node and hands focus back to it
 * on close, the way a badge-opened panel returns to its badge. */
function openPanelFromMenu(open) {
    const nodeId = activeNodeContextId;
    hideNodeContextMenu();
    if (!nodeId) return;
    const nodeEl = appState.nodeElements?.filter((d) => d.id === nodeId).node();
    if (nodeEl) nodeEl.focus();
    open(nodeId);
}

export function hideNodeContextMenu() {
    const menu = document.getElementById('node-context-menu');
    if (!menu) return;
    menu.classList.add('hidden');
    menu.setAttribute('aria-hidden', 'true');
    activeNodeContextId = null;
}

export function showNodeContextMenu(node, event, focusFirstAction = false) {
    // The Internet cloud offers no actions, so it opens no menu.
    if (node?.type === 'internet') return;
    hideLinkContextMenu();

    const menu = document.getElementById('node-context-menu');
    const viewport = document.querySelector('.topology-viewport');
    if (!menu || !viewport || !node) return;

    activeNodeContextId = node.id;
    const dnsButton = document.getElementById('node-dns-btn');
    const hasDns = nodeHasService(node, 'dns');
    if (dnsButton) {
        dnsButton.classList.toggle('hidden', !hasDns);
        dnsButton.setAttribute('aria-hidden', String(!hasDns));
    }

    const resolverButton = document.getElementById('node-resolver-btn');
    const hasResolver = nodeHasService(node, 'resolver');
    if (resolverButton) {
        resolverButton.classList.toggle('hidden', !hasResolver);
        resolverButton.setAttribute('aria-hidden', String(!hasResolver));
    }

    // A Speaker is a Node kind, not a Service: the entry follows the display
    // type the Topology payload carries, the same way the Speaker's icon does.
    const speakerButton = document.getElementById('node-speaker-btn');
    const isSpeaker = node.type === 'bgp-speaker';
    if (speakerButton) {
        speakerButton.classList.toggle('hidden', !isSpeaker);
        speakerButton.setAttribute('aria-hidden', String(!isSpeaker));
    }

    const captureButton = document.getElementById('node-capture-btn');
    const capturable = CAPTURABLE_TYPES.has(node.type);
    if (captureButton) {
        captureButton.classList.toggle('hidden', !capturable);
        captureButton.setAttribute('aria-hidden', String(!capturable));
    }

    // Only a Node that translates for its Lab has a NAT Table to show.
    const natTableButton = document.getElementById('node-nat-table-btn');
    const translates = isTranslator(node.id);
    if (natTableButton) {
        natTableButton.classList.toggle('hidden', !translates);
        natTableButton.setAttribute('aria-hidden', String(!translates));
    }

    // A Switch has no Terminal: its panel is the only way in.
    const isSwitch = node.type === 'switch';
    const terminalButton = document.getElementById('node-terminal-btn');
    if (terminalButton) {
        terminalButton.classList.toggle('hidden', isSwitch);
        terminalButton.setAttribute('aria-hidden', String(isSwitch));
    }

    const switchButton = document.getElementById('node-switch-btn');
    if (switchButton) {
        switchButton.classList.toggle('hidden', !isSwitch);
        switchButton.setAttribute('aria-hidden', String(!isSwitch));
    }

    menu.classList.remove('hidden');
    menu.setAttribute('aria-hidden', 'false');

    const rect = viewport.getBoundingClientRect();
    let x;
    let y;
    if (typeof event?.clientX === 'number' && typeof event?.clientY === 'number' &&
        (event.clientX !== 0 || event.clientY !== 0)) {
        x = event.clientX - rect.left + 12;
        y = event.clientY - rect.top + 12;
    } else {
        const [nodeX, nodeY] = appState.transform?.apply
            ? appState.transform.apply([node.x ?? 0, node.y ?? 0])
            : [node.x ?? 0, node.y ?? 0];
        x = nodeX + 12;
        y = nodeY + 12;
    }

    const margin = 8;
    const menuWidth = menu.offsetWidth || 220;
    const menuHeight = menu.offsetHeight || 90;
    const maxX = Math.max(margin, viewport.clientWidth - menuWidth - margin);
    const maxY = Math.max(margin, viewport.clientHeight - menuHeight - margin);
    menu.style.left = `${Math.min(Math.max(x, margin), maxX)}px`;
    menu.style.top = `${Math.min(Math.max(y, margin), maxY)}px`;

    if (focusFirstAction) {
        const firstAction = menu.querySelector('button:not(.hidden)');
        if (firstAction) firstAction.focus();
    }
}

export function showLinkContextMenu(link, event, focusFirstAction = false) {
    hideNodeContextMenu();
    if (appState.linkElements) appState.linkElements.classed('selected', false);
    if (event && event.currentTarget) {
        d3.select(event.currentTarget).classed('selected', true);
    }

    const menu = document.getElementById('link-context-menu');
    const viewport = document.querySelector('.topology-viewport');
    if (!menu || !viewport || !link) return;

    const node1 = getLinkNodeId(link.source);
    const node2 = getLinkNodeId(link.target);
    setSelectedLinkInputs(node1, node2);
    setLinkActionButtons(link.status || 'up');

    setInternetLinkActions(link);

    menu.classList.remove('hidden');
    menu.setAttribute('aria-hidden', 'false');

    const rect = viewport.getBoundingClientRect();
    let x;
    let y;

    if (typeof event.clientX === 'number' && typeof event.clientY === 'number' &&
        (event.clientX !== 0 || event.clientY !== 0)) {
        x = event.clientX - rect.left + 12;
        y = event.clientY - rect.top + 12;
    } else {
        const sx = link.source?.x ?? 0;
        const sy = link.source?.y ?? 0;
        const tx = link.target?.x ?? 0;
        const ty = link.target?.y ?? 0;
        const midX = (sx + tx) / 2;
        const midY = (sy + ty) / 2;
        const [zoomX, zoomY] = appState.transform.apply([midX, midY]);
        x = zoomX + 12;
        y = zoomY + 12;
    }

    const margin = 8;
    const menuWidth = menu.offsetWidth || 220;
    const menuHeight = menu.offsetHeight || 120;
    const maxX = Math.max(margin, viewport.clientWidth - menuWidth - margin);
    const maxY = Math.max(margin, viewport.clientHeight - menuHeight - margin);
    const clampedX = Math.min(Math.max(x, margin), maxX);
    const clampedY = Math.min(Math.max(y, margin), maxY);

    menu.style.left = `${clampedX}px`;
    menu.style.top = `${clampedY}px`;

    if (focusFirstAction) {
        const firstAction = menu.querySelector('button:not(.hidden)');
        if (firstAction) firstAction.focus();
    }
}

export function setupLinkContextMenuListeners() {
    if (appState.linkContextMenuListenersInitialized && appState.nodeContextMenuListenersInitialized) return;
    appState.linkContextMenuListenersInitialized = true;
    appState.nodeContextMenuListenersInitialized = true;

    const menu = document.getElementById('link-context-menu');
    if (menu) {
        menu.addEventListener('click', (event) => {
            if (event.target.closest('button')) {
                hideLinkContextMenu();
            }
        });
    }

    const nodeMenu = document.getElementById('node-context-menu');
    if (nodeMenu) {
        nodeMenu.addEventListener('click', (event) => {
            if (event.target.closest('button')) hideNodeContextMenu();
        });
    }

    const terminalButton = document.getElementById('node-terminal-btn');
    if (terminalButton) {
        terminalButton.addEventListener('click', () => {
            const nodeId = activeNodeContextId;
            hideNodeContextMenu();
            if (nodeId) openTerminal(nodeId);
        });
    }

    const dnsButton = document.getElementById('node-dns-btn');
    if (dnsButton) {
        dnsButton.addEventListener('click', () => openPanelFromMenu(openDnsPanel));
    }

    const resolverButton = document.getElementById('node-resolver-btn');
    if (resolverButton) {
        resolverButton.addEventListener('click', () => openPanelFromMenu(openResolverPanel));
    }

    const speakerButton = document.getElementById('node-speaker-btn');
    if (speakerButton) {
        speakerButton.addEventListener('click', () => openPanelFromMenu(openSpeakerPanel));
    }

    const switchButton = document.getElementById('node-switch-btn');
    if (switchButton) {
        switchButton.addEventListener('click', () => openPanelFromMenu(openSwitchPanel));
    }

    const captureButton = document.getElementById('node-capture-btn');
    if (captureButton) {
        captureButton.addEventListener('click', () => openPanelFromMenu(
            (nodeId) => appState.networkConsole && appState.networkConsole.openNodeCaptureModal(nodeId)
        ));
    }

    const natTableButton = document.getElementById('node-nat-table-btn');
    if (natTableButton) {
        natTableButton.addEventListener('click', () => openPanelFromMenu(
            () => appState.packetAnalysis && appState.packetAnalysis.openNatTable()
        ));
    }

    document.addEventListener('click', (event) => {
        const menuEl = document.getElementById('link-context-menu');
        if (menuEl && !menuEl.classList.contains('hidden') && !menuEl.contains(event.target)) {
            hideLinkContextMenu();
        }
        const nodeMenuEl = document.getElementById('node-context-menu');
        if (nodeMenuEl && !nodeMenuEl.classList.contains('hidden') && !nodeMenuEl.contains(event.target)) {
            hideNodeContextMenu();
        }
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            hideLinkContextMenu();
            hideNodeContextMenu();
        }
    });
}

export function setupBackgroundClick() {
    appState.svg.on('click', (event) => {
        hideLinkContextMenu();
        hideNodeContextMenu();

        const target = event.target;
        const isSvg = target && (target.tagName?.toLowerCase() === 'svg' || target.id === 'topology');
        const isGrid = target && target.classList && target.classList.contains('grid-background');
        if (isSvg || isGrid) {
            clearPinnedRelayPath();
            appState.selectedGroup = null;
            const btn = document.getElementById('delete-group-btn');
            if (btn) {
                btn.disabled = true;
                btn.style.display = 'none';
            }
            // updateGroups will be called by the caller or event handler

            // Also clear node selection if not holding Ctrl
            if (!event.ctrlKey && !event.metaKey) {
                appState.selectedNodes.clear();
                updateSelectionVisuals();
            }
        }
    });
}

export function updateContextControls() {
    const node1Input = document.getElementById('link-node1');
    const node2Input = document.getElementById('link-node2');
    const disconnectBtn = document.getElementById('link-disconnect-btn');
    const reconnectBtn = document.getElementById('link-reconnect-btn');

    if (appState.selectedNodes.size === 2) {
        const [n1, n2] = Array.from(appState.selectedNodes);

        // Find if link exists in currentTopology
        let linkExists = false;
        let linkStatus = 'up';
        let foundLink = null;

        if (appState.currentTopology && appState.currentTopology.links) {
            const link = appState.currentTopology.links.find(l => {
                const srcId = typeof l.source === 'object' ? l.source.id : l.source;
                const tgtId = typeof l.target === 'object' ? l.target.id : l.target;
                return (srcId === n1 && tgtId === n2) || (srcId === n2 && tgtId === n1);
            });
            if (link) {
                linkExists = true;
                linkStatus = link.status || 'up';
                foundLink = link;
            }
        }

        if (linkExists) {
            if (node1Input) node1Input.value = n1;
            if (node2Input) node2Input.value = n2;

            if (disconnectBtn && reconnectBtn) {
                if (linkStatus === 'down') {
                    disconnectBtn.classList.add('hidden');
                    reconnectBtn.classList.remove('hidden');
                } else {
                    disconnectBtn.classList.remove('hidden');
                    reconnectBtn.classList.add('hidden');
                }
            }
            // After the status buttons above: a cloud Link hides both of them.
            setInternetLinkActions(foundLink);
        }
    }
}

export function toggleNodeSelection(nodeId) {
    if (appState.selectedNodes.has(nodeId)) {
        appState.selectedNodes.delete(nodeId);
        if (appState.pinnedRelayNode === nodeId) {
            unpinRelayPath();
        }
    } else {
        appState.selectedNodes.add(nodeId);
        const hasRelays = appState.currentTopology?.relays?.some(r => r.relay_node === nodeId);
        if (hasRelays) {
            pinRelayPath(nodeId);
        } else if (appState.pinnedRelayNode && appState.pinnedRelayNode !== nodeId) {
            unpinRelayPath();
        }
    }
    updateSelectionVisuals();
    // Small delay to ensure D3 has processed links
    setTimeout(() => updateContextControls(), 50);
}

export function updateSelectionVisuals() {
    if (appState.nodeElements) {
        appState.nodeElements.classed('selected', d => appState.selectedNodes.has(d.id));
    }
}
