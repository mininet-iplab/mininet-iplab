// Server settings: the Limits an Instructor changes while the class runs,
// and the Learner accounts added on the page.
//
// Offered under `serve` only, to an Instructor. The server checks every
// value, so the dialog shows its refusal against the field it names and
// sends only the Limits whose value changed. A login's password is shown
// only when the Instructor asks for it, and is never kept by the page.
// Resetting or removing a login asks first: both sign its Learners out.

import { appState } from '../state/app-state.js';
import {
    addLogin,
    getLoginPassword,
    getLogins,
    getServerSettings,
    removeLogin,
    resetLoginPassword,
    saveServerSettings,
} from '../services/api.js';
import { trapFocus } from '../utils/focus-trap.js';
import { showConfirmModal } from '../utils/modal.js';
import { bindMenuButton } from '../ui/menu-button.js';

let focusTrap = null;
let bound = false;
// More logins than this and the list gets a filter, and the .env logins open folded.
const FILTER_FROM = 8;
// Whether the Instructor opened or folded the .env logins; null until they do.
let envOpen = null;
// The values the dialog was opened with, by Limit name.
let shown = {};

function modal() {
    return document.getElementById('server-settings-modal');
}

function fieldGroups() {
    return [...document.querySelectorAll('#server-settings-form [data-limit]')];
}

function inputFor(name) {
    return document.getElementById(`limit-${name}`);
}

function setStatus(text) {
    document.getElementById('server-settings-status').textContent = text;
}

/**
 * Show the toolbar item to an Instructor on a server that has Server settings.
 *
 * @param {string} role - The signed-in role.
 */
export function applyServerSettingsAvailability(role) {
    const button = document.getElementById('server-settings-btn');
    if (!button) return;
    button.hidden = !(role === 'instructor' && appState.serverSettings);
    if (bound) return;
    bound = true;
    button.addEventListener('click', openServerSettings);
    document.getElementById('server-settings-close').addEventListener('click', closeServerSettings);
    document.getElementById('server-settings-form').addEventListener('submit', save);
    document.getElementById('server-settings-add-login').addEventListener('submit', add);
    bindTabs();
    // The explanations are there on request, not in the way: a disclosure, as
    // in Packet Capture.
    for (const toggle of modal().querySelectorAll('.info-toggle')) {
        toggle.addEventListener('click', () => {
            const open = toggle.getAttribute('aria-expanded') !== 'true';
            toggle.setAttribute('aria-expanded', String(open));
            document.getElementById(toggle.getAttribute('aria-controls')).hidden = !open;
        });
    }
    for (const group of fieldGroups()) {
        inputFor(group.dataset.limit).addEventListener('input', () => showReadable(group.dataset.limit));
    }
    document.getElementById('login-filter').addEventListener('input', applyFilter);
    document.getElementById('server-settings-env-toggle').addEventListener('click', () => {
        envOpen = document.getElementById('server-settings-env-toggle').getAttribute('aria-expanded') !== 'true';
        applyFilter();
    });
}

// --- Tabs -----------------------------------------------------------------

function tabs() {
    return [...modal().querySelectorAll('[role="tab"]')];
}

function selectTab(tab, focus = false) {
    for (const each of tabs()) {
        const selected = each === tab;
        each.setAttribute('aria-selected', String(selected));
        each.tabIndex = selected ? 0 : -1;
        document.getElementById(each.getAttribute('aria-controls')).hidden = !selected;
    }
    modal().querySelector('.server-settings-body').scrollTop = 0;
    if (focus) tab.focus();
}

function bindTabs() {
    for (const tab of tabs()) tab.addEventListener('click', () => selectTab(tab));
    modal().querySelector('[role="tablist"]').addEventListener('keydown', (event) => {
        const all = tabs();
        const at = all.indexOf(document.activeElement);
        const next = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: all.length - 1 }[event.key];
        if (next === undefined || at < 0) return;
        event.preventDefault();
        selectTab(all[(next + all.length) % all.length], true);
    });
}

// --- Limits ---------------------------------------------------------------

// A timeout in seconds, also as people say it: 9000 is "2 h 30 min".
export function readableSeconds(value) {
    const seconds = Number(value);
    if (String(value).trim() === '' || !Number.isInteger(seconds) || seconds <= 0) return '';
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const rest = seconds % 60;
    const parts = [];
    if (days) parts.push(`${days} d`);
    if (hours) parts.push(`${hours} h`);
    if (minutes) parts.push(`${minutes} min`);
    if (rest && !days && !hours) parts.push(`${rest} s`);
    return parts.join(' ');
}

function showReadable(name) {
    const readable = document.getElementById(`limit-${name}-readable`);
    if (!readable) return;
    const text = readableSeconds(inputFor(name).value);
    readable.textContent = text ? ` · ${text}` : '';
}

