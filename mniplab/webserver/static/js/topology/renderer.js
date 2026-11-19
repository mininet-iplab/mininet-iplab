import { appState } from '../state/app-state.js';
import { nodeIcons, getNodeColor } from './icons.js';
import { applyAuthoredLayout, fitTopology } from './layout.js';
import { ZOOM_EXTENT } from './fit-rule.js';
import { toggleNodeSelection, setupBackgroundClick } from './interactions.js';
import { openTerminal } from '../ui/terminal-tabs.js';
import { openSwitchPanel } from '../switch/switch-panel.js';
import { dragStarted, dragged, dragEnded } from './simulation.js';
import { openLeasesPanel } from '../dhcp/leases-panel.js';
import { openRelayPanel } from '../dhcp/relay-panel.js';
import { openDnsPanel } from '../dns/dns-panel.js';
import { openResolverPanel } from '../resolver/resolver-panel.js';
import { openRpkiCaPanel } from '../rpki/ca-panel.js';
import { openValidatorPanel } from '../rpki/validator-panel.js';
import {
    initRelayPathLayer,
    previewRelayBadge,
    clearRelayBadgePreview,
    pinRelayPath,
    unpinRelayPath,
    updateRelayPathsOnTick,
    renderRelayPaths,
} from './relay-paths.js';

// Services whose badge opens a detail panel, keyed by Service name (see
// `Service.name` in mniplab/service.py). A badge for a Service not listed
// here stays a plain (non-interactive) indicator.
const SERVICE_BADGE_HANDLERS = {
    dhcp: openLeasesPanel,
    dns: openDnsPanel,
    resolver: openResolverPanel,
    'rpki-ca': openRpkiCaPanel,
    validator: openValidatorPanel,
};

// What the badge's panel shows, named in the aria-label so the "press Enter"
// hint says what opening it reveals.
const SERVICE_BADGE_VERBS = {
    dhcp: 'view Leases',
    dns: 'view Records',
    resolver: 'inspect the Cache',
    'rpki-ca': 'edit ROAs and ASPAs',
    validator: 'view VRPs',
};

// Fixed step (in canvas coordinates) applied per Arrow-key press when nudging
// a focused Node. Deliberate, not hair-fine: a few presses reposition a Node
// without dozens of tiny taps.
const NODE_NUDGE_STEP = 20;

// A finger held still on a Node this long opens its menu: iOS Safari sends no
// `contextmenu` for a long-press, so a touch screen has no other way in.
const LONG_PRESS_MS = 500;
const LONG_PRESS_SLOP_PX = 10;

// The pending long-press, and the Node whose menu one just opened, so the tap
// that ends it does not also open the Node's Terminal.
let longPress = null;
let longPressOpenedFor = null;

function startLongPress(event, d) {
    cancelLongPress();
    if (event.pointerType !== 'touch') return;
    const timer = setTimeout(() => {
        longPress = null;
        longPressOpenedFor = d.id;
        showNodeContextMenu(d, { clientX: event.clientX, clientY: event.clientY }, true);
    }, LONG_PRESS_MS);
    longPress = { timer, x: event.clientX, y: event.clientY };
}

function moveLongPress(event) {
    if (!longPress) return;
    if (Math.hypot(event.clientX - longPress.x, event.clientY - longPress.y) > LONG_PRESS_SLOP_PX) {
        cancelLongPress();
    }
}

function cancelLongPress() {
    if (!longPress) return;
    clearTimeout(longPress.timer);
    longPress = null;
}

// Move a focused Node's own pinned position by one step in the arrow's
// direction. This pins the Node exactly the way a drag does (fx/fy set), so it
// is a Personal Layout edit — in memory only, captured only by
// an explicit instructor Save. `ticked()` repositions the DOM and any group
// box that contains the Node.
function nudgeNode(d, key) {
    const dx = key === 'ArrowLeft' ? -NODE_NUDGE_STEP : key === 'ArrowRight' ? NODE_NUDGE_STEP : 0;
    const dy = key === 'ArrowUp' ? -NODE_NUDGE_STEP : key === 'ArrowDown' ? NODE_NUDGE_STEP : 0;

    d.fx = (d.fx != null ? d.fx : d.x) + dx;
    d.fy = (d.fy != null ? d.fy : d.y) + dy;
    d.x = d.fx;
    d.y = d.fy;

    ticked();
}

// Move focus to the connected neighbor most aligned with the arrow's
// direction. This is the behavior plain Arrow keys had before nudging landed;
// it stays available behind Ctrl/Cmd so neither use of the keys is lost.
function focusNeighborInDirection(data, d, key) {
    const neighbors = data.links
        .filter(l => l.source.id === d.id || l.target.id === d.id)
        .map(l => l.source.id === d.id ? l.target : l.source);

    if (neighbors.length === 0) return;

    const targetAngles = {
        'ArrowUp': -Math.PI / 2,
        'ArrowDown': Math.PI / 2,
        'ArrowLeft': Math.PI,
        'ArrowRight': 0
    };
    const targetAngle = targetAngles[key];

    let bestNeighbor = null;
    let minAngleDiff = Infinity;

    neighbors.forEach(n => {
        const dx = n.x - d.x;
        const dy = n.y - d.y;
        let angle = Math.atan2(dy, dx);

        let diff = Math.abs(angle - targetAngle);
        if (diff > Math.PI) diff = 2 * Math.PI - diff;

        if (diff < minAngleDiff) {
            minAngleDiff = diff;
            bestNeighbor = n;
        }
    });

    if (bestNeighbor) {
        const neighborNode = appState.nodeElements.filter(n => n.id === bestNeighbor.id).node();
        if (neighborNode) neighborNode.focus();
    }
}

