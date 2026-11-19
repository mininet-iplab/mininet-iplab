// Dragging a Pane by its title bar onto Slot targets. A press on a title bar
// outside its controls, or on its grip, becomes a drag after 5 pixels of
// movement (no press-and-hold). While it lasts, every Slot the Pane can go
// to is drawn over the workspace as a labelled target, the one under the
// pointer is highlighted, and the rectangle the Pane would land in is
// shaded. A drop on a target moves the Pane through the same operation as
// the Pane actions menu; a drop elsewhere, a cancelled pointer or Escape
// changes nothing.

import {
    NARROW, PANE_NAMES, dragTargets, dropPane, inFullView, landingRect, paneWorkspace, targetRect,
} from './pane-arrangement.js';

const THRESHOLD = 5;
// What a press must not start a drag on, except the grip: the title bar's
// own controls and the Terminal surface.
const NOT_A_HANDLE = 'button, input, select, textarea, a, label, [role="tab"], [role="tablist"], '
    + '[role="menu"], .xterm, .pane-menu';

let drag = null;

function place(el, r) {
    Object.assign(el.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
}

function start() {
    const workspace = paneWorkspace();
    drag.started = true;
    drag.targets = dragTargets(drag.id);
    drag.overlay = document.createElement('div');
    drag.overlay.className = 'pane-drag-overlay';
    drag.overlay.setAttribute('aria-hidden', 'true');
    // The Centre first, so the Slots' targets are drawn over it.
    const ordered = [...drag.targets.filter(t => t.slot === 'centre'), ...drag.targets.filter(t => t.slot !== 'centre')];
    for (const target of ordered) {
        const el = document.createElement('div');
        el.className = 'pane-drop-target';
        el.dataset.slot = target.slot;
        const slot = document.createElement('strong');
        slot.textContent = target.slot[0].toUpperCase() + target.slot.slice(1);
        const result = document.createElement('span');
        result.textContent = target.label;
        const text = document.createElement('div');
        text.append(slot, result);
        el.append(text);
        place(el, targetRect(target, drag.id));
        target.el = el;
        drag.overlay.append(el);
    }
    drag.shade = document.createElement('div');
    drag.shade.className = 'pane-drop-shade';
    drag.shade.hidden = true;
    drag.ghost = document.createElement('div');
    drag.ghost.className = 'pane-drag-label';
    drag.ghost.textContent = `${PANE_NAMES[drag.id]} pane`;
    drag.overlay.append(drag.shade, drag.ghost);
    workspace.append(drag.overlay);
    document.body.classList.add('pane-dragging');
    window.addEventListener('keydown', onKey, true);
}

// The target under the pointer, by the same rectangles that are drawn.
function hit(x, y) {
    const box = paneWorkspace().getBoundingClientRect();
    const px = x - box.left;
    const py = y - box.top;
    return drag.targets.find((target) => {
        const r = targetRect(target, drag.id);
        return px >= r.x && px < r.x + r.w && py >= r.y && py < r.y + r.h;
    }) || null;
}

function onMove(event) {
    const dx = event.clientX - drag.x0;
    const dy = event.clientY - drag.y0;
    if (!drag.started) {
        if (Math.hypot(dx, dy) < THRESHOLD) return;
        start();
    }
    const box = paneWorkspace().getBoundingClientRect();
    drag.ghost.style.transform = `translate(${event.clientX - box.left + 12}px, ${event.clientY - box.top + 12}px)`;
    const target = hit(event.clientX, event.clientY);
    if (target === drag.target) return;
    drag.target = target;
    for (const t of drag.targets) t.el.classList.toggle('pane-drop-hot', t === target);
    drag.shade.hidden = !target;
    if (target) {
        place(drag.shade, landingRect(drag.id, target));
        drag.shade.textContent = target.label;
    }
}

function onUp() {
    if (!drag) return;
    const { id, target, started } = drag;
    end();
    if (started && target) dropPane(id, target);
}

function onKey(event) {
    if (event.key !== 'Escape') return;
    // Escape belongs to the drag while one lasts, and to the Terminal after.
    event.preventDefault();
    event.stopPropagation();
    end();
}

function end() {
    if (!drag) return;
    const { bar, pointer } = drag;
    try {
        bar.releasePointerCapture(pointer);
    } catch {
        // Already released.
    }
    bar.removeEventListener('pointermove', onMove);
    bar.removeEventListener('pointerup', onUp);
    bar.removeEventListener('pointercancel', end);
    bar.removeEventListener('lostpointercapture', end);
    window.removeEventListener('keydown', onKey, true);
    drag.overlay?.remove();
    document.body.classList.remove('pane-dragging');
    drag = null;
}

function onDown(event) {
    if (drag || event.button !== 0 || NARROW.matches || inFullView()) return;
    const bar = event.currentTarget;
    if (!event.target.closest('.pane-grip') && event.target.closest(NOT_A_HANDLE)) return;
    if (!paneWorkspace().hasAttribute('data-panes')) return;
    drag = {
        id: bar.closest('[data-pane]').dataset.pane, bar, x0: event.clientX, y0: event.clientY,
        pointer: event.pointerId, started: false, target: null,
    };
    bar.setPointerCapture(event.pointerId);
    bar.addEventListener('pointermove', onMove);
    bar.addEventListener('pointerup', onUp);
    bar.addEventListener('pointercancel', end);
    bar.addEventListener('lostpointercapture', end);
}

/** Make each Pane's title bar a drag handle. Call after the bars have their grips. */
export function initPaneDrag() {
    for (const bar of document.querySelectorAll('#lab-tabpanel > [data-pane] .pane-bar')) {
        bar.addEventListener('pointerdown', onDown);
    }
}