function clearErrors() {
    for (const group of fieldGroups()) {
        const name = group.dataset.limit;
        inputFor(name).removeAttribute('aria-invalid');
        const error = document.getElementById(`limit-${name}-error`);
        error.textContent = '';
        error.hidden = true;
    }
}

function showError(name, message) {
    const input = inputFor(name);
    const error = document.getElementById(`limit-${name}-error`);
    if (!input || !error) {
        setStatus(`Not saved: ${message}`);
        return;
    }
    error.textContent = message;
    error.hidden = false;
    input.setAttribute('aria-invalid', 'true');
    setStatus('Not saved. Nothing was changed.');
    input.focus();
}

// Only the Limits the server offers in its Lab mode are shown: the
// Labs-alive field is absent in Single-Lab Mode.
function render(limits) {
    const offered = new Map(limits.map((limit) => [limit.name, limit]));
    shown = {};
    for (const group of fieldGroups()) {
        const limit = offered.get(group.dataset.limit);
        group.hidden = !limit;
        if (!limit) continue;
        const input = inputFor(limit.name);
        input.value = String(limit.value);
        input.min = String(limit.min);
        input.max = String(limit.max);
        input.disabled = limit.pinned;
        // Short, since the intro says once what an empty field does.
        const bounds = [`${limit.min}–${limit.max}`];
        if (limit.name === 'session_per_user_cap') bounds.push('0 = no cap');
        bounds.push(limit.pinned ? 'fixed where the server was started' : `started with ${limit.started_with}`);
        const boundsEl = document.getElementById(`limit-${limit.name}-bounds`);
        boundsEl.textContent = bounds.join(' · ');
        boundsEl.classList.toggle('server-settings-pinned', limit.pinned);
        showReadable(limit.name);
        if (!limit.pinned) shown[limit.name] = limit.value;
    }
}

async function openServerSettings() {
    clearErrors();
    setStatus('');
    const dialog = modal();
    try {
        const response = await getServerSettings();
        if (!response.ok) {
            setStatus('Could not read the server settings. Please try again.');
        } else {
            render((await response.json()).limits);
        }
    } catch (_) {
        setStatus('Could not reach the server. Check your connection.');
    }
    await openLogins();
    selectTab(tabs()[0]);
    dialog.style.display = 'flex';
    focusTrap = trapFocus(dialog, { onClose: closeServerSettings });
}

export function closeServerSettings() {
    const dialog = modal();
    if (dialog) dialog.style.display = 'none';
    if (focusTrap) focusTrap.release();
    focusTrap = null;
}

// An empty field is sent as null: the server clears that Limit.
function changedLimits() {
    const changes = {};
    for (const name of Object.keys(shown)) {
        const raw = inputFor(name).value.trim();
        const value = raw === '' ? null : Number(raw);
        if (value !== shown[name]) changes[name] = value;
    }
    return changes;
}

async function save(event) {
    event.preventDefault();
    clearErrors();
    const changes = changedLimits();
    if (Object.keys(changes).length === 0) {
        setStatus('Nothing to save: no Limit was changed.');
        return;
    }
    try {
        const response = await saveServerSettings(changes);
        if (response.status === 422) {
            const refusal = await response.json();
            showError(refusal.field, refusal.detail);
            return;
        }
        if (!response.ok) {
            setStatus('Could not save. Nothing was changed.');
            return;
        }
        render((await response.json()).limits);
        setStatus('Saved. The new Limits apply now.');
    } catch (_) {
        setStatus('Could not reach the server. Nothing was changed.');
    }
}

// --- Logins ---------------------------------------------------------------

function loginsList() {
    return document.getElementById('server-settings-logins-list');
}

function setLoginsStatus(text) {
    document.getElementById('server-settings-logins-status').textContent = text;
}

function clearNameError() {
    document.getElementById('login-name').removeAttribute('aria-invalid');
    const error = document.getElementById('login-name-error');
    error.textContent = '';
    error.hidden = true;
}

function showNameError(message) {
    const input = document.getElementById('login-name');
    const error = document.getElementById('login-name-error');
    error.textContent = message;
    error.hidden = false;
    input.setAttribute('aria-invalid', 'true');
    setLoginsStatus('Not added.');
    input.focus();
}

// Put a password on a login's row, where the Instructor can read it out.
function revealPassword(item, password) {
    const shownPassword = item.querySelector('.server-settings-login-password');
    shownPassword.textContent = password;
    setPasswordShown(item, true);
}

