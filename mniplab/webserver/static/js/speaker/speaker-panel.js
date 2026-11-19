// Speaker panel — the modal a Learner opens from a Speaker's node context
// menu to see what the Speaker is doing and to make it do something
// (ExaBGP parity, tickets 03 and 04).
//
// Summary first: one section per Neighbor whose header carries the session
// state, uptime and route counts, with the announced and received tables
// opening from those counts and each row's remaining attributes
// (communities, large communities, MED) opening from the row. What is always
// on screen is the picture a Learner compares with `show ip bgp summary`;
// the rest is one press away. Everything comes from one poll of
// GET /speakers/{node} (see mniplab/exabgp/query.py), so the panel shows what
// ExaBGP holds — the Lab Example's declared routes and anything announced
// since, in one list — and never keeps a record of its own.
//
// Above the sections sit the announce form, whose field set is the CLI's
// `announce` line (POST /speakers/{node}/announce), and the generate form,
// the CLI's `announce … generate` options (POST /speakers/{node}/generate),
// which lists the prefixes a burst announced so one can be withdrawn by
// hand; every announced row has a withdraw, and every Neighbor a "withdraw
// all" that asks first, because it also takes the Lab Example's routes
// (POST /speakers/{node}/withdraw). A write reports its result in the
// status region and re-reads the Speaker at once rather than patching the
// picture: ExaBGP's memory is the only record.
//
// A Neighbor that is configured but not connected is listed as down, not
// omitted, and the routes announced to it stay visible beside that state so a
// Learner reads them as queued rather than delivered. A read or write that
// fails is rendered with the reason the service gave: a daemon
// that does not answer within its timeout says so, as does a refused route.

import { announceSpeakerRoute, generateSpeakerRoutes, getSpeaker, withdrawSpeakerRoute } from '../services/api.js';
import { formatResponseError, setElementText } from '../utils/dom.js';
import { trapFocus } from '../utils/focus-trap.js';
import { showConfirmModal } from '../utils/modal.js';

// The interval the DHCP Leases, Relay and instructor panels poll at.
const POLL_INTERVAL_MS = 3000;

// AS-PATH is the column a Learner reads across; the attributes it shares a
// route with open from the row. The announced side adds the withdraw column.
const ROUTE_COLUMNS = ['Prefix', 'Next hop', 'AS-PATH', 'Attributes'];

// The two route tables under a Neighbor, by direction.
const ROUTE_SIDES = {
    announced: { heading: 'Announced', empty: 'nothing yet', columns: [...ROUTE_COLUMNS, 'Withdraw'] },
    received: { heading: 'Received', empty: 'nothing yet', columns: ROUTE_COLUMNS },
};

// The form's inputs, by the field the REST body carries; list fields are
// typed space-separated, the way the CLI takes them.
const ANNOUNCE_FIELDS = {
    prefix: { id: 'speaker-announce-prefix' },
    next_hop: { id: 'speaker-announce-next-hop' },
    as_path: { id: 'speaker-announce-as-path', list: true, numeric: true },
    community: { id: 'speaker-announce-community', list: true },
    large_community: { id: 'speaker-announce-large-community', list: true },
    med: { id: 'speaker-announce-med', numeric: true },
};

// The generate form's inputs, by the field the REST body carries; all
// numbers, and all but the count optional.
const GENERATE_FIELDS = {
    count: { id: 'speaker-generate-count' },
    sub_prefixes: { id: 'speaker-generate-sub-prefixes' },
    prefix_seed: { id: 'speaker-generate-prefix-seed' },
    as_path_seed: { id: 'speaker-generate-as-path-seed' },
};

// The value of the "All Neighbors" option.
const ALL_NEIGHBORS = '';

// What the count field goes back to when the panel opens.
const DEFAULT_GENERATE_COUNT = '20';

const QUEUED_NOTE = 'Queued: this session is down, so the routes announced to it are held until it comes up.';

let currentNode = null;
let pollTimer = null;
let trap = null;
let refreshId = 0;
// Heading and row ids must stay unique across sections and rows that come
// and go between polls, so they are numbered by a running sequence.
let idSeq = 0;
// Per-Neighbor route counts from the last render, so a poll that changes
// them is told to assistive technology even when the session summary — the
// only other live text — did not change.
let lastCounts = null;
// The Neighbors as last rendered, by address, for the selector and the
// next-hop it pre-fills.
let neighborsByAddress = new Map();
// Whether a write is in flight; a second one waits for the first.
let writing = false;

