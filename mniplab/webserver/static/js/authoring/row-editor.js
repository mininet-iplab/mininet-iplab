// Row editors for Lab Authoring: a list in the definition, edited as labelled
// rows. One declaration per list says its fields; the editor renders them,
// keeps the definition's own objects as its model, and names every control
// "<item> <n> <field>" so a row is never anonymous to assistive technology.
//
// A list is a flat table of rows, or, when it declares a ``summary``, a stack
// of collapsible cards. A row may hold ``children``: further lists rendered as
// a table inside its card, so a Reservation sits under its Pool.
//
// The model is the definition array itself, edited in place. A control whose
// text will not parse leaves the raw text in the model, and ``validateRows``
// reports it, so a draft holding bad text is refused before it is saved.

import { announce } from './status-line.js';
import { attachAddressPicker } from './address-picker.js';

/**
 * @typedef {object} Field
 * @property {boolean} [required] Mark the field visibly and as aria-required; its check enforces the rule.
 * @property {string} key Property of the row this field edits, and the last part of its id.
 * @property {string} [invalid] What to say when a number field's text is not a number.
 * @property {string|((row: ?object, parent: ?object, ui: ?object) => string)} label Visible
 *     column or label text; a function gives the wording for a row (or, with a
 *     null row, for the column) once the parent's settings are known.
 * @property {'text'|'number'|'select'|'address'|'checkbox'|'lines'} [kind='text'] An ``address`` is typed
 *     freely and may be picked from the Interfaces ``hooks.addressChoices`` offers.
 *     A ``checkbox`` writes its native checked boolean only when edited.
 *     A ``lines`` is a text area holding a list, one item per line; its ``parse``
 *     and ``format`` convert between that text and the list.
 * @property {Array<[string, string]>|((row: object, parent: ?object) => Array<[string, string]>)} [options] Value and label pairs of a select.
 * @property {string|((row: object, parent: ?object, ui: object) => string)} [placeholder]
 * @property {(text: string) => *} [parse] Control text to value. Returns the
 *     raw text when it is malformed, for ``check`` to report.
 * @property {(value: *) => string} [format] Value to control text.
 * @property {(value: *, row: object, parent: ?object) => ?string} [check] The message for a bad value.
 * @property {(row: ?object, parent: ?object, ui: ?object) => boolean} [visibleWhen] Whether
 *     the field applies. A field that does not is hidden, not removed, so
 *     focus and Tab order never move under the Instructor.
 * @property {*} [hiddenValue] What an edit leaves in a field that does not apply.
 * @property {(row: object, ui: object) => *} [read] Value of a field with no
 *     property of its own, such as one that shows MAC or DUID by a setting.
 * @property {(row: object, value: *, ui: object) => void} [write] Its inverse.
 * @property {boolean} [state] A setting that lives in the editor, not the definition.
 * @property {(row: object) => *} [initial] A state field's value for a loaded row.
 * @property {(row: object, value: *, ui: object) => void} [after] Runs when a state field changes.
 */

/**
 * @typedef {object} ListSpec
 * @property {string} name Id of the list's group; row controls are `${name}-${i}-${key}`.
 * @property {string} itemLabel Singular noun: "Access rule" names "Access rule 2 Subnet".
 * @property {(parent: ?object) => object} blank A new row, with every key the definition needs.
 * @property {Field[]} fields
 * @property {(row: object) => string} [summary] Makes the list collapsible
 *     cards; this is each closed card's one line.
 * @property {Array<ListSpec & {key: string, behindMore?: boolean}>} [children] Lists inside a row,
 *     under ``key``; optional arrays stay absent until edited. ``behindMore`` puts a child in More.
 * @property {Field[]} [more] Fields a row keeps behind its own **More** button.
 * @property {number} [max] Most rows the list may hold; Add stops there.
 * @property {(item: *) => object} [itemToRow] For a list of plain values: the row that edits one.
 * @property {(row: object) => *} [rowToItem] Its inverse, written back to the definition after each edit.
 * @property {(row: object, parent: ?object, siblings: object[]) => Object<string, string>}
 *     [validate] Messages for rules that span fields or rows, keyed by field key.
 */

