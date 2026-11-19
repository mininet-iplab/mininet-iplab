// The NAT Table pane: what each translating Router remembers about the
// connections it rewrote — one entry per connection, its inside pair, the
// outside pair substituted for it, its state, and how long the entry has
// left.
//
// It is a sibling of the Capture panes in Packet Analysis rather than a mode
// inside one: it lists connections, not packets; it polls rather than streams; and
// it has no field tree. It exists only in a Lab where a Router translates.
//
// NAT's defining property is that it is stateful, and state is invisible on
// the wire. The lifetime column is the lesson: it counts down as the table
// refreshes, and an entry that reaches zero is forgotten.

import { appState } from '../state/app-state.js';
import { getNatTable } from '../services/api.js';

// How often the table is read while it is on show.
export const REFRESH_MS = 2000;

let paneSerial = 0;

/**
 * An address and its port (or ICMP identifier), as a Learner reads them.
 *
 * @param {string} address - An IPv4 or IPv6 address.
 * @param {string|null} port - A port or ICMP identifier, or null.
 * @param {string|null} kind - 'port', 'id', or null.
 * @returns {string}
 */
export function formatPair(address, port, kind) {
    if (port == null || port === '') return address;
    if (kind === 'id') return `${address} id ${port}`;
    return address.includes(':') ? `[${address}]:${port}` : `${address}:${port}`;
}

/**
 * Seconds left, in the largest two units that matter.
 *
 * @param {number} seconds - The remaining lifetime.
 * @returns {string}
 */
export function formatLifetime(seconds) {
    const s = Math.max(0, Math.floor(seconds));
    if (s < 60) return `${s} s`;
    if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
    return `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`;
}

/**
 * A rule of the Router's NAT as a Learner reads it. A rule this view does not
 * recognise is shown as iptables printed it.
 *
 * @param {object} rule - One rule from the NAT Table payload.
 * @returns {string}
 */
function describeRule(rule) {
    if (rule.kind === 'masquerade') return `Masquerade out of ${rule.interface}`;
    if (rule.kind === 'static') {
        return `Static mapping of ${rule.inside_address} to ${rule.outside_address} on ${rule.interface}`;
    }
    if (rule.kind === 'port_forward') {
        const on = rule.interface ? ` on ${rule.interface}` : '';
        const hairpin = rule.hairpin ? ', with hairpin' : '';
        return `${rule.protocol.toUpperCase()} ${formatPair(rule.outside_address, rule.outside_port, null)}`
            + ` forwards to ${formatPair(rule.inside_address, rule.inside_port, null)}${on}${hairpin}`;
    }
    return rule.raw;
}

// A connection's identity across reads: what does not change while it lives.
function rowKey(t) {
    return [t.kind, t.protocol, t.inside_address, t.inside_port, t.destination, t.destination_port].join('|');
}

const PAUSE_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="9" y1="5" x2="9" y2="19"></line><line x1="15" y1="5" x2="15" y2="19"></line></svg>';
const RESUME_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="7,5 19,12 7,19"></polygon></svg>';

