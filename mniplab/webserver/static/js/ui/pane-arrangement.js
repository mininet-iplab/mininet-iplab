// The Pane arrangement: the running-Lab page as four Panes — Topology,
// Guide, Terminal and Packet Analysis — in four fixed Slots: Left, Centre,
// Right and Bottom. The Centre holds exactly one Pane; Left, Right and
// Bottom up to two each, stacked at the sides and side by side at the
// Bottom. The four Pane elements are siblings in the Lab workspace and never
// leave it: this module turns the arrangement into one CSS grid, placing
// each Pane by grid lines, and reorders the Panes within the workspace so
// Tab follows what is on screen.
//
// A Pane is drawn only while it has something to show: the Topology always,
// the Guide while a Lab runs, the Terminal pane while a Terminal is open
// (and not hidden with Ctrl+T), Packet Analysis while a Capture or the NAT
// Table is open. An empty Slot takes no room.

import { appState } from '../state/app-state.js';
import { handleGraphResize } from '../topology/layout.js';
import { Separator, cssPixels } from './separator.js';

const PANES = ['topology', 'guide', 'terminal', 'packet'];
const SIDES = ['left', 'right', 'bottom'];
// Every Pane but the Topology can be closed.
const CLOSABLE = ['guide', 'terminal', 'packet'];
/** Each Pane's name as announced and as shown in menus. */
export const PANE_NAMES = { topology: 'Topology', guide: 'Guide', terminal: 'Terminal', packet: 'Packet Analysis' };
const SLOT_NAMES = { left: 'Left', centre: 'Centre', right: 'Right', bottom: 'Bottom' };
// The one browser-preference key, under the prefix Reset browser preferences clears.
const STORE = 'mniplab-pane-arrangement';
// Phone widths ignore the arrangement and stack the open Panes in PANES
// order; only Full view and Close remain there.
export const NARROW = window.matchMedia('(max-width: 768px)');

/**
 * The default arrangement: Guide at the Left, Topology in the Centre,
 * Terminal over Packet Analysis at the Right. Sizes are percentages of the
 * workspace; a split is the first Pane's share of its Slot; `closed` marks
 * each closed Pane with true. A closed Pane stays in its Slot's list, which is
 * the Slot it reopens in.
 *
 * @returns {object}
 */
function defaultArrangement() {
    return {
        slots: { left: ['guide'], centre: ['topology'], right: ['terminal', 'packet'], bottom: [] },
        size: { left: 20, right: 40, bottom: 40 },
        split: { left: 50, right: 45, bottom: 50 },
        closed: {},
    };
}

// The stored arrangement if it is sound: every Pane once, the Centre exactly
// one, no more than two in a side Slot. Anything else is ignored.
function sound(saved) {
    const fallback = defaultArrangement();
    if (!saved || typeof saved !== 'object' || !saved.slots) return null;
    const placed = [];
    for (const slot of ['left', 'centre', 'right', 'bottom']) {
        const list = saved.slots[slot];
        // Up to three: two on show and a closed Pane waiting to reopen.
        if (!Array.isArray(list) || list.length > (slot === 'centre' ? 1 : 3)) return null;
        placed.push(...list);
    }
    if (saved.slots.centre.length !== 1) return null;
    if (placed.length !== PANES.length || !PANES.every(id => placed.includes(id))) return null;
    const numbers = (given, base) => Object.fromEntries(Object.keys(base).map(
        key => [key, Number.isFinite(given && given[key]) ? given[key] : base[key]]));
    return {
        slots: Object.fromEntries(Object.keys(fallback.slots).map(slot => [slot, [...saved.slots[slot]]])),
        size: numbers(saved.size, fallback.size),
        split: numbers(saved.split, fallback.split),
        closed: Object.fromEntries(CLOSABLE.filter(id => saved.closed && saved.closed[id] === true).map(id => [id, true])),
    };
}

function load() {
    try {
        return sound(JSON.parse(window.localStorage.getItem(STORE))) || defaultArrangement();
    } catch {
        return defaultArrangement();
    }
}

function save() {
    try {
        window.localStorage.setItem(STORE, JSON.stringify(arrangement));
    } catch {
        // Storage blocked: the arrangement lasts for this page only.
    }
}

