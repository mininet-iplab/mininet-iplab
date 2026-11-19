// The Config Reset dialog.  One row per Config Copy in scope, each naming
// its Lab Example in visible text.
//
// A surface never renders one reset control per Config Copy: a Group that
// has worked through four assigned Lab Examples holds four Copies, and
// four controls reading alike would be chosen between by guessing, where a
// wrong guess discards another Lab's saved work.  The card and the Overview
// header each carry one opener, and this dialog does the naming.

import { escapeHtml } from '../utils/dom.js';
import { trapFocus } from '../utils/focus-trap.js';

let trap = null;
let openGroup = null;
let classOpen = false;

export function openGroupConfigReset(group, examples, runningExample) {
    const modal = document.getElementById('config-reset-modal');
    const list = document.getElementById('config-reset-list');
    if (!modal || !list) return;
    openGroup = group;
    classOpen = false;
    const title = document.getElementById('config-reset-modal-title');
    if (title) title.textContent = `Reset Router Config — ${group}`;
    list.innerHTML = (examples || []).map(id => renderRow(id, group, runningExample)).join('');
    modal.style.display = 'flex';
    trap = trapFocus(modal, { onClose: closeConfigReset });
}

// Mirror an announcement into the open dialog: the dashboard's status line
// is behind the overlay and cannot be read from here.
export function announceInConfigReset(text) {
    const modal = document.getElementById('config-reset-modal');
    const status = document.getElementById('config-reset-status');
    if (!modal || modal.style.display !== 'flex' || !status) return;
    status.textContent = text;
}

export function closeConfigReset() {
    const modal = document.getElementById('config-reset-modal');
    if (modal) modal.style.display = 'none';
    openGroup = null;
    classOpen = false;
    const status = document.getElementById('config-reset-status');
    if (status) status.textContent = '';
    if (trap) { trap.release(); trap = null; }
}

// Every Group's Config Copies of one Lab Example, discarded together. The
// list is the Copies that exist, never the Lab Examples declaring
// Persistent Config: a flag-driven list offered a control per declaration,
// which is how the old Catalog came to carry eleven of them.
export function openClassConfigReset(groups) {
    const modal = document.getElementById('config-reset-modal');
    const list = document.getElementById('config-reset-list');
    if (!modal || !list) return;
    openGroup = null;
    classOpen = true;
    const title = document.getElementById('config-reset-modal-title');
    if (title) title.textContent = 'Reset Router Config — every Group';
    list.innerHTML = classRows(groups);
    modal.style.display = 'flex';
    trap = trapFocus(modal, { onClose: closeConfigReset });
}

function classExamples(groups) {
    const seen = new Set();
    for (const group of groups || []) {
        for (const example of group.config_copies || []) seen.add(example);
    }
    return [...seen].sort();
}

function classRows(groups) {
    return classExamples(groups)
        .map(id => renderClassRow(id, blockedBy(id, groups)))
        .join('');
}

// Every Group whose Lab is alive on this Lab Example.  A class-wide reset
// is all or nothing — the server refuses it whole, with nothing reset,
// while any one of these runs — so one live Lab blocks the row for every
// Group, and the row has to say whose Lab to go and stop.
function blockedBy(exampleId, groups) {
    return (groups || [])
        .filter(group => group.lab && group.lab.example === exampleId)
        .map(group => group.name);
}

function renderClassRow(exampleId, blockers) {
    const example = escapeHtml(exampleId);
    const blocked = blockers.length > 0;
    const who = escapeHtml(blockers.join(', '));
    const sentence = blockers.length > 1
        ? `${who} run this Lab — stop them first.`
        : `${who} runs this Lab — stop it first.`;
    const reason = blocked
        ? `<span class="config-reset-reason">${sentence}</span>`
        : '';
    return `
        <li class="config-reset-row">
            <button type="button" class="dashboard-action dashboard-action-destructive"
                data-action="reset-all" data-example="${example}"
                data-focus-key="reset-all:${example}"
                aria-label="Reset ${example} for every Group"
                ${blocked ? 'disabled' : ''}>Reset ${example}</button>
            ${reason}
        </li>`;
}

// Re-render an open dialog from fresh dashboard data, so a Config Copy just
// discarded leaves the list there and then. A row still offering a Copy that
// no longer exists invites a second reset of nothing, and reads as though
// the first one failed.
export function syncConfigReset(groups) {
    const modal = document.getElementById('config-reset-modal');
    const list = document.getElementById('config-reset-list');
    if (!modal || modal.style.display !== 'flex' || !list) return;
    if (classOpen) {
        const examples = classExamples(groups);
        // The last Copy gone leaves the dialog without a subject.
        if (!examples.length) { closeConfigReset(); return; }
        list.innerHTML = classRows(groups);
        return;
    }
    if (!openGroup) return;
    const row = (groups || []).find(g => g.name === openGroup);
    const examples = row ? (row.config_copies || []) : [];
    if (!examples.length) { closeConfigReset(); return; }
    const running = row && row.lab ? row.lab.example : null;
    list.innerHTML = examples.map(id => renderRow(id, openGroup, running)).join('');
}

// The Lab Example is named in the button's own text, so two rows never read
// alike.  The scope — whose Copy, or every Group's — is named once in the
// dialog's title rather than repeated down every row, and the accessible
// name carries the whole sentence for a reader arriving at the button
// without the title.
//
// A Lab alive on that Lab Example holds the Copy open and the server would
// refuse, so the row is disabled with its reason rather than taking a
// confirmation for an act that cannot happen.
function renderRow(exampleId, group, runningExample) {
    const example = escapeHtml(exampleId);
    const who = escapeHtml(group);
    const blocked = runningExample === exampleId;
    const reason = blocked
        ? `<span class="config-reset-reason">${who} runs this Lab — stop it first.</span>`
        : '';
    return `
        <li class="config-reset-row">
            <button type="button" class="dashboard-action dashboard-action-destructive"
                data-action="reset" data-group="${who}" data-example="${example}"
                data-focus-key="reset:${who}:${example}"
                aria-label="Reset ${example} for ${who}"
                ${blocked ? 'disabled' : ''}>Reset ${example}</button>
            ${reason}
        </li>`;
}
