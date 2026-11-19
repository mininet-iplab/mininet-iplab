// The authoring canvas: drawing the Topology, one Tab stop with arrow-key
// focus between Nodes, moving Nodes by keyboard and pointer, and the "+"
// handle that links two Nodes.
const NODE_RADIUS = 30;
const LABEL_OFFSET = 56;
// Shift+Arrow moves a Node this far; Ctrl+Shift+Arrow, the larger step.
const MOVE_STEP = 10;
const LARGE_MOVE_STEP = 50;
const DIRECTIONS = {
    ArrowRight: { x: 1, y: 0, name: 'to the right of' },
    ArrowLeft: { x: -1, y: 0, name: 'to the left of' },
    ArrowDown: { x: 0, y: 1, name: 'below' },
    ArrowUp: { x: 0, y: -1, name: 'above' },
};

let context = null;
// The one canvas item in the Tab order: the last one focused, else the first Node.
let tabStop = null;
let pendingLink = null;
// A click that chose a Link's other end also ends a double click, which must not open a dialog.
let clickPicked = false;

/**
 * The canvas element for a Node or a Link.
 *
 * @param {'host'|'link'} kind
 * @param {string} id
 * @returns {Element|undefined}
 */
export function canvasItem(kind, id) {
    const attribute = kind === 'host' ? 'data-node-id' : 'data-link-id';
    return [...document.querySelectorAll(`#draft-canvas [${attribute}]`)]
        .find(element => element.getAttribute(attribute) === id);
}

function itemOf(element) {
    const item = element?.closest?.('#draft-canvas [data-node-id], #draft-canvas [data-link-id]');
    if (!item) return null;
    return item.hasAttribute('data-node-id')
        ? { kind: 'host', id: item.getAttribute('data-node-id'), element: item }
        : { kind: 'link', id: item.getAttribute('data-link-id'), element: item };
}

// Exactly one item is reachable with Tab; the rest are reached with arrows.
function syncTabStop() {
    const items = [...document.querySelectorAll('#draft-canvas [data-node-id], #draft-canvas [data-link-id]')];
    const current = (tabStop && canvasItem(tabStop.kind, tabStop.id))
        || document.querySelector('#draft-canvas [data-node-id]');
    for (const element of items) element.setAttribute('tabindex', element === current ? '0' : '-1');
}

// Where an item sits: a Node's position, or the middle of a Link.
function pointOf(item) {
    if (item.kind === 'host') return context.position(item.id);
    const link = context.links().find(entry => entry.id === item.id);
    const [first, second] = link.ends.map(end => context.position(end.node_id));
    return { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 };
}

// Top to bottom, then left to right; placement order breaks exact ties.
function readingOrder() {
    return context.nodes()
        .map((node, index) => ({ node, index, ...context.position(node.id) }))
        .sort((a, b) => a.y - b.y || a.x - b.x || a.index - b.index);
}

/**
 * The nearest Node in an arrow's direction, or null when none lies that way.
 *
 * Candidates lie in the 90° cone around the direction; when the cone is
 * empty, anywhere ahead of the item. The nearest wins; ties go to the smaller
 * sideways offset, then to reading order.
 */
function nearestInDirection(item, direction) {
    const origin = pointOf(item);
    const candidates = readingOrder()
        .filter(entry => entry.node.id !== item.id)
        .map((entry, order) => {
            const dx = entry.x - origin.x;
            const dy = entry.y - origin.y;
            const ahead = dx * direction.x + dy * direction.y;
            const aside = Math.abs(dx * direction.y - dy * direction.x);
            return { entry, order, ahead, aside, distance: Math.hypot(dx, dy) };
        })
        .filter(candidate => candidate.ahead > 0);
    const inCone = candidates.filter(candidate => candidate.aside <= candidate.ahead);
    const best = (inCone.length ? inCone : candidates)
        .sort((a, b) => a.distance - b.distance || a.aside - b.aside || a.order - b.order)[0];
    return best?.entry.node || null;
}