let arrangement = load();
let seen = new Set();
let workspace = null;
let anchor = null;
let panes = {};
// The six separators, by key: the Left, Right and Bottom Slots' edges and
// the line between the two Panes of each Slot.
let separators = {};
// The geometry of the last layout, which the separators report and step from.
let drawn = null;
// Whether the Bottom's Panes are being drawn at the Right for want of height.
let fellBack = false;
let note = null;
let noteTimer = null;
let sayTimer = null;
// The Pane that has the whole page, and the Lab it was given in: full view
// ends when the Lab stops, starts or is switched.
let fullView = null;
let fullLab = null;

function running() {
    return !!appState.isLabRunning;
}

// Whether a Pane has something to show.
function available(id) {
    if (id === 'topology') return true;
    if (!running() || arrangement.closed[id]) return false;
    if (id === 'guide') return true;
    if (id === 'terminal') return !!panes.terminal.querySelector('.terminal-instance');
    if (id === 'packet') return !panes.packet.hidden;
    return false;
}

// The Pane drawn in the Centre: the one stored there, or, while it has
// nothing to show, the Topology standing in for it.
function centreId() {
    return available(arrangement.slots.centre[0]) ? arrangement.slots.centre[0] : 'topology';
}

// The Panes drawn in each Slot.
function shownSlots() {
    const centre = centreId();
    const side = (slot) => arrangement.slots[slot].filter(id => id !== centre && available(id));
    return { left: side('left'), centre: [centre], right: side('right'), bottom: side('bottom') };
}

// A Pane that comes back to a Slot already showing two (a Terminal opened
// beside a Guide and a Capture) goes to the first Slot with room, in the
// order Right, Bottom, Left.
function normalize() {
    let changed = false;
    for (const slot of SIDES) {
        for (let shown = shownSlots()[slot]; shown.length > 2; shown = shownSlots()[slot]) {
            const fresh = shown.filter(id => !seen.has(id));
            const extra = fresh.length === 1 ? fresh[0] : shown[2];
            const room = ['right', 'bottom', 'left'].find(
                other => other !== slot && shownSlots()[other].length < 2);
            if (!room) break;
            arrangement.slots[slot] = arrangement.slots[slot].filter(id => id !== extra);
            arrangement.slots[room].push(extra);
            changed = true;
        }
    }
    if (changed) save();
}

// The minimum sizes, in pixels, from the custom properties in main.css.
function minimums() {
    const px = (name) => cssPixels(workspace, `--pane-min-${name}`);
    return {
        centreW: px('centre-w'), centreH: px('centre-h'), bottomH: px('bottom-h'), stackH: px('stack-h'),
        pane: Object.fromEntries(PANES.map(id => [id, px(`${id}-w`)])),
    };
}

// The Slots as drawn: in a workspace too short for the Centre and the
// Bottom together, the Bottom's Panes go to the Right below its own. The
// stored arrangement is not changed.
function drawnSlots(shown, H) {
    const m = minimums();
    if (shown.bottom.length && H < m.centreH + m.bottomH) {
        return { slots: { ...shown, right: [...shown.right, ...shown.bottom], bottom: [] }, fell: true };
    }
    return { slots: shown, fell: false };
}

function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
}

// Each shown Pane's rectangle in a W×H workspace, in pixels, with the
// sizes the separators report. The Centre keeps its minimum: when the sides
// do not fit beside it they share what is left in proportion.
function geometry(slots, W, H) {
    const m = minimums();
    const sideMin = (list) => Math.max(0, ...list.map(id => m.pane[id]));
    const rects = {};
    const splits = {};
    const bottom = slots.bottom.length
        ? Math.round(clamp(arrangement.size.bottom / 100 * H, m.bottomH, H - m.centreH)) : 0;
    const top = H - bottom;
    let left = slots.left.length ? Math.max(sideMin(slots.left), arrangement.size.left / 100 * W) : 0;
    let right = slots.right.length ? Math.max(sideMin(slots.right), arrangement.size.right / 100 * W) : 0;
    const room = Math.max(0, W - m.centreW);
    if (left + right > room) {
        const share = room / (left + right);
        left *= share;
        right *= share;
    }
    left = Math.round(left);
    right = Math.round(right);
    rects[slots.centre[0]] = { x: left, y: 0, w: W - left - right, h: top };
    const stack = (list, x, w, slot) => {
        if (list.length === 1) rects[list[0]] = { x, y: 0, w, h: top };
        if (list.length === 2) {
            const y = Math.round(clamp(arrangement.split[slot] / 100 * top, m.stackH, top - m.stackH));
            rects[list[0]] = { x, y: 0, w, h: y };
            rects[list[1]] = { x, y, w, h: top - y };
            splits[slot] = { x, y, w };
        }
        if (list.length > 2) {
            list.forEach((id, i) => {
                const y0 = Math.round(i * top / list.length);
                const y1 = Math.round((i + 1) * top / list.length);
                rects[id] = { x, y: y0, w, h: y1 - y0 };
            });
        }
    };
    stack(slots.left, 0, left, 'left');
    stack(slots.right, W - right, right, 'right');
    if (slots.bottom.length === 1) rects[slots.bottom[0]] = { x: 0, y: top, w: W, h: bottom };
    if (slots.bottom.length === 2) {
        const [first, second] = slots.bottom;
        const x = Math.round(clamp(arrangement.split.bottom / 100 * W, m.pane[first], W - m.pane[second]));
        rects[first] = { x: 0, y: top, w: x, h: bottom };
        rects[second] = { x, y: top, w: W - x, h: bottom };
        splits.bottom = { x, y: top, h: bottom };
    }
    return { rects, slots, splits, W, H, left, right, bottom, top, m, sideMin };
}