export function getDnsZonesInfo(serviceOrDetail) {
    if (getServiceName(serviceOrDetail) === 'dns' && typeof serviceOrDetail === 'object' && Array.isArray(serviceOrDetail.zones) && serviceOrDetail.zones.length > 0) {
        return ` (Zones: ${serviceOrDetail.zones.join(', ')})`;
    }
    return '';
}

// The Internet Gateway is drawn as the Internet cloud: a labelled graphic that
// offers no actions.
export function isInternetNode(d) {
    return d.type === 'internet';
}

// A Link with the Internet cloud at one end is labelled with both ends'
// addresses, always, so the Learner reads the Gateway's Link without opening
// the Interface labels. An end is a Node object once the simulation has
// resolved the Link, or a Node id before that.
export function isInternetLink(link) {
    const nodes = appState.currentTopology?.nodes || [];
    const kindOf = end => (end && typeof end === 'object'
        ? end.type
        : nodes.find(n => n.id === end)?.type);
    return [link.source, link.target].some(end => kindOf(end) === 'internet');
}

function linkEndText(link, end) {
    const intf = end === 'source' ? link.source_intf : link.target_intf;
    if (!isInternetLink(link)) return intf || '';
    const addresses = (end === 'source' ? link.source_addresses : link.target_addresses) || [];
    return [intf, ...addresses].filter(Boolean).join(' ');
}

export function getNodeAriaLabel(d) {
    if (isInternetNode(d)) return `Internet ${d.label}`;
    const pinned = d.fx != null ? ' (pinned)' : '';
    const ip = d.ip || 'none';
    const services = getNodeServices(d);
    const relays = getNodeRelays(d);
    const servicesText = services.length > 0
        ? `, Services: ${services.map(s => `${getServiceBadgeText(s)}${getDnsZonesInfo(s)}`).join(', ')}`
        : '';
    const relaysText = relays.length > 0
        ? `, Relays: ${relays.map(r => getRelaySubnets(r).join(', ')).join('; ')}`
        : '';
    return `${d.type} ${d.label}${pinned}, IP: ${ip}${servicesText}${relaysText}`;
}

export function getServiceBadgeText(serviceOrDetail) {
    if (!serviceOrDetail) return '';
    if (typeof serviceOrDetail === 'string') {
        return serviceOrDetail.toUpperCase();
    }
    if (serviceOrDetail.badge !== undefined && serviceOrDetail.badge !== null) {
        return serviceOrDetail.badge;
    }
    if (Array.isArray(serviceOrDetail.families)) {
        const has4 = serviceOrDetail.families.includes(4);
        const has6 = serviceOrDetail.families.includes(6);
        if (has4 && has6) return 'DHCP · DHCPv6';
        if (has6) return 'DHCPv6';
        if (has4) return 'DHCP';
        return '';
    }
    return (serviceOrDetail.name || '').toUpperCase();
}

export function getServiceName(serviceOrDetail) {
    if (!serviceOrDetail) return '';
    return typeof serviceOrDetail === 'string' ? serviceOrDetail : (serviceOrDetail.name || '');
}

export function getNodeServices(d) {
    if (Array.isArray(d.service_details) && d.service_details.length > 0) {
        return d.service_details.filter(s => Boolean(s));
    }
    return (d.services || []).filter(s => Boolean(s));
}

export function getRelaySubnets(relayOrDetail) {
    if (!relayOrDetail) return [];
    if (Array.isArray(relayOrDetail.subnets)) {
        return relayOrDetail.subnets.filter(subnet => typeof subnet === 'string' && subnet);
    }
    return [];
}

export function getRelayBadgeText(relayOrDetail) {
    const subnets = getRelaySubnets(relayOrDetail);
    return subnets.length > 0 ? `Relay: ${subnets.join(', ')}` : 'Relay';
}

// The Relay badge is drawn stacked — a DHCP RELAY heading, then one line per
// covered Subnet tagged with its address family — so it stays narrow enough
// to sit above a Router. The tags match the v4/v6 chip on the Relay Path.
export function getRelayBadgeLines(relayOrDetail) {
    return [
        { tag: '', text: 'DHCP RELAY' },
        ...getRelaySubnets(relayOrDetail).map(subnet => ({
            tag: subnet.includes(':') ? 'v6' : 'v4',
            text: subnet,
        })),
    ];
}

export function getNodeRelays(d) {
    const rawRelays = Array.isArray(d.relays) ? d.relays : [];
    return rawRelays.filter(relay => getRelaySubnets(relay).length > 0);
}

export function hasNodeRelays(d) {
    if (!d) return false;
    if (appState.currentTopology?.relays?.some(r => r.relay_node === d.id)) {
        return true;
    }
    return getNodeRelays(d).length > 0;
}

export function getVlanColor(vlanId) {
    if (!vlanId) return '#6c757d';
    // Golden ratio hue stepping ensures consistent, well-separated colors per VLAN ID
    const hue = Math.round((Number(vlanId) * 137.508) % 360);
    return `hsl(${hue}, 72%, 40%)`;
}

export function getVlanBadgeDescriptor(vlanData) {
    if (!vlanData || typeof vlanData !== 'object' || vlanData.mode === 'none') {
        return null;
    }
    if (vlanData.mode === 'access') {
        const tag = vlanData.tag != null ? vlanData.tag : '';
        return {
            text: `VLAN ${tag}`,
            color: getVlanColor(tag),
            title: `Access VLAN ${tag}`,
        };
    }
    if (vlanData.mode === 'trunk') {
        const trunks = Array.isArray(vlanData.trunks) ? vlanData.trunks : [];
        const trunksStr = trunks.length > 0 ? trunks.join(', ') : 'all';
        const primeColor = trunks.length > 0 ? getVlanColor(trunks[0]) : '#4a5568';
        return {
            text: `Trunk [${trunksStr}]`,
            color: primeColor,
            title: `Trunk [${trunksStr}]`,
        };
    }
    return null;
}


