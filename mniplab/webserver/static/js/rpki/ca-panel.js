// RPKI CA panel — the modal a Learner opens from an RPKI CA Node's badge to
// read and edit the ROAs and ASPAs each Hosted CA holds.
//
// The tables are Krill's own state, not a copy of it: every read goes to the
// RPKI CA's Krill, so a change made with `krillc` in a Terminal appears on the
// next poll, and a change made here is the state `krillc` sees. Each committed
// change is broadcast to every open view of the Lab (type
// "RPKI_OBJECTS_UPDATED"). The broadcast handler is last-write-wins: it
// refreshes the tables and leaves any form a Learner is filling in alone.
//
// One Hosted CA shows at a time, chosen from the picker at the top, as an RIR
// portal shows one account: the panel stays short however many it holds.
// Its add form starts filled with what a ROA there almost always says: the
// Hosted CA's own AS as origin, and its prefix when it holds just one.
//
// A refusal comes back from the Lab as a 400 whose detail says what Krill would
// not do; it is shown beside the form that made the change. Input that cannot
// be a ROA or an ASPA at all is caught before the round trip, with its field
// marked, so the Lab is only asked about things it could answer.

import {
    getRpkiCas,
    saveRpkiRoa,
    deleteRpkiRoa,
    saveRpkiAspa,
    deleteRpkiAspa,
} from '../services/api.js';
import { formatResponseError, patchTableRows } from '../utils/dom.js';
import { trapFocus } from '../utils/focus-trap.js';

const POLL_INTERVAL_MS = 3000;
const LOADING_MESSAGE = 'Loading Hosted CAs…';
const ASN_TEXT = /^(?:AS)?\d+$/i;

const ADD_LABEL = { roa: 'Add ROA', aspa: 'Add ASPA' };
const SAVE_LABEL = { roa: 'Save ROA', aspa: 'Save ASPA' };

let currentNode = null;
// The Hosted CA the picker shows, kept across polls and re-opens.
let selectedAsn = null;
let pollTimer = null;
let trap = null;
let refreshId = 0;
// The object each form is editing, keyed by `${asn}-${kind}`: the object as
// its table shows it, and the Edit button that opened the form, which regains
// focus on Cancel.
const editing = new Map();

/** Create an element with attributes and children.
 *
 * Args:
 *     tag: The element name.
 *     props: Attributes by name; ``text`` sets the text content, ``class`` the
 *         class, ``true`` sets a boolean attribute, and ``false``/``null``
 *         leave the attribute out.
 *     children: Nodes to append, in order.
 *
 * Returns:
 *     The element.
 */
function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(props)) {
        if (value === null || value === undefined || value === false) continue;
        if (name === 'text') node.textContent = value;
        else if (name === 'class') node.className = value;
        else node.setAttribute(name, value === true ? '' : String(value));
    }
    node.append(...children);
    return node;
}

function roaLabel(roa) {
    return `${roa.prefix}-${roa.max_length} => AS${roa.origin}`;
}

function roaKey(roa) {
    return `${roa.prefix}|${roa.max_length}|${roa.origin}`;
}

function aspaLabel(aspa) {
    return `AS${aspa.customer} => ${aspa.providers.map((p) => `AS${p}`).join(', ')}`;
}

function aspaKey(aspa) {
    return `${aspa.customer}|${aspa.providers.join(',')}`;
}

function setStatus(message, isError = false) {
    const status = document.getElementById('rpki-status');
    if (!status) return;
    status.textContent = message;
    status.classList.toggle('rpki-modal-status-error', Boolean(isError && message));
}

function formGroup(id, labelText, name, attrs = {}) {
    return el('div', { class: 'rpki-form-group' }, [
        el('label', { for: id, text: labelText }),
        el('input', {
            id,
            name,
            type: 'text',
            autocomplete: 'off',
            spellcheck: 'false',
            ...attrs,
        }),
    ]);
}

function formError(base) {
    return el('p', {
        id: `${base}-error`,
        class: 'rpki-form-error',
        role: 'alert',
        tabindex: '-1',
        hidden: true,
    });
}

function formActions(base, kind) {
    return el('div', { class: 'rpki-form-actions' }, [
        el('button', { type: 'submit', id: `${base}-submit`, class: 'rpki-submit-btn', text: ADD_LABEL[kind] }),
        el('button', { type: 'button', id: `${base}-cancel`, class: 'rpki-cancel-btn', text: 'Cancel edit', hidden: true }),
    ]);
}

