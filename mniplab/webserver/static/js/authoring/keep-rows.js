// One review row per Node or Service, shared by Keep, Stop and Publish.
import { escapeHtml } from '../utils/dom.js';

function diffLine(line) {
    const kind = { '+': ['diff-added', 'Added'], '-': ['diff-removed', 'Removed'] }[line[0]];
    const text = escapeHtml(line.slice(1)) || ' ';
    return kind
        ? `<span class="${kind[0]}"><span class="sr-only">${kind[1]}: </span>${line[0]} ${text}</span>`
        : `<span>  ${text}</span>`;
}

const text = value => (value === null || value === undefined || value === '' ? '' : String(value));
const list = values => (values || []).join(', ');

// Rows present on both sides, one per field; a field is left out when neither
// side uses it.
function fieldRows(fields, before, after) {
    return fields
        .map(([label, read, always = true]) => [label, text(read(before)), text(read(after)), always])
        .filter(([, old, now, always]) => always || old || now)
        .map(([label, old, now]) => ({ label, old, now }));
}

// Rows for a list keyed by identity: only the items that differ.
function keyedRows(label, keyOf, describe, before, after) {
    const group = items => {
        const map = new Map();
        for (const item of items || []) {
            const key = keyOf(item);
            map.set(key, [...(map.get(key) || []), describe(item)]);
        }
        return map;
    };
    const old = group(before);
    const now = group(after);
    return [...new Set([...old.keys(), ...now.keys()])]
        .map(key => ({ label: label(key), old: (old.get(key) || []).join(', '),
            now: (now.get(key) || []).join(', ') }))
        .filter(row => row.old !== row.now);
}

const VLAN_FIELDS = [
    ['Mode', value => value?.mode || 'none'],
    ['Access VLAN', value => (value?.mode === 'access' ? value.vlan : ''), false],
    ['Trunk VLANs', value => (value?.mode === 'trunk' ? list(value.trunks) : ''), false],
    ['Native VLAN', value => (value?.mode === 'trunk' ? value.native : ''), false],
];

const CONDITION_FIELDS = [
    ['Status', value => value.status],
    ['Delay', value => value.delay],
    ['Jitter', value => value.jitter],
    ['Loss', value => value.loss],
    ['Bandwidth', value => (value.bandwidth === null ? '' : `${value.bandwidth} Mbps`)],
    ['Scheduler', value => value.scheduler],
];

const POOL_FIELDS = [
    ['Subnet', value => value.cidr],
    ['Range start', value => value.start],
    ['Range end', value => value.end],
    ['Lease time', value => (value.lease_time === null ? '' : `${value.lease_time} s`)],
    ['Preferred lifetime', value => (value.preferred_lifetime === null
        || value.preferred_lifetime === undefined ? '' : `${value.preferred_lifetime} s`), false],
    ['Router', value => value.router],
    ['DNS server', value => value.dns_server],
];



// Each kind's rows: what the Instructor would compare, field by field.
const ROWS = {
    switch_vlan: (before, after) => fieldRows(VLAN_FIELDS, before, after),
    link_condition: (before, after) => fieldRows(CONDITION_FIELDS, before, after),
    dhcp_pool: (before, after) => [
        ...fieldRows(POOL_FIELDS, before, after),
        ...keyedRows(key => `Reservation ${key}`, item => item.mac || item.duid,
            item => [item.ip, item.hostname].filter(Boolean).join(' · '),
            before.reservations, after.reservations),
    ],
    dns_records: (before, after) => keyedRows(key => key, record => `${record.name} ${record.type}`,
        record => `${record.rdata} (TTL ${record.ttl})`, before, after),
    resolver_acl: (before, after) => {
        const rows = [];
        for (let index = 0; index < Math.max(before.length, after.length); index += 1) {
            const describe = rule => (rule ? `${rule.subnet} ${rule.action}` : '');
            rows.push({ label: `Rule ${index + 1}`, old: describe(before[index]), now: describe(after[index]) });
        }
        return rows.filter(row => row.old !== row.now);
    },
    // Each ROA or ASPA line is its own identity, so a line is only ever Added or Removed.
    rpki_objects: (before, after) => [
        ...keyedRows(() => 'ROA', line => line, line => line, before.roas, after.roas),
        ...keyedRows(() => 'ASPA', line => line, line => line, before.aspas, after.aspas),
    ],
};