export function initSpeakerPanel() {
    const modal = document.getElementById('speaker-modal');
    if (!modal) return;

    const closeBtn = document.getElementById('speaker-modal-close');
    if (closeBtn) closeBtn.addEventListener('click', closeSpeakerPanel);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeSpeakerPanel();
    });

    const form = document.getElementById('speaker-announce-form');
    if (form) form.addEventListener('submit', onAnnounceSubmit);
    const select = document.getElementById('speaker-announce-neighbor');
    if (select) select.addEventListener('change', () => fillNextHop(select.value));
    const generateForm = document.getElementById('speaker-generate-form');
    if (generateForm) generateForm.addEventListener('submit', onGenerateSubmit);
}

export function openSpeakerPanel(nodeName) {
    const modal = document.getElementById('speaker-modal');
    if (!modal) return;

    // Whatever the Learner pressed to get here. Captured before the old trap
    // is released, because releasing moves focus back to the *previous*
    // trigger, and the new trap would otherwise adopt that stale element.
    const trigger = document.activeElement;

    currentNode = nodeName;
    refreshId += 1;
    setElementText('speaker-modal-title', `${nodeName} — BGP Speaker`);
    setElementText('speaker-summary', '');
    setStatus('Reading the Speaker…', false);
    setElementText('speaker-live', '');
    lastCounts = null;
    neighborsByAddress = new Map();
    resetAnnounceForm();
    resetGenerateForm();
    hideWriteError();
    clearNeighbors();

    modal.style.display = 'flex';
    // Re-opening while already open (another menu press) must not leak the
    // previous trap's document-level listener.
    if (trap) {
        trap.release();
        if (trigger && trigger.isConnected) trigger.focus();
    }
    trap = trapFocus(modal, { onClose: closeSpeakerPanel });

    void refreshSpeaker();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => { void refreshSpeaker(); }, POLL_INTERVAL_MS);
}

export function closeSpeakerPanel() {
    const modal = document.getElementById('speaker-modal');
    if (modal) modal.style.display = 'none';
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    currentNode = null;
    refreshId += 1;
    lastCounts = null;
    neighborsByAddress = new Map();
    if (trap) {
        trap.release();
        trap = null;
    }
}

async function refreshSpeaker() {
    const node = currentNode;
    if (!node) return;
    const requestId = ++refreshId;

    let response;
    try {
        response = await getSpeaker(node);
    } catch (error) {
        if (node === currentNode && requestId === refreshId) {
            setStatus(`Can't reach the server: ${error.message || error}`, true);
            clearNeighbors();
        }
        return;
    }

    // The panel may have been closed (or reopened for a different Node)
    // while this request was in flight.
    if (node !== currentNode || requestId !== refreshId) return;

    let data = null;
    try {
        data = await response.json();
    } catch (_) { /* fall through with data === null */ }
    if (node !== currentNode || requestId !== refreshId) return;

    if (!response.ok) {
        // The service names the cause — the daemon rejected a command, or
        // fell silent past its timeout. A stale picture under that message
        // would read as current, so it goes.
        setStatus(formatResponseError(data, `Request failed (${response.status})`), true);
        clearNeighbors();
        return;
    }

    const neighbors = (data && data.neighbors) || [];
    setElementText(
        'speaker-summary',
        data && data.local_as != null ? `Local AS ${data.local_as}` : '',
    );
    setStatus(summarise(neighbors), false);
    announceRouteChanges(neighbors);
    syncNeighborOptions(neighbors);
    renderNeighbors(neighbors);
}

// ── The announce form and the writes ────────────────────────────────────────

/** Keep both Neighbor selectors in step with the payload without disturbing
 * a choice the Learner made: options are rebuilt only when the set of
 * Neighbors changes, and the first Neighbor is chosen (with the announce
 * form's next hop filled in) the first time the list arrives. */