function getNodeBadgeItems(d) {
    return [
        ...getNodeServices(d).map(service => ({ kind: 'service', value: service })),
        ...getNodeRelays(d).map(relay => ({ kind: 'relay', value: relay })),
    ];
}

function getBadgeDescriptor(item) {
    const isService = item.kind === 'service';
    const name = isService ? getServiceName(item.value) : 'relay';
    const text = isService ? getServiceBadgeText(item.value) : getRelayBadgeText(item.value);
    // A Relay badge opens the Relay's activity panel exactly the way a
    // Service badge opens its own panel: one shape for "what does this
    // Node offer".
    const handler = isService ? SERVICE_BADGE_HANDLERS[name] : openRelayPanel;
    const zonesInfo = isService ? getDnsZonesInfo(item.value) : '';

    return {
        text: text,
        lines: isService ? null : getRelayBadgeLines(item.value),
        handler: handler,
        key: isService
            ? `service:${name}`
            : `relay:${getRelaySubnets(item.value).join('|')}`,
        className: `${item.kind}-badge ${item.kind}-badge-${name} topology-badge`,
        backgroundClass: `${item.kind}-badge-bg`,
        textClass: `${item.kind}-badge-text`,
        ariaLabel: isService
            ? (handler
                ? `${text} Service${zonesInfo} — press Enter to ${SERVICE_BADGE_VERBS[name] || 'view details'}`
                : `${text} Service${zonesInfo}`)
            : `${text} — Relay for Subnets — press Enter to view what the Relay forwarded`,
        title: isService ? `${text} Service${zonesInfo}` : `${text} — Relay for Subnets`,
        cornerRadius: isService ? 4 : 0,
    };
}

function getBadgeText(item) {
    return getBadgeDescriptor(item).text;
}

function getBadgeKey(item) {
    return getBadgeDescriptor(item).key;
}

export function updateNodeServiceFromPools(nodeId, pools) {
    if (!appState.currentTopology || !Array.isArray(appState.currentTopology.nodes)) {
        return;
    }
    const node = appState.currentTopology.nodes.find(n => n.id === nodeId);
    if (!node) return;

    // Derive families from pools array
    const poolList = Array.isArray(pools) ? pools : [];
    const hasV4 = poolList.some(p => p.family === 4 || (p.cidr && p.cidr.includes('.')));
    const hasV6 = poolList.some(p => p.family === 6 || (p.cidr && p.cidr.includes(':')));

    const families = [];
    if (hasV4) families.push(4);
    if (hasV6) families.push(6);

    const badgeText = getServiceBadgeText({ name: 'dhcp', families });
    const detail = { name: 'dhcp', badge: badgeText, families: families };

    if (!Array.isArray(node.service_details) || node.service_details.length === 0) {
        node.service_details = [detail];
    } else {
        const idx = node.service_details.findIndex(s => getServiceName(s) === 'dhcp');
        if (idx >= 0) {
            node.service_details[idx] = detail;
        } else {
            node.service_details.push(detail);
        }
    }

    if (!Array.isArray(node.services)) {
        node.services = ['dhcp'];
    } else if (!node.services.some(s => getServiceName(s) === 'dhcp')) {
        node.services.push('dhcp');
    }

    if (appState.nodeElements) {
        appState.nodeElements.each(function (d) {
            if (d.id === nodeId) {
                d.service_details = node.service_details;
                d.services = node.services;
            }
        });

        appState.nodeElements.attr('aria-label', d => getNodeAriaLabel(d));
        appState.nodeElements.select('title').text(d => getNodeAriaLabel(d));
        renderNodeBadges(appState.nodeElements);
    }
}