// Home, End, Page Up and Page Down step through every Node in reading order,
// so no Node is out of reach when arrows never lead to it.
function inReadingOrder(item, key) {
    const order = readingOrder();
    if (key === 'Home') return order[0]?.node;
    if (key === 'End') return order.at(-1)?.node;
    const origin = pointOf(item);
    const index = item.kind === 'host' ? order.findIndex(entry => entry.node.id === item.id) : -1;
    const after = entry => entry.y > origin.y || (entry.y === origin.y && entry.x > origin.x);
    if (key === 'PageDown') {
        return (index >= 0 ? order[index + 1] : order.find(after))?.node || null;
    }
    return (index >= 0 ? order[index - 1] : order.filter(entry => !after(entry)).at(-1))?.node || null;
}

function nameOf(item) {
    if (item.kind === 'host') return context.nodes().find(node => node.id === item.id)?.name || 'this Node';
    return 'this Link';
}

function moveNode(item, direction, step) {
    const node = context.nodes().find(entry => entry.id === item.id);
    const from = context.position(node.id);
    const to = { x: from.x + direction.x * step, y: from.y + direction.y * step };
    context.move(node.id, to);
    context.announce(`Moved ${context.describeNode(node)} to x ${to.x}, y ${to.y}.`);
}

// The focusable Link is invisible, so its line shows where focus is.
function markFocusedLine(item) {
    d3.selectAll('#draft-canvas .link-line:not(.link-pending)')
        .classed('focused', d => item?.kind === 'link' && d?.link?.id === item.id);
}

function onKeydown(event) {
    const item = itemOf(event.target);
    if (!item || event.altKey || event.metaKey) return;
    const connecting = Boolean(context.connecting());
    const direction = DIRECTIONS[event.key];
    if (direction) {
        event.preventDefault();
        if (event.shiftKey) {
            if (connecting) return;
            if (item.kind === 'host') moveNode(item, direction, event.ctrlKey ? LARGE_MOVE_STEP : MOVE_STEP);
            return;
        }
        if (event.ctrlKey) return;
        const next = nearestInDirection(item, direction);
        if (next) canvasItem('host', next.id)?.focus();
        else context.announce(`No Node ${direction.name} ${nameOf(item)}.`);
    } else if (['Home', 'End', 'PageDown', 'PageUp'].includes(event.key)) {
        event.preventDefault();
        const next = inReadingOrder(item, event.key);
        if (next) canvasItem('host', next.id)?.focus();
        else context.announce(`No Node ${event.key === 'PageDown' ? 'after' : 'before'} ${nameOf(item)}.`);
    } else if (connecting) {
        if ((event.key === 'Enter' || event.key === ' ') && item.kind === 'host') {
            event.preventDefault();
            context.pick(item.id);
        }
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        event.stopPropagation();
        context.remove(item.kind, item.id);
    } else if (event.key === 'F2' && item.kind === 'host') {
        event.preventDefault();
        context.rename(item.id);
    } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        context.open(item.kind, item.id);
    }
}

// The second press of a double click that chose a Link's other end leaves
// focus on the new Link.
function secondPressOfPick(event) {
    return clickPicked && event.detail > 1;
}

// Pointer gestures are conveniences: every one has a keyboard path through
// the arrow keys, Connect… and the properties dialogs (WCAG 2.1.1, 2.5.7).
const nodeDrag = d3.drag()
    .container(() => document.getElementById('draft-canvas-g'))
    .filter(event => !event.button && !event.target.closest('.link-handle'))
    .subject((event, d) => ({ x: d.x, y: d.y }))
    .on('start', function (event) {
        // The press's own default would focus the Node, so it is prevented too.
        if (secondPressOfPick(event.sourceEvent)) event.sourceEvent.preventDefault();
        else this.focus();
    })
    .on('drag', (event, d) => {
        context.move(d.node.id, { x: Math.round(event.x), y: Math.round(event.y) });
    });

