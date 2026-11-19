// How the authoring canvas is viewed: pan and zoom, fitting the Topology,
// and which Interface labels are large enough to read.
import { fitTransform, ZOOM_EXTENT } from '../topology/fit-rule.js';

// Interface labels are 12 units tall; below 11 px on screen they are noise.
const LINK_LABEL_UNITS = 12;
const MIN_LABEL_PX = 11;

let zoom = null;
let nodePoints = () => [];

function canvasSize() {
    const container = document.getElementById('draft-canvas-drop');
    return { width: container?.clientWidth || 0, height: container?.clientHeight || 0 };
}

// Small labels hide unless they belong to the selection, which is drawn at
// the smallest readable size instead.
function showReadableLabels(scale) {
    const svg = document.getElementById('draft-canvas');
    svg.classList.toggle('small-labels', LINK_LABEL_UNITS * scale < MIN_LABEL_PX);
    svg.style.setProperty('--near-label-size', `${Math.max(LINK_LABEL_UNITS, MIN_LABEL_PX / scale)}px`);
}

/**
 * Fit the view to the Nodes' bounding box.
 *
 * @param {object} [options]
 * @param {boolean} [options.smooth] Animate the change.
 */
export function fitCanvasView({ smooth = false } = {}) {
    const { width, height } = canvasSize();
    if (!zoom || !width || !height) return;
    const transform = fitTransform(width, height, nodePoints());
    const svg = d3.select('#draft-canvas');
    if (smooth) svg.transition().duration(150).call(zoom.transform, transform);
    else svg.call(zoom.transform, transform);
}

function zoomStep(direction) {
    d3.select('#draft-canvas').transition().duration(150)
        .call(zoom.scaleBy, direction > 0 ? 1.2 : 1 / 1.2);
}

/**
 * Bind pan, zoom and the zoom controls to the authoring canvas.
 *
 * @param {() => Array<{x: number, y: number}>} points The Nodes' layout positions.
 */
export function initCanvasView(points) {
    nodePoints = points;
    zoom = d3.zoom()
        .scaleExtent(ZOOM_EXTENT)
        .filter(event => {
            if (event.type === 'wheel') return true;
            if (event.ctrlKey || event.metaKey || event.button) return false;
            return !event.target.closest('.node, .link-line');
        })
        .on('zoom', event => {
            d3.select('#draft-canvas-g').attr('transform', event.transform);
            d3.select('#draft-grid').attr('patternTransform', event.transform.toString());
            showReadableLabels(event.transform.k);
        });
    d3.select('#draft-canvas').call(zoom);
    document.getElementById('canvas-zoom-in').addEventListener('click', () => zoomStep(1));
    document.getElementById('canvas-zoom-out').addEventListener('click', () => zoomStep(-1));
    document.getElementById('canvas-zoom-fit').addEventListener('click', () => fitCanvasView({ smooth: true }));
    // Whatever changes the canvas's size — the sidebar, the Script pane, the
    // window — fits the view to it again.
    let size = '';
    new ResizeObserver(() => {
        const { width, height } = canvasSize();
        if (`${width}x${height}` === size) return;
        size = `${width}x${height}`;
        fitCanvasView();
    }).observe(document.getElementById('draft-canvas-drop'));
}