export function renderNodeBadges(nodeSelection) {
    nodeSelection.each(function (d) {
        const nodeEl = d3.select(this);
        let badgesGroup = nodeEl.select('.node-badges');
        if (badgesGroup.empty()) {
            badgesGroup = nodeEl.append('g').attr('class', 'node-badges');
        }

        const badgeItems = getNodeBadgeItems(d);
        const badges = badgesGroup.selectAll('.topology-badge')
            .data(badgeItems, item => getBadgeKey(item));

        badges.exit().remove();

        const badgeEnter = badges.enter()
            .append('g')
            .attr('class', item => getBadgeDescriptor(item).className)
            // Interactivity is wired once at enter, matching every other
            // click/keydown handler in this file — D3 nodes persist across
            // updates, so an update-time rebind would just be a duplicate.
            .attr('tabindex', item => {
                return getBadgeDescriptor(item).handler ? '0' : null;
            })
            .attr('role', item => getBadgeDescriptor(item).handler ? 'button' : null)
            .on('click', (event, item) => {
                const handler = getBadgeDescriptor(item).handler;
                if (!handler) return;
                event.stopPropagation();
                handler(d.id);
            })
            .on('keydown', (event, item) => {
                const handler = getBadgeDescriptor(item).handler;
                if (!handler) return;
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    event.stopPropagation();
                    handler(d.id);
                }
            })
            .on('mouseenter', (event, item) => {
                if (item.kind === 'relay') {
                    previewRelayBadge(d.id, item.value);
                }
            })
            .on('mouseleave', (event, item) => {
                if (item.kind === 'relay') {
                    clearRelayBadgePreview();
                }
            })
            .on('focus', (event, item) => {
                if (item.kind === 'relay') {
                    previewRelayBadge(d.id, item.value);
                }
            })
            .on('blur', (event, item) => {
                if (item.kind === 'relay') {
                    clearRelayBadgePreview();
                }
            });

        badgeEnter.append('title');
        badgeEnter.append('rect').attr('class', item => getBadgeDescriptor(item).backgroundClass);
        badgeEnter.append('text').attr('class', item => getBadgeDescriptor(item).textClass);

        const allBadges = badgeEnter.merge(badges).order();

        allBadges
            .attr('class', item => getBadgeDescriptor(item).className)
            .attr('aria-label', item => getBadgeDescriptor(item).ariaLabel);

        allBadges.select('title')
            .text(item => getBadgeDescriptor(item).title);
        allBadges.select('rect')
            .attr('class', item => getBadgeDescriptor(item).backgroundClass);
        allBadges.select('text')
            .attr('class', item => getBadgeDescriptor(item).textClass)
            .each(function (item) {
                const lines = getBadgeDescriptor(item).lines;
                const textEl = d3.select(this);
                if (!lines) {
                    textEl.text(getBadgeDescriptor(item).text);
                    return;
                }
                textEl.text(null);
                lines.forEach(line => {
                    if (line.tag) {
                        textEl.append('tspan').attr('class', 'relay-badge-tag').text(line.tag);
                    }
                    textEl.append('tspan')
                        .attr('class', line.tag ? 'relay-badge-subnet' : 'relay-badge-heading')
                        .text(line.text);
                });
            });

        if (badgeItems.length === 0) {
            return;
        }

        // Layout badges horizontally, centered above the node circle. Width
        // comes from the rendered glyphs; the character-count estimate is
        // only a fallback for a text node that has no layout yet.
        const LINE_HEIGHT = 12;
        const PAD_X = 8;
        const TAG_WIDTH = 18;
        const badgeData = [];
        allBadges.each(function (item) {
            const lines = getBadgeDescriptor(item).lines;
            const textEl = d3.select(this).select('text');
            let contentWidth = 0;
            if (lines) {
                textEl.selectAll('tspan').each(function () {
                    const isTag = this.classList.contains('relay-badge-tag');
                    const own = this.getComputedTextLength ? this.getComputedTextLength() : 0;
                    const w = isTag ? 0 : own + (this.previousElementSibling ? TAG_WIDTH : 0);
                    contentWidth = Math.max(contentWidth, w);
                });
            } else {
                const node = textEl.node();
                contentWidth = node && node.getComputedTextLength ? node.getComputedTextLength() : 0;
            }
            const fallback = getBadgeText(item).length * 6;
            const width = Math.max(30, Math.ceil((contentWidth > 0 ? contentWidth : fallback) + PAD_X * 2));
            const height = lines ? lines.length * LINE_HEIGHT + 6 : 16;
            badgeData.push({ item: item, width: width, height: height });
        });

        const gap = 4;
        const totalWidth = badgeData.reduce((sum, b) => sum + b.width, 0) + (badgeData.length - 1) * gap;
        let currentX = -totalWidth / 2;
        // Badges sit on a common baseline just above the Node circle; a
        // taller (stacked) badge grows upward from it.
        const bottom = -25;

        allBadges.each(function (s, i) {
            const b = badgeData[i];
            const bgX = currentX;
            const bgY = bottom - b.height;
            const badge = getBadgeDescriptor(b.item);

            d3.select(this).select('rect')
                .attr('x', bgX)
                .attr('y', bgY)
                .attr('width', b.width)
                .attr('height', b.height)
                .attr('rx', badge.cornerRadius)
                .attr('ry', badge.cornerRadius);

            // Vertical centring is the stylesheet's job (dominant-baseline:
            // central); adding dy here as well pushed the text below centre.
            const textEl = d3.select(this).select('text');
            if (!badge.lines) {
                textEl
                    .style('text-anchor', null)
                    .attr('x', bgX + b.width / 2)
                    .attr('y', bgY + b.height / 2);
            } else {
                // Inline style: the stylesheet anchors badge text at middle.
                textEl.style('text-anchor', 'start').attr('x', null).attr('y', null);
                let lineIndex = 0;
                textEl.selectAll('tspan').each(function () {
                    const isTag = this.classList.contains('relay-badge-tag');
                    const startsLine = isTag || !(this.previousElementSibling
                        && this.previousElementSibling.classList.contains('relay-badge-tag'));
                    if (startsLine && this.previousElementSibling) lineIndex += 1;
                    const x = bgX + PAD_X + (!isTag && !startsLine ? TAG_WIDTH : 0);
                    d3.select(this)
                        .attr('x', x)
                        .attr('y', bgY + 3 + LINE_HEIGHT * (lineIndex + 0.5));
                });
            }

            currentX += b.width + gap;
        });
    });
}

export function updateTopologyColors() {
    const textColor = getComputedStyle(document.documentElement).getPropertyValue('--text-color').trim();
    const linkColor = getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim();
    const nodeBg = getComputedStyle(document.documentElement).getPropertyValue('--node-bg').trim();

    if (appState.nodeElements) {
        appState.nodeElements.select('circle')
            .attr('fill', nodeBg)
            .attr('stroke', d => getNodeColor(d.type));

        appState.nodeElements.select('text.node-label')
            .style('fill', textColor);
    }

    if (appState.labelElements) {
        appState.labelElements.selectAll('text.link-label')
            .style('fill', textColor);

        appState.labelElements.selectAll('.vlan-badge text')
            .style('fill', '#ffffff');
    }

    if (appState.linkElements) {
        appState.linkElements.style('stroke', linkColor);
    }

    if (appState.gridPattern) {
        appState.gridPattern.select('path')
            .attr('stroke', linkColor);
    }
}

export function getLinkNodeId(endpoint) {
    return typeof endpoint === 'object' ? endpoint.id : endpoint;
}

