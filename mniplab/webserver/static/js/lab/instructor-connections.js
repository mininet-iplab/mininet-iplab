// One polling owner and one Session → Terminal renderer for both modes.
import { appState } from '../state/app-state.js';
import { getSessions, getTerminals, forceLogoutSession, closeTerminal } from '../services/api.js';
import { showConfirmModal } from '../utils/modal.js';
import { patchTableRows } from '../utils/dom.js';

let sessions = [];
let terminals = [];
let overviewGroup = null;
let overviewKey = null;
let interval = null;
let pending = null;
let active = false;

export function startInstructorConnections() {
    if (appState.currentUserRole !== 'instructor') return;
    active = true;
    const search = document.getElementById('connection-search');
    if (search && !search.dataset.bound) {
        search.dataset.bound = 'true';
        search.addEventListener('input', renderInstructorConnections);
    }
    refreshInstructorConnections();
    // In Multi-Lab Mode the Overview owns the existing ten-second poll.
    if (appState.labMode !== 'multi' && !interval) {
        interval = setInterval(refreshInstructorConnections, 10000);
    }
}

export function stopInstructorConnections() {
    active = false;
    clearInterval(interval);
    interval = null;
    overviewGroup = null;
    sessions = [];
    terminals = [];
    renderInstructorConnections();
    renderLearnerCount();
}

export function refreshInstructorConnections() {
    if (appState.currentUserRole !== 'instructor' || !active) return Promise.resolve();
    if (pending) return pending;
    pending = loadConnections().finally(() => { pending = null; });
    return pending;
}

async function loadConnections() {
    try {
        const [people, shells] = await Promise.all([getSessions(), getTerminals()]);
        if (!people.ok || !shells.ok || !active) return;
        const peopleData = await people.json();
        const terminalData = await shells.json();
        if (!active) return;
        sessions = peopleData.sessions || [];
        terminals = terminalData.terminals || [];
        renderInstructorConnections();
        renderLearnerCount();
    } catch (_) {
        connectionStatus('Could not refresh connections. Retrying on the next update.');
    }
}

export function inspectGroupConnections(group, key = null) {
    overviewGroup = group;
    overviewKey = key;
    renderInstructorConnections();
}

export function renderInstructorConnections() {
    renderConnections(document.getElementById('overview-connection-details'), overviewGroup, overviewKey, !!overviewGroup);
    const group = appState.labMode === 'multi'
        ? (appState.boundGroup || appState.currentGroup) : null;
    renderConnections(document.getElementById('instructor-connection-list'), group, appState.labKey, true);
}

// The learner count beside Nodes and Active Terminals, for an Instructor in
// Single-Lab Mode. Class Overview carries the same count in Multi-Lab Mode.
function renderLearnerCount() {
    const item = document.getElementById('learner-session-status');
    if (!item) return;
    const show = active && appState.labMode !== 'multi';
    item.hidden = !show;
    if (!show) return;
    const count = String(sessions.filter(session => session.role === 'learner').length);
    const value = document.getElementById('learner-session-count');
    if (value.textContent !== count) value.textContent = count;
}

export function connectionAge(epoch) {
    if (!epoch) return '—';
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    return `${Math.floor(seconds / 3600)}h ago`;
}

function renderConnections(container, group, key, enabled) {
    if (!container) return;
    const scoped = !enabled ? [] : sessions.filter(session => !group || session.username === group
        || terminals.some(t => t.client === session.session_id && key && t.lab === key));
    const needle = container.id === 'instructor-connection-list'
        ? document.getElementById('connection-search').value.trim().toLowerCase() : '';
    const matching = scoped.filter(session => `${session.nickname} ${session.connection_code} ${session.username}`.toLowerCase().includes(needle));
    if (container.id === 'instructor-connection-list') {
        const owners = new Set(scoped.map(session => session.session_id));
        const count = terminals.filter(t => owners.has(t.client) && (!key || t.lab === key)).length;
        document.getElementById('connection-counts').textContent = `${matching.length} of ${scoped.length} Sessions · ${count} Terminals`;
    }
    const live = new Set(matching.map(session => session.session_id));
    let lostFocus = false;
    for (const row of container.querySelectorAll(':scope > details')) {
        if (!live.has(row.dataset.sessionId)) {
            lostFocus ||= row.contains(document.activeElement);
            row.remove();
        }
    }
    let empty = container.querySelector(':scope > .connection-empty');
    if (!empty) {
        empty = document.createElement('p');
        empty.className = 'connection-empty instructor-panel-empty';
        container.appendChild(empty);
    }
    empty.hidden = matching.length > 0 || !enabled;
    empty.textContent = needle ? 'No Sessions match this search.' : 'No signed-in Sessions in this view.';
    for (const session of matching) {
        let row = Array.from(container.children).find(e => e.dataset.sessionId === session.session_id);
        if (!row) {
            row = document.createElement('details');
            row.className = 'connection-session';
            row.dataset.sessionId = session.session_id;
            row.innerHTML = '<summary><strong class="connection-name"></strong> <code class="connection-code"></code>'
                + '<span class="connection-meta"></span></summary><div class="connection-body"></div>';
            row.addEventListener('toggle', () => renderInstructorConnections());
            container.appendChild(row);
        }
        const owned = terminals.filter(t => t.client === session.session_id && (!key || t.lab === key));
        row.querySelector('.connection-name').textContent = session.nickname || session.username;
        row.querySelector('.connection-code').textContent = session.connection_code || '';
        const input = session.last_input_at ? connectionAge(session.last_input_at) : 'none yet';
        row.querySelector('.connection-meta').textContent = `${session.username} · ${owned.length} Terminals · last input ${input}`;
        if (row.open) renderSessionBody(row, session, owned);
        else row.querySelector('.connection-body').replaceChildren();
    }
    if (lostFocus) {
        const next = container.querySelector('summary');
        if (next) next.focus();
        else {
            container.tabIndex = -1;
            container.focus();
        }
    }
}