function syncNeighborOptions(neighbors) {
    const addresses = neighbors.map((n) => n.address).filter(Boolean);
    const previous = Array.from(neighborsByAddress.keys());
    neighborsByAddress = new Map(neighbors.filter((n) => n.address).map((n) => [n.address, n]));
    if (addresses.join('|') === previous.join('|')) return;

    // The announce form may target all Neighbors; a burst goes to one.
    const announceSelect = document.getElementById('speaker-announce-neighbor');
    if (announceSelect) {
        const chosen = announceSelect.value;
        fillNeighborOptions(announceSelect, true);
        if (previous.length === 0 || !neighborsByAddress.has(chosen) && chosen !== ALL_NEIGHBORS) {
            announceSelect.value = addresses[0] ?? ALL_NEIGHBORS;
            fillNextHop(announceSelect.value);
        } else {
            announceSelect.value = chosen;
        }
    }
    const generateSelect = document.getElementById('speaker-generate-neighbor');
    if (generateSelect) {
        const chosen = generateSelect.value;
        fillNeighborOptions(generateSelect, false);
        generateSelect.value = neighborsByAddress.has(chosen) ? chosen : (addresses[0] ?? '');
    }
}

function fillNeighborOptions(select, withAll) {
    select.replaceChildren();
    for (const neighbor of neighborsByAddress.values()) {
        const option = document.createElement('option');
        option.value = neighbor.address;
        const peerAs = neighbor.peer_as != null ? `AS ${neighbor.peer_as}` : 'AS —';
        const family = neighbor.family ? `, ${formatFamily(neighbor.family)}` : '';
        option.textContent = `${neighbor.address} (${peerAs}${family})`;
        select.appendChild(option);
    }
    if (withAll) {
        const all = document.createElement('option');
        all.value = ALL_NEIGHBORS;
        all.textContent = 'All Neighbors';
        select.appendChild(all);
    }
}

/** The common case is one field and a click: the next hop is the chosen
 * Neighbor's own address. "All Neighbors" has no single address, so the
 * field is left for the Learner. */
function fillNextHop(address) {
    const input = document.getElementById('speaker-announce-next-hop');
    if (!input) return;
    const neighbor = neighborsByAddress.get(address);
    input.value = neighbor && neighbor.local_address ? neighbor.local_address : '';
}

function resetAnnounceForm() {
    const select = document.getElementById('speaker-announce-neighbor');
    if (select) select.replaceChildren();
    for (const field of Object.values(ANNOUNCE_FIELDS)) {
        const input = document.getElementById(field.id);
        if (input) input.value = '';
    }
}

function resetGenerateForm() {
    const select = document.getElementById('speaker-generate-neighbor');
    if (select) select.replaceChildren();
    for (const [name, field] of Object.entries(GENERATE_FIELDS)) {
        const input = document.getElementById(field.id);
        if (input) input.value = name === 'count' ? DEFAULT_GENERATE_COUNT : '';
    }
    hideGenerateResult();
}

/** The body the generate route takes: the Neighbor, the count, and each
 * option a Learner filled in. A non-number is sent as typed so the service
 * names it. */
function readGenerateForm() {
    const body = {};
    const select = document.getElementById('speaker-generate-neighbor');
    if (select && select.value) body.neighbor = select.value;
    for (const [name, field] of Object.entries(GENERATE_FIELDS)) {
        const input = document.getElementById(field.id);
        const text = input ? input.value.trim() : '';
        if (text === '' && name !== 'count') continue;
        body[name] = numberOrText(text);
    }
    return body;
}

async function onGenerateSubmit(event) {
    event.preventDefault();
    const node = currentNode;
    if (!node) return;
    hideGenerateResult();
    const body = readGenerateForm();
    await performWrite(
        () => generateSpeakerRoutes(node, body),
        (result) => {
            showGenerateResult(result);
            const n = (result.announced || []).length;
            return `Announced ${n} generated ${n === 1 ? 'prefix' : 'prefixes'} to ${result.neighbor || body.neighbor}.`;
        },
        document.getElementById('speaker-generate-submit'),
    );
}

/** List what the burst announced, so a Learner can withdraw one by hand. */
function showGenerateResult(result) {
    const section = document.getElementById('speaker-generate-result');
    const list = document.getElementById('speaker-generate-prefixes');
    if (!section || !list) return;
    const announced = result.announced || [];
    setElementText(
        'speaker-generate-result-heading',
        `Announced ${announced.length} ${announced.length === 1 ? 'prefix' : 'prefixes'} to ${result.neighbor || '—'}`,
    );
    list.replaceChildren();
    for (const route of announced) {
        const item = document.createElement('li');
        item.textContent = route.prefix || '—';
        list.appendChild(item);
    }
    section.hidden = false;
}

function hideGenerateResult() {
    const section = document.getElementById('speaker-generate-result');
    if (section) section.hidden = true;
}