function setPasswordShown(item, shown) {
    item.querySelector('.server-settings-login-password').hidden = !shown;
    item.querySelector('.server-settings-login-mask').hidden = shown;
    const eye = item.querySelector('.server-settings-eye');
    eye.setAttribute('aria-pressed', String(shown));
    eye.title = shown ? 'Hide password' : 'Show password';
    eye.innerHTML = shown ? EYE_OFF_ICON : EYE_ICON;
}

// Showing asks the server; hiding forgets it, so the page keeps no password
// it is not showing.
async function togglePassword(item) {
    const password = item.querySelector('.server-settings-login-password');
    if (!password.hidden) {
        password.textContent = '';
        setPasswordShown(item, false);
        return;
    }
    const name = item.dataset.login;
    try {
        const response = await getLoginPassword(name);
        if (!response.ok) {
            setLoginsStatus(`Could not read the password of ${name}.`);
            return;
        }
        revealPassword(item, (await response.json()).password);
    } catch (_) {
        setLoginsStatus('Could not reach the server. Check your connection.');
    }
}

function resetMessage(name) {
    return `Reset the password of ${name}? Everyone signed in as ${name} is signed out and ` +
        'needs the new password. Their Lab and work stay.';
}

function removeMessage(name) {
    const consequences = appState.labMode === 'multi'
        ? 'Everyone signed in as it is signed out, its running Lab is stopped and its ' +
          'Assignment is dropped. Its Config Copy stays, so adding the name again brings the work back.'
        : 'Everyone signed in as it is signed out, and it can no longer sign in.';
    return `Remove ${name}? ${consequences}`;
}

async function resetPassword(item) {
    const name = item.dataset.login;
    try {
        const response = await resetLoginPassword(name);
        if (!response.ok) {
            setLoginsStatus(`Could not reset the password of ${name}. Nothing was changed.`);
            return;
        }
        const reset = await response.json();
        revealPassword(item, reset.password);
        setLoginsStatus(
            `The password of ${name} was reset. The new password is ${reset.password}. ` +
            'Everyone signed in with the old one is signed out.'
        );
    } catch (_) {
        setLoginsStatus('Could not reach the server. Nothing was changed.');
    }
}

async function remove(item) {
    const name = item.dataset.login;
    try {
        const response = await removeLogin(name);
        if (!response.ok) {
            setLoginsStatus(`Could not remove ${name}. Nothing was changed.`);
            return;
        }
        item.remove();
        applyFilter();
        setLoginsStatus(`Removed ${name}.`);
        document.getElementById('login-name').focus();
    } catch (_) {
        setLoginsStatus('Could not reach the server. Nothing was changed.');
    }
}

const EYE_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 19c-7 0-11-7-11-7a18.45 18.45 0 0 1 5.06-5.94"/>'
    + '<path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 7 11 7a18.5 18.5 0 0 1-2.16 3.19"/>'
    + '<line x1="1" y1="1" x2="23" y2="23"/></svg>';
const MORE_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
    + '<circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';

function envList() {
    return document.getElementById('server-settings-env-list');
}

// An .env login: its name and role, nothing to change here.
function envItem(login) {
    const item = document.createElement('li');
    item.dataset.login = login.name;
    const name = document.createElement('span');
    name.className = 'server-settings-login-name';
    name.textContent = login.name;
    const role = document.createElement('span');
    role.className = 'server-settings-login-source';
    role.textContent = login.role === 'instructor' ? 'Instructor' : 'Learner';
    item.append(name, role);
    return item;
}

// A login added here: name, its password (masked until the eye is pressed)
// and a menu with the two actions that sign its Learners out.
function loginItem(login) {
    const item = document.createElement('li');
    item.dataset.login = login.name;
    const name = document.createElement('span');
    name.className = 'server-settings-login-name';
    name.textContent = login.name;
    const mask = document.createElement('span');
    mask.className = 'server-settings-login-mask';
    mask.setAttribute('aria-hidden', 'true');
    mask.textContent = '••••••••';
    // Empty and hidden until asked for: the page keeps no password.
    const password = document.createElement('code');
    password.className = 'server-settings-login-password';
    password.id = `login-${login.name}-password`;
    password.hidden = true;
    const eye = document.createElement('button');
    eye.type = 'button';
    eye.className = 'btn-icon server-settings-eye';
    eye.setAttribute('aria-label', `Show password for ${login.name}`);
    eye.setAttribute('aria-controls', password.id);
    eye.addEventListener('click', () => togglePassword(item));

    const menu = document.createElement('div');
    menu.className = 'server-settings-login-menu';
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'btn-icon pane-menu-button';
    more.setAttribute('aria-label', `Actions for ${login.name}`);
    more.title = 'Actions';
    more.innerHTML = MORE_ICON;
    const list = document.createElement('div');
    list.className = 'pane-menu-list';
    list.setAttribute('role', 'menu');
    list.setAttribute('aria-label', `Actions for ${login.name}`);
    const reset = document.createElement('button');
    reset.type = 'button';
    reset.setAttribute('role', 'menuitem');
    reset.textContent = 'Reset password…';
    reset.addEventListener('click', () => showConfirmModal({
        title: 'Reset password',
        message: resetMessage(login.name),
        confirmText: 'Reset password',
        onConfirm: () => resetPassword(item),
    }));
    const removal = document.createElement('button');
    removal.type = 'button';
    removal.setAttribute('role', 'menuitem');
    removal.className = 'menu-item-danger';
    removal.textContent = 'Remove…';
    removal.addEventListener('click', () => showConfirmModal({
        title: 'Remove login',
        message: removeMessage(login.name),
        confirmText: 'Remove login',
        onConfirm: () => remove(item),
    }));
    list.append(reset, removal);
    menu.append(more, list);
    bindMenuButton(more, list);

    item.append(name, mask, password, eye, menu);
    setPasswordShown(item, false);
    return item;
}