function renderSessionBody(row, session, owned) {
    const body = row.querySelector('.connection-body');
    if (!body.firstChild) {
        body.innerHTML = '<p class="connection-since"></p><button type="button" class="btn-secondary connection-signout">Sign out Session</button>'
            + '<table class="connection-terminals"><caption>Terminals in this view</caption>'
            + '<thead><tr><th scope="col">Node</th><th scope="col">State</th><th scope="col">Idle</th><th scope="col">Action</th></tr></thead><tbody></tbody></table>';
        body.querySelector('.connection-signout').addEventListener('click', () => confirmSignout(row.dataset.sessionId));
    }
    const since = new Date(session.created_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    body.querySelector('.connection-since').textContent = `Signed in ${since} · ${session.role} · connection seen ${connectionAge(session.last_seen)}`;
    body.querySelector('.connection-signout').setAttribute('aria-label', `Sign out Session ${session.nickname} ${session.connection_code}`);
    const table = body.querySelector('table');
    table.hidden = owned.length === 0;
    const tbody = table.tBodies[0];
    const rows = owned.map(terminal => {
        let button = tbody.querySelector(`[data-terminal-id="${CSS.escape(terminal.id)}"]`);
        if (!button) {
            button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn-secondary';
            button.textContent = 'Close Terminal';
            button.dataset.terminalId = terminal.id;
            button.addEventListener('click', () => confirmTerminal(terminal.id));
        }
        button.setAttribute('aria-label', `Close Terminal on ${terminal.node} for ${session.nickname} ${session.connection_code}`);
        // A detached Terminal's grace time is the server's count at this
        // refresh; the view shows it until the next one (poll or status update).
        const state = terminal.attached ? 'Attached' : `Detached, ${terminal.grace_left} s left`;
        const idle = terminal.attached ? `${terminal.idle}s` : detachedIdleCell();
        return { key: terminal.id, cells: [terminal.node, state, idle, button] };
    });
    patchTableRows(tbody, rows);
    for (const tr of tbody.rows) tr.className = 'terminal-row';
}

// A detached Terminal has no idle time; say so to a screen reader, not just a dash.
function detachedIdleCell() {
    const cell = document.createElement('span');
    const dash = document.createElement('span');
    dash.setAttribute('aria-hidden', 'true');
    dash.textContent = '—';
    const spoken = document.createElement('span');
    spoken.className = 'sr-only';
    spoken.textContent = 'Not applicable while detached';
    cell.append(dash, spoken);
    return cell;
}

function confirmSignout(id) {
    const session = sessions.find(item => item.session_id === id);
    if (!session) return;
    const count = terminals.filter(t => t.client === id).length;
    showConfirmModal({
        title: 'Sign out Session',
        message: `Sign out ${session.nickname} · ${session.connection_code} (${session.username})?\n\nThis closes ${count} Terminals across all Labs. Unsaved Terminal work may be lost. The Lab keeps running.`,
        confirmText: 'Sign out Session',
        onConfirm: () => performAction(() => forceLogoutSession(id), 'Session signed out.')
    });
}

function confirmTerminal(id) {
    const terminal = terminals.find(item => item.id === id);
    if (!terminal) return;
    const session = sessions.find(item => item.session_id === terminal.client);
    if (!session) return;
    showConfirmModal({
        title: 'Close Terminal',
        message: `Close the Terminal on ${terminal.node} for ${session.nickname} · ${session.connection_code} (${session.username})?\n\nThe Terminal ends immediately and unsaved work may be lost. Other Terminals and the Lab keep running.`,
        confirmText: 'Close Terminal',
        onConfirm: () => performAction(() => closeTerminal(id), 'Terminal closed.')
    });
}

async function performAction(operation, success) {
    try {
        const response = await operation();
        connectionStatus(response.ok ? success : 'Could not complete the action. Refresh and try again.');
        await refreshInstructorConnections();
    } catch (_) {
        connectionStatus('Could not complete the action. Check your connection and try again.');
    }
}

function connectionStatus(message) {
    const monitor = document.getElementById('workspace-monitor');
    const status = document.getElementById(monitor && !monitor.hidden ? 'monitor-status' : 'instructor-dashboard-status');
    if (status) status.textContent = message;
}