/** The body the REST route takes: the CLI's field set, an attribute left
 * out when its field is empty. Nothing is validated here — the service
 * refuses a bad route naming the field, and that message is shown as is. */
function readAnnounceForm() {
    const body = {};
    const select = document.getElementById('speaker-announce-neighbor');
    if (select && select.value !== ALL_NEIGHBORS) body.neighbor = select.value;
    for (const [name, field] of Object.entries(ANNOUNCE_FIELDS)) {
        const input = document.getElementById(field.id);
        const text = input ? input.value.trim() : '';
        if (text === '') continue;
        if (field.list) {
            const values = text.split(/\s+/);
            body[name] = field.numeric ? values.map(numberOrText) : values;
        } else {
            body[name] = field.numeric ? numberOrText(text) : text;
        }
    }
    return body;
}

/** A number where one was typed; otherwise the text, so the service names
 * the offending word rather than the form swallowing it. */
function numberOrText(text) {
    return /^\d+$/.test(text) ? Number(text) : text;
}

async function onAnnounceSubmit(event) {
    event.preventDefault();
    const node = currentNode;
    if (!node) return;
    const body = readAnnounceForm();
    const done = await performWrite(
        () => announceSpeakerRoute(node, body),
        (result) => `Announced ${result.route?.prefix ?? body.prefix} to ${listText(result.neighbors) || 'no Neighbor'}.`,
        document.getElementById('speaker-announce-submit'),
    );
    if (done) {
        // The prefix is the field that changes between announces; the
        // Neighbor, next hop and attributes usually carry over.
        const prefix = document.getElementById('speaker-announce-prefix');
        if (prefix) {
            prefix.value = '';
            prefix.focus();
        }
    }
}

function onWithdrawRoute(address, prefix, button) {
    const node = currentNode;
    if (!node) return;
    void performWrite(
        () => withdrawSpeakerRoute(node, { neighbor: address, prefix }),
        () => `Withdrew ${prefix} from ${address}.`,
        button,
    );
}

/** Withdraw-all is ExaBGP's `clear adj-rib out`: it takes the Lab Example's
 * routes with everything announced since, so it asks first. */
function onWithdrawAll(address, count, button) {
    const node = currentNode;
    if (!node) return;
    const noun = count === 1 ? 'route' : 'routes';
    showConfirmModal({
        title: 'Withdraw all routes',
        message: `Withdraw all ${count} ${noun} announced to ${address}?\n\n`
            + 'This also withdraws the routes the Lab Example shipped to this Neighbor; '
            + 'the Router loses them until they are announced again.',
        confirmText: 'Withdraw all',
        onConfirm: () => {
            void performWrite(
                () => withdrawSpeakerRoute(node, { neighbor: address, all: true }),
                () => `Withdrew every route announced to ${address}.`,
                button,
            );
        },
    });
}

/** Run one write: report its result in the status region and re-read the
 * Speaker, or show the reason it failed beside the form. Returns whether it
 * succeeded. */
async function performWrite(send, describe, control) {
    if (writing) return false;
    writing = true;
    if (control) control.disabled = true;
    hideWriteError();
    let response = null;
    let data = null;
    try {
        response = await send();
        try {
            data = await response.json();
        } catch (_) { /* fall through with data === null */ }
    } catch (error) {
        showWriteError(`Can't reach the server: ${error.message || error}`);
    } finally {
        writing = false;
        if (control && control.isConnected) control.disabled = false;
    }
    if (!response) return false;
    if (!response.ok) {
        showWriteError(formatResponseError(data, `Request failed (${response.status})`));
        return false;
    }
    setElementText('speaker-live', describe(data || {}));
    // The re-read that follows would otherwise replace this line with the
    // route counts it changed; the write's own words are the announcement.
    lastCounts = null;
    void refreshSpeaker();
    return true;
}

function showWriteError(message) {
    const el = document.getElementById('speaker-write-error');
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
    el.focus();
}

function hideWriteError() {
    const el = document.getElementById('speaker-write-error');
    if (!el) return;
    el.textContent = '';
    el.hidden = true;
}

/** Tell the status region which Neighbors' route counts changed since the
 * last poll. The first render says nothing: the session summary already
 * announces the panel's arrival. */