const NUMBER = {
    parse: text => (text.trim() === '' ? null : /^-?\d+$/.test(text.trim()) ? Number(text) : text),
    format: value => (value === null || value === undefined ? '' : String(value)),
};

function codec(field) {
    const base = field.kind === 'number' ? NUMBER : {};
    return {
        parse: field.parse || base.parse || (text => text),
        format: field.format || base.format || (value => String(value ?? '')),
    };
}

const uiStates = new WeakMap();

// A row's settings that are not in the definition, such as which identifier a
// Reservation names, kept beside the row and never saved.
function uiOf(list, row) {
    let ui = uiStates.get(row);
    if (!ui) uiStates.set(row, ui = {});
    for (const field of allFields(list)) if (field.state && !(field.key in ui)) ui[field.key] = field.initial(row);
    return ui;
}

function read(list, field, row) {
    const ui = uiOf(list, row);
    if (field.state) return ui[field.key];
    return field.read ? field.read(row, ui) : row[field.key];
}

function write(list, field, row, value) {
    const ui = uiOf(list, row);
    if (field.state) {
        if (ui[field.key] === value) return;
        ui[field.key] = value;
        field.after?.(row, value, ui);
    } else if (field.write) field.write(row, value, ui);
    else row[field.key] = value;
}

const allFields = list => [...list.fields, ...(list.more || [])];
const blankValue = value => value === null || value === undefined || value === '';
const visible = (field, row, parent, ui) => (field.visibleWhen ? field.visibleWhen(row, parent, ui) : true);
const labelOf = (list, field, row, parent) => (typeof field.label === 'function'
    ? field.label(row, parent, row && uiOf(list, row)) : field.label);
const placeholderOf = (list, field, row, parent) => (typeof field.placeholder === 'function'
    ? field.placeholder(row, parent, row && uiOf(list, row)) : field.placeholder);

// The message for one field of one row, or null when it is fine.
function messageFor(list, field, row, parent, siblings) {
    const value = read(list, field, row);
    if (field.kind === 'number' && typeof value === 'string') return field.invalid || 'Enter a whole number.';
    const own = field.check?.(value, row, parent);
    if (own) return own;
    return (list.validate ? list.validate(row, parent, siblings)[field.key] : null) || null;
}

/**
 * Every message a list's rows give, in row order.
 *
 * @param {ListSpec} list
 * @param {object[]} rows The list from the definition.
 * @param {?object} [parent] The row this list is inside, if any.
 * @param {string} [prefix] Id prefix of the list's controls.
 * @returns {Array<{controlId: string, message: string}>} One entry per bad field.
 */
function validateRows(list, rows, parent = null, prefix = list.name) {
    const found = [];
    rows.forEach((row, index) => {
        for (const field of allFields(list)) {
            if (!visible(field, row, parent)) continue;
            const message = messageFor(list, field, row, parent, rows);
            if (message) found.push({ controlId: `${prefix}-${index}-${field.key}`, message });
        }
        for (const child of list.children || []) {
            if (!visible(child, null, row, uiOf(list, row))) continue;
            found.push(...validateRows(child, row[child.key] || [], row, `${prefix}-${index}-${child.key}`));
        }
    });
    return found;
}

/**
 * ``validateRows`` for a list as the definition holds it, whether of objects or of plain values.
 *
 * @param {ListSpec} list
 * @param {Array<*>} items The list from the definition.
 * @param {?object} [parent] What the list sits in, such as the Speaker holding Global Routes.
 * @returns {Array<{controlId: string, message: string}>}
 */
export function validateItems(list, items, parent = null) {
    return validateRows(list, list.itemToRow ? items.map(list.itemToRow) : items, parent);
}

/**
 * Open whatever hides a control: every collapsed row around it, and the
 * **More** region holding it, so an error or a fix can be seen and reached.
 *
 * @param {HTMLElement} control A row editor control.
 */
export function revealRowControl(control) {
    for (let group = control.closest('details'); group; group = group.parentElement?.closest('details')) {
        group.open = true;
    }
    for (let region = control.closest('.row-more'); region; region = region.parentElement?.closest('.row-more')) {
        region.hidden = false;
        document.querySelector(`[aria-controls="${region.id}"]`)?.setAttribute('aria-expanded', 'true');
    }
}