// What changed is said in words, not colour alone.
function afterCell(row) {
    if (row.old === row.now) return escapeHtml(row.now || 'none');
    const mark = !row.old ? 'Added' : !row.now ? 'Removed' : 'Changed';
    return `<span class="authoring-keep-mark">${mark}</span>${row.now ? ` ${escapeHtml(row.now)}` : ''}`;
}

function featureTable(feature) {
    const rows = ROWS[feature.kind]?.(feature.before, feature.after) || [];
    return `<table class="authoring-keep-table">
        <thead><tr><th scope="col">Field</th><th scope="col">Before</th><th scope="col">After</th></tr></thead>
        <tbody>${rows.map(row => `<tr><th scope="row">${escapeHtml(row.label)}</th>
            <td>${escapeHtml(row.old || 'none')}</td><td>${afterCell(row)}</td></tr>`).join('')}</tbody>
    </table>`;
}

const FEATURE_NAMES = { switch_vlan: 'Switch', link_condition: 'Link',
    dhcp_pool: 'DHCP', dns_records: 'Primary Zone Records', resolver_acl: 'Resolver Access Control',
    rpki_objects: 'RPKI objects' };

/** Count changed Router and Service rows, including changes that need script edits. */
export function keepRowCount({ routers, features }) {
    return routers.filter(router => router.sections.length).length
        + new Set(features.map(feature => `${feature.kind}:${feature.node_id || feature.id}`)).size;
}

/**
 * Render complete Node and Service choices with folded changes.
 *
 * Each row has one Tab stop; arrows move between its checkbox, Show changes
 * and (when shown) Router sections. Diffs have no separate Tab stops.
 *
 * @param {HTMLElement} container Where the rows are shown.
 * @param {{routers: object[], features: object[]}} changes The reviewed changes.
 * @returns {object} Row count, selection payload and Select all operation.
 */
