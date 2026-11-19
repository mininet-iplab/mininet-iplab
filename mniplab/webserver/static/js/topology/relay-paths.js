// Relay Path canvas overlay module
// Renders the 3-node relayed DHCP flow (client_nodes -> relaying_router -> target_node)
// on demand as an accessible, interactive SVG overlay between layer-links and layer-nodes.

import { appState } from '../state/app-state.js';

/**
 * Initialize the layer and arrowhead marker for relay paths.
 *
 * @param {d3.Selection} g - The main SVG group.
 * @param {d3.Selection} defs - The SVG defs element.
 */
export function initRelayPathLayer(g, defs) {
    if (!defs.select('#relay-arrow').node()) {
        defs.append('marker')
            .attr('id', 'relay-arrow')
            .attr('viewBox', '0 -5 10 10')
            .attr('refX', 8)
            .attr('refY', 0)
            .attr('markerWidth', 6)
            .attr('markerHeight', 6)
            .attr('orient', 'auto')
            .append('path')
            .attr('d', 'M0,-5L10,0L0,5')
            .attr('class', 'relay-arrow-head');
    }

    if (g.select('.layer-relay-paths').empty()) {
        // Insert layer between layer-links and layer-nodes
        const layerNodes = g.select('.layer-nodes');
        if (!layerNodes.empty()) {
            g.insert('g', '.layer-nodes').attr('class', 'layer-relay-paths');
        } else {
            g.append('g').attr('class', 'layer-relay-paths');
        }
    }
}

/**
 * Announce a message to assistive technology via #topology-live-region.
 *
 * @param {string} message - The polite announcement text.
 */
export function announceRelayPath(message) {
    const liveRegion = document.getElementById('topology-live-region');
    if (!liveRegion) return;
    liveRegion.textContent = '';
    setTimeout(() => {
        liveRegion.textContent = message;
    }, 50);
}

/**
 * Format live region announcement for a relay.
 *
 * @param {object} relay - Resolved relay entity from topology payload.
 * @returns {string} Announcement text.
 */
export function formatRelayAnnouncement(relay) {
    const clients = (relay.client_nodes && relay.client_nodes.length > 0)
        ? relay.client_nodes.join(', ')
        : 'clients';
    const target = relay.target_node
        ? relay.target_node
        : (relay.server_ip ? `${relay.server_ip} (Unreachable)` : 'upstream service');

    let familyStr = 'IPv4';
    if (relay.families && relay.families.includes(4) && relay.families.includes(6)) {
        familyStr = 'IPv4/IPv6';
    } else if (relay.families && relay.families.includes(6)) {
        familyStr = 'IPv6';
    }

    return `Relay path active: ${clients} via ${relay.relay_node} to ${target} (${familyStr})`;
}

/**
 * Get family badge text (v4, v6, or v4/v6) for a relay.
 *
 * @param {object} relay - Relay descriptor.
 * @returns {string}
 */
export function getRelayChipText(relay) {
    if (relay.families && relay.families.includes(4) && relay.families.includes(6)) {
        return 'v4/v6';
    }
    if (relay.families && relay.families.includes(6)) {
        return 'v6';
    }
    return 'v4';
}

/**
 * Preview a single relay path on badge hover or keyboard focus.
 *
 * @param {string} routerNodeId - The relaying router node name.
 * @param {object} relayDescriptor - The relay descriptor or subnets from badge.
 */
export function previewRelayBadge(routerNodeId, relayDescriptor) {
    const relays = appState.currentTopology?.relays || [];
    const subnets = Array.isArray(relayDescriptor?.subnets)
        ? relayDescriptor.subnets
        : (Array.isArray(relayDescriptor) ? relayDescriptor : []);

    // Find matching relay entity
    const matched = relays.find(r => {
        if (r.relay_node !== routerNodeId) return false;
        if (subnets.length === 0) return true;
        return r.subnets && r.subnets.some(s => subnets.includes(s));
    });

    if (matched) {
        appState.activeRelayPath = matched;
        renderRelayPaths();
        announceRelayPath(formatRelayAnnouncement(matched));
    }
}

/**
 * Clear the hover/focus preview.
 */
export function clearRelayBadgePreview() {
    if (!appState.activeRelayPath) return;
    appState.activeRelayPath = null;

    if (appState.pinnedRelayNode) {
        renderRelayPaths();
    } else {
        clearRelayPathsDOM();
        announceRelayPath('Relay path dismissed');
    }
}

/**
 * Pin all relay paths attached to a router node.
 *
 * @param {string} routerNodeId - Router node ID.
 */