function tableHead(headers) {
    return el('thead', {}, [el('tr', {}, headers.map((text) => el('th', { scope: 'col', text })))]);
}

function buildRoaBlock(asn) {
    const base = `rpki-${asn}-roa`;
    const heading = el('h4', { id: `${base}-heading`, class: 'rpki-subheading', text: 'ROAs' });
    const form = el('form', { id: `${base}-form`, class: 'rpki-form', novalidate: true }, [
        el('fieldset', { class: 'rpki-fieldset' }, [
            el('legend', { id: `${base}-legend`, class: 'rpki-legend', text: `Add a ROA at AS${asn}` }),
            el('div', { class: 'rpki-form-row' }, [
                formGroup(`${base}-prefix`, 'Prefix', 'prefix', {
                    'aria-required': 'true',
                }),
                formGroup(`${base}-max`, 'maxLength', 'max_length', {
                    inputmode: 'numeric', 'aria-describedby': `${base}-max-hint`,
                    'data-describedby': `${base}-max-hint`,
                }),
                formGroup(`${base}-origin`, 'Origin', 'origin', {
                    value: `AS${asn}`, 'aria-required': 'true',
                }),
            ]),
            el('p', {
                id: `${base}-max-hint`,
                class: 'rpki-hint',
                text: 'Leave maxLength empty to allow only the prefix itself, not longer ones (recommended).',
            }),
            formActions(base, 'roa'),
        ]),
        formError(base),
    ]);
    form.addEventListener('submit', (event) => {
        event.preventDefault();
        void submitRoa(asn, form);
    });
    form.querySelector(`#${base}-cancel`).addEventListener('click', () => cancelEdit(asn, 'roa'));
    return el('div', { class: 'rpki-block' }, [
        heading,
        el('table', { id: `${base}-table`, class: 'rpki-table', 'aria-labelledby': heading.id, hidden: true }, [
            tableHead(['Prefix', 'maxLength', 'Origin', 'Actions']),
            el('tbody', { id: `${base}-tbody` }),
        ]),
        el('p', { id: `${base}-empty`, class: 'rpki-empty', text: `No ROAs at AS${asn} yet.`, hidden: true }),
        form,
    ]);
}

function buildAspaBlock(asn) {
    const base = `rpki-${asn}-aspa`;
    const heading = el('h4', { id: `${base}-heading`, class: 'rpki-subheading', text: 'ASPAs' });
    const form = el('form', { id: `${base}-form`, class: 'rpki-form', novalidate: true }, [
        el('fieldset', { class: 'rpki-fieldset' }, [
            el('legend', { id: `${base}-legend`, class: 'rpki-legend', text: `Add an ASPA for AS${asn}` }),
            el('div', { class: 'rpki-form-row' }, [
                formGroup(`${base}-providers`, 'Providers', 'providers', {
                    placeholder: 'AS65001, AS65005', 'aria-required': 'true',
                }),
            ]),
            formActions(base, 'aspa'),
        ]),
        formError(base),
    ]);
    form.addEventListener('submit', (event) => {
        event.preventDefault();
        void submitAspa(asn, form);
    });
    form.querySelector(`#${base}-cancel`).addEventListener('click', () => cancelEdit(asn, 'aspa'));
    return el('div', { class: 'rpki-block' }, [
        heading,
        el('table', { id: `${base}-table`, class: 'rpki-table', 'aria-labelledby': heading.id, hidden: true }, [
            tableHead(['Customer', 'Providers', 'Actions']),
            el('tbody', { id: `${base}-tbody` }),
        ]),
        el('p', { id: `${base}-empty`, class: 'rpki-empty', text: `No ASPAs at AS${asn} yet.`, hidden: true }),
        form,
    ]);
}

/** Build one Hosted CA's section: its heading, resources, ROA block and ASPA block. */
function buildSection(asn) {
    const base = `rpki-${asn}`;
    return el('section', {
        class: 'rpki-ca-section',
        'data-asn': asn,
        'aria-labelledby': `${base}-heading`,
    }, [
        el('h3', { id: `${base}-heading`, class: 'rpki-ca-heading', text: `AS${asn}` }),
        el('p', { id: `${base}-resources`, class: 'rpki-ca-resources' }),
        buildRoaBlock(asn),
        buildAspaBlock(asn),
    ]);
}