export function renderKeepRows(container, { routers, features }) {
    const rows = routers.map(router => ({ name: `Router ${router.name}`, router,
        summary: `${router.sections.map(section => section.name).join(', ')} · ${router.added} added · ${router.removed} removed`,
        features: [], notKeepable: router.not_keepable }));
    const grouped = new Map();
    for (const feature of features) {
        const key = `${feature.kind}:${feature.node_id || feature.id}`;
        if (!grouped.has(key)) {
            const name = FEATURE_NAMES[feature.kind] || feature.kind;
            const owner = feature.node_name || feature.name;
            const row = { name: `${name}${feature.node_name && feature.kind !== 'switch_vlan' ? ' on' : ''} ${owner}`,
                summary: '', features: [], notKeepable: feature.not_keepable };
            grouped.set(key, row);
            rows.push(row);
        }
        grouped.get(key).features.push(feature);
    }
    container.innerHTML = rows.map((row, index) => {
        const id = `${container.id}-row-${index}`;
        const router = row.router;
        const description = router ? row.summary : row.features.map(feature => feature.name).join(', ');
        const whole = router?.sections[0]?.whole;
        return `<fieldset class="authoring-keep-row" data-row="${index}" aria-describedby="keep-row-keys">
            <legend>${escapeHtml(row.name)}</legend>
            <label class="authoring-choice" for="${id}-choice"><input type="checkbox" id="${id}-choice"
                data-row-choice ${row.notKeepable ? 'disabled' : ''} aria-describedby="${id}-summary"> Keep ${escapeHtml(row.name)}</label>
            <p id="${id}-summary" class="authoring-hint">${escapeHtml(description)}${row.notKeepable ? ` · ${escapeHtml(row.notKeepable)}` : ''}</p>
            <button type="button" class="authoring-link-button" data-show-changes tabindex="${row.notKeepable ? '0' : '-1'}"
                aria-label="Show changes on ${escapeHtml(router?.name || row.name)}" aria-controls="${id}-changes"
                aria-expanded="false">Show changes</button>
            <div id="${id}-changes" class="authoring-keep-region" role="region" hidden
                aria-label="Changes on ${escapeHtml(router?.name || row.name)}">
                ${whole ? '<p>This configuration can only be kept whole. Edit its FRR text afterwards.</p>' : ''}
                ${router ? router.sections.map(section => `<div>
                    ${router.sections.length > 1 && !row.notKeepable ? `<label class="authoring-choice" for="${id}-section-${section.id}">
                        <input type="checkbox" id="${id}-section-${section.id}" tabindex="-1" data-section="${section.id}">
                        Keep ${escapeHtml(section.name)} on ${escapeHtml(router.name)}</label>`
                        : `<p>${escapeHtml(section.name)}</p>`}
                    <pre class="authoring-diff">${section.lines.map(diffLine).join('')}</pre>
                    </div>`).join('') : row.features.map(feature => `<p>${escapeHtml(feature.name)}</p>${featureTable(feature)}`).join('')}
            </div>
        </fieldset>`;
    }).join('') || '<p>No supported changes to Keep.</p>';

    const groups = [...container.querySelectorAll('[data-row]')];
    function selected() {
        const selections = [];
        const takenFeatures = [];
        let selectedCount = 0;
        let completeCount = 0;
        for (const [index, row] of rows.entries()) {
            const group = groups[index];
            const box = group.querySelector('[data-row-choice]');
            if (box.disabled || (!box.checked && !box.indeterminate)) continue;
            selectedCount += 1;
            if (box.checked && !box.indeterminate) completeCount += 1;
            if (row.router) {
                const sections = row.router.sections.length === 1 ? [row.router.sections[0].id]
                    : [...group.querySelectorAll('[data-section]:checked')].map(item => Number(item.dataset.section));
                selections.push({ node_id: row.router.node_id, digest: row.router.digest, sections });
            }
            takenFeatures.push(...row.features.map(feature => ({ id: feature.id, digest: feature.digest })));
        }
        return { selections, features: takenFeatures, selectedCount, completeCount };
    }
    for (const group of groups) {
        const box = group.querySelector('[data-row-choice]');
        const sections = [...group.querySelectorAll('[data-section]')];
        box.addEventListener('change', () => {
            box.removeAttribute('aria-checked');
            for (const section of sections) section.checked = box.checked;
        });
        for (const section of sections) section.addEventListener('change', () => {
            box.checked = sections.every(item => item.checked);
            box.indeterminate = !box.checked && sections.some(item => item.checked);
            if (box.indeterminate) box.setAttribute('aria-checked', 'mixed');
            else box.removeAttribute('aria-checked');
        });
        const show = group.querySelector('[data-show-changes]');
        show.addEventListener('click', () => {
            const panel = group.querySelector('[role="region"]');
            panel.hidden = !panel.hidden;
            show.setAttribute('aria-expanded', String(!panel.hidden));
        });
        group.addEventListener('focusin', event => {
            for (const control of group.querySelectorAll('input, button')) {
                control.tabIndex = control === event.target ? 0 : -1;
            }
        });
        group.addEventListener('keydown', event => {
            if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
            const controls = [...group.querySelectorAll('input, button')].filter(item => !item.disabled && !item.closest('[hidden]'));
            const previous = controls.indexOf(document.activeElement);
            const direction = ['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1;
            const index = event.key === 'Home' ? 0 : event.key === 'End' ? controls.length - 1
                : (previous + direction + controls.length) % controls.length;
            event.preventDefault();
            controls[index]?.focus();
        });
    }
    return { count: rows.filter(row => !row.notKeepable).length, selected, selectAll(checked) {
        for (const group of groups) {
            if (group.querySelector('[data-row-choice]').disabled) continue;
            for (const box of group.querySelectorAll('input')) {
                box.checked = checked;
                box.indeterminate = false;
                box.removeAttribute('aria-checked');
            }
        }
    } };
}
