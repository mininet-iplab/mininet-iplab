// Instructor-only view of the class in Multi-Lab Mode: one card per Group
// with its Activity State, a one-line summary, and the start, stop and
// Assignment controls for any Group.
//
// Connection detail is rendered by instructor-connections.js only when a
// Group is inspected; the initial class view contains compact summaries.

import { bindStopAllLabs, syncStopAllLabs, resetStopAllLabs } from './stop-all-labs.js';
import { createGroupPicker } from '../ui/group-picker.js';
import { appState } from '../state/app-state.js';
import { getDashboard, getExamples, resetConfig, setAssignment, startLab as apiStartLab, stopLabByKey } from '../services/api.js';
import { escapeHtml } from '../utils/dom.js';
import { trapFocus } from '../utils/focus-trap.js';
import { showConfirmModal } from '../utils/modal.js';
import { loadGuide } from '../services/api.js';
import { renderGuideMarkdown } from './guide.js';
import { refreshInstructorConnections, inspectGroupConnections, connectionAge } from './instructor-connections.js';
import { openGroupConfigReset, openClassConfigReset, closeConfigReset, syncConfigReset, announceInConfigReset } from './config-reset.js';
import { selectGroupTab, updateLabTabs } from '../ui/tabstrip.js';

let dashboardInterval = null;
let handlersBound = false;
let lastDashboard = null;
let selectedGroup = null;
let groupFilter = null;

export function startInstructorDashboard() {
    if (appState.currentUserRole !== 'instructor' || appState.labMode !== 'multi') return;
    const panel = document.getElementById('instructor-dashboard');
    if (panel) panel.style.display = '';
    bindHandlers();
    refreshInstructorDashboard();
    if (!dashboardInterval) {
        dashboardInterval = setInterval(refreshInstructorDashboard, 10000);
    }
}

export function stopInstructorDashboard() {
    resetStopAllLabs();
    const panel = document.getElementById('instructor-dashboard');
    if (panel) panel.style.display = 'none';
    if (dashboardInterval) {
        clearInterval(dashboardInterval);
        dashboardInterval = null;
    }
    selectedGroup = null;
    showSelectedGroup();
    groupFilter?.select('');
    for (const id of ['classroom-lab-count', 'classroom-session-count', 'classroom-terminal-count']) {
        document.getElementById(id).textContent = '—';
    }
}