/** The row's Actions cell, rebuilt only when the row is new: reusing the node
 * across polls keeps focus on its buttons, the same contract the DNS panel's
 * Actions cell has. */
function actionsCell(tbody, key, build) {
    const row = Array.from(tbody.rows).find((tr) => tr.dataset.rowKey === key);
    const existing = row ? row.lastElementChild.firstElementChild : null;
    return existing || build();
}

function buildActions(label, onEdit, onRemove) {
    const edit = el('button', {
        type: 'button', class: 'rpki-action-btn', 'aria-label': `Edit ${label}`, text: 'Edit',
    });
    edit.addEventListener('click', () => onEdit(edit));
    const remove = el('button', {
        type: 'button', class: 'rpki-action-btn rpki-remove-btn', 'aria-label': `Remove ${label}`, text: 'Remove',
    });
    remove.addEventListener('click', () => onRemove(remove));
    return el('div', { class: 'rpki-row-actions' }, [edit, remove]);
}

function updateRoaRows(ca) {
    const base = `rpki-${ca.asn}-roa`;
    const tbody = document.getElementById(`${base}-tbody`);
    const rows = ca.roas.map((roa) => {
        const key = roaKey(roa);
        const actions = actionsCell(tbody, key, () => buildActions(
            `ROA ${roaLabel(roa)}`,
            (button) => startEdit(ca.asn, 'roa', roa, button),
            () => removeRoa(ca.asn, roa),
        ));
        return { key, cells: [roa.prefix, String(roa.max_length), `AS${roa.origin}`, actions] };
    });
    patchTableRows(tbody, rows);
    document.getElementById(`${base}-table`).hidden = rows.length === 0;
    document.getElementById(`${base}-empty`).hidden = rows.length > 0;
}

function updateAspaRows(ca) {
    const base = `rpki-${ca.asn}-aspa`;
    const tbody = document.getElementById(`${base}-tbody`);
    const rows = ca.aspas.map((aspa) => {
        const key = aspaKey(aspa);
        const actions = actionsCell(tbody, key, () => buildActions(
            `ASPA ${aspaLabel(aspa)}`,
            (button) => startEdit(ca.asn, 'aspa', aspa, button),
            () => removeAspa(ca.asn, aspa),
        ));
        return {
            key,
            cells: [
                `AS${aspa.customer}`,
                aspa.providers.map((p) => `AS${p}`).join(', '),
                actions,
            ],
        };
    });
    patchTableRows(tbody, rows);
    document.getElementById(`${base}-table`).hidden = rows.length === 0;
    document.getElementById(`${base}-empty`).hidden = rows.length > 0;
}

/** Show the Hosted CAs the Lab reported, building each section once and
 * updating its resources and rows in place on every later render.
 *
 * Args:
 *     cas: Each Hosted CA with ``asn``, ``prefixes``, ``roas`` and ``aspas``,
 *         as the Lab's ``list_cas`` returns them.
 */
function renderHostedCas(cas) {
    const container = document.getElementById('rpki-cas');
    if (!container) return;
    const present = new Set(cas.map((ca) => String(ca.asn)));
    for (const section of Array.from(container.querySelectorAll('[data-asn]'))) {
        if (!present.has(section.dataset.asn)) section.remove();
    }
    if (!present.has(String(selectedAsn))) selectedAsn = cas.length > 0 ? cas[0].asn : null;
    renderPicker(container, cas);
    for (const ca of cas) {
        let section = container.querySelector(`[data-asn="${ca.asn}"]`);
        if (!section) {
            section = buildSection(ca.asn);
            container.appendChild(section);
        }
        section.hidden = String(ca.asn) !== String(selectedAsn);
        section.querySelector(`#rpki-${ca.asn}-resources`).textContent =
            `Resources: ${ca.prefixes.length > 0 ? ca.prefixes.join(', ') : 'none'}`;
        // Setting the default leaves a prefix the Learner already typed alone.
        const prefix = document.getElementById(`rpki-${ca.asn}-roa-prefix`);
        if (prefix) prefix.defaultValue = ca.prefixes.length === 1 ? ca.prefixes[0] : '';
        updateRoaRows(ca);
        updateAspaRows(ca);
    }
}