function announceRouteChanges(neighbors) {
    const counts = new Map(neighbors.map((n) => [
        n.address, `${(n.announced || []).length} announced, ${(n.received || []).length} received`,
    ]));
    if (lastCounts !== null) {
        const changed = [];
        for (const [address, text] of counts) {
            if (lastCounts.get(address) !== text) changed.push(`${address}: ${text}.`);
        }
        if (changed.length > 0) setElementText('speaker-live', changed.join(' '));
    }
    lastCounts = counts;
}

/** One line for the live region: announced only when it changes, so a
 * session going down is heard and an unchanged poll is silence. */
function summarise(neighbors) {
    if (neighbors.length === 0) return 'This Speaker has no Neighbors configured.';
    const up = neighbors.filter((n) => n.up).length;
    const down = neighbors.length - up;
    const parts = [];
    if (up > 0) parts.push(`${up} Established`);
    if (down > 0) parts.push(`${down} down`);
    const noun = neighbors.length === 1 ? 'Neighbor' : 'Neighbors';
    return `${neighbors.length} ${noun}: ${parts.join(', ')}`;
}

// ── Neighbor sections ───────────────────────────────────────────────────────

/** Reconcile the per-Neighbor sections with the payload, keyed by address,
 * patching in place so an unchanged poll touches no DOM node. */
function renderNeighbors(neighbors) {
    const container = document.getElementById('speaker-neighbors');
    if (!container) return;

    const existing = new Map();
    for (const section of Array.from(container.children)) {
        existing.set(section.dataset.address, section);
    }

    const seen = new Set();
    neighbors.forEach((neighbor, index) => {
        const address = neighbor.address || `#${index}`;
        seen.add(address);
        let section = existing.get(address);
        if (!section) {
            section = buildNeighborSection(address);
            container.appendChild(section);
        }
        updateNeighborSection(section, neighbor);
        const current = container.children[index];
        if (current !== section) container.insertBefore(section, current);
    });

    for (const [address, section] of existing) {
        if (!seen.has(address)) section.remove();
    }
}

function clearNeighbors() {
    const container = document.getElementById('speaker-neighbors');
    if (container) container.replaceChildren();
}

function buildNeighborSection(address) {
    const section = document.createElement('section');
    section.className = 'speaker-neighbor';
    section.dataset.address = address;
    const headingId = `speaker-neighbor-${++idSeq}-heading`;
    section.setAttribute('aria-labelledby', headingId);

    const heading = document.createElement('h3');
    heading.id = headingId;
    heading.className = 'speaker-neighbor-heading';
    section.appendChild(heading);

    const meta = document.createElement('p');
    meta.className = 'speaker-neighbor-meta';
    section.appendChild(meta);

    const queued = document.createElement('p');
    queued.className = 'speaker-queued-note';
    queued.hidden = true;
    queued.textContent = QUEUED_NOTE;
    section.appendChild(queued);

    section.appendChild(buildRouteBlock('announced', `Routes announced to ${address}`));
    section.appendChild(buildRouteBlock('received', `Routes received from ${address}`));

    const withdrawAll = document.createElement('button');
    withdrawAll.type = 'button';
    withdrawAll.className = 'speaker-withdraw-all-btn';
    withdrawAll.textContent = 'Withdraw all';
    withdrawAll.setAttribute('aria-label', `Withdraw all routes announced to ${address}`);
    withdrawAll.addEventListener('click', () => {
        onWithdrawAll(address, Number(withdrawAll.dataset.count || 0), withdrawAll);
    });
    section.appendChild(withdrawAll);
    return section;
}

/** A route table behind a native disclosure whose summary is the count:
 * closed by default, so what a Learner sees first is how many, and the
 * browser handles keyboard and the expanded state for assistive technology. */
function buildRouteBlock(kind, label) {
    const block = document.createElement('details');
    block.className = `speaker-routes speaker-${kind}`;

    const summary = document.createElement('summary');
    summary.className = 'speaker-route-summary';
    block.appendChild(summary);

    const empty = document.createElement('p');
    empty.className = 'speaker-routes-empty';
    empty.textContent = `Nothing ${kind} yet.`;
    block.appendChild(empty);

    const table = document.createElement('table');
    table.className = `speaker-table speaker-${kind}-table`;
    table.setAttribute('aria-label', label);
    const thead = document.createElement('thead');
    const row = document.createElement('tr');
    for (const column of ROUTE_SIDES[kind].columns) {
        const th = document.createElement('th');
        th.scope = 'col';
        th.textContent = column;
        row.appendChild(th);
    }
    thead.appendChild(row);
    table.appendChild(thead);
    table.appendChild(document.createElement('tbody'));
    block.appendChild(table);
    return block;
}