export function pinRelayPath(routerNodeId) {
    const relays = (appState.currentTopology?.relays || []).filter(
        r => r.relay_node === routerNodeId
    );
    if (relays.length === 0) return;

    appState.pinnedRelayNode = routerNodeId;
    appState.activeRelayPath = null;
    renderRelayPaths();

    if (relays.length === 1) {
        announceRelayPath(formatRelayAnnouncement(relays[0]));
    } else {
        const descriptions = relays.map(r => formatRelayAnnouncement(r));
        announceRelayPath(descriptions.join('; '));
    }
}

/**
 * Unpin the pinned relay router.
 */
export function unpinRelayPath() {
    if (!appState.pinnedRelayNode) return;
    appState.pinnedRelayNode = null;
    appState.activeRelayPath = null;
    clearRelayPathsDOM();
    announceRelayPath('Relay path dismissed');
}

/**
 * Clear all pinned and preview states.
 */
export function clearPinnedRelayPath() {
    const wasActive = Boolean(appState.pinnedRelayNode || appState.activeRelayPath);
    appState.pinnedRelayNode = null;
    appState.activeRelayPath = null;
    clearRelayPathsDOM();
    if (wasActive) {
        announceRelayPath('Relay path dismissed');
    }
}

/**
 * Clear DOM elements inside layer-relay-paths.
 */
function clearRelayPathsDOM() {
    if (!appState.g) return;
    const layer = appState.g.select('.layer-relay-paths');
    if (!layer.empty()) {
        layer.selectAll('*').remove();
    }
}

/**
 * Calculate quadratic Bezier curve and midpoint.
 */
export function getQuadraticCurve(sx, sy, tx, ty, curvature = 25) {
    const dx = tx - sx;
    const dy = ty - sy;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len === 0) {
        return { d: `M ${sx} ${sy} L ${tx} ${ty}`, cx: sx, cy: sy, midX: sx, midY: sy };
    }
    const nx = -dy / len;
    const ny = dx / len;
    const cx = (sx + tx) / 2 + nx * curvature;
    const cy = (sy + ty) / 2 + ny * curvature;
    const midX = 0.25 * sx + 0.5 * cx + 0.25 * tx;
    const midY = 0.25 * sy + 0.5 * cy + 0.25 * ty;
    return {
        d: `M ${sx} ${sy} Q ${cx} ${cy} ${tx} ${ty}`,
        cx,
        cy,
        midX,
        midY,
    };
}

/**
 * Render or re-render relay paths into .layer-relay-paths based on current state.
 */
export function renderRelayPaths() {
    if (!appState.g) return;
    const layer = appState.g.select('.layer-relay-paths');
    if (layer.empty()) return;

    let activeRelays = [];
    if (appState.activeRelayPath) {
        // Hover isolates the specific relay path
        activeRelays = [appState.activeRelayPath];
    } else if (appState.pinnedRelayNode) {
        // Selection pins all attached relay paths
        activeRelays = (appState.currentTopology?.relays || []).filter(
            r => r.relay_node === appState.pinnedRelayNode
        );
    }

    if (activeRelays.length === 0) {
        layer.selectAll('*').remove();
        return;
    }

    const groups = layer.selectAll('.relay-path-group')
        .data(activeRelays, d => d.id);

    groups.exit().remove();

    const groupEnter = groups.enter()
        .append('g')
        .attr('class', 'relay-path-group')
        .attr('data-relay-id', d => d.id);

    groupEnter.append('g').attr('class', 'relay-client-legs');
    groupEnter.append('g').attr('class', 'relay-forwarding-leg');
    groupEnter.append('g').attr('class', 'relay-path-chip');

    // Also update coordinates immediately
    updateRelayPathsOnTick();
}

/**
 * Append a protective halo path and matching dashed spline to an SVG group.
 */
function appendCurvePair(container, legClass, d, markerEnd = null) {
    container.append('path')
        .attr('class', `relay-path-halo ${legClass}`)
        .attr('d', d);
    const spline = container.append('path')
        .attr('class', `relay-path-spline ${legClass}`)
        .attr('d', d);
    if (markerEnd) {
        spline.attr('marker-end', markerEnd);
    }
    return spline;
}

/**
 * Update the SVG paths and chip coordinates during simulation ticks or node drag.
 */
