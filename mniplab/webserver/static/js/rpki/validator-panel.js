// Validator panel — the modal a Learner opens from a Validator Node's badge to
// see what the Validator serves to its Routers: the VRPs and ASPAs Routinator
// has validated, when it last ran and when it runs next, and which Routers are
// connected over RTR.
//
// Read-only. Every answer comes from the Validator's Routinator, so a ROA a
// Learner signed at the RPKI CA appears here once the Validator's next run has
// fetched it. The panel polls while it is open, as the Resolver panel does, and
// has no write controls: nothing on it changes the Lab, so no broadcast needs
// handling.
//
// The fetch times read relative to now ("6 s ago", "in 4 s") and are redrawn
// locally every second; a poll every few seconds corrects them. Each keeps the
// full local time in its title.
//
// The filter shows only once the VRPs are too many to read at a glance. It
// matches a VRP's prefix as a substring, or its origin when the text is an AS
// number, written with or without the AS prefix.

import { getRpkiVrps } from '../services/api.js';
import { formatResponseError, patchTableRows, setElementText } from '../utils/dom.js';
import { trapFocus } from '../utils/focus-trap.js';

const POLL_INTERVAL_MS = 5000;
const TICK_INTERVAL_MS = 1000;
const LOADING_MESSAGE = 'Loading the Validator…';
const ASN_TEXT = /^(?:AS)?\d+$/i;
// More VRPs than this, and the filter shows.
const FILTER_MIN_ROWS = 10;
const EMPTY_VRPS = 'No VRPs yet. Add a ROA at the RPKI CA, and it shows here after the next fetch.';

let currentNode = null;
let pollTimer = null;
let tickTimer = null;
let trap = null;
let refreshId = 0;
// The last answer the Validator gave, held here so a filter change or a
// countdown tick re-renders from the data rather than from the DOM.
let view = null;

function setStatus(message, isError = false) {
    const status = document.getElementById('validator-status');
    if (!status) return;
    status.textContent = message;
    status.classList.toggle('validator-status-error', Boolean(isError && message));
}

function vrpKey(vrp) {
    return `${vrp.prefix}|${vrp.max_length}|${vrp.origin}`;
}

function aspaKey(aspa) {
    return `${aspa.customer}|${aspa.providers.join(',')}`;
}

/** Keep the first of each item whose key repeats. A VRP is a member of a set,
 * and patchTableRows needs one row per key. */