function formatUptime(seconds) {
    const s = Math.max(0, Math.floor(seconds || 0));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
    return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

let announceTimer = null;
function announce(text) {
    const status = document.getElementById('instructor-dashboard-status');
    if (!status) return;
    status.textContent = text;
    announceInConfigReset(text);
    if (announceTimer) clearTimeout(announceTimer);
    announceTimer = setTimeout(() => {
        status.textContent = '';
        announceTimer = null;
    }, 6000);
}

function bindHandlers() {
    if (handlersBound) return;
    const container = document.getElementById('instructor-dashboard-list');
    if (!container) return;
    // Delegated: the list is re-rendered on each poll, so a listener on a
    // button would be lost with its row; the container survives.
    container.addEventListener('click', handleDashboardClick);
    document.getElementById('overview-group-list')?.addEventListener('click', event => {
        const row = event.target.closest('.group-summary');
        if (!row) return;
        const control = event.target.closest('button, a, input, select, textarea');
        if (control && !control.matches('[data-inspect-group]')) return;
        if (!control && window.getSelection()?.toString()) return;
        if (selectedGroup === row.dataset.groupName) {
            selectedGroup = null;
            showSelectedGroup();
        } else {
            inspectGroup(row.dataset.groupName);
        }
        if (!control) row.querySelector('button')?.focus({ preventScroll: true });
    });
    groupFilter = createGroupPicker({
        id: 'overview-group-select', label: 'Filter Groups',
        placeholder: 'Find or select a Group…',
        onSelect: value => {
            if (value && selectedGroup && selectedGroup !== value) {
                selectedGroup = null;
                showSelectedGroup();
            }
            filterGroups();
        },
    });
    groupFilter.setOptions([{ value: '', label: 'All Groups' }]);
    groupFilter.select('');
    document.getElementById('overview-group-filter').append(groupFilter.element);
    document.getElementById('overview-lab-status-filter').addEventListener('change', filterGroups);
    document.getElementById('overview-presence-filter').addEventListener('change', filterGroups);
    document.getElementById('overview-details-close').addEventListener('click', () => {
        const prior = selectedGroup;
        selectedGroup = null;
        showSelectedGroup();
        document.querySelector(`[data-inspect-group="${CSS.escape(prior)}"]`)?.focus();
    });
    const classReset = document.getElementById('overview-config-reset');
    if (classReset) classReset.addEventListener(
        'click', () => openClassConfigReset(lastDashboard.groups));
    const resetClose = document.getElementById('config-reset-modal-close');
    if (resetClose) resetClose.addEventListener('click', closeConfigReset);
    // The dialog's rows carry the same data-action contract as the card's
    // own controls, so one handler serves both surfaces.
    const resetList = document.getElementById('config-reset-list');
    if (resetList) resetList.addEventListener('click', handleDashboardClick);
    bindStopAllLabs(refreshInstructorDashboard);
    handlersBound = true;
}

export async function refreshInstructorDashboard() {
    if (appState.currentUserRole !== 'instructor' || appState.labMode !== 'multi') return;
    try {
        const res = await getDashboard();
        if (!res.ok) return;
        lastDashboard = await res.json();
        renderDashboard(lastDashboard);
        syncConfigReset(lastDashboard.groups);
        updateLabTabs(lastDashboard);
        await refreshInstructorConnections();
    } catch (_) { /* network error, ignore */ }
}

function renderDashboard(dashboard) {
    syncStopAllLabs(dashboard);
    const capEl = document.getElementById('instructor-dashboard-cap');
    if (capEl) capEl.textContent = `${dashboard.labs.length} of ${dashboard.cap} Labs alive`
        + (dashboard.labs.length >= dashboard.cap ? ' · Full' : '');
    document.getElementById('classroom-lab-count').textContent = String(dashboard.labs.length);
    document.getElementById('classroom-session-count').textContent = String(dashboard.groups
        .filter(group => group.role === 'learner').reduce((total, group) => total + group.session_count, 0));
    document.getElementById('classroom-terminal-count').textContent = String(dashboard.labs
        .reduce((total, lab) => total + (lab.active_terminals || 0), 0));

    const container = document.getElementById('instructor-dashboard-list');
    if (!container) return;

    groupFilter?.setOptions([{ value: '', label: 'All Groups' },
        ...[...dashboard.groups]
            .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))
            .map(group => ({ value: group.name, label: group.name,
                description: group.lab?.example ? exampleName(group.lab.example) : 'No Lab running' })),
    ]);
    if (groupFilter?.value === null) groupFilter.select('');
    if (!dashboard.groups.length) {
        container.innerHTML = '<p class="instructor-panel-empty">No Groups can log in.</p>';
        return;
    }

    const panel = document.getElementById('instructor-dashboard');
    const focused = document.activeElement;
    const focusKey = focused && panel.contains(focused) ? focusKeyOf(focused) : null;
    const groups = [...dashboard.groups].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    const list = document.getElementById('overview-group-list');
    const current = new Set(groups.map(g => g.name));
    for (const row of list.children) {
        if (!current.has(row.dataset.groupName)) row.remove();
    }
    for (const [index, group] of groups.entries()) {
        let row = Array.from(list.children).find(e => e.dataset.groupName === group.name);
        if (!row) {
            row = document.createElement('tr');
            row.className = 'group-summary';
            row.dataset.groupName = group.name;
            for (const [label, className] of [['Group', 'summary-group'], ['Lab', 'summary-lab'],
                ['Lab status', 'summary-state'], ['Sessions', 'summary-sessions'],
                ['Terminals', 'summary-terminals'], ['Last learner input', 'summary-input']]) {
                const cell = document.createElement('td');
                cell.className = className;
                cell.dataset.label = label;
                row.appendChild(cell);
            }
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'group-inspect';
            button.dataset.inspectGroup = group.name;
            button.dataset.focusKey = `inspect:${group.name}`;
            button.setAttribute('aria-label', `Inspect Group ${group.name}`);
            button.setAttribute('aria-controls', 'overview-group-details');
            button.textContent = group.name;
            const indicator = document.createElement('span');
            indicator.className = 'group-inspect-indicator';
            indicator.setAttribute('aria-hidden', 'true');
            indicator.textContent = '›';
            button.appendChild(indicator);
            row.querySelector('.summary-group').appendChild(button);
            const badge = document.createElement('span');
            badge.className = 'lab-status-badge';
            row.querySelector('.summary-state').appendChild(badge);
        }
        row.querySelector('.summary-lab').textContent = group.lab?.example ? exampleName(group.lab.example) : 'No Lab running';
        row.dataset.labStatus = group.lab ? 'running' : 'stopped';
        row.dataset.signedIn = String(!!group.session_count);
        const badge = row.querySelector('.lab-status-badge');
        badge.textContent = group.lab ? 'Running' : 'Stopped';
        badge.dataset.status = row.dataset.labStatus;
        row.querySelector('.summary-sessions').textContent = group.session_count ? `${group.session_count} signed in` : 'No sessions';
        row.querySelector('.summary-terminals').textContent = String(group.lab?.active_terminals || 0);
        row.querySelector('.summary-input').textContent = group.last_learner_input
            ? connectionAge(group.last_learner_input) : group.role === 'learner' && group.session_count ? 'No input yet' : '—';
        if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
    }
    showSelectedGroup();
    filterGroups();
    if (focusKey && !focused.isConnected) {
        const again = panel.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`);
        if (again) again.focus();
    }
}

export function inspectGroup(name) {
    if (groupFilter?.value && groupFilter.value !== name) {
        groupFilter.select(name);
        filterGroups();
    }
    const changed = selectedGroup !== name;
    selectedGroup = name;
    showSelectedGroup();
    if (changed) document.getElementById('overview-group-details-body').scrollTop = 0;
    if (window.innerWidth < 1100) {
        document.getElementById('overview-group-details')?.scrollIntoView({ block: 'nearest' });
    }
}

function filterGroups() {
    const groupName = groupFilter?.value;
    const status = document.getElementById('overview-lab-status-filter').value;
    const presence = document.getElementById('overview-presence-filter').value;
    let visible = 0;
    for (const row of document.querySelectorAll('.group-summary')) {
        row.hidden = !!(groupName && row.dataset.groupName !== groupName)
            || !!(status && row.dataset.labStatus !== status)
            || !!(presence && (row.dataset.signedIn === 'true') !== (presence === 'signed-in'));
        if (!row.hidden) visible++;
    }
    document.getElementById('overview-no-matches').hidden = visible > 0;
}

function showSelectedGroup() {
    const group = lastDashboard?.groups.find(g => g.name === selectedGroup);
    const details = document.getElementById('overview-group-details');
    if (!details) return;
    details.hidden = !group;
    for (const row of document.querySelectorAll('.group-summary')) {
        const selected = row.dataset.groupName === selectedGroup && !!group;
        row.dataset.selected = String(selected);
        row.querySelector('button').setAttribute('aria-expanded', String(selected));
    }
    const container = document.getElementById('instructor-dashboard-list');
    if (!group) {
        container.replaceChildren();
        delete container.dataset.signature;
        inspectGroupConnections(null);
        return;
    }
    document.getElementById('overview-group-heading').textContent = `Group ${selectedGroup}`;
    document.getElementById('overview-group-summary').textContent =
        `${group.session_count} Sessions · ${group.lab?.active_terminals || 0} Terminals`;
    // Preserve unchanged action controls (and their focus/select state) on
    // activity-only polls. Rebuild only when this Group's Lab/actions change.
    const signature = JSON.stringify({ ...group, last_activity: 0, last_learner_input: null, session_count: 0, activity_state: '',
        lab: group.lab ? { ...group.lab, uptime: 0, active_terminals: 0 } : null });
    if (container.dataset.signature !== signature) {
        container.innerHTML = renderGroup(group);
        container.dataset.signature = signature;
    }
    const start = container.querySelector('[data-action="start"]');
    if (start) {
        const full = lastDashboard.labs.length >= lastDashboard.cap;
        const incompatible = group.incompatible_config_copies || [];
        const assigned = group.assignment.some(id => !incompatible.includes(id));
        start.disabled = full || !assigned || start.dataset.pending === 'true';
        const notice = container.querySelector('#overview-capacity-notice');
        notice.hidden = !full;
        const message = full
            ? `Capacity full (${lastDashboard.labs.length}/${lastDashboard.cap}). Start becomes available when a Lab stops.` : '';
        // Unchanged polls must not repeat the live announcement.
        if (notice.textContent !== message) notice.textContent = message;
        const reasons = [];
        if (!assigned && incompatible.length) reasons.push(`dashboard-incompatible-${group.name}`);
        if (full) reasons.push('overview-capacity-notice');
        if (reasons.length) start.setAttribute('aria-describedby', reasons.join(' '));
        else start.removeAttribute('aria-describedby');
    }
    const summary = container.querySelector('.group-card-summary');
    if (summary) summary.textContent = groupSummary(group);
    const dot = container.querySelector('.activity-dot');
    if (dot) {
        dot.className = `activity-dot activity-dot-${group.lab ? 'active' : 'stopped'}`;
        dot.setAttribute('aria-label', group.lab ? 'Lab running' : 'Lab stopped');
    }
    inspectGroupConnections(selectedGroup, group.lab?.key || null);
}

function focusKeyOf(element) {
    return element.dataset.focusKey || null;
}

function renderGroup(group) {
    const name = escapeHtml(group.name);
    const lab = group.lab;
    const state = lab ? 'active' : 'stopped';
    const dotLabel = lab ? 'Lab running' : 'Lab stopped';
    // Which Lab Example is running, shown in the actions row beside Stop so
    // the running Lab is always identifiable without opening its tab.
    // A link to the Lab Example's Guide: with no Catalog surface, the name on
    // the card is the nearest way to read what a Group is working on.
    const labName = lab && lab.example
        ? (lab.has_guide === false
            ? `<span class="group-card-lab">${escapeHtml(exampleName(lab.example))}</span>`
            : `<a class="group-card-lab" href="/guide?example=${encodeURIComponent(lab.example)}&key=${encodeURIComponent(lab.key)}"
                target="_blank" rel="noopener noreferrer"
                aria-label="Open the Guide for ${escapeHtml(exampleName(lab.example))}">${escapeHtml(exampleName(lab.example))}</a>`)
        : '';

    const incompatible = group.incompatible_config_copies || [];
    const available = group.assignment.filter(id => !incompatible.includes(id));
    const incompatibilityNoteId = `dashboard-incompatible-${name}`;
    const incompatibilityDescription = !available.length && incompatible.length
        ? `aria-describedby="${incompatibilityNoteId}"` : '';
    const options = group.assignment.map(id =>
        `<option value="${escapeHtml(id)}" ${incompatible.includes(id) ? 'disabled' : ''}
            ${id === available[0] ? 'selected' : ''}>${escapeHtml(exampleName(id))}</option>`).join('');
    // One control row per state: before start, Assign / pick a Lab / Start;
    // after start, the running Lab name / Lab Prompt / Go to the Lab / Stop.
    // Reset Lab config sits on its own line below in both states.
    const assignControl = group.role === 'instructor' ? '' : `
            <button type="button" class="dashboard-action" data-action="assign" data-group="${name}"
                data-focus-key="assign:${name}" aria-label="Edit Assignment of ${name}">Assign…</button>`;
    const startControls = lab ? '' : `
            <label class="sr-only" for="dashboard-start-${name}">Lab Example to start for ${name}</label>
            <select id="dashboard-start-${name}" data-focus-key="select:${name}" ${available.length ? '' : 'disabled'} ${incompatibilityDescription}>
                ${options || '<option value="">Nothing assigned</option>'}
            </select>
            <button type="button" class="dashboard-action dashboard-action-start" data-action="start" data-group="${name}"
                data-focus-key="start:${name}" aria-label="Start Lab for ${name}" ${available.length ? '' : 'disabled'} ${incompatibilityDescription}>Start</button>`;
    const runningControls = lab ? `
            <button type="button" class="dashboard-action" data-action="prompt"
                data-group="${name}" data-key="${escapeHtml(lab.key)}" data-focus-key="prompt:${name}"
                aria-label="Open Lab Prompt of ${name}">Lab Prompt →</button>
            <button type="button" class="dashboard-action group-card-switch" data-action="switch-tab"
                data-group="${name}" data-focus-key="switch:${name}"
                aria-label="Go to the Lab of ${name}">Go to the Lab →</button>
            <button type="button" class="dashboard-action dashboard-action-stop" data-action="stop"
                data-group="${name}" data-key="${escapeHtml(lab.key)}" data-focus-key="stop:${name}"
                aria-label="Stop Lab of ${name}">Stop</button>` : '';

    // One control, never one per Config Copy.  A Group that has worked
    // through four assigned Lab Examples holds four Copies, and four resets
    // side by side read alike, so the Instructor would pick between them by
    // guessing and a wrong guess discards another Lab's saved work.  The
    // dialog this opens names each Lab Example.
    const incompatibleNote = incompatible.length
        ? `<p id="${incompatibilityNoteId}" class="config-reset-reason">Structurally incompatible Config Copies: ${incompatible.map(escapeHtml).join(', ')}. Group work remains saved. Stop any running Lab on that example before Config Reset.</p>`
        : '';
    const copies = (group.config_copies || []).length ? `
            <button type="button" class="dashboard-action dashboard-action-destructive"
                data-action="reset-config" data-group="${name}" data-focus-key="reset-config:${name}"
                aria-label="Reset a Router Config Copy of ${name}">Reset Router Config\u2026</button>
            ${incompatibleNote}` : '';

    const actions = `${assignControl}${startControls}${labName}${runningControls}`;
    // The token the host prints for this Lab (`ovs-vsctl show`, `docker ps`),
    // so an Instructor can match what they see there to a Group.
    const keyLabel = lab ? `<span class="group-card-key">Lab key <code>${escapeHtml(lab.key)}</code></span>` : '';
    return `
        <article class="group-card" data-group="${name}" aria-label="Group ${name}">
            <div class="group-card-header">
                <span class="activity-dot activity-dot-${state}" role="img" aria-label="${dotLabel}"></span>
                <span class="group-card-name">${name}</span>
                <span class="group-card-role">${escapeHtml(group.role)}</span>
                <span class="group-card-summary">${escapeHtml(groupSummary(group))}</span>
                ${keyLabel}
            </div>
            ${actions ? `<div class="group-card-actions">${actions}</div>` : ''}
            ${lab ? '' : '<p id="overview-capacity-notice" class="lab-capacity-notice" role="status" hidden></p>'}
            ${copies ? `<div class="group-card-footer">${copies}</div>` : ''}
        </article>`;
}

// One line per card: how long the Lab has been up and how many terminals are
// on it, or why there is no Lab. Presence and learner input are independent.
function groupSummary(group) {
    if (group.lab) {
        const terminals = group.lab.active_terminals || 0;
        return `Running · ${formatUptime(group.lab.uptime)} · ${terminals} terminal${terminals === 1 ? '' : 's'}`;
    }
    return group.stop_reason && group.stop_reason !== 'stopped'
        ? `stopped: ${group.stop_reason}`
        : 'stopped';
}

async function handleDashboardClick(event) {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const group = button.dataset.group;
    if (button.dataset.action === 'assign') {
        // Left enabled: the modal's focus trap hands focus back to it on close.
        openAssignmentModal(group);
        return;
    }
    if (button.dataset.action === 'reset') {
        confirmReset(button.dataset.example, group);
        return;
    }
    if (button.dataset.action === 'prompt') {
        // Wait for the Group binding before opening the shared terminal tab.
        // A stopped/replaced Lab or later navigation must not open a prompt
        // against whichever Lab the page happened to be bound to before.
        const bound = await selectGroupTab(group);
        if (bound && appState.labKey === button.dataset.key) {
            appState.tabManager?.openLabPrompt();
        }
        return;
    }
    if (button.dataset.action === 'switch-tab') {
        selectGroupTab(group);
        return;
    }
    if (button.dataset.action === 'reset-all') {
        confirmResetAll(button.dataset.example);
        return;
    }
    if (button.dataset.action === 'reset-config') {
        const row = (lastDashboard.groups || []).find(g => g.name === group);
        openGroupConfigReset(
            group, row ? row.config_copies : [], row && row.lab ? row.lab.example : null);
        return;
    }
    if (button.dataset.action === 'stop') {
        const key = button.dataset.key;
        const row = lastDashboard.groups.find(item => item.name === group && item.lab?.key === key);
        if (!row) return;
        showConfirmModal({
            title: 'Stop Lab',
            message: `Stop ${exampleName(row.lab.example)} for Group ${group}?\n\nAll Terminals in this Lab will be closed.`,
            confirmText: 'Stop Lab',
            cancelText: 'Cancel',
            onConfirm: async () => {
                button.disabled = true;
                try {
                    const res = await stopLabByKey(key);
                    announce(res.ok ? `Lab of ${group} stopped.` : `Could not stop the Lab of ${group}.`);
                } catch (_) {
                    announce(`Could not stop the Lab of ${group}.`);
                } finally {
                    button.disabled = false;
                }
                await refreshInstructorDashboard();
            },
        });
        return;
    }
    const select = document.getElementById(`dashboard-start-${group}`);
    const exampleId = select ? select.value : '';
    if (button.dataset.action === 'start' && !exampleId) return;
    button.disabled = true;
    button.dataset.pending = 'true';
    try {
        if (button.dataset.action === 'start') {
            announce(`Starting ${exampleName(exampleId)} for ${group}…`);
            const res = await apiStartLab(exampleId, group);
            if (res.ok) {
                announce(`Lab ${exampleName(exampleId)} started for ${group}.`);
            } else {
                const data = await res.json().catch(() => ({}));
                announce(data.detail || `Could not start ${exampleName(exampleId)} for ${group}.`);
            }
        }
    } catch (_) { /* network error, ignore */ }
    finally { delete button.dataset.pending; }
    await refreshInstructorDashboard();
}

// ── Reset to seed ─────────────────────────────────────────────────────────

// Confirmed first: what `write memory` put in a Group's copy has no other
// way back.  Refused by the server, naming the Lab, while a Group's Lab on that
// Lab Example is alive; the refusal is announced as it came.
function confirmReset(exampleId, group) {
    showConfirmModal({
        title: 'Reset to seed',
        message: `Discard the copy of ${exampleId}'s Persistent Config held by ${group}?\n\nThe next start of ${exampleId} will begin from the seed. What write memory put in the copy cannot be recovered.`,
        confirmText: 'Reset',
        cancelText: 'Cancel',
        onConfirm: () => resetToSeed(exampleId, group)
    });
}