export function getLinkNodeLabel(endpoint) {
    if (typeof endpoint === 'object') {
        return endpoint.label || endpoint.id;
    }
    return endpoint;
}

export function initTopology() {
    const topoSel = d3.select('#topology');
    const container = topoSel.node().parentElement;
    const width = container.clientWidth;
    const height = container.clientHeight;

    topoSel.on('.zoom', null);
    topoSel.selectAll('*').remove();
    appState.svg = topoSel.attr('width', width).attr('height', height);

    // Create grid pattern
    const defs = appState.svg.append('defs');
    appState.gridPattern = defs.append('pattern')
        .attr('id', 'grid')
        .attr('width', 40)
        .attr('height', 40)
        .attr('patternUnits', 'userSpaceOnUse');

    appState.gridPattern.append('path')
        .attr('d', 'M 40 0 L 0 0 0 40')
        .attr('fill', 'none')
        .attr('stroke', 'var(--text-muted)')
        .attr('stroke-width', 1)
        .attr('stroke-dasharray', '4 4')
        .attr('opacity', 0.3);

    // Background rect for grid
    appState.svg.append('rect')
        .attr('class', 'grid-background')
        .attr('width', '100%')
        .attr('height', '100%')
        .attr('fill', 'url(#grid)')
        .style('pointer-events', 'none')
        .style('display', appState.showGrid ? 'block' : 'none');

    // Create zoom behavior
    appState.zoom = d3.zoom()
        .scaleExtent(ZOOM_EXTENT)
        .on('zoom', (event) => {
            appState.transform = event.transform;
            // The zoom behavior is attached to the svg before appState.g is
            // created a few lines below, so a wheel/zoom event can fire while
            // g is still null (or right after initTopology re-runs and removes
            // the old group). Guard it the same way gridPattern is guarded.
            if (appState.g) {
                appState.g.attr('transform', appState.transform);
            }
            if (appState.gridPattern) {
                appState.gridPattern.attr('patternTransform', appState.transform.toString());
            }
        });

    appState.svg.call(appState.zoom);

    // Create main group for all elements
    appState.g = appState.svg.append('g');

    // Create layers for z-indexing
    appState.g.append('g').attr('class', 'layer-groups');
    appState.g.append('g').attr('class', 'layer-links');
    appState.g.append('g').attr('class', 'layer-relay-paths');
    appState.g.append('g').attr('class', 'layer-nodes');
    appState.g.append('g').attr('class', 'layer-labels');

    initRelayPathLayer(appState.g, defs);
    setupBackgroundClick();
}