function uniqueBy(items, keyOf) {
    const seen = new Set();
    return items.filter((item) => {
        const key = keyOf(item);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

/** Read an RFC 3339 instant the Lab wrote, as milliseconds since the epoch.
 *
 * Routinator's clock is read to nanoseconds, and some browsers refuse more
 * than milliseconds, so the fraction is cut to three digits first. Returns
 * null for a missing or unreadable instant.
 */
function parseInstant(text) {
    if (!text) return null;
    const ms = Date.parse(text.replace(/(\.\d{3})\d+/, '$1'));
    return Number.isNaN(ms) ? null : ms;
}

function formatLocal(ms) {
    return new Date(ms).toLocaleString();
}

/** A duration in whole seconds, minutes, hours or days, the largest that fits. */
function formatSpan(seconds) {
    if (seconds < 60) return `${seconds} s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} h`;
    return `${Math.floor(seconds / 86400)} d`;
}

/** Write a time relative to now into an element, with the local time as its title. */
function setTime(id, ms, describe) {
    const el = document.getElementById(id);
    if (!el) return;
    if (ms === null) {
        el.textContent = 'never';
        el.removeAttribute('title');
        return;
    }
    el.textContent = describe(Math.round((ms - Date.now()) / 1000));
    el.title = formatLocal(ms);
}

function plural(count, noun) {
    return `${count} ${count === 1 ? noun : `${noun}s`}`;
}

function filterTerm() {
    const input = document.getElementById('validator-filter');
    return input ? input.value.trim().toLowerCase() : '';
}

/** Whether a VRP answers the filter: its prefix contains the text, or its
 * origin is the AS number the text names. */
function matchesFilter(vrp, term) {
    if (!term) return true;
    if (vrp.prefix.toLowerCase().includes(term)) return true;
    return ASN_TEXT.test(term) && vrp.origin === Number(term.replace(/^as/, ''));
}

/** Redraw the last and next fetch times, relative to now. */
function renderTimes() {
    if (!view) return;
    setTime('validator-last-refresh', view.lastRefresh, (s) => `${formatSpan(Math.max(0, -s))} ago`);
    setTime('validator-next-refresh', view.nextRefresh, (s) => (s > 0 ? `in ${formatSpan(s)}` : 'due now'));
}

function renderRouters() {
    const list = document.getElementById('validator-routers');
    const empty = document.getElementById('validator-routers-empty');
    if (!list || !empty) return;
    const names = view ? view.routers : [];
    list.replaceChildren(...names.map((name) => {
        const item = document.createElement('li');
        item.textContent = name;
        return item;
    }));
    empty.hidden = names.length > 0 || !view;
}

function renderVrps() {
    if (!view) return;
    const term = filterTerm();
    const shown = view.vrps.filter((vrp) => matchesFilter(vrp, term));
    const rows = shown.map((vrp) => ({
        key: vrpKey(vrp),
        cells: [vrp.prefix, String(vrp.max_length), `AS${vrp.origin}`],
    }));
    patchTableRows(document.getElementById('validator-vrp-tbody'), rows);
    document.getElementById('validator-vrp-table').hidden = rows.length === 0;

    const total = view.vrps.length;
    const filterGroup = document.getElementById('validator-filter-group');
    filterGroup.hidden = total <= FILTER_MIN_ROWS && !term;
    let count = '';
    if (term) count = `Showing ${shown.length} of ${plural(total, 'VRP')}`;
    else if (total > 0) count = plural(total, 'VRP');
    setElementText('validator-vrp-count', count);
    const empty = document.getElementById('validator-vrp-empty');
    empty.textContent = total === 0 ? EMPTY_VRPS : 'No VRP matches this filter.';
    empty.hidden = rows.length > 0;
}

function renderAspas() {
    if (!view) return;
    const rows = view.aspas.map((aspa) => ({
        key: aspaKey(aspa),
        cells: [`AS${aspa.customer}`, aspa.providers.map((p) => `AS${p}`).join(', ')],
    }));
    patchTableRows(document.getElementById('validator-aspa-tbody'), rows);
    document.getElementById('validator-aspa-table').hidden = rows.length === 0;
    const empty = document.getElementById('validator-aspa-empty');
    empty.hidden = rows.length > 0;
}

function showView(data) {
    view = {
        vrps: uniqueBy(data.vrps || [], vrpKey),
        aspas: uniqueBy(data.aspas || [], aspaKey),
        lastRefresh: parseInstant(data.last_refresh),
        nextRefresh: parseInstant(data.next_refresh),
        routers: data.routers || [],
    };
    renderTimes();
    renderRouters();
    renderVrps();
    renderAspas();
}

/** Clear the panel for a new Validator: nothing from the last one stays on screen. */
function clearPanel() {
    view = null;
    setElementText('validator-last-refresh', '');
    setElementText('validator-next-refresh', '');
    document.getElementById('validator-last-refresh').removeAttribute('title');
    document.getElementById('validator-next-refresh').removeAttribute('title');
    renderRouters();
    document.getElementById('validator-vrp-tbody').replaceChildren();
    document.getElementById('validator-aspa-tbody').replaceChildren();
    setElementText('validator-vrp-count', '');
    document.getElementById('validator-vrp-empty').hidden = true;
    document.getElementById('validator-aspa-empty').hidden = true;
    document.getElementById('validator-filter').value = '';
    document.getElementById('validator-filter-group').hidden = true;
    document.getElementById('validator-vrp-table').hidden = true;
    document.getElementById('validator-aspa-table').hidden = true;
}

async function refreshVrps() {
    const node = currentNode;
    if (!node) return;

    const requestId = ++refreshId;

    let response;
    try {
        response = await getRpkiVrps(node);
    } catch (error) {
        if (node === currentNode) setStatus(`Can't reach the server: ${error.message || error}`, true);
        return;
    }
    if (node !== currentNode || requestId !== refreshId) return;

    let data = null;
    try {
        data = await response.json();
    } catch (_) { /* fall through with data === null */ }
    if (node !== currentNode || requestId !== refreshId) return;

    if (!response.ok) {
        setStatus(formatResponseError(data, `Request failed (${response.status})`), true);
        return;
    }
    showView(data);
    setStatus('');
}

function startTimers() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => { void refreshVrps(); }, POLL_INTERVAL_MS);
    if (tickTimer) clearInterval(tickTimer);
    tickTimer = setInterval(renderTimes, TICK_INTERVAL_MS);
}

function stopTimers() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    if (tickTimer) {
        clearInterval(tickTimer);
        tickTimer = null;
    }
}

/** Open the panel for a Validator Node.
 *
 * Args:
 *     nodeName: The Validator's Node name, as the Topology names it.
 */
export function openValidatorPanel(nodeName) {
    const modal = document.getElementById('validator-modal');
    const title = document.getElementById('validator-modal-title');
    if (!modal) return;

    currentNode = nodeName;
    refreshId += 1;
    if (title) title.textContent = `${nodeName} — Validator`;
    clearPanel();
    setStatus(LOADING_MESSAGE);

    modal.style.display = 'flex';
    // Re-opening while already open (another badge press) must not leak the
    // previous trap's document-level listener.
    if (trap) trap.release();
    // The trap focuses the Close button: focusing a field here would raise
    // a phone's keyboard before anything is to be typed.
    trap = trapFocus(modal, { onClose: closeValidatorPanel });

    void refreshVrps();
    startTimers();
}

export function closeValidatorPanel() {
    const modal = document.getElementById('validator-modal');
    if (modal) modal.style.display = 'none';
    stopTimers();
    currentNode = null;
    refreshId += 1;
    view = null;
    if (trap) {
        trap.release();
        trap = null;
    }
}

/** Wire the panel's controls once the page has loaded. */
export function initValidatorPanel() {
    const modal = document.getElementById('validator-modal');
    if (!modal) return;

    const closeBtn = document.getElementById('validator-modal-close');
    if (closeBtn) closeBtn.addEventListener('click', closeValidatorPanel);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeValidatorPanel();
    });

    const filter = document.getElementById('validator-filter');
    if (filter) filter.addEventListener('input', () => renderVrps());
}