/** Build the Hosted CA picker once, and keep its options to the Hosted CAs
 * present. With a single Hosted CA there is nothing to choose: it hides. */
function renderPicker(container, cas) {
    let picker = document.getElementById('rpki-ca-picker');
    if (!picker) {
        const select = el('select', { id: 'rpki-ca-select' });
        select.addEventListener('change', () => {
            selectedAsn = select.value;
            for (const section of container.querySelectorAll('[data-asn]')) {
                section.hidden = section.dataset.asn !== selectedAsn;
            }
        });
        picker = el('div', { id: 'rpki-ca-picker', class: 'rpki-ca-picker' }, [
            el('label', { for: 'rpki-ca-select', text: 'Hosted CA' }),
            select,
        ]);
        container.prepend(picker);
    }
    const select = document.getElementById('rpki-ca-select');
    const wanted = cas.map((ca) => String(ca.asn));
    const have = Array.from(select.options).map((option) => option.value);
    if (wanted.join() !== have.join()) {
        select.replaceChildren(...cas.map((ca) => el('option', { value: ca.asn, text: `AS${ca.asn}` })));
    }
    select.value = String(selectedAsn);
    picker.hidden = cas.length < 2;
}

async function refreshCas() {
    const node = currentNode;
    if (!node) return;
    const requestId = ++refreshId;

    let response;
    try {
        response = await getRpkiCas(node);
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

    const cas = (data && data.cas) || [];
    renderHostedCas(cas);
    const status = document.getElementById('rpki-status');
    const showingError = status && status.classList.contains('rpki-modal-status-error');
    if (cas.length === 0) {
        setStatus('This RPKI CA holds no Hosted CAs.');
    } else if (showingError || (status && status.textContent === LOADING_MESSAGE)) {
        setStatus('');
    }
}

function fieldValue(form, name) {
    const input = form.querySelector(`[name="${name}"]`);
    return input ? input.value.trim() : '';
}

/** The first problem with a ROA as typed, or null. Naming the field lets the
 * form mark it. Krill and the Lab judge the rest: whether the prefix is one
 * the Hosted CA holds, or the maxLength fits the prefix. */
function roaInputError(form) {
    const prefix = fieldValue(form, 'prefix');
    const maxLength = fieldValue(form, 'max_length');
    const origin = fieldValue(form, 'origin');
    if (!prefix) return { field: 'prefix', message: 'Prefix is required, such as 10.1.0.0/24.' };
    if (!prefix.includes('/')) {
        return { field: 'prefix', message: `Prefix must be in CIDR notation, such as 10.1.0.0/24; got "${prefix}".` };
    }
    if (maxLength !== '' && !/^\d+$/.test(maxLength)) {
        return { field: 'max_length', message: 'maxLength must be a whole number, such as 25, or left empty for the prefix length.' };
    }
    if (!origin) return { field: 'origin', message: 'Origin is required, such as AS65002.' };
    if (!ASN_TEXT.test(origin)) {
        return { field: 'origin', message: `Origin must be an AS number, such as AS65002; got "${origin}".` };
    }
    return null;
}

/** The first problem with an ASPA's providers as typed, or null. */
function aspaInputError(form) {
    const items = fieldValue(form, 'providers').split(',').map((item) => item.trim()).filter(Boolean);
    if (items.length === 0) {
        return { field: 'providers', message: 'Providers are required, such as AS65001, AS65005.' };
    }
    const bad = items.find((item) => !ASN_TEXT.test(item));
    if (bad) {
        return { field: 'providers', message: `Providers must be AS numbers separated by commas, such as AS65001, AS65005; got "${bad}".` };
    }
    return null;
}

function clearFormErrors(form) {
    const error = form.querySelector('.rpki-form-error');
    if (error) {
        error.textContent = '';
        error.hidden = true;
    }
    form.querySelectorAll('[aria-invalid]').forEach((input) => {
        input.removeAttribute('aria-invalid');
        if (input.dataset.describedby) input.setAttribute('aria-describedby', input.dataset.describedby);
        else input.removeAttribute('aria-describedby');
    });
}

function showFormError(form, message) {
    const error = form.querySelector('.rpki-form-error');
    error.textContent = message;
    error.hidden = false;
    error.focus();
}

function showFieldError(form, { field, message }) {
    const error = form.querySelector('.rpki-form-error');
    error.textContent = message;
    error.hidden = false;
    const input = form.querySelector(`[name="${field}"]`);
    input.setAttribute('aria-invalid', 'true');
    input.setAttribute('aria-describedby', [error.id, input.dataset.describedby].filter(Boolean).join(' '));
    input.focus();
}

async function showRefusal(form, response, fallback) {
    let data = null;
    try {
        data = await response.json();
    } catch (_) { /* fall through with data === null */ }
    showFormError(form, formatResponseError(data, `${fallback} (${response.status})`));
}

function resetForm(asn, kind) {
    const key = `${asn}-${kind}`;
    const form = document.getElementById(`rpki-${asn}-${kind}-form`);
    editing.delete(key);
    if (!form) return;
    form.reset();
    clearFormErrors(form);
    form.querySelector('.rpki-legend').textContent = kind === 'roa'
        ? `Add a ROA at AS${asn}`
        : `Add an ASPA for AS${asn}`;
    form.querySelector('[type="submit"]').textContent = ADD_LABEL[kind];
    form.querySelector('.rpki-cancel-btn').hidden = true;
}

function startEdit(asn, kind, record, button) {
    const form = document.getElementById(`rpki-${asn}-${kind}-form`);
    editing.set(`${asn}-${kind}`, { record, trigger: button });
    clearFormErrors(form);
    if (kind === 'roa') {
        form.querySelector('[name="prefix"]').value = record.prefix;
        form.querySelector('[name="max_length"]').value = String(record.max_length);
        form.querySelector('[name="origin"]').value = `AS${record.origin}`;
        form.querySelector('.rpki-legend').textContent = `Edit ROA at AS${asn}`;
    } else {
        form.querySelector('[name="providers"]').value = record.providers.map((p) => `AS${p}`).join(', ');
        form.querySelector('.rpki-legend').textContent = `Edit ASPA for AS${asn}`;
    }
    form.querySelector('[type="submit"]').textContent = SAVE_LABEL[kind];
    form.querySelector('.rpki-cancel-btn').hidden = false;
    form.querySelector('input').focus();
}

function cancelEdit(asn, kind) {
    const state = editing.get(`${asn}-${kind}`);
    resetForm(asn, kind);
    if (state && state.trigger && document.contains(state.trigger)) {
        state.trigger.focus();
    } else {
        document.querySelector(`#rpki-${asn}-${kind}-form input`).focus();
    }
}

/** Run one form's change: disable its submit while the Lab answers, reset the
 * form and refresh on success, show a refusal beside the form on failure. */
async function submitChange(asn, kind, form, request) {
    const submit = form.querySelector('[type="submit"]');
    const editingNow = editing.has(`${asn}-${kind}`);
    submit.disabled = true;
    submit.textContent = editingNow ? 'Saving…' : 'Adding…';
    try {
        const response = await request();
        if (!response.ok) {
            await showRefusal(form, response, editingNow ? 'Could not save the change' : 'Could not add the change');
            return;
        }
        resetForm(asn, kind);
        form.querySelector('input').focus();
        await refreshCas();
    } catch (error) {
        showFormError(form, `Network error: ${error.message || error}`);
    } finally {
        submit.disabled = false;
        submit.textContent = editing.has(`${asn}-${kind}`) ? SAVE_LABEL[kind] : ADD_LABEL[kind];
    }
}

async function submitRoa(asn, form) {
    clearFormErrors(form);
    const problem = roaInputError(form);
    if (problem) {
        showFieldError(form, problem);
        return;
    }
    const body = {
        prefix: fieldValue(form, 'prefix'),
        origin: fieldValue(form, 'origin'),
    };
    const maxLength = fieldValue(form, 'max_length');
    if (maxLength !== '') body.max_length = Number(maxLength);
    const state = editing.get(`${asn}-roa`);
    if (state) {
        const { prefix, max_length: oldMax, origin } = state.record;
        body.old = { prefix, max_length: oldMax, origin };
    }
    await submitChange(asn, 'roa', form, () => saveRpkiRoa(currentNode, asn, body));
}

async function submitAspa(asn, form) {
    clearFormErrors(form);
    const problem = aspaInputError(form);
    if (problem) {
        showFieldError(form, problem);
        return;
    }
    const providers = fieldValue(form, 'providers').split(',').map((item) => item.trim()).filter(Boolean);
    await submitChange(asn, 'aspa', form, () => saveRpkiAspa(currentNode, asn, { customer: asn, providers }));
}

/** Give focus to the row that took the removed one's place, else the form. */
function focusAfterRemoval(tbody, index, form) {
    const row = tbody.rows[index] || tbody.rows[index - 1];
    const target = row ? row.querySelector('button') : form.querySelector('input');
    target.focus();
}

async function removeRow(asn, kind, key, form, tbody, request, label) {
    const index = Array.from(tbody.rows).findIndex((tr) => tr.dataset.rowKey === key);
    clearFormErrors(form);
    let response;
    try {
        response = await request();
    } catch (error) {
        showFormError(form, `Network error: ${error.message || error}`);
        return;
    }
    if (!response.ok) {
        await showRefusal(form, response, `Could not remove ${label}`);
        return;
    }
    const state = editing.get(`${asn}-${kind}`);
    if (state && (kind === 'roa' ? roaKey(state.record) : aspaKey(state.record)) === key) {
        resetForm(asn, kind);
    }
    setStatus(`Removed ${label} at AS${asn}.`);
    await refreshCas();
    focusAfterRemoval(tbody, index, form);
}

async function removeRoa(asn, roa) {
    const form = document.getElementById(`rpki-${asn}-roa-form`);
    const tbody = document.getElementById(`rpki-${asn}-roa-tbody`);
    const body = { prefix: roa.prefix, max_length: roa.max_length, origin: roa.origin };
    await removeRow(
        asn, 'roa', roaKey(roa), form, tbody,
        () => deleteRpkiRoa(currentNode, asn, body),
        `ROA ${roaLabel(roa)}`,
    );
}

async function removeAspa(asn, aspa) {
    const form = document.getElementById(`rpki-${asn}-aspa-form`);
    const tbody = document.getElementById(`rpki-${asn}-aspa-tbody`);
    await removeRow(
        asn, 'aspa', aspaKey(aspa), form, tbody,
        () => deleteRpkiAspa(currentNode, asn, aspa.customer),
        `ASPA ${aspaLabel(aspa)}`,
    );
}

/** Re-read the tables while the panel is open, so changes made elsewhere
 * (a `krillc` run in a Terminal included) show without a reload. */
function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => { void refreshCas(); }, POLL_INTERVAL_MS);
}