function updateNeighborSection(section, neighbor) {
    const heading = section.querySelector('.speaker-neighbor-heading');
    const peerAs = neighbor.peer_as != null ? ` — AS ${neighbor.peer_as}` : '';
    if (heading && heading.textContent !== `Neighbor ${neighbor.address}${peerAs}`) {
        heading.textContent = `Neighbor ${neighbor.address}${peerAs}`;
    }

    const announced = neighbor.announced || [];
    const received = neighbor.received || [];

    // State is stated in words — "Established" or "Down (idle)" — with the
    // class as a secondary cue, never colour alone.
    const meta = section.querySelector('.speaker-neighbor-meta');
    const stateText = neighbor.up
        ? `Established, up ${formatUptime(neighbor.uptime)}`
        : `Down (${neighbor.state || 'not connected'})`;
    const family = neighbor.family ? ` · ${formatFamily(neighbor.family)}` : '';
    const local = neighbor.local_address ? ` · from ${neighbor.local_address}` : '';
    if (meta) {
        const text = `${stateText}${family}${local}`;
        if (meta.textContent !== text) meta.textContent = text;
        meta.classList.toggle('speaker-state-down', !neighbor.up);
    }

    const queued = section.querySelector('.speaker-queued-note');
    if (queued) queued.hidden = neighbor.up || announced.length === 0;

    renderRoutes(section.querySelector('.speaker-announced'), 'announced', announced, neighbor.address);
    renderRoutes(section.querySelector('.speaker-received'), 'received', received, neighbor.address);

    const withdrawAll = section.querySelector('.speaker-withdraw-all-btn');
    if (withdrawAll) {
        withdrawAll.dataset.count = String(announced.length);
        withdrawAll.disabled = announced.length === 0;
    }
}

function renderRoutes(block, kind, routes, address) {
    if (!block) return;
    const side = ROUTE_SIDES[kind];
    const summary = block.querySelector('.speaker-route-summary');
    const count = routes.length === 0
        ? side.empty
        : `${routes.length} ${routes.length === 1 ? 'prefix' : 'prefixes'}`;
    if (summary && summary.textContent !== `${side.heading}: ${count}`) {
        summary.textContent = `${side.heading}: ${count}`;
    }
    const empty = block.querySelector('.speaker-routes-empty');
    const table = block.querySelector('table');
    if (empty) empty.hidden = routes.length > 0;
    if (table) table.hidden = routes.length === 0;
    const tbody = block.querySelector('tbody');
    if (tbody) patchRouteRows(tbody, routes, kind, address);
}

/** Reconcile a route table's rows with the listing, in place.
 *
 * Each route is keyed on its whole line — the prefix alone is not unique,
 * as ExaBGP can hold one prefix twice with different attributes — and a row
 * whose key is already present is left untouched, so a toggle a Learner
 * opened stays open across an unchanged poll. Rows are pairs: the route,
 * then its attributes row, hidden until the toggle opens it. */
function patchRouteRows(tbody, routes, kind, address) {
    const existing = new Map();
    for (const tr of Array.from(tbody.rows)) {
        if (tr.classList.contains('speaker-route')) existing.set(tr.dataset.rowKey, tr);
    }

    const seen = new Set();
    routes.forEach((route, index) => {
        const key = routeKey(route);
        seen.add(key);
        let tr = existing.get(key);
        if (!tr) {
            const [routeRow, attrsRow] = buildRouteRows(route, key, kind, address);
            tbody.appendChild(routeRow);
            tbody.appendChild(attrsRow);
            tr = routeRow;
        }
        // Keep DOM order in step with listing order without recreating rows;
        // rows come in pairs, so the route row belongs at an even index.
        const current = tbody.rows[index * 2];
        if (current !== tr) {
            const attrs = attrsRowOf(tr, tbody);
            tbody.insertBefore(tr, current);
            if (attrs) tbody.insertBefore(attrs, tr.nextSibling);
        }
    });

    for (const [key, tr] of existing) {
        if (!seen.has(key)) {
            attrsRowOf(tr, tbody)?.remove();
            tr.remove();
        }
    }
}