export function renderTopology(data) {
    const width = +appState.svg.attr('width');
    const height = +appState.svg.attr('height');

    // Initialize simulation if it doesn't exist
    if (!appState.simulation) {
        appState.simulation = d3.forceSimulation()
            .force('link', d3.forceLink().id(d => d.id).distance(200))
            .force('charge', d3.forceManyBody().strength(-400))
            .force('center', d3.forceCenter(width / 2, height / 2))
            .force('collision', d3.forceCollide().radius(60));

        appState.simulation.on('tick', ticked);

        // When the bootstrap simulation settles, pin all nodes so the
        // arrangement stays put. This covers both a brand-new Lab (nothing
        // authored yet) and an Authored Layout that a code-added Node
        // outgrew (only that Node was left free to settle; the rest were
        // already pinned by applyAuthoredLayout and hold their position).
        // Nothing is persisted — only an explicit instructor Save captures
        // an Authored Layout.
        appState.simulation.on('end', () => {
            if (appState.isInitialLoad) {
                // Pin all nodes to their calculated positions
                appState.simulation.nodes().forEach(d => {
                    d.fx = d.x;
                    d.fy = d.y;
                });
                if (appState.nodeElements) appState.nodeElements.classed('pinned', true);
                appState.isInitialLoad = false;
                fitTopology();
            }
        });
    }

    // Update global reference
    appState.currentTopology = data;

    // Map existing nodes to preserve state
    const oldNodes = new Map(appState.simulation.nodes().map(d => [d.id, d]));
    data.nodes.forEach(d => {
        const old = oldNodes.get(d.id);
        if (old) {
            // Preserve all position and state data
            d.x = old.x;
            d.y = old.y;
            d.vx = old.vx || 0;
            d.vy = old.vy || 0;
            d.fx = old.fx;
            d.fy = old.fy;
        }
    });

    // On a fresh load, apply the Authored Layout (fetched into
    // appState.authoredLayout by loadTopology) directly and statically.
    let appliedAuthored = false;
    if (oldNodes.size === 0) {
        appliedAuthored = applyAuthoredLayout(data.nodes, appState.authoredLayout);
    }

    // A Node that code added after the Authored Layout was captured has no
    // entry in the file, so applyAuthoredLayout leaves it unpinned
    // (fx == null). When that happens the simulation still needs to run —
    // just for that Node — while every already-placed Node stays pinned.
    const hasUnplacedNodes = data.nodes.some(d => d.fx == null);
    const fullyStatic = appliedAuthored && !hasUnplacedNodes;

    // Update simulation data
    appState.simulation.nodes(data.nodes);
    appState.simulation.force('link').links(data.links);

    if (fullyStatic) {
        // Authored Layout covers every Node: place statically, never run the
        // force simulation, so there is no settle-flicker.
        appState.simulation.stop();
        appState.isInitialLoad = false;
    } else if (appState.isInitialLoad && oldNodes.size === 0) {
        // Bootstrap case: either there is no Authored Layout at all, or an
        // Authored Layout exists but doesn't cover every Node. Either way,
        // run the simulation — pinned Nodes hold their fx/fy, so only the
        // unplaced Node(s) actually move.
        appState.simulation.alpha(1).restart();
    } else if (!appState.isInitialLoad) {
        // After bootstrap settles, keep later updates completely static.
        // A second startup render must let the running simulation finish.
        appState.simulation.stop();
    }

    // Update Links (Lines)
    const linkLayer = appState.g.select('.layer-links');
    const links = linkLayer.selectAll('.link')
        .data(data.links, d => `${d.source.id}-${d.target.id}`);

    links.exit().remove();

    const linksEnter = links.enter()
        .append('line')
        .attr('class', 'link')
        .attr('tabindex', '0')
        .attr('role', 'button')
        .attr('aria-haspopup', 'menu');

    appState.linkElements = linksEnter.merge(links)
        .classed('status-down', d => d.status === 'down')
        .classed('link', true)
        .attr('tabindex', '0')
        .attr('role', 'button')
        .attr('aria-haspopup', 'menu')
        .attr('aria-label', d => {
            const sourceLabel = getLinkNodeLabel(d.source);
            const targetLabel = getLinkNodeLabel(d.target);
            const status = d.status === 'down' ? 'down' : 'up';
            const addresses = isInternetLink(d)
                ? ` Addresses: ${[...(d.source_addresses || []), ...(d.target_addresses || [])].join(', ')}.`
                : '';
            return `Link between ${sourceLabel} and ${targetLabel}.${addresses} Status: ${status}. Press Enter for link actions.`;
        })
        .on('click', (event, d) => {
            event.stopPropagation();
            showLinkContextMenu(d, event);
        })
        .on('keydown', (event, d) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                event.stopPropagation();
                showLinkContextMenu(d, event, true);
            } else if (event.key === 'Escape') {
                event.preventDefault();
                hideLinkContextMenu();
            }
        });

    // Update Link Labels
    const labelLayer = appState.g.select('.layer-labels');
    const labels = labelLayer.selectAll('.link-label-group')
        .data(data.links, d => `${d.source.id}-${d.target.id}`);

    labels.exit().remove();

    const labelsEnter = labels.enter()
        .append('g')
        .attr('class', 'link-label-group');

    labelsEnter.append('text')
        .attr('class', 'link-label source-label')
        .attr('dy', -5);

    labelsEnter.append('text')
        .attr('class', 'link-label target-label')
        .attr('dy', -5);

    appState.labelElements = labelsEnter.merge(labels);

    // Update interface labels text
    appState.labelElements.select('.source-label')
        .text(d => linkEndText(d, 'source'))
        .style('display', d => (appState.showInterfaceLabels || isInternetLink(d)) ? 'block' : 'none');

    appState.labelElements.select('.target-label')
        .text(d => linkEndText(d, 'target'))
        .style('display', d => (appState.showInterfaceLabels || isInternetLink(d)) ? 'block' : 'none');

    // Render switch VLAN pill badges on links
    appState.labelElements.each(function (d) {
        const group = d3.select(this);

        // Source VLAN badge
        const sDesc = getVlanBadgeDescriptor(d.source_vlan);
        let sBadge = group.select('.source-vlan-badge');
        if (sDesc) {
            if (sBadge.empty()) {
                sBadge = group.append('g').attr('class', 'vlan-badge source-vlan-badge');
                sBadge.append('rect').attr('rx', 7).attr('ry', 7);
                sBadge.append('text').attr('class', 'vlan-badge-text').attr('text-anchor', 'middle').attr('dy', '0.35em');
            }
            sBadge.style('display', 'block');
            const badgeWidth = Math.max(50, sDesc.text.length * 6.5 + 14);
            const badgeHeight = 16;
            sBadge.select('rect')
                .attr('width', badgeWidth)
                .attr('height', badgeHeight)
                .attr('x', -badgeWidth / 2)
                .attr('y', -badgeHeight / 2)
                .attr('fill', sDesc.color)
                .attr('stroke', '#ffffff')
                .attr('stroke-width', 1)
                .attr('opacity', 0.95);
            sBadge.select('text')
                .text(sDesc.text)
                .attr('fill', '#ffffff')
                .style('fill', '#ffffff')
                .attr('font-size', '10px')
                .attr('font-weight', '600')
                .attr('font-family', 'var(--font-main, sans-serif)');
            sBadge.attr('aria-label', sDesc.title);
        } else if (!sBadge.empty()) {
            sBadge.style('display', 'none');
        }

        // Target VLAN badge
        const tDesc = getVlanBadgeDescriptor(d.target_vlan);
        let tBadge = group.select('.target-vlan-badge');
        if (tDesc) {
            if (tBadge.empty()) {
                tBadge = group.append('g').attr('class', 'vlan-badge target-vlan-badge');
                tBadge.append('rect').attr('rx', 7).attr('ry', 7);
                tBadge.append('text').attr('class', 'vlan-badge-text').attr('text-anchor', 'middle').attr('dy', '0.35em');
            }
            tBadge.style('display', 'block');
            const badgeWidth = Math.max(50, tDesc.text.length * 6.5 + 14);
            const badgeHeight = 16;
            tBadge.select('rect')
                .attr('width', badgeWidth)
                .attr('height', badgeHeight)
                .attr('x', -badgeWidth / 2)
                .attr('y', -badgeHeight / 2)
                .attr('fill', tDesc.color)
                .attr('stroke', '#ffffff')
                .attr('stroke-width', 1)
                .attr('opacity', 0.95);
            tBadge.select('text')
                .text(tDesc.text)
                .attr('fill', '#ffffff')
                .style('fill', '#ffffff')
                .attr('font-size', '10px')
                .attr('font-weight', '600')
                .attr('font-family', 'var(--font-main, sans-serif)');
            tBadge.attr('aria-label', tDesc.title);
        } else if (!tBadge.empty()) {
            tBadge.style('display', 'none');
        }
    });

    // The View menu's VLAN Labels option applies to every redraw, and is only
    // offered when the Lab has a Switch.
    labelLayer.classed('hide-vlan-labels', !appState.showVlanLabels);
    const vlanLabelsOption = document.getElementById('vlan-labels-option');
    if (vlanLabelsOption) {
        vlanLabelsOption.classList.toggle('hidden', !data.nodes.some(n => n.type === 'switch'));
    }

    // Update Nodes
    const nodeLayer = appState.g.select('.layer-nodes');
    appState.nodeElements = nodeLayer.selectAll('.node')
        .data(data.nodes, d => d.id);

    appState.nodeElements.exit().remove();

    const nodeEnter = appState.nodeElements.enter()
        .append('g')
        .attr('class', d => `node ${d.fx != null ? 'pinned' : ''}`)
        .attr('tabindex', '0')
        .attr('role', 'button')
        .attr('aria-haspopup', 'menu')
        .attr('aria-label', d => getNodeAriaLabel(d))
        .call(d3.drag()
            .filter(event => !event.ctrlKey && !event.button && !event.metaKey)
            .on('start', dragStarted)
            .on('drag', dragged)
            .on('end', dragEnded))
        .on('contextmenu', (event, d) => {
            event.preventDefault();
            event.stopPropagation();
            cancelLongPress();
            showNodeContextMenu(d, event);
        })
        .on('pointerdown.longpress', startLongPress)
        .on('pointermove.longpress', moveLongPress)
        .on('pointerup.longpress pointercancel.longpress', cancelLongPress)
        .on('click', (event, d) => {
            if (longPressOpenedFor === d.id) {
                // The tap that ended a long-press leaves its menu open.
                longPressOpenedFor = null;
                event.preventDefault();
                event.stopPropagation();
                return;
            }
            longPressOpenedFor = null;
            if (event.ctrlKey || event.metaKey) {
                event.stopPropagation();
                toggleNodeSelection(d.id);
            } else {
                if (hasNodeRelays(d)) {
                    pinRelayPath(d.id);
                } else if (appState.pinnedRelayNode && appState.pinnedRelayNode !== d.id) {
                    unpinRelayPath();
                }
                if (d.type === 'switch') {
                    openSwitchPanel(d.id);
                } else {
                    openTerminal(d.id);
                }
            }
        })
        .on('keydown', (event, d) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                if (hasNodeRelays(d)) {
                    pinRelayPath(d.id);
                } else if (appState.pinnedRelayNode && appState.pinnedRelayNode !== d.id) {
                    unpinRelayPath();
                }
                if (d.type === 'switch') {
                    openSwitchPanel(d.id);
                } else {
                    openTerminal(d.id);
                }
                return;
            }

            if (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey)) {
                event.preventDefault();
                event.stopPropagation();
                showNodeContextMenu(d, event, true);
                return;
            }

            if (event.key === 'Escape') {
                hideNodeContextMenu();
                return;
            }

            if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) {
                return;
            }
            event.preventDefault();

            // Ctrl/Cmd + Arrow keeps the original "jump focus to the neighbor in
            // this direction" behavior. Plain Arrow nudges this Node's own
            // pinned position instead, so a keyboard-only user can arrange the
            // canvas without a mouse (see the Help modal's shortcut list).
            if (event.ctrlKey || event.metaKey) {
                focusNeighborInDirection(data, d, event.key);
            } else {
                nudgeNode(d, event.key);
            }
        });

    // Background circle for hit area and contrast
    nodeEnter.append('circle')
        .attr('r', 30)
        .attr('fill', 'var(--node-bg)')
        .attr('stroke', d => getNodeColor(d.type))
        .attr('stroke-width', 2);
    // Icon image (replaced with inline SVG)
    // Check if icon is simple (string) or complex (object)
    const iconDef = d => nodeIcons[d.type] || nodeIcons.host;
    const isComplex = d => typeof iconDef(d) === 'object';
    const viewBox = d => isComplex(d) ? iconDef(d).viewBox : '0 0 24 24';

    const iconGroup = nodeEnter.append('svg')
        .attr('aria-hidden', 'true').attr('focusable', 'false')
        .attr('viewBox', viewBox)
        .attr('width', 56)
        .attr('height', 56)
        .attr('x', -28)
        .attr('y', -28)
        .style('overflow', 'visible');

    iconGroup.each(function (d) {
        const currentIconDef = iconDef(d);
        if (isComplex(d)) {
            // Complex icon with multiple paths/colors
            currentIconDef.elements.forEach(el => {
                d3.select(this).append('path')
                    .attr('d', el.d)
                    .attr('fill', el.fill);
            });
        } else {
            // Simple icon handling
            d3.select(this).append('path')
                .attr('d', currentIconDef)
                .attr('fill', getNodeColor(d.type));
        }
    });

    nodeEnter.append('title');

    nodeEnter.append('text')
        .attr('class', 'node-label')
        .attr('dy', 5);

    appState.nodeElements = nodeEnter.merge(appState.nodeElements);

    // Update classes for pinned state
    appState.nodeElements.attr('class', d => `node ${d.fx != null ? 'pinned' : ''}`);
    // The Internet cloud offers no actions, so it is a labelled graphic, not a
    // button that opens a menu, and it takes no focus stop.
    appState.nodeElements
        .attr('role', d => (isInternetNode(d) ? 'img' : 'button'))
        .attr('tabindex', d => (isInternetNode(d) ? null : '0'))
        .attr('aria-haspopup', d => (isInternetNode(d) ? null : 'menu'));

    // Update aria-label and title tooltip for all nodes
    appState.nodeElements.attr('aria-label', d => getNodeAriaLabel(d));
    appState.nodeElements.select('title').text(d => getNodeAriaLabel(d));

    // Update text labels for all nodes and apply label visibility
    // The Internet cloud carries its caption, "Internet", inside the icon the
    // way a Node carries its label; the caption always shows.
    appState.textElements = appState.nodeElements.select('text.node-label');
    appState.textElements
        .text(d => (isInternetNode(d) ? 'Internet' : d.label))
        .style('display', d => (appState.showLabels || isInternetNode(d)) ? 'block' : 'none');

    // Render service badges
    renderNodeBadges(appState.nodeElements);

    // Render relay paths
    renderRelayPaths();

    // Apply theme colors
    updateTopologyColors();

    // Fully-static Authored Layout path: every node is placed statically, so
    // drive one tick to position the DOM at their canonical coordinates,
    // mark them pinned, and perform the single deterministic fit-to-view. No
    // simulation runs, so there is no settle-flicker. When a Node is still
    // unplaced, skip this — the simulation is running instead, and its own
    // 'tick'/'end' handlers take care of positioning, pinning, and fitting.
    if (fullyStatic) {
        appState.nodeElements.classed('pinned', true);
        ticked();
        fitTopology();
    }
}