const handleDrag = d3.drag()
    .container(() => document.getElementById('draft-canvas-g'))
    .subject((event, d) => ({ x: d.x + NODE_RADIUS, y: d.y }))
    .on('start', (event, d) => {
        pendingLink = d3.select('#draft-canvas .layer-links').append('line')
            .attr('class', 'link-line link-pending')
            .attr('x1', d.x).attr('y1', d.y).attr('x2', event.x).attr('y2', event.y);
    })
    .on('drag', event => {
        if (pendingLink) pendingLink.attr('x2', event.x).attr('y2', event.y);
    })
    .on('end', (event, d) => {
        if (!pendingLink) return;
        pendingLink.remove();
        pendingLink = null;
        const { clientX, clientY } = event.sourceEvent.changedTouches?.[0] || event.sourceEvent;
        const target = document.elementFromPoint(clientX, clientY)?.closest('#draft-canvas .node');
        const targetId = target?.getAttribute('data-node-id');
        if (targetId && targetId !== d.node.id) {
            const link = context.link(d.node.id, targetId);
            if (link) canvasItem('link', link.id)?.focus();
        }
    });

/**
 * Draw the draft's Nodes and Links.
 *
 * Nodes come before Links in the DOM, so a screen reader meets them first,
 * while each Link's visible line is painted beneath the Nodes: the focusable
 * Link after the Nodes is transparent and leaves the pointer to its line.
 */