/** The NAT Table tab and pane, one per Packet Analysis. */
export class NatTablePane {
    constructor(analysis) {
        this.analysis = analysis;
        this.paused = false;
        this.timer = null;
        this.inFlight = false;
        this.tables = new Map();
        this.lastStatus = '';
        // True until a read succeeds, and again after one fails: the next
        // good read then clears the error rather than announcing a change.
        this.failed = true;

        const id = `nat-table-pane-${++paneSerial}`;
        const template = document.getElementById('nat-table-pane-template');
        this.el = template.content.firstElementChild.cloneNode(true);
        this.el.id = id;
        this.el.setAttribute('aria-labelledby', `${id}-tab`);
        this.header = this.el.querySelector(':scope > .packet-analysis-header');
        this.stateEl = this.el.querySelector('.nat-table-state');
        this.statusEl = this.el.querySelector('.nat-table-status');
        this.errorEl = this.el.querySelector('.nat-table-error');
        this.tablesEl = this.el.querySelector('.nat-table-tables');

        // The explanation is there on request, not in the way: a disclosure.
        const note = this.el.querySelector('.nat-table-note');
        note.id = `${id}-note`;
        this.infoButton = this.el.querySelector('.nat-table-info');
        this.infoButton.setAttribute('aria-controls', note.id);
        this.infoButton.addEventListener('click', () => {
            const open = this.infoButton.getAttribute('aria-expanded') !== 'true';
            this.infoButton.setAttribute('aria-expanded', String(open));
            note.hidden = !open;
        });

        this.pauseButton = this.el.querySelector('.nat-table-pause');
        this.pauseButton.addEventListener('click', () => this.setPaused(!this.paused));
        this.el.querySelector('.nat-table-close').addEventListener('click', () => this.analysis.close(this));
        this.showState();

        this.tab = document.createElement('button');
        this.tab.type = 'button';
        this.tab.id = `${id}-tab`;
        this.tab.className = 'capture-tab';
        this.tab.setAttribute('role', 'tab');
        this.tab.setAttribute('aria-controls', id);
        this.tab.textContent = 'NAT Table';
        this.tab.addEventListener('click', () => this.analysis.select(this, false));
        this.tab.addEventListener('keydown', (e) => this.analysis.onTabKey(e, this));
    }

    matches() {
        return false;
    }

    /** Start reading the table, now and every REFRESH_MS while on show. */
    connect() {
        this.timer = setInterval(() => this.refresh(), REFRESH_MS);
        this.refresh();
    }

    /** Stop reading: the pane is closing or its Lab has gone. */
    disconnect() {
        if (this.timer) clearInterval(this.timer);
        this.timer = null;
    }

    // Updates stop while paused, and while nobody could see them.
    shouldRefresh() {
        return !this.paused && !this.el.hidden && this.analysis.isOpen() && !document.hidden;
    }

    // The button keeps one name, "Pause updates"; aria-pressed says whether
    // it is in force. Only its icon and tooltip change.
    showState() {
        this.pauseButton.setAttribute('aria-pressed', String(this.paused));
        this.pauseButton.innerHTML = this.paused ? RESUME_ICON : PAUSE_ICON;
        this.pauseButton.title = this.paused ? 'Resume updates' : 'Pause updates';
        this.stateEl.textContent = this.paused ? 'Paused' : `Refreshing every ${REFRESH_MS / 1000} s`;
        this.stateEl.classList.toggle('nat-table-paused', this.paused);
    }

    // An auto-updating region must be able to stand still (WCAG 2.2.2).
    setPaused(paused) {
        this.paused = paused;
        this.showState();
        this.announce(paused ? 'Updates paused.' : 'Updates resumed.');
        if (!paused) this.refresh();
    }

    async refresh() {
        if (!this.shouldRefresh() || this.inFlight) return;
        this.inFlight = true;
        try {
            const response = await getNatTable();
            let body = null;
            try {
                body = await response.json();
            } catch (e) { /* not JSON */ }
            if (!response.ok) {
                this.fail(`${(body && body.detail) || `the server answered ${response.status}`}.`);
                return;
            }
            if (this.paused || !this.timer) return;
            this.render(body.tables || []);
        } catch (e) {
            this.fail('the server did not answer.');
        } finally {
            this.inFlight = false;
        }
    }

    render(tables) {
        const changes = [];
        const seen = new Set();
        tables.forEach(table => {
            seen.add(table.node);
            let view = this.tables.get(table.node);
            if (!view) {
                view = this.buildTable(table);
                this.tables.set(table.node, view);
            }
            this.tablesEl.appendChild(view.section);
            const before = view.count;
            this.fill(view, table.entries || []);
            this.fillRules(view, table.rules || []);
            if (before !== null && before !== view.count) {
                changes.push(`${table.node} now has ${view.count} ${view.count === 1 ? 'entry' : 'entries'}.`);
            }
        });
        for (const [node, view] of this.tables) {
            if (!seen.has(node)) {
                view.section.remove();
                this.tables.delete(node);
            }
        }
        if (this.failed) {
            this.failed = false;
            this.errorEl.hidden = true;
            this.errorEl.textContent = '';
        }
        // Only a change in what is held is announced; the countdown is not,
        // or the status would speak every two seconds.
        if (changes.length) this.announce(changes.join(' '));
    }

