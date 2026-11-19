import { appState } from '../state/app-state.js';
import { saveLayoutToServer } from '../services/api.js';
import { getCurrentLabName } from '../utils/dom.js';
import { fitTransform } from './fit-rule.js';

export function handleGraphResize() {
    if (!appState.svg) return;

    const container = appState.svg.node().parentElement;
    if (!container) return;

    const width = container.clientWidth;
    const height = container.clientHeight;
    // Overview collapses the Lab workspace. Preserve the last usable canvas
    // until the observer measures it again when the Lab becomes visible.
    if (width <= 0 || height <= 0) return;

    appState.svg.attr('width', width).attr('height', height);

    if (appState.simulation) {
        appState.simulation.force('center', d3.forceCenter(width / 2, height / 2));
        appState.simulation.alpha(0.3).restart();
    }

    // Auto-fit to view
    fitTopology();
}

// Instructor-only: capture the current canvas arrangement and POST it to
// /api/save_layout, which normalizes it and writes examples/layouts/<lab>.json
// as the Lab Example's authored layout. Only pinned nodes (fx/fy set) are
// captured: an unpinned node is still being moved by the bootstrap force
// simulation, and its position is transient until the simulation settles and
// pins it (see renderer.js's `simulation.on('end', ...)`), so capturing it
// mid-settle would save an arrangement the Instructor never actually chose.
export async function captureAndSaveLayout() {
    if (!appState.simulation) {
        return null;
    }
    const positions = {};
    appState.simulation.nodes().forEach(d => {
        if (typeof d.fx === 'number' && typeof d.fy === 'number') {
            positions[d.id] = { x: d.fx, y: d.fy };
        }
    });
    return saveLayoutToServer(getCurrentLabName(), positions);
}

// Apply an Authored Layout's canonical coordinates to the given nodes, statically
// and pinned. Positions are placed as-is in the virtual-canvas space they were
// stored in; `fitTopology` performs the single deterministic fit-to-view into the
// current SVG viewport, so the same layout renders identically at any size. No
// per-viewport re-centering is done here. Returns true if at least one node was
// placed.
export function applyAuthoredLayout(nodes, authored) {
    if (!authored || !authored.nodes) return false;

    let placed = false;
    nodes.forEach(d => {
        const pos = authored.nodes[d.id];
        if (pos && typeof pos.x === 'number' && typeof pos.y === 'number') {
            d.x = pos.x;
            d.y = pos.y;
            d.fx = pos.x;
            d.fy = pos.y;
            placed = true;
        }
    });
    return placed;
}

export function fitTopology() {
    if (!appState.g || !appState.svg) return;

    const viewport = appState.svg.node().parentElement;
    const fullWidth = viewport.clientWidth;
    const fullHeight = viewport.clientHeight;
    const points = appState.nodeElements ? appState.nodeElements.data()
        .filter(d => Number.isFinite(d.x) && Number.isFinite(d.y)) : [];

    if (!points.length || fullWidth === 0 || fullHeight === 0) return;

    appState.svg.attr('width', fullWidth).attr('height', fullHeight);
    appState.svg.transition()
        .duration(750)
        .call(
            appState.zoom.transform,
            fitTransform(fullWidth, fullHeight, points)
        );
}

export function releaseAllNodes() {
    if (!appState.simulation) return;

    // Unpin all nodes
    appState.simulation.nodes().forEach(d => {
        d.fx = null;
        d.fy = null;
    });

    if (appState.nodeElements) appState.nodeElements.classed('pinned', false);

    // Restart simulation to recalculate layout
    appState.isInitialLoad = true;
    appState.simulation.alpha(1).restart();
}

// Incremental zoom, the single-pointer/keyboard alternative to pinch-zoom.
// Both delegate to d3's own zoom behavior (the same transform the wheel and
// pinch gestures drive), so the existing scaleExtent and the fit-to-view math
// keep governing the result. `direction` is +1 to zoom in, -1 to zoom out.
export function zoomStep(direction) {
    if (!appState.svg || !appState.zoom) return;

    const factor = direction > 0 ? 1.2 : 1 / 1.2;
    appState.svg.transition()
        .duration(150)
        .call(appState.zoom.scaleBy, factor);
}