// Every Group's Copy of one Lab Example, discarded together. The server
// refuses the whole batch if any Group's Lab on that Lab Example is alive,
// so a class is never left half reset.
function confirmResetAll(exampleId) {
    showConfirmModal({
        title: 'Reset to seed',
        message: `Discard every Group's copy of ${exampleId}'s Persistent Config?\n\nEach Group's next start of ${exampleId} will begin from the seed. What write memory put in those copies cannot be recovered.`,
        confirmText: 'Reset',
        cancelText: 'Cancel',
        onConfirm: () => resetAllToSeed(exampleId)
    });
}

async function resetAllToSeed(exampleId) {
    try {
        const res = await resetConfig(exampleId, null);
        if (res.ok) {
            const data = await res.json();
            const who = data.reset.length ? data.reset.join(', ') : 'no Group';
            announce(`Copy of ${exampleId} held by ${who} reset to the seed.`);
        } else {
            const data = await res.json().catch(() => ({}));
            announce(data.detail || `Could not reset ${exampleId} to the seed.`);
        }
    } catch (_) { /* network error, ignore */ }
    await refreshInstructorDashboard();
}

async function resetToSeed(exampleId, group) {
    try {
        const res = await resetConfig(exampleId, group);
        if (res.ok) {
            announce(`Copy of ${exampleId} held by ${group} reset to the seed.`);
        } else {
            const data = await res.json().catch(() => ({}));
            announce(data.detail || `Could not reset ${exampleId} to the seed.`);
        }
    } catch (_) { /* network error, ignore */ }
    await refreshInstructorDashboard();
}

