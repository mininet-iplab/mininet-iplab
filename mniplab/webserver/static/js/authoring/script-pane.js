// The Script pane beside the canvas: the Script toggle shows it, the divider
// on its edge sets its width, and the sidebar folds to its rail while it is
// open. Below 1024 px the canvas and the pane stack instead.
import { setSidebarOpen, sidebarIsOpen } from './shell.js';

const STACKED_LAYOUT = '(max-width: 1023px)';
// Neither the canvas nor the pane gets narrower than this.
const MINIMUM = 320;
const STEP = 40;

let pane = null;
let toggle = null;
let divider = null;
let workspace = null;
let canvasRegion = null;
let onShow = () => {};
// The sidebar the pane folded, which comes back when the pane closes.
let foldedSidebar = false;
let preferredWidth = null;

/** Whether the Script pane is shown. */
export function scriptPaneOpen() {
    return !pane.hidden;
}

/**
 * Show or hide the Script pane, folding the sidebar while it is shown.
 *
 * @param {boolean} open Whether the pane is shown.
 */
export function setScriptPaneOpen(open) {
    if (open === scriptPaneOpen()) return;
    pane.hidden = !open;
    toggle.setAttribute('aria-pressed', String(open));
    workspace.classList.toggle('script-open', open);
    if (open) {
        foldedSidebar = sidebarIsOpen();
        setSidebarOpen(false);
        renderWidth();
        onShow();
    } else if (foldedSidebar) {
        foldedSidebar = false;
        setSidebarOpen(true);
    }
}

function renderWidth() {
    if (pane.hidden || window.matchMedia(STACKED_LAYOUT).matches) return;
    // The canvas and the pane share what the palette and sidebar leave.
    const shared = canvasRegion.offsetWidth + pane.offsetWidth;
    if (!shared) return;
    const maximum = Math.max(MINIMUM, shared - MINIMUM);
    const width = Math.max(MINIMUM, Math.min(maximum, Math.round(preferredWidth ?? shared / 2)));
    workspace.style.setProperty('--script-pane-width', `${width}px`);
    divider.setAttribute('aria-valuenow', String(width));
    divider.setAttribute('aria-valuemax', String(maximum));
}

function setWidth(next) {
    preferredWidth = Math.round(next);
    renderWidth();
}

function bindDivider() {
    const width = () => Number(divider.getAttribute('aria-valuenow'));
    // Where the divider was grabbed, from the pane's edge, so it never jumps.
    let grab = 0;
    divider.addEventListener('keydown', event => {
        const next = {
            ArrowLeft: width() + STEP, ArrowRight: width() - STEP,
            Home: MINIMUM, End: Number(divider.getAttribute('aria-valuemax')),
        }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        setWidth(next);
    });
    divider.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        grab = event.clientX - pane.getBoundingClientRect().left;
        divider.setPointerCapture(event.pointerId);
        event.preventDefault();
    });
    divider.addEventListener('pointermove', event => {
        if (!divider.hasPointerCapture(event.pointerId)) return;
        setWidth(pane.getBoundingClientRect().right - (event.clientX - grab));
    });
    const endResize = event => {
        if (divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId);
    };
    divider.addEventListener('pointerup', endResize);
    divider.addEventListener('pointercancel', endResize);
}

/**
 * Bind the Script toggle and the pane's divider.
 *
 * @param {object} hooks
 * @param {() => void} hooks.onShow Called each time the pane is shown.
 */
export function initScriptPane({ onShow: shown }) {
    pane = document.getElementById('script-pane');
    toggle = document.getElementById('script-toggle');
    divider = document.getElementById('script-resize');
    workspace = document.querySelector('.authoring-workspace');
    canvasRegion = document.getElementById('canvas-region');
    onShow = shown;
    toggle.addEventListener('click', () => setScriptPaneOpen(!scriptPaneOpen()));
    bindDivider();
    const observer = new ResizeObserver(renderWidth);
    observer.observe(workspace);
    observer.observe(canvasRegion);
}