export function updateRelayPathsOnTick() {
    if (!appState.g) return;
    const layer = appState.g.select('.layer-relay-paths');
    if (layer.empty()) return;

    const nodeElements = appState.simulation ? appState.simulation.nodes() : (appState.currentTopology?.nodes || []);
    const nodeMap = new Map(nodeElements.map(n => [n.id, n]));

    layer.selectAll('.relay-path-group').each(function (relay) {
        const group = d3.select(this);
        const routerNode = nodeMap.get(relay.relay_node);
        if (!routerNode) return;

        const rx = routerNode.x ?? 0;
        const ry = routerNode.y ?? 0;

        const intermediateSwitch = relay.intermediate_switch ? nodeMap.get(relay.intermediate_switch) : null;
        const isx = intermediateSwitch?.x ?? 0;
        const isy = intermediateSwitch?.y ?? 0;

        const clientNodes = (relay.client_nodes || []).map(id => nodeMap.get(id)).filter(Boolean);

        // 1. Client legs
        const clientLegsGroup = group.select('.relay-client-legs');
        clientLegsGroup.selectAll('*').remove();

        if (intermediateSwitch) {
            // Clients fan in to switch
            clientNodes.forEach(c => {
                const curve = getQuadraticCurve(c.x, c.y, isx, isy, 15);
                appendCurvePair(clientLegsGroup, 'client-leg', curve.d, 'url(#relay-arrow)');
            });
            // Switch to router
            const trunkCurve = getQuadraticCurve(isx, isy, rx, ry, 20);
            appendCurvePair(clientLegsGroup, 'trunk-leg', trunkCurve.d, 'url(#relay-arrow)');
        } else {
            // Clients fan in directly to router
            clientNodes.forEach(c => {
                const curve = getQuadraticCurve(c.x, c.y, rx, ry, 15);
                appendCurvePair(clientLegsGroup, 'client-leg', curve.d, 'url(#relay-arrow)');
            });
        }

        // 2. Forwarding leg (router to target node or floating terminus)
        const forwardingGroup = group.select('.relay-forwarding-leg');
        forwardingGroup.selectAll('*').remove();

        let tx = rx + 90;
        let ty = ry - 40;
        let isReachable = false;

        if (relay.target_node && nodeMap.has(relay.target_node)) {
            const targetNode = nodeMap.get(relay.target_node);
            tx = targetNode.x;
            ty = targetNode.y;
            isReachable = true;
        } else {
            // Unreachable terminus: project opposite incident client links
            if (clientNodes.length > 0 || intermediateSwitch) {
                let avgDx = 0;
                let avgDy = 0;
                if (intermediateSwitch) {
                    avgDx = rx - isx;
                    avgDy = ry - isy;
                } else {
                    for (const c of clientNodes) {
                        avgDx += rx - c.x;
                        avgDy += ry - c.y;
                    }
                }
                const dist = Math.sqrt(avgDx * avgDx + avgDy * avgDy);
                if (dist > 0.001) {
                    tx = rx + (avgDx / dist) * 90;
                    ty = ry + (avgDy / dist) * 90;
                }
            }
        }

        const fwdCurve = getQuadraticCurve(rx, ry, tx, ty, 25);
        appendCurvePair(forwardingGroup, 'forwarding-leg', fwdCurve.d, 'url(#relay-arrow)');

        // Unreachable floating marker
        if (!isReachable) {
            const tooltip = `DHCP Service at ${relay.server_ip || 'Server'} is unreachable — no node in the topology owns this address`;
            const terminus = forwardingGroup.append('g')
                .attr('class', 'relay-unreachable-terminus')
                .attr('tabindex', '0')
                .attr('role', 'img')
                .attr('aria-label', tooltip)
                .attr('transform', `translate(${tx}, ${ty})`);

            terminus.append('title').text(tooltip);

            const labelText = `⚠️ ${relay.server_ip || 'Server'} (Unreachable)`;
            const textWidth = Math.max(80, labelText.length * 7.5);

            terminus.append('rect')
                .attr('x', -textWidth / 2)
                .attr('y', -11)
                .attr('width', textWidth)
                .attr('height', 22)
                .attr('rx', 4)
                .attr('ry', 4);

            terminus.append('text')
                .text(labelText);

            terminus.on('focus', () => {
                announceRelayPath(tooltip);
            });
        }

        // 3. Midpoint family chip
        const chipGroup = group.select('.relay-path-chip');
        chipGroup.selectAll('*').remove();

        const chipText = getRelayChipText(relay);
        const chipWidth = chipText.length > 3 ? 38 : 28;

        const chipInner = chipGroup.append('g')
            .attr('transform', `translate(${fwdCurve.midX}, ${fwdCurve.midY})`);

        chipInner.append('rect')
            .attr('x', -chipWidth / 2)
            .attr('y', -8)
            .attr('width', chipWidth)
            .attr('height', 16)
            .attr('rx', 3)
            .attr('ry', 3);

        chipInner.append('text')
            .text(chipText);
    });
}