/** Open the panel for an RPKI CA Node.
 *
 * Args:
 *     nodeName: The RPKI CA's Node name, as the Topology names it.
 */
export function openRpkiCaPanel(nodeName) {
    const modal = document.getElementById('rpki-modal');
    const title = document.getElementById('rpki-modal-title');
    if (!modal) return;

    currentNode = nodeName;
    refreshId += 1;
    editing.clear();
    if (title) title.textContent = `${nodeName} — RPKI CA`;
    const container = document.getElementById('rpki-cas');
    if (container) container.replaceChildren();
    setStatus(LOADING_MESSAGE);

    modal.style.display = 'flex';
    // Re-opening while already open (another badge press) must not leak the
    // previous trap's document-level listener.
    if (trap) trap.release();
    trap = trapFocus(modal, { onClose: closeRpkiCaPanel });

    void refreshCas();
    startPolling();
}

function closeRpkiCaPanel() {
    const modal = document.getElementById('rpki-modal');
    if (modal) modal.style.display = 'none';
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    currentNode = null;
    refreshId += 1;
    editing.clear();
    if (trap) {
        trap.release();
        trap = null;
    }
}

/** Wire the panel's close control and backdrop once the page has loaded. */
export function initRpkiCaPanel() {
    const modal = document.getElementById('rpki-modal');
    const closeBtn = document.getElementById('rpki-modal-close');
    if (!modal) return;

    if (closeBtn) closeBtn.addEventListener('click', closeRpkiCaPanel);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeRpkiCaPanel();
    });
}

/** Refresh the open panel when a Lab change lands on its RPKI CA.
 *
 * Args:
 *     data: The broadcast's data: the ``node`` that changed, plus the ``asn``,
 *         ``kind`` and ``action``.
 */
export function handleRpkiObjectsBroadcast(data) {
    if (!data || !currentNode || data.node !== currentNode) return;
    void refreshCas();
}