// ── Assignment editor ──────────────────────────────────────────────────────

let assignmentTrap = null;
let assignmentGroup = null;

async function openAssignmentModal(group) {
    const modal = document.getElementById('assignment-modal');
    const title = document.getElementById('assignment-modal-title');
    const list = document.getElementById('assignment-list');
    const form = document.getElementById('assignment-form');
    const errorEl = document.getElementById('assignment-modal-error');
    if (!modal || !title || !list || !form) return;

    assignmentGroup = group;
    title.textContent = `Assignment for ${group}`;
    if (errorEl) { errorEl.textContent = ''; errorEl.classList.add('hidden'); }

    const current = new Set(((lastDashboard && lastDashboard.groups) || [])
        .filter(g => g.name === group).flatMap(g => g.assignment));
    // The whole catalog is offered — the Instructor sees every Lab Example —
    // fetched here rather than read off the sidebar, which only holds it
    // while the Instructor's own Lab is not running.
    let examples = [];
    try {
        const res = await getExamples();
        if (res.ok) examples = (await res.json()).examples || [];
    } catch (_) { /* shown as an empty catalog below */ }
    // The facts and the Guide ride on the row being chosen: this is the one
    // listing of Lab Examples, so an Instructor comparing them has nowhere
    // else to look.  The Guide sits outside the label, so following it never
    // reads as ticking the box.
    list.innerHTML = examples.map(example => {
        const id = escapeHtml(example.id);
        const desc = example.description ? `<span class="assignment-example-desc">${escapeHtml(example.description)}</span>` : '';
        const protocols = (example.protocols || []).map(escapeHtml).join(', ');
        const nodes = example.node_count;
        const meta = `<span class="assignment-example-meta">${protocols ? protocols + ' · ' : ''}${nodes} node${nodes === 1 ? '' : 's'}</span>`;
        return `<li><label><input type="checkbox" name="example" value="${id}" ${current.has(example.id) ? 'checked' : ''}>
            <span>${escapeHtml(example.name || example.id)}${meta}${desc}</span></label>
            ${example.has_guide ? `<button type="button" class="assignment-example-guide"
                data-action="guide" data-example="${id}">Open Guide</button>` : ''}</li>`;
    }).join('') || '<li class="instructor-panel-empty">The catalog is empty.</li>';

    // A ticked row stays visible however the filter is set: hiding one would
    // hide a decision the Instructor has made and cannot see they made.
    const guideClose = document.getElementById('assignment-guide-close');
    if (guideClose) guideClose.onclick = () => hideAssignmentGuide(true);

    const list2 = document.getElementById('assignment-list');
    if (list2) list2.onclick = handleAssignmentGuideClick;
    hideAssignmentGuide();

    const search = document.getElementById('assignment-search');
    if (search) {
        search.value = '';
        search.oninput = () => filterAssignmentList(search.value);
    }

    form.onsubmit = handleAssignmentSubmit;
    document.getElementById('assignment-cancel').onclick = closeAssignmentModal;
    document.getElementById('assignment-modal-close').onclick = closeAssignmentModal;

    modal.style.display = 'flex';
    assignmentTrap = trapFocus(modal, { onClose: closeAssignmentModal });
}