export function drawCanvas() {
    const svg = d3.select('#draft-canvas').classed('connecting', Boolean(context.connecting()));
    const { nodeId: selectedNodeId, linkId: selectedLinkId } = context.selection();
    const nodes = context.nodes().map(node => ({ node, ...context.position(node.id) }));
    const points = new Map(nodes.map(item => [item.node.id, item]));
    const links = context.links()
        .map(link => ({ link, source: points.get(link.ends[0].node_id), target: points.get(link.ends[1].node_id) }))
        .filter(item => item.source && item.target);
    const ends = selection => selection
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);

    svg.select('.layer-links').selectAll('line.link-line:not(.link-pending)')
        .data(links, d => d.link.id)
        .join(enter => enter.append('line')
            .attr('class', 'link-line')
            .on('click', (event, d) => {
                event.stopPropagation();
                canvasItem('link', d.link.id)?.focus();
            })
            .on('dblclick', (event, d) => context.open('link', d.link.id)))
        .classed('selected', d => d.link.id === selectedLinkId)
        .call(ends);

    svg.select('.layer-labels').selectAll('g.link-label-group')
        .data(links, d => d.link.id)
        .join(enter => {
            const group = enter.append('g').attr('class', 'link-label-group').attr('aria-hidden', 'true');
            group.append('text').attr('class', 'link-label source-label').attr('dy', -5);
            group.append('text').attr('class', 'link-label target-label').attr('dy', -5);
            return group;
        })
        .each(function (d) {
            const dx = d.target.x - d.source.x;
            const dy = d.target.y - d.source.y;
            const length = Math.hypot(dx, dy) || 1;
            const offsetX = dx / length * LABEL_OFFSET;
            const offsetY = dy / length * LABEL_OFFSET;
            const group = d3.select(this);
            group.select('.source-label')
                .attr('x', d.source.x + offsetX).attr('y', d.source.y + offsetY)
                .text(`eth${d.link.ends[0].port}`);
            group.select('.target-label')
                .attr('x', d.target.x - offsetX).attr('y', d.target.y - offsetY)
                .text(`eth${d.link.ends[1].port}`);
        });

    const nodeElements = svg.select('.layer-nodes').selectAll('g.node')
        .data(nodes, d => d.node.id)
        .join(enter => {
            const group = enter.append('g')
                .attr('class', 'node')
                .attr('role', 'button')
                .attr('aria-haspopup', 'dialog')
                .call(nodeDrag)
                .on('click', (event, d) => {
                    event.stopPropagation();
                    if (context.connecting()) {
                        clickPicked = true;
                        context.pick(d.node.id);
                    } else if (event.detail <= 1) {
                        clickPicked = false;
                    }
                })
                .on('dblclick', (event, d) => {
                    if (!clickPicked) context.open('host', d.node.id);
                    clickPicked = false;
                });
            group.append('circle').attr('class', 'node-body').attr('r', NODE_RADIUS)
                .attr('fill', 'var(--node-bg)').attr('stroke-width', 2.5);
            group.append('svg').attr('class', 'node-icon').attr('viewBox', '0 0 24 24')
                .attr('aria-hidden', 'true').attr('focusable', 'false')
                .attr('width', 56).attr('height', 56).attr('x', -28).attr('y', -28)
                .style('overflow', 'visible')
                .append('path');
            group.append('text').attr('class', 'node-label').attr('dy', 5);
            group.append('text').attr('class', 'node-tag').attr('dy', NODE_RADIUS + 18);
            // The handle is pointer-only; Connect… in the Selection is its
            // keyboard and single-pointer equivalent (WCAG 2.5.7).
            const handle = group.append('g').attr('class', 'link-handle')
                .attr('transform', `translate(${NODE_RADIUS},0)`)
                .call(handleDrag);
            handle.append('title').text('Drag to another Node to add a Link');
            handle.append('circle').attr('r', 12);
            handle.append('path').attr('d', 'M -6 0 H 6 M 0 -6 V 6');
            return group;
        })
        .attr('data-node-id', d => d.node.id)
        .attr('transform', d => `translate(${d.x},${d.y})`)
        .classed('selected', d => d.node.id === selectedNodeId)
        .classed('connect-source', d => d.node.id === context.connecting())
        // Only the script changes a drawn Node: it can be moved, never edited.
        .classed('read-only', d => Boolean(d.node.readOnly))
        .classed('from-code', d => Boolean(d.node.fromCode))
        .attr('aria-haspopup', d => (d.node.readOnly ? null : 'dialog'))
        // While connecting, each Node is described by what choosing it does.
        .attr('aria-describedby', d => (context.connecting() ? 'connect-message'
            : d.node.readOnly ? 'canvas-read-only-hint' : 'canvas-item-hint'))
        .attr('aria-label', d => `${context.describeNode(d.node)}${readOnlyLabel(d.node)}${d.node.readOnly ? ',' : ''} at x ${d.x}, y ${d.y}`);
    nodeElements.select('.node-body').attr('stroke', d => context.color(d.node.type));
    nodeElements.select('.node-icon path')
        .attr('d', d => context.iconPath(d.node.type))
        .attr('fill', d => context.color(d.node.type));
    nodeElements.select('.node-label').text(d => d.node.name);
    nodeElements.select('.node-tag').text(d => (d.node.fromCode ? 'from Code section' : ''));

    svg.select('.layer-link-items').selectAll('line.link')
        .data(links, d => d.link.id)
        .join(enter => enter.append('line')
            .attr('class', 'link')
            .attr('role', 'button')
            .attr('aria-haspopup', 'dialog'))
        .attr('data-link-id', d => d.link.id)
        .classed('selected', d => d.link.id === selectedLinkId)
        .call(ends)
        .attr('aria-haspopup', d => (d.link.readOnly ? null : 'dialog'))
        .attr('aria-label', d => `${context.linkLabel(d.link)}${readOnlyLabel(d.link)}`)
        .attr('aria-describedby', d => (d.link.readOnly ? 'canvas-read-only-hint' : 'canvas-link-hint'));
    svg.select('.layer-links').selectAll('line.link-line:not(.link-pending)')
        .classed('read-only', d => Boolean(d.link.readOnly))
        .classed('from-code', d => Boolean(d.link.fromCode));

    showSelection();
    syncTabStop();
}

// A drawn item says it is read-only, and where it comes from.
function readOnlyLabel(item) {
    if (!item.readOnly) return '';
    return item.fromCode ? ', from Code section, read-only' : ', read-only';
}

// The selection's Interface labels stay readable at any zoom.
function isNearSelection(link, { nodeId, linkId }) {
    return link.id === linkId || link.ends.some(end => end.node_id === nodeId);
}

// A selected Node's relationships, as dashed curves that bow aside so they
// do not hide a Link between the same Nodes. Nothing else draws them.
function drawRelations(nodeId) {
    const from = nodeId && context.position(nodeId);
    const targets = nodeId ? context.related(nodeId) : [];
    d3.select('#draft-canvas .layer-relations').selectAll('path.relation-line')
        .data(targets, id => id)
        .join('path')
        .attr('class', 'relation-line')
        .attr('data-related-node-id', id => id)
        .attr('d', id => {
            const to = context.position(id);
            const [dx, dy] = [to.x - from.x, to.y - from.y];
            const [bendX, bendY] = [(from.x + to.x) / 2 - dy / 4, (from.y + to.y) / 2 + dx / 4];
            return `M ${from.x} ${from.y} Q ${bendX} ${bendY} ${to.x} ${to.y}`;
        });
}