function attrsRowOf(routeRow, tbody) {
    const id = routeRow.querySelector('.speaker-route-toggle')?.getAttribute('aria-controls');
    return id ? tbody.querySelector(`#${id}`) : null;
}

function routeKey(route) {
    return [
        route.prefix, route.next_hop,
        listText(route.as_path), listText(route.community), listText(route.large_community),
        route.med == null ? '' : String(route.med),
    ].join('|');
}

/** The route row and, beneath it, its attributes row. An announced row ends
 * with its withdraw; a received row is the Router's to change. */
function buildRouteRows(route, key, kind, address) {
    const routeRow = document.createElement('tr');
    routeRow.className = 'speaker-route';
    routeRow.dataset.rowKey = key;
    for (const text of [route.prefix || '—', route.next_hop || '—', listText(route.as_path) || '—']) {
        const td = document.createElement('td');
        td.textContent = text;
        routeRow.appendChild(td);
    }

    const attrsRow = document.createElement('tr');
    attrsRow.className = 'speaker-route-attrs';
    attrsRow.id = `speaker-route-${++idSeq}-attrs`;
    attrsRow.hidden = true;
    const attrsCell = document.createElement('td');
    attrsCell.colSpan = ROUTE_SIDES[kind].columns.length;
    attrsRow.appendChild(attrsCell);

    const attributes = [
        ['Communities', listText(route.community)],
        ['Large communities', listText(route.large_community)],
        ['MED', route.med == null ? '' : String(route.med)],
    ].filter(([, value]) => value !== '');

    const toggleCell = document.createElement('td');
    if (attributes.length === 0) {
        // Nothing beyond what the row shows: say so instead of offering an
        // empty disclosure.
        toggleCell.textContent = 'none';
    } else if (attributes.length === 1) {
        // One attribute fits in the cell; a toggle would hide a word.
        toggleCell.textContent = `${attributes[0][0]} ${attributes[0][1]}`;
    } else {
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'speaker-route-toggle';
        toggle.textContent = 'Details';
        toggle.setAttribute('aria-expanded', 'false');
        toggle.setAttribute('aria-controls', attrsRow.id);
        toggle.setAttribute('aria-label', `Details of ${route.prefix}`);
        toggle.addEventListener('click', () => {
            const open = attrsRow.hidden;
            attrsRow.hidden = !open;
            toggle.setAttribute('aria-expanded', String(open));
        });
        toggleCell.appendChild(toggle);

        const list = document.createElement('dl');
        list.className = 'speaker-route-attr-list';
        for (const [name, value] of attributes) {
            const dt = document.createElement('dt');
            dt.textContent = name;
            const dd = document.createElement('dd');
            dd.textContent = value;
            list.appendChild(dt);
            list.appendChild(dd);
        }
        attrsCell.appendChild(list);
    }
    routeRow.appendChild(toggleCell);

    if (kind === 'announced') {
        const withdrawCell = document.createElement('td');
        const withdraw = document.createElement('button');
        withdraw.type = 'button';
        withdraw.className = 'speaker-withdraw-btn';
        withdraw.textContent = 'Withdraw';
        withdraw.setAttribute('aria-label', `Withdraw ${route.prefix} from ${address}`);
        withdraw.addEventListener('click', () => onWithdrawRoute(address, route.prefix, withdraw));
        withdrawCell.appendChild(withdraw);
        routeRow.appendChild(withdrawCell);
    }
    return [routeRow, attrsRow];
}

function listText(values) {
    return Array.isArray(values) ? values.join(' ') : '';
}

/** "ipv4" → "IPv4", the way the family is written everywhere else. */
function formatFamily(family) {
    return String(family).replace(/^ipv(\d)/i, 'IPv$1');
}

/** Seconds → "35 s", "1 min 35 s", "2 h 3 min": read at a glance, as a
 * Learner reads `show ip bgp summary`'s Up/Down column. */
function formatUptime(seconds) {
    const total = Number(seconds);
    if (!Number.isFinite(total) || total < 0) return '—';
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = Math.floor(total % 60);
    if (hours > 0) return `${hours} h ${minutes} min`;
    if (minutes > 0) return `${minutes} min ${secs} s`;
    return `${secs} s`;
}

function setStatus(message, isError) {
    const el = document.getElementById('speaker-status');
    if (!el) return;
    if (el.textContent !== message) el.textContent = message;
    el.classList.toggle('speaker-status-error', Boolean(isError && message));
}