// Plain case-insensitive substring match over the text already on the row;
// at this size nothing more is needed, and an Instructor typing "bgp" means
// the letters, not a ranking.
function filterAssignmentList(term) {
    const list = document.getElementById('assignment-list');
    if (!list) return;
    const needle = term.trim().toLowerCase();
    for (const row of list.querySelectorAll('li')) {
        const box = row.querySelector('input[type="checkbox"]');
        const kept = (box && box.checked)
            || !needle
            || row.textContent.toLowerCase().includes(needle);
        row.hidden = !kept;
    }
}

function handleAssignmentGuideClick(event) {
    const button = event.target.closest('button[data-action="guide"]');
    if (!button) return;
    event.preventDefault();
    void showAssignmentGuide(button.dataset.example);
}

// Closing hands focus back to the Open Guide that opened the pane, so the
// keyboard returns to where it left rather than to the top of the modal.
function hideAssignmentGuide(restoreFocus = false) {
    const pane = document.getElementById('assignment-guide');
    if (pane) pane.hidden = true;
    if (restoreFocus && guideOpener) {
        const opener = document.querySelector(
            `#assignment-list button[data-action="guide"][data-example="${CSS.escape(guideOpener)}"]`);
        if (opener) opener.focus();
    }
    guideOpener = null;
    const modal = document.getElementById('assignment-modal');
    const content = modal && modal.querySelector('.modal-content');
    if (content) content.classList.remove('assignment-modal-wide');
}