/** Mark the selected Node or Link, the Interface labels and relationships that belong to it. */
export function showSelection() {
    const selection = context.selection();
    drawRelations(selection.nodeId);
    d3.selectAll('#draft-canvas .node').classed('selected', d => d?.node?.id === selection.nodeId);
    d3.selectAll('#draft-canvas .link, #draft-canvas .link-line:not(.link-pending)')
        .classed('selected', d => d?.link?.id === selection.linkId);
    d3.selectAll('#draft-canvas .link-label-group').classed('near', d => isNearSelection(d.link, selection));
}

/**
 * Bind the canvas's keyboard and pointer behaviour.
 *
 * @param {object} options
 * @param {() => Array<object>} options.nodes The draft's Nodes.
 * @param {() => Array<object>} options.links The draft's Links.
 * @param {(nodeId: string) => {x: number, y: number}} options.position A Node's layout position.
 * @param {() => {nodeId: ?string, linkId: ?string}} options.selection What is selected.
 * @param {(node: object) => string} options.describeNode Kind and name, such as "Host h1".
 * @param {(link: object) => string} options.linkLabel A Link's accessible name.
 * @param {(type: string) => string} options.iconPath A Node kind's icon.
 * @param {(type: string) => string} options.color A Node kind's colour.
 * @param {(kind: ?string, id: ?string) => void} options.select Select an item, or nothing.
 * @param {(kind: string, id: string) => void} options.open Open an item's dialog.
 * @param {(kind: string, id: string) => void} options.remove Remove an item.
 * @param {(nodeId: string) => void} options.rename Rename a Node in place.
 * @param {(nodeId: string, position: {x: number, y: number}) => void} options.move Move a Node.
 * @param {(firstId: string, secondId: string) => ?object} options.link Link two Nodes; returns
 *     the Link, or null when it is refused.
 * @param {(message: string) => void} options.announce Say what happened.
 * @param {(nodeId: string) => Array<string>} options.related The Nodes a Node relates to.
 * @param {() => ?string} options.connecting The Node being connected, if any.
 * @param {(nodeId: string) => void} options.pick Choose this Node as the other end of a Link.
 */
export function initCanvas(options) {
    context = options;
    const svg = document.getElementById('draft-canvas');
    svg.addEventListener('keydown', onKeydown);
    // Focus selects, whether it arrives by keyboard, pointer or script.
    svg.addEventListener('focusin', event => {
        const item = itemOf(event.target);
        if (!item) return;
        tabStop = { kind: item.kind, id: item.id };
        syncTabStop();
        markFocusedLine(item);
        // Choosing the other end of a Link keeps the Selection on its first.
        if (!context.connecting()) context.select(item.kind, item.id);
    });
    svg.addEventListener('focusout', () => markFocusedLine(null));
    svg.addEventListener('click', event => {
        if (context.connecting()) return;
        if (!itemOf(event.target) && !event.target.closest('.link-line')) context.select(null, null);
    });
    window.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !pendingLink) return;
        pendingLink.remove();
        pendingLink = null;
        context.announce('Cancelled Link creation.');
        event.stopImmediatePropagation();
    });
}

/**
 * Focus the Node nearest to this one; ties go to reading order.
 *
 * @param {string} nodeId
 */
export function focusNearestNode(nodeId) {
    const origin = context.position(nodeId);
    const nearest = readingOrder()
        .filter(entry => entry.node.id !== nodeId)
        .map(entry => ({ entry, distance: Math.hypot(entry.x - origin.x, entry.y - origin.y) }))
        .reduce((best, candidate) => (!best || candidate.distance < best.distance ? candidate : best), null);
    if (nearest) canvasItem('host', nearest.entry.node.id)?.focus();
}

/** Forget which item held the Tab stop, as when another draft opens. */
export function resetCanvasFocus() {
    tabStop = null;
}