    fail(reason) {
        this.failed = true;
        this.errorEl.hidden = false;
        this.errorEl.textContent = `Cannot read the NAT Table: ${reason}`;
    }

    buildTable(table) {
        const template = document.getElementById('nat-table-table-template');
        const section = template.content.firstElementChild.cloneNode(true);
        const headingId = `${this.el.id}-${table.node}`;
        const heading = section.querySelector('.nat-table-heading');
        heading.id = headingId;
        heading.textContent = `${table.node} · ${table.interface}`;
        section.setAttribute('aria-labelledby', headingId);
        section.dataset.node = table.node;
        section.querySelector('caption').textContent =
            `NAT Table of ${table.node}, which translates on ${table.interface}: one entry per connection. Expires in counts down as the table refreshes.`;
        const rulesHeading = section.querySelector('.nat-table-rules-heading');
        rulesHeading.id = `${headingId}-rules`;
        const rules = section.querySelector('.nat-table-rules');
        rules.setAttribute('aria-labelledby', rulesHeading.id);
        return {
            section,
            rows: section.querySelector('tbody'),
            table: section.querySelector('table'),
            empty: section.querySelector('.nat-table-empty'),
            rules,
            rulesEmpty: section.querySelector('.nat-table-rules-empty'),
            rulesKey: null,
            byKey: new Map(),
            count: null,
        };
    }

    // The rules strip is read, not spoken: it is redrawn only when what it
    // says has changed, and its refresh is never announced.
    fillRules(view, rules) {
        const labels = rules.map(describeRule);
        const key = labels.join('\n');
        if (view.rulesKey !== key) {
            view.rulesKey = key;
            view.rules.replaceChildren(...labels.map(label => {
                const item = document.createElement('li');
                item.textContent = label;
                return item;
            }));
        }
        view.rules.hidden = labels.length === 0;
        view.rulesEmpty.hidden = labels.length !== 0;
    }

    // Rows are updated in place, keyed by connection, so a screen reader
    // reading the table is not thrown back to its start on every refresh.
    fill(view, entries) {
        const keep = new Set();
        entries.forEach(e => {
            const key = rowKey(e);
            keep.add(key);
            let row = view.byKey.get(key);
            if (!row) {
                row = document.createElement('tr');
                for (let i = 0; i < 6; i += 1) row.appendChild(document.createElement('td'));
                row.children[3].className = 'nat-table-lifetime';
                view.byKey.set(key, row);
            }
            const cells = row.children;
            // A port forward reads the other way round: the client is the far end.
            const inbound = e.kind === 'destination';
            const values = [
                e.protocol.toUpperCase() + (inbound ? ', inbound' : ''),
                formatPair(e.inside_address, e.inside_port, e.port_kind),
                formatPair(e.outside_address, e.outside_port, e.port_kind),
                formatLifetime(e.lifetime),
                e.state,
                formatPair(e.destination, e.destination_port, e.port_kind) + (inbound ? ' (client)' : ''),
            ];
            values.forEach((value, i) => {
                if (cells[i].textContent !== value) cells[i].textContent = value;
            });
            row.dataset.lifetime = String(e.lifetime);
            view.rows.appendChild(row);
        });
        for (const [key, row] of view.byKey) {
            if (!keep.has(key)) {
                row.remove();
                view.byKey.delete(key);
            }
        }
        view.count = entries.length;
        view.table.hidden = view.count === 0;
        view.empty.hidden = view.count !== 0;
    }

    // Spoken, not shown: the tables themselves show what changed.
    announce(text) {
        if (text === this.lastStatus) return;
        this.lastStatus = text;
        this.statusEl.textContent = text;
    }
}

/**
 * Whether a Node translates addresses for its Lab, by the Topology payload.
 *
 * @param {string} nodeId - The Node's name.
 * @returns {boolean}
 */
export function isTranslator(nodeId) {
    const translators = (appState.currentTopology && appState.currentTopology.translators) || [];
    return translators.some(t => t.node === nodeId);
}