function element(tag, attributes = {}, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) {
        if (value === false || value === undefined) continue;
        if (name === 'text') node.textContent = value;
        else node.setAttribute(name, value === true ? '' : value);
    }
    node.append(...children);
    return node;
}

function markRequired(label, field) {
    if (field.required) label.append(element('span', { 'aria-hidden': 'true', text: ' *' }));
}

/**
 * Mount a row editor for one list.
 *
 * @param {HTMLElement} host Empty element the editor renders into.
 * @param {ListSpec} spec
 * @param {object} hooks
 * @param {() => void} hooks.onChange Called after every edit, add or remove.
 * @param {(control: HTMLElement, message: string) => void} hooks.showError
 * @param {(control: HTMLElement) => void} hooks.clearError
 * @param {() => Array<{address: string, label: string}>} [hooks.addressChoices]
 *     The Interfaces an ``address`` field offers right now.
 * @returns {{setRows: (rows: object[]) => void}} ``setRows`` shows a list from
 *     the definition; the editor then edits that array in place.
 */
export function createRowEditor(host, spec, hooks) {
    // Every control with the rule that judges it, so an edit can re-judge the
    // fields that are showing an error, and the fix clears it at once.
    const judged = new Map();

    function rejudge() {
        for (const [control, judge] of judged) {
            if (!control.isConnected) judged.delete(control);
            else if (control.getAttribute('aria-invalid') === 'true') {
                const message = judge();
                if (message) hooks.showError(control, message);
                else hooks.clearError(control);
            }
        }
    }

    // Build one list's view: rows as a table or cards, and an Add button.
    function buildList(list, rows, prefix, labelPrefix, parent, notify) {
        const container = element('div', { class: 'row-list' });
        const asCards = Boolean(list.summary);
        let open = rows.map(() => false);
        let views = [];
        let headers = [];
        const noun = index => `${labelPrefix}${list.itemLabel} ${index + 1}`;

        function buildField(field, row, index, refreshRow) {
            const id = `${prefix}-${index}-${field.key}`;
            const { parse, format } = codec(field);
            const name = () => `${noun(index)} ${labelOf(list, field, row, parent)}`;
            const attributes = { id, 'aria-label': name(), 'aria-describedby': `${id}-error`,
                'aria-required': field.required ? 'true' : false };
            let control;
            if (field.kind === 'select') {
                control = element('select', attributes);
            } else if (field.kind === 'lines') {
                control = element('textarea', { ...attributes, rows: 3, autocomplete: 'off', spellcheck: 'false' });
            } else {
                control = element('input', { ...attributes, autocomplete: 'off', spellcheck: 'false',
                    type: field.kind === 'checkbox' ? 'checkbox' : 'text',
                    inputmode: field.kind === 'number' ? 'numeric' : false });
            }
            const picker = field.kind === 'address' ? attachAddressPicker(control, {
                choices: () => hooks.addressChoices?.() ?? [],
                buttonLabel: `Pick a Node Interface for ${name()}` }) : null;
            let optionsKey;
            const show = () => {
                if (field.kind === 'select') {
                    const options = typeof field.options === 'function' ? field.options(row, parent) : field.options;
                    const key = JSON.stringify(options);
                    if (key !== optionsKey) {
                        control.replaceChildren(...options.map(([value, text]) => element('option', { value, text })));
                        control.value = format(read(list, field, row));
                        optionsKey = key;
                    }
                }
                if (field.kind === 'checkbox') control.checked = Boolean(read(list, field, row));
                else {
                    const value = format(read(list, field, row));
                    if (control.value !== value && control !== document.activeElement) control.value = value;
                }
                control.setAttribute('aria-label', name());
                picker?.rename(`Pick a Node Interface for ${name()}`);
                const hint = placeholderOf(list, field, row, parent);
                if (hint !== undefined) control.setAttribute('placeholder', hint);
            };
            show();
            const judge = () => messageFor(list, field, row, parent, rows);
            judged.set(control, judge);
            const edit = () => {
                write(list, field, row, field.kind === 'checkbox' ? control.checked : parse(control.value));
                refreshRow(true);
                rejudge();
                notify();
            };
            control.addEventListener('input', edit);
            if (field.kind === 'select') control.addEventListener('change', edit);
            control.addEventListener('blur', () => {
                const message = judge();
                if (message) hooks.showError(control, message);
            });
            const error = element('p', { id: `${id}-error`, class: 'field-error', hidden: true });
            return { field, control, error, show, node: picker ? picker.node : control };
        }

        function buildRow(row, index) {
            const parts = [];
            const childViews = [];
            const view = { row, index, parts, childViews };
            const refreshRow = normalize => {
                for (const part of parts) {
                    const applies = visible(part.field, row, parent);
                    part.wrapper.hidden = !applies;
                    if (normalize && !applies && 'hiddenValue' in part.field) {
                        write(list, part.field, row, part.field.hiddenValue);
                    }
                    part.show();
                }
                if (view.title) view.title.textContent = list.summary(row);
                view.describeMore?.();
                childViews.forEach(child => child.refresh(normalize));
            };
            view.refresh = refreshRow;
            const makeField = (field, inMore) => {
                const built = buildField(field, row, index, refreshRow);
                built.wrapper = asCards || inMore
                    ? element('div', { class: 'row-field' },
                        element('label', { for: built.control.id, text: labelOf(list, field, row, parent) }),
                        built.node, built.error)
                    : element('td', { 'data-field': field.key }, built.node, built.error);
                if (asCards || inMore) {
                    const label = built.wrapper.firstChild;
                    const showControl = built.show;
                    built.show = () => {
                        showControl();
                        label.textContent = labelOf(list, field, row, parent);
                        markRequired(label, field);
                    };
                }
                parts.push(built);
                return built;
            };
            const mainParts = list.fields.map(field => makeField(field, false));
            const moreParts = (list.more || []).map(field => makeField(field, true));
            const remove = element('button', { type: 'button', class: 'btn-secondary btn-small',
                'aria-label': `Remove ${noun(index)}`, text: asCards ? `Remove ${list.itemLabel}` : 'Remove' });
            remove.addEventListener('click', () => {
                rows.splice(index, 1);
                open.splice(index, 1);
                render();
                announce(`${noun(index)} removed`);
                const next = views[index];
                (next ? (asCards ? next.el.querySelector('summary') : next.parts[0].control)
                    : container.querySelector(':scope > .row-editor-add')).focus();
                notify();
            });
            view.remove = remove;
            const moreChildren = (list.children || []).filter(child => child.behindMore);
            let moreRegion, moreToggle;
            if (moreParts.length || moreChildren.length) {
                const regionId = `${prefix}-${index}-more`;
                moreToggle = element('button', { type: 'button', class: 'btn-secondary btn-small row-more-toggle',
                    'aria-expanded': 'false', 'aria-controls': regionId });
                moreRegion = element('div', { id: regionId, class: 'row-more', hidden: true },
                    element('div', { class: 'row-card-grid' }, ...moreParts.map(part => part.wrapper)));
                moreToggle.addEventListener('click', () => {
                    moreRegion.hidden = !moreRegion.hidden;
                    moreToggle.setAttribute('aria-expanded', String(!moreRegion.hidden));
                });
                view.describeMore = () => {
                    moreToggle.hidden = !moreParts.some(part => visible(part.field, row, parent))
                        && !moreChildren.some(child => visible(child, null, row, uiOf(list, row)));
                    const set = moreParts.filter(part => {
                        const value = read(list, part.field, row);
                        return Array.isArray(value) ? value.length : !blankValue(value) && value !== false;
                    }).map(part => labelOf(list, part.field, row, parent));
                    set.push(...moreChildren.filter(child => row[child.key]?.length).map(child => child.itemLabel));
                    moreToggle.textContent = set.length ? `More · ${set.join(', ')}` : 'More';
                    moreToggle.setAttribute('aria-label', `More settings for ${noun(index)}`
                        + (set.length ? `: ${set.join(', ')} set` : ''));
                };
            }
            if (!asCards) {
                const actions = [remove];
                if (moreToggle) {
                    actions.push(moreToggle);
                    view.extra = element('tr', { class: 'row-more-row' },
                        element('td', { colspan: mainParts.length + 1 }, moreRegion));
                }
                view.el = element('tr', {}, ...mainParts.map(part => part.wrapper),
                    element('td', { class: 'row-actions' }, ...actions));
            } else {
                view.title = element('span', { class: 'row-card-title' });
                const grid = element('div', { class: 'row-card-grid' }, ...mainParts.map(part => part.wrapper));
                const body = element('div', { class: 'row-card-body' }, grid);
                for (const child of list.children || []) {
                    // Optional child lists stay absent until the Instructor edits them.
                    let items = row[child.key] || [];
                    let built;
                    const childHost = element('div');
                    const mount = () => {
                        built = buildList(child, items, `${prefix}-${index}-${child.key}`,
                            `${noun(index)} `, row, () => {
                                row[child.key] = items;
                                refreshRow(false);
                                notify();
                            });
                        childHost.replaceChildren(built.el);
                    };
                    mount();
                    childViews.push({ refresh: normalize => {
                        if (row[child.key] && row[child.key] !== items) {
                            items = row[child.key];
                            mount();
                        }
                        childHost.hidden = !visible(child, null, row, uiOf(list, row));
                        built.refresh(normalize);
                    } });
                    (child.behindMore ? moreRegion : body).append(childHost);
                }
                if (moreToggle) body.append(moreToggle, moreRegion);
                body.append(element('div', { class: 'row-card-actions' }, remove));
                view.el = element('details', { class: 'row-card', open: open[index] },
                    element('summary', {},
                        element('span', { class: 'sr-only', text: `${noun(index)}: ` }), view.title,
                        element('span', { class: 'row-card-error', role: 'img', 'aria-label': 'has an error',
                            text: '⚠' })),
                    body);
                view.el.addEventListener('toggle', () => { open[index] = view.el.open; });
            }
            refreshRow(false);
            return view;
        }

        function headerRow() {
            headers = list.fields.map(field => ({ field,
                cell: element('th', { scope: 'col', 'data-field': field.key }) }));
            return element('tr', {}, ...headers.map(header => header.cell),
                element('th', { scope: 'col' }, element('span', { class: 'sr-only', text: 'Remove' })));
        }

        function refreshHeaders() {
            for (const { field, cell } of headers) {
                cell.hidden = !visible(field, null, parent);
                cell.textContent = labelOf(list, field, null, parent);
                markRequired(cell, field);
            }
        }

        function render() {
            views = rows.map(buildRow);
            const add = element('button', { type: 'button', class: 'btn-secondary row-editor-add',
                'aria-label': labelPrefix ? `Add ${list.itemLabel} to ${labelPrefix.trim()}` : false,
                text: `Add ${list.itemLabel}` });
            add.addEventListener('click', () => {
                rows.push(list.blank(parent));
                open.push(true);
                render();
                announce(`${noun(rows.length - 1)} added`);
                views[rows.length - 1].parts[0].control.focus();
                notify();
            });
            add.disabled = list.max !== undefined && rows.length >= list.max;
            if (asCards) container.replaceChildren(...views.map(view => view.el), add);
            else {
                container.replaceChildren(
                    element('div', { class: 'row-table-scroll' },
                        element('table', { class: 'row-editor-table' },
                            element('caption', { class: 'sr-only', text: `${labelPrefix}${list.itemLabel}s` }),
                            ...(rows.length ? [element('thead', {}, headerRow())] : []),
                            element('tbody', {}, ...views.flatMap(view => [view.el, view.extra].filter(Boolean))))),
                    add);
                refreshHeaders();
            }
        }

        render();
        return {
            el: container,
            refresh: normalize => { views.forEach(view => view.refresh(normalize)); if (!asCards) refreshHeaders(); },
        };
    }

    return {
        rejudge,
        setRows(items, parent = null) {
            judged.clear();
            const rows = spec.itemToRow ? items.map(spec.itemToRow) : items;
            // A list of plain values is written back whole after each edit.
            const changed = () => {
                if (spec.rowToItem) items.splice(0, items.length, ...rows.map(spec.rowToItem));
                hooks.onChange();
            };
            host.replaceChildren(buildList(spec, rows, spec.name, '', parent, changed).el);
        },
    };
}