let guideOpener = null;

async function showAssignmentGuide(exampleId) {
    const pane = document.getElementById('assignment-guide');
    const title = document.getElementById('assignment-guide-title');
    const body = document.getElementById('assignment-guide-body');
    if (!pane || !title || !body) return;
    guideOpener = exampleId;
    pane.hidden = false;
    const modal = document.getElementById('assignment-modal');
    const content = modal && modal.querySelector('.modal-content');
    if (content) content.classList.add('assignment-modal-wide');
    title.textContent = exampleId;
    body.textContent = 'Loading…';
    try {
        const res = await loadGuide(exampleId);
        const markdown = res.ok ? await res.text() : '';
        const html = markdown ? renderGuideMarkdown(markdown) : null;
        body.innerHTML = html || '';
    } catch (_) {
        body.textContent = '';
    }
}

function closeAssignmentModal() {
    const modal = document.getElementById('assignment-modal');
    if (modal) modal.style.display = 'none';
    // The trap returns focus to the Assign… button that opened the modal.
    if (assignmentTrap) { assignmentTrap.release(); assignmentTrap = null; }
    assignmentGroup = null;
}

async function handleAssignmentSubmit(event) {
    event.preventDefault();
    const errorEl = document.getElementById('assignment-modal-error');
    const saveBtn = document.getElementById('assignment-save');
    const ids = Array.from(document.querySelectorAll('#assignment-list input[name="example"]:checked'))
        .map(input => input.value);
    if (saveBtn) saveBtn.disabled = true;
    try {
        const res = await setAssignment(assignmentGroup, ids);
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || `Server error ${res.status}`);
        }
        announce(`Assignment of ${assignmentGroup} saved.`);
        closeAssignmentModal();
        await refreshInstructorDashboard();
    } catch (err) {
        if (errorEl) { errorEl.textContent = err.message; errorEl.classList.remove('hidden'); }
    } finally {
        if (saveBtn) saveBtn.disabled = false;
    }
}

function exampleName(id) {
    return lastDashboard?.example_names?.[id] || id;
}