export function ticked() {
    if (appState.linkElements) {
        appState.linkElements
            .attr('x1', d => d.source.x)
            .attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x)
            .attr('y2', d => d.target.y);
    }

    if (appState.labelElements) {
        const nodeRadius = 55; // Adjusted radius for optimal visibility (30 + padding)

        appState.labelElements.select('.source-label')
            .attr('x', d => {
                const dx = d.target.x - d.source.x;
                const dy = d.target.y - d.source.y;
                const l = Math.sqrt(dx * dx + dy * dy);
                if (l === 0) return d.source.x;
                return d.source.x + (dx / l) * nodeRadius;
            })
            .attr('y', d => {
                const dx = d.target.x - d.source.x;
                const dy = d.target.y - d.source.y;
                const l = Math.sqrt(dx * dx + dy * dy);
                if (l === 0) return d.source.y;
                return d.source.y + (dy / l) * nodeRadius;
            });

        appState.labelElements.select('.target-label')
            .attr('x', d => {
                const dx = d.target.x - d.source.x;
                const dy = d.target.y - d.source.y;
                const l = Math.sqrt(dx * dx + dy * dy);
                if (l === 0) return d.target.x;
                return d.target.x - (dx / l) * nodeRadius;
            })
            .attr('y', d => {
                const dx = d.target.x - d.source.x;
                const dy = d.target.y - d.source.y;
                const l = Math.sqrt(dx * dx + dy * dy);
                if (l === 0) return d.target.y;
                return d.target.y - (dy / l) * nodeRadius;
            });

        const vlanBadgeOffset = 88;

        appState.labelElements.select('.source-vlan-badge')
            .attr('transform', d => {
                const dx = d.target.x - d.source.x;
                const dy = d.target.y - d.source.y;
                const l = Math.sqrt(dx * dx + dy * dy);
                if (l === 0) return `translate(${d.source.x},${d.source.y})`;
                const bx = d.source.x + (dx / l) * vlanBadgeOffset;
                const by = d.source.y + (dy / l) * vlanBadgeOffset;
                return `translate(${bx},${by})`;
            });

        appState.labelElements.select('.target-vlan-badge')
            .attr('transform', d => {
                const dx = d.target.x - d.source.x;
                const dy = d.target.y - d.source.y;
                const l = Math.sqrt(dx * dx + dy * dy);
                if (l === 0) return `translate(${d.target.x},${d.target.y})`;
                const bx = d.target.x - (dx / l) * vlanBadgeOffset;
                const by = d.target.y - (dy / l) * vlanBadgeOffset;
                return `translate(${bx},${by})`;
            });
    }

    if (appState.nodeElements) {
        appState.nodeElements
            .attr('transform', d => `translate(${d.x},${d.y})`)
            .attr('aria-label', d => getNodeAriaLabel(d));
    }

    // Update group positions
    if (appState.groupElements && appState.simulation) {
        const nodeMap = new Map(appState.simulation.nodes().map(n => [n.id, n]));
        const padding = 15;

        appState.groupElements.each(function (d) {
            let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
            let hasNodes = false;

            d.nodes.forEach(id => {
                const node = nodeMap.get(id);
                if (node) {
                    hasNodes = true;
                    minX = Math.min(minX, node.x);
                    maxX = Math.max(maxX, node.x);
                    minY = Math.min(minY, node.y);
                    maxY = Math.max(maxY, node.y);
                }
            });

            if (hasNodes) {
                const r = 32; // Node radius approx
                minX -= (r + padding);
                maxX += (r + padding);
                minY -= (r + padding);
                maxY += (r + padding);

                const width = maxX - minX;
                const height = maxY - minY;

                d3.select(this).select('.group-box')
                    .attr('x', minX)
                    .attr('y', minY)
                    .attr('width', width)
                    .attr('height', height);

                d3.select(this).select('.group-label')
                    .attr('x', minX + 5)
                    .attr('y', minY + 20); // Inside top-left corner

                d3.select(this).attr('display', 'block');
            } else {
                d3.select(this).attr('display', 'none');
            }
        });
    }

    updateRelayPathsOnTick();
}

// Note: showLinkContextMenu and hideLinkContextMenu are imported from interactions.js
// to avoid circular dependencies. They are referenced in renderTopology via the
// event handlers above but since they are called at runtime, the import is safe.
import {
    showLinkContextMenu,
    hideLinkContextMenu,
    showNodeContextMenu,
    hideNodeContextMenu,
} from './interactions.js';