function renderLogins(logins) {
    loginsList().replaceChildren(...logins.filter((login) => login.added).map(loginItem));
    envList().replaceChildren(...logins.filter((login) => !login.added).map(envItem));
    applyFilter();
}

// Narrow both lists to the names holding the filter's text, and keep every
// count and heading in step: a group with no match is left out, a match in
// the folded .env logins opens them, and the tab says how many there are.
function applyFilter() {
    const filter = document.getElementById('login-filter');
    const query = filter.value.trim().toLowerCase();
    const counts = [loginsList(), envList()].map((list) => {
        let shown = 0;
        for (const item of list.children) {
            item.hidden = query !== '' && !item.dataset.login.includes(query);
            if (!item.hidden) shown += 1;
        }
        return { shown, total: list.children.length };
    });
    const [added, env] = counts;
    const total = added.total + env.total;
    const shown = added.shown + env.shown;
    const of = (count) => (query ? `${count.shown} of ${count.total}` : `${count.total}`);

    document.getElementById('server-settings-logins-filter').hidden = total <= FILTER_FROM && !query;
    document.getElementById('login-filter-count').textContent = query ? `${shown} of ${total} shown` : '';

    const addedHeading = document.getElementById('server-settings-added-heading');
    addedHeading.textContent = `Added here (${of(added)})`;
    addedHeading.hidden = added.total === 0 || (query !== '' && added.shown === 0);
    loginsList().hidden = addedHeading.hidden;

    const toggle = document.getElementById('server-settings-env-toggle');
    const envHidden = env.total === 0 || (query !== '' && env.shown === 0);
    toggle.closest('h4').hidden = envHidden;
    if (envOpen === null) envOpen = env.total <= FILTER_FROM;
    const open = envOpen || (query !== '' && env.shown > 0);
    toggle.textContent = `Set in .env · change them there (${of(env)})`;
    toggle.setAttribute('aria-expanded', String(open));
    envList().hidden = envHidden || !open;

    const empty = document.getElementById('server-settings-logins-empty');
    empty.hidden = !query || shown > 0;
    empty.textContent = `No login matches “${filter.value.trim()}”.`;

    document.getElementById('server-settings-tab-logins').textContent = `Accounts (${total})`;
}

// Accounts in both Lab modes; in Multi-Lab Mode the intro adds that each
// Learner account is a Group, the name the Class Overview uses.
async function openLogins() {
    document.getElementById('server-settings-group-note').hidden = appState.labMode !== 'multi';
    clearNameError();
    setLoginsStatus('');
    document.getElementById('login-name').value = '';
    document.getElementById('login-filter').value = '';
    try {
        const response = await getLogins();
        if (!response.ok) {
            setLoginsStatus('Could not read the logins. Please try again.');
            return;
        }
        renderLogins((await response.json()).logins);
    } catch (_) {
        setLoginsStatus('Could not reach the server. Check your connection.');
    }
}

async function add(event) {
    event.preventDefault();
    clearNameError();
    const input = document.getElementById('login-name');
    const name = input.value.trim();
    try {
        const response = await addLogin(name);
        if (response.status === 422 || response.status === 409) {
            showNameError((await response.json()).detail);
            return;
        }
        if (!response.ok) {
            setLoginsStatus('Could not add the login. Nothing was changed.');
            return;
        }
        const added = await response.json();
        const item = loginItem({ name: added.name, role: 'learner', added: true });
        loginsList().appendChild(item);
        revealPassword(item, added.password);
        applyFilter();
        input.value = '';
        setLoginsStatus(`Added. ${added.name} can sign in now with the password ${added.password}.`);
    } catch (_) {
        setLoginsStatus('Could not reach the server. Nothing was changed.');
    }
}