// Put the Pane elements, and the separators beside them, in this order
// within the workspace, so the DOM order (and Tab) is the visual order. The
// elements left out of `order` follow it. Elements move only when the order
// changes.
function reorder(order) {
    const all = [...Object.values(panes), ...Object.values(separators).map(entry => entry.handle)];
    const wanted = [...order, ...all.filter(el => !order.includes(el))];
    const now = [...workspace.children].filter(el => wanted.includes(el));
    if (now.every((el, i) => el === wanted[i])) return;
    for (const el of wanted) {
        if (typeof workspace.moveBefore === 'function') {
            workspace.moveBefore(el, anchor);
        } else {
            const focused = document.activeElement;
            workspace.insertBefore(el, anchor);
            if (focused && el.contains(focused) && document.activeElement !== focused) focused.focus();
        }
    }
}

function show(id, on) {
    panes[id].setAttribute('data-pane-shown', String(on));
}

function hideSeparators() {
    for (const { handle } of Object.values(separators)) handle.hidden = true;
}

function clearPlacement() {
    workspace.style.gridTemplateColumns = '';
    workspace.style.gridTemplateRows = '';
    for (const id of PANES) {
        panes[id].style.gridColumn = '';
        panes[id].style.gridRow = '';
    }
}

/**
 * Lay the Panes out again from the arrangement, the workspace's size and
 * what each Pane has to show. Called whenever any of those may change.
 */
export function layoutPanes() {
    if (!workspace) return;
    const on = running();
    workspace.toggleAttribute('data-panes', on);
    if (fullView && (!on || appState.labKey !== fullLab || !available(fullView))) fullView = null;
    workspace.toggleAttribute('data-pane-full', !!fullView);
    for (const id of PANES) {
        panes[id].toggleAttribute('data-pane-full', id === fullView);
        panes[id].inert = !!fullView && id !== fullView;
    }
    syncPaneChecks();
    if (!on) {
        clearPlacement();
        hideSeparators();
        fellBack = false;
        for (const id of PANES) show(id, id === 'topology');
        return;
    }
    if (NARROW.matches) {
        // The stored arrangement is ignored here, not changed.
        clearPlacement();
        hideSeparators();
        fellBack = false;
        const ids = PANES.filter(available);
        for (const id of PANES) show(id, ids.includes(id));
        reorder(ids.map(id => panes[id]));
        return;
    }
    const box = workspace.getBoundingClientRect();
    const W = Math.round(box.width);
    const H = Math.round(box.height);
    if (W <= 0 || H <= 0) return;
    normalize();
    const { slots, fell } = drawnSlots(shownSlots(), H);
    if (fell !== fellBack) {
        fellBack = fell;
        queueNote(fell
            ? "The window is too short for the Bottom Slot: its Panes are shown at the Right"
            : 'The Bottom Slot is back at the bottom of the window');
    }
    seen = new Set(PANES.filter(available));
    drawn = geometry(slots, W, H);
    const rects = drawn.rects;
    const xs = new Set([0, W]);
    const ys = new Set([0, H]);
    for (const r of Object.values(rects)) {
        xs.add(r.x); xs.add(r.x + r.w); ys.add(r.y); ys.add(r.y + r.h);
    }
    const X = [...xs].sort((a, b) => a - b);
    const Y = [...ys].sort((a, b) => a - b);
    // Proportional tracks, never pixels: pixel tracks would hold the
    // workspace at its old width when the window narrows, and it would
    // never be measured again.
    workspace.style.gridTemplateColumns = X.slice(1).map((v, i) => `minmax(0, ${v - X[i]}fr)`).join(' ');
    workspace.style.gridTemplateRows = Y.slice(1).map((v, i) => `minmax(0, ${v - Y[i]}fr)`).join(' ');
    for (const id of PANES) {
        const r = rects[id];
        show(id, !!r);
        if (!r) continue;
        panes[id].style.gridColumn = `${X.indexOf(r.x) + 1} / ${X.indexOf(r.x + r.w) + 1}`;
        panes[id].style.gridRow = `${Y.indexOf(r.y) + 1} / ${Y.indexOf(r.y + r.h) + 1}`;
    }
    // Left top to bottom, the Centre, Right top to bottom, Bottom left to
    // right, each separator beside the Pane it resizes.
    const inSlot = (slot) => slots[slot].flatMap((id, i) => (
        i === 1 && slots[slot].length === 2 ? [separators[`split-${slot}`].handle, panes[id]] : [panes[id]]));
    reorder([
        ...inSlot('left'), separators['edge-left'].handle, ...slots.centre.map(id => panes[id]),
        separators['edge-right'].handle, ...inSlot('right'),
        separators['edge-bottom'].handle, ...inSlot('bottom'),
    ]);
    placeSeparators();
}

// Where each separator sits, over the boundary it resizes, and whether it
// is shown: its Slot or split must be.
function placeSeparators() {
    const g = drawn;
    const place = (entry, box) => {
        entry.handle.hidden = !box;
        if (!box) return;
        Object.assign(entry.handle.style, {
            left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px`,
        });
        entry.separator.describe();
    };
    const T = SEPARATOR_THICKNESS;
    const some = (slot) => g.slots[slot].length > 0;
    place(separators['edge-left'], some('left') ? { x: g.left - T / 2, y: 0, w: T, h: g.top } : null);
    place(separators['edge-right'], some('right') ? { x: g.W - g.right - T / 2, y: 0, w: T, h: g.top } : null);
    place(separators['edge-bottom'], some('bottom') ? { x: 0, y: g.top - T / 2, w: g.W, h: T } : null);
    for (const slot of ['left', 'right']) {
        const at = g.splits[slot];
        place(separators[`split-${slot}`], at ? { x: at.x, y: at.y - T / 2, w: at.w, h: T } : null);
    }
    const at = g.splits.bottom;
    place(separators['split-bottom'], at ? { x: at.x - T / 2, y: at.y, w: T, h: at.h } : null);
}

/** Bring a closed Pane back, in the Slot it was closed from. */
function openPane(id) {
    if (!arrangement.closed[id]) return;
    delete arrangement.closed[id];
    const empty = id === 'terminal' && !panes.terminal.querySelector('.terminal-instance')
        || id === 'packet' && panes.packet.hidden;
    commit(`${PANE_NAMES[id]} pane reopened${empty ? '; it appears when something is open in it' : ''}`);
}

/** Show the Terminal pane, as opening a Terminal does. */
export function showTerminalPane() {
    openPane('terminal');
    layoutPanes();
}

/** Show the Packet Analysis pane, as opening a Capture or the NAT Table does. */
export function showPacketPane() {
    openPane('packet');
}

/**
 * Close a Pane: it leaves its Slot's room but remembers the Slot, and ends
 * no Terminal and no Capture. Closing the Pane in the Centre brings the
 * Topology back there, and the closed Pane takes the Topology's Slot. The
 * Topology pane is never closed.
 *
 * @param {string} id - 'guide', 'terminal' or 'packet'.
 * @param {boolean} [focus] - Move focus to the Topology's actions button, for
 *   a close chosen from a Pane's own menu, whose button has just gone.
 */
function closePane(id, focus = false) {
    if (!CLOSABLE.includes(id) || arrangement.closed[id]) return;
    if (arrangement.slots.centre[0] === id) swapPanes(id, 'topology');
    arrangement.closed[id] = true;
    commit(`${PANE_NAMES[id]} pane closed`);
    const button = document.querySelector('[data-pane="topology"] .pane-menu-button');
    if (focus && button && button.offsetParent) button.focus();
}

/**
 * Show or hide the Terminal pane, from the toolbar button or Ctrl+T. Its
 * Terminals keep running while it is hidden. Does nothing with no Lab
 * running: no Node has a Terminal to show.
 */
export function toggleTerminalPane() {
    if (!running()) return;
    if (arrangement.closed.terminal) openPane('terminal');
    else closePane('terminal');
}

/**
 * Give a Pane the whole page, or (with null) take it back. Escape never
 * leaves it: Escape belongs to the program in a Terminal.
 *
 * @param {string | null} id - The Pane, or null to leave full view.
 */
export function setFullView(id) {
    const was = fullView;
    fullView = id;
    fullLab = appState.labKey;
    layoutPanes();
    if (id) {
        say(`${PANE_NAMES[id]} pane in full view`);
        panes[id].querySelector('.pane-exit-full')?.focus();
    } else if (was) {
        say(`${PANE_NAMES[was]} pane left full view`);
        panes[was].querySelector('.pane-menu-button')?.focus();
    }
}

/** Whether a Pane has the whole page. */
export function inFullView() {
    return fullView !== null;
}

// The View menu's Panes group: a checkbox each for the Panes that can be
// closed, checked while the Pane is open.
function syncPaneChecks() {
    for (const id of CLOSABLE) {
        const box = document.getElementById(`pane-open-${id}`);
        if (box) box.checked = !arrangement.closed[id];
    }
}

/**
 * The actions a Pane's menu offers: its moves, then Full view (Exit full
 * view while it has the page) and Close. In full view only Exit full view
 * and Close are offered. At phone width only Full view and Close: the
 * Panes stack there and cannot be moved.
 *
 * @param {string} id - 'topology', 'guide', 'terminal' or 'packet'.
 * @returns {{label: string, disabled: boolean, run: Function}[]}
 */
export function paneActions(id) {
    if (!running()) return [];
    const items = fullView ? [] : paneMoves(id);
    items.push(fullView === id
        ? { label: 'Exit full view', disabled: false, run: () => setFullView(null) }
        : { label: 'Full view', disabled: false, run: () => setFullView(id) });
    if (CLOSABLE.includes(id)) items.push({ label: 'Close', disabled: false, run: () => closePane(id, true) });
    return items;
}

// Announce a change once: clear the region, then set the text a moment
// later so a repeat of the same text is read again.
function say(message) {
    const status = document.getElementById('pane-arrangement-status');
    if (!status) return;
    // A note from the layout this change caused is read with the change.
    const text = note ? `${message}. ${note}` : message;
    note = null;
    clearTimeout(noteTimer);
    clearTimeout(sayTimer);
    status.textContent = '';
    sayTimer = setTimeout(() => { status.textContent = text; }, 50);
}

// A note about the layout itself (the Bottom falling back to the Right, or
// returning) is said alone unless a change announces itself first.
function queueNote(text) {
    note = text;
    clearTimeout(noteTimer);
    noteTimer = setTimeout(() => {
        const alone = note;
        note = null;
        if (alone) say(alone);
    }, 0);
}

function swapPanes(a, b, on = arrangement) {
    const where = (id) => {
        const slot = Object.keys(on.slots).find(name => on.slots[name].includes(id));
        return [slot, on.slots[slot].indexOf(id)];
    };
    const [slotA, at] = where(a);
    const [slotB, to] = where(b);
    on.slots[slotA][at] = b;
    on.slots[slotB][to] = a;
}

// Swap a Pane with another that is on show, as a move, a drop and the
// announcement all do. The Topology standing in for a Centre Pane with
// nothing to show takes the Centre first, so the swap is with what is seen.
function swapShown(id, other, on = arrangement) {
    const centre = centreId();
    if ((id === centre || other === centre) && on.slots.centre[0] !== centre) {
        swapPanes(on.slots.centre[0], centre, on);
    }
    swapPanes(id, other, on);
}

function commit(message) {
    save();
    layoutPanes();
    say(message);
}

/**
 * The moves a Pane's actions menu offers, as {label, disabled, run}. The
 * Pane in the Centre can only swap with another Pane on show, because the
 * Centre is never empty; any other Pane can go to a side Slot with room,
 * swap with the Centre, or swap with the Pane sharing its Slot. None at
 * phone width or with no Lab running.
 *
 * @param {string} id - 'topology', 'guide', 'terminal' or 'packet'.
 * @returns {{label: string, disabled: boolean, run: Function}[]}
 */
function paneMoves(id) {
    if (!running() || NARROW.matches) return [];
    const shown = shownSlots();
    const centre = centreId();
    const items = [];
    const swap = (label, other) => items.push({
        label, disabled: false,
        run: () => {
            swapShown(id, other);
            commit(`${PANE_NAMES[id]} pane swapped with ${PANE_NAMES[other]} pane`);
        },
    });
    if (id === centre) {
        for (const slot of SIDES) {
            for (const other of shown[slot]) {
                items.push({
                    label: `Move to ${SLOT_NAMES[slot]}, swapping with ${PANE_NAMES[other]}`,
                    disabled: false,
                    run: () => {
                        swapShown(id, other);
                        commit(`${PANE_NAMES[id]} pane swapped with ${PANE_NAMES[other]} pane`);
                    },
                });
            }
        }
        return items;
    }
    const here = SIDES.find(slot => shown[slot].includes(id));
    for (const slot of SIDES) {
        if (slot === here) continue;
        const full = shown[slot].length >= 2;
        items.push({
            label: `Move to ${SLOT_NAMES[slot]}${full ? ' (full)' : ''}`,
            disabled: full,
            run: () => {
                for (const name of SIDES) arrangement.slots[name] = arrangement.slots[name].filter(x => x !== id);
                arrangement.slots[slot].push(id);
                commit(`${PANE_NAMES[id]} pane moved to the ${SLOT_NAMES[slot]}`);
            },
        });
    }
    swap(`Move to Centre, swapping with ${PANE_NAMES[centre]}`, centre);
    const sibling = (shown[here] || []).find(other => other !== id);
    if (sibling) swap(`Swap places with ${PANE_NAMES[sibling]}`, sibling);
    return items;
}

// How thick the strip over a boundary is, in pixels.
const SEPARATOR_THICKNESS = 8;

// Each separator: what it is called, which way it resizes and which way
// grows the area (1 when moving right or down does), and the size it reads
// and sets, in percent, with its bounds, from the last layout.
const SEPARATORS = {
    'edge-left': {
        label: 'Resize the Left Slot', axis: 'width', direction: 1,
        read: (g) => ({
            total: g.W, value: g.left / g.W * 100, min: g.sideMin(g.slots.left) / g.W * 100,
            max: (g.W - g.m.centreW - g.right) / g.W * 100, text: (v) => `Left Slot, ${v}% of the width`,
        }),
        write: (percent) => { arrangement.size.left = percent; },
    },
    'edge-right': {
        label: 'Resize the Right Slot', axis: 'width', direction: -1,
        read: (g) => ({
            total: g.W, value: g.right / g.W * 100, min: g.sideMin(g.slots.right) / g.W * 100,
            max: (g.W - g.m.centreW - g.left) / g.W * 100, text: (v) => `Right Slot, ${v}% of the width`,
        }),
        write: (percent) => { arrangement.size.right = percent; },
    },
    'edge-bottom': {
        label: 'Resize the Bottom Slot', axis: 'height', direction: -1,
        read: (g) => ({
            total: g.H, value: g.bottom / g.H * 100, min: g.m.bottomH / g.H * 100,
            max: (g.H - g.m.centreH) / g.H * 100, text: (v) => `Bottom Slot, ${v}% of the height`,
        }),
        write: (percent) => { arrangement.size.bottom = percent; },
    },
    'split-left': splitSpec('left', 'height'),
    'split-right': splitSpec('right', 'height'),
    'split-bottom': splitSpec('bottom', 'width'),
};

// The line between the two Panes of a Slot: stacked at the sides, side by
// side at the Bottom. Its value is the first Pane's share of the Slot.
function splitSpec(slot, axis) {
    return {
        label: `Share the ${SLOT_NAMES[slot]} Slot`, axis, direction: 1,
        read: (g) => {
            const at = g.splits[slot];
            const total = axis === 'width' ? g.W : g.top;
            const [first, second] = g.slots[slot];
            const [low, high] = axis === 'width'
                ? [g.m.pane[first], total - g.m.pane[second]]
                : [g.m.stackH, total - g.m.stackH];
            return {
                total, value: (axis === 'width' ? at.x : at.y) / total * 100,
                min: low / total * 100, max: high / total * 100,
                text: (v) => `${PANE_NAMES[first]} ${v}% of the ${SLOT_NAMES[slot]} Slot`,
            };
        },
        write: (percent) => { arrangement.split[slot] = percent; },
    };
}

// Hold the Topology's refit until a drag (or a key's step) ends, then refit
// once, after the layout has settled.
function resizing(active) {
    if (active) {
        appState.paneResizing = true;
        return;
    }
    requestAnimationFrame(() => requestAnimationFrame(() => {
        appState.paneResizing = false;
        handleGraphResize();
    }));
}

function buildSeparators() {
    for (const [key, spec] of Object.entries(SEPARATORS)) {
        const handle = document.createElement('div');
        handle.className = 'pane-separator';
        handle.setAttribute('role', 'separator');
        handle.setAttribute('aria-label', spec.label);
        handle.dataset.separator = key;
        handle.tabIndex = 0;
        handle.hidden = true;
        const read = () => {
            const slotShown = drawn && (key.startsWith('split-') ? drawn.splits[key.slice(6)] : true);
            if (!slotShown) return null;
            const state = spec.read(drawn);
            return { ...state, text: state.text(Math.round(state.value)) };
        };
        const separator = new Separator(handle, {
            axis: spec.axis, direction: spec.direction, step: 2, read,
            write: (percent) => { spec.write(percent); layoutPanes(); },
            commit: save,
            onDrag: resizing,
        });
        separators[key] = { handle, separator };
        workspace.insertBefore(handle, anchor);
    }
}

// Each Pane's box on screen, to slide the Panes from where they were.
function snapshotBoxes() {
    return Object.fromEntries(PANES.map(id => [id, panes[id].getBoundingClientRect()]));
}

// Slide each Pane from its old box to its new one: transform only, so the
// Terminals are laid out once, at their final size. None under reduced motion.
function slideFrom(before) {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    for (const id of PANES) {
        const was = before[id];
        const now = panes[id].getBoundingClientRect();
        if (panes[id].getAttribute('data-pane-shown') !== 'true' || !was.width || !now.width) continue;
        panes[id].animate([
            {
                transformOrigin: '0 0',
                transform: `translate(${was.left - now.left}px, ${was.top - now.top}px) `
                    + `scale(${was.width / now.width}, ${was.height / now.height})`,
            },
            { transformOrigin: '0 0', transform: 'none' },
        ], { duration: 180, easing: 'ease-out' });
    }
}

// The word for putting a Pane beside another in a Slot: above and below at
// the sides, left of and right of at the Bottom.
const ALONG = { left: ['above', 'below'], right: ['above', 'below'], bottom: ['left of', 'right of'] };

/**
 * Every place a Pane can be dropped, as {slot, kind, ref, with, half, label}:
 * a whole empty Slot, two halves beside the one Pane in a Slot, two swaps
 * with the two Panes in a Slot, and the Centre as a swap. The Pane in the
 * Centre is offered swaps only, so the Centre is never left empty. In the
 * order a pointer is tested, so a Slot's own targets win where they overlap
 * the Centre.
 *
 * @param {string} id - The dragged Pane.
 * @returns {object[]}
 */
export function dragTargets(id) {
    if (!running() || NARROW.matches || !drawn) return [];
    const shown = shownSlots();
    const centre = centreId();
    const inCentre = id === centre;
    const out = [];
    for (const slot of SIDES) {
        const others = shown[slot].filter(other => other !== id);
        if (inCentre || others.length > 1) {
            others.forEach((other, i) => out.push({
                slot, kind: 'swap', with: other, half: others.length === 1 ? null : i,
                label: `Swap ${PANE_NAMES[id]} with ${PANE_NAMES[other]}`,
            }));
        } else if (others.length === 0) {
            if (shown[slot].includes(id)) continue;
            out.push({
                slot, kind: 'whole', half: null,
                label: `Put ${PANE_NAMES[id]} at the ${SLOT_NAMES[slot]}`,
            });
        } else {
            const [other] = others;
            out.push({
                slot, kind: 'before', ref: other, half: 0,
                label: `Put ${PANE_NAMES[id]} ${ALONG[slot][0]} ${PANE_NAMES[other]}`,
            });
            out.push({
                slot, kind: 'after', ref: other, half: 1,
                label: `Put ${PANE_NAMES[id]} ${ALONG[slot][1]} ${PANE_NAMES[other]}`,
            });
        }
    }
    if (!inCentre) {
        out.push({
            slot: 'centre', kind: 'swap', with: centre, half: null,
            label: `Put ${PANE_NAMES[id]} in the Centre; ${PANE_NAMES[centre]} takes its place`,
        });
    }
    return out;
}

/**
 * Where a drop target is drawn and tested, in workspace pixels: an occupied
 * Slot where its Panes are now, an empty one as a strip along its edge.
 * Side targets stop above an empty Bottom's strip.
 *
 * @param {object} target - One of dragTargets().
 * @param {string} id - The dragged Pane.
 * @returns {{x: number, y: number, w: number, h: number}}
 */
export function targetRect(target, id) {
    const g = drawn;
    const shown = shownSlots();
    const bottomStrip = Math.max(g.m.bottomH, g.H / 3);
    const bottomEmpty = !g.slots.bottom.length && !shown.bottom.length;
    let r;
    if (shown[target.slot].length) {
        const boxes = shown[target.slot].map(other => g.rects[other]);
        const x0 = Math.min(...boxes.map(b => b.x));
        const y0 = Math.min(...boxes.map(b => b.y));
        const x1 = Math.max(...boxes.map(b => b.x + b.w));
        const y1 = Math.max(...boxes.map(b => b.y + b.h));
        r = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
        if (target.slot !== 'bottom' && bottomEmpty) r.h = Math.min(r.h, g.H - bottomStrip);
    } else if (target.slot === 'bottom') {
        r = { x: 0, y: g.H - bottomStrip, w: g.W, h: bottomStrip };
    } else {
        const w = Math.max(g.m.pane[id], g.W / 5);
        r = { x: target.slot === 'left' ? 0 : g.W - w, y: 0, w, h: bottomEmpty ? g.H - bottomStrip : g.top };
    }
    if (target.half === null) return r;
    if (target.slot === 'bottom') return { x: r.x + target.half * r.w / 2, y: r.y, w: r.w / 2, h: r.h };
    return { x: r.x, y: r.y + target.half * r.h / 2, w: r.w, h: r.h / 2 };
}

// What a drop would do to a copy of the arrangement, and what to announce.
function afterDrop(id, target) {
    const next = JSON.parse(JSON.stringify(arrangement));
    if (target.kind === 'swap') {
        swapShown(id, target.with, next);
        return { next, message: `${PANE_NAMES[id]} pane swapped with ${PANE_NAMES[target.with]} pane` };
    }
    for (const name of SIDES) next.slots[name] = next.slots[name].filter(x => x !== id);
    const list = next.slots[target.slot];
    if (target.kind === 'whole') {
        list.push(id);
    } else {
        const at = list.indexOf(target.ref);
        list.splice(target.kind === 'before' ? at : at + 1, 0, id);
    }
    return { next, message: `${PANE_NAMES[id]} pane moved to the ${SLOT_NAMES[target.slot]}` };
}

/**
 * The rectangle a Pane would get if dropped on a target, computed from the
 * arrangement the drop would make.
 *
 * @param {string} id - The dragged Pane.
 * @param {object} target - One of dragTargets().
 * @returns {{x: number, y: number, w: number, h: number} | undefined}
 */
export function landingRect(id, target) {
    const kept = arrangement;
    arrangement = afterDrop(id, target).next;
    try {
        return geometry(drawnSlots(shownSlots(), drawn.H).slots, drawn.W, drawn.H).rects[id];
    } finally {
        arrangement = kept;
    }
}

/**
 * Drop a Pane on a target: the same commit as a menu move, so the order,
 * the announcement and the stored arrangement are shared, then the Panes
 * slide to their new places.
 *
 * @param {string} id - The dragged Pane.
 * @param {object} target - One of dragTargets().
 */
export function dropPane(id, target) {
    const before = snapshotBoxes();
    const { next, message } = afterDrop(id, target);
    arrangement = next;
    commit(message);
    slideFrom(before);
}

/** The workspace element, which the drag overlay is drawn in. */
export function paneWorkspace() {
    return workspace;
}

/** Put the Panes back in the default arrangement, as the View menu's Reset does. */
export function resetPaneArrangement() {
    arrangement = defaultArrangement();
    commit('Pane arrangement reset');
}

export function initPaneArrangement() {
    workspace = document.getElementById('lab-tabpanel');
    anchor = document.getElementById('pane-arrangement-status');
    if (!workspace || !anchor) return;
    for (const id of PANES) {
        panes[id] = workspace.querySelector(`:scope > [data-pane="${id}"]`);
        if (!panes[id]) return;
    }
    buildSeparators();
    for (const id of CLOSABLE) {
        document.getElementById(`pane-open-${id}`)?.addEventListener('change', (event) => {
            if (event.target.checked) openPane(id);
            else closePane(id);
        });
    }
    new ResizeObserver(() => layoutPanes()).observe(workspace);
    NARROW.addEventListener('change', () => layoutPanes());
    layoutPanes();
}
