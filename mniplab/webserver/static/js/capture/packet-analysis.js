// The Packet Capture panes: decoded packets from one Interface each, streamed
// into the Packet Analysis pane, beside the Terminals rather than into a
// modal, so a Learner can run `dhclient` in a Terminal and watch the exchange
// appear.
//
// Up to two Captures run at once, as tabs in Packet Analysis: the number a
// before-and-after comparison across a Router needs. The server holds each
// viewer to the same two.
//
// A Capture is shared on the server: each pane is one viewer of it. Closing
// a pane on purpose says so, and the Capture stops unless someone else is
// watching; a reload does not, so the Capture runs on through the grace
// window and the reloaded page picks it up again from sessionStorage.

import { appState } from '../state/app-state.js';
import { getCapturePcap } from '../services/api.js';
import { parsePacket } from './pdml.js';
import { NatTablePane } from './nat-table.js';
import { layoutPanes, showPacketPane } from '../ui/pane-arrangement.js';

// Rows kept in a pane; older packets scroll out. The pcap holds them all.
const RING_MAX = 1000;

// Captures one browser may run at once.
export const MAX_CAPTURES = 2;

// The Node types whose Interfaces the Node menu offers: every Interface of
// a Router, Host or Speaker. A Switch is not here: its ports are offered
// from a Link only, one end at a time.
export const CAPTURABLE_TYPES = new Set(['router', 'host', 'bgp-speaker']);

// The Node types whose end of a Link the Link menu offers.
const LINK_END_TYPES = new Set([...CAPTURABLE_TYPES, 'switch']);

// The protocols the Lab Examples teach, each with the BPF expression it
// expands to. The expression is shown, not hidden behind the name: choosing
// a preset writes it into the filter box, which is how a Learner learns the
// syntax.
export const CAPTURE_PRESETS = [
    { name: 'All', filter: '' },
    { name: 'DHCP', filter: 'udp port 67 or udp port 68 or udp port 546 or udp port 547' },
    { name: 'DNS', filter: 'port 53' },
    { name: 'OSPF', filter: 'proto ospf' },
    { name: 'BGP', filter: 'tcp port 179' },
    { name: 'ICMP', filter: 'icmp or icmp6' },
    { name: 'ARP', filter: 'arp' },
];

const LIMIT_REASON = 'Two Captures are already open, the most at once. Stop one to open another.';

const STORAGE_PREFIX = 'mniplab-captures:';

let paneSerial = 0;

function storageKey() {
    return STORAGE_PREFIX + (appState.labKey || '-');
}

function remember(targets) {
    try {
        if (targets.length) {
            sessionStorage.setItem(storageKey(), JSON.stringify(targets));
        } else {
            sessionStorage.removeItem(storageKey());
        }
    } catch (e) {
        // Storage may be unavailable (private mode); a reload then loses the panes.
    }
}

function remembered() {
    try {
        const raw = sessionStorage.getItem(storageKey());
        const saved = raw ? JSON.parse(raw) : [];
        const list = Array.isArray(saved) ? saved : [saved];
        return list.filter(t => t && t.node && t.interface).slice(0, MAX_CAPTURES);
    } catch (e) {
        return [];
    }
}

function presetFor(filter) {
    return CAPTURE_PRESETS.find(p => p.filter === (filter || '').trim()) || null;
}

function describeFilter(filter) {
    const f = (filter || '').trim();
    if (!f) return 'no filter: every packet crossing the Interface';
    const preset = presetFor(f);
    return preset ? `${preset.name}: ${f}` : f;
}

// A packet in words, for its heading and its row's name: the Description
// column names only the message, the Protocol column beside it the rest.
function describePacket(summary) {
    return [summary.protocol, summary.description].filter(Boolean).join(' ');
}

function formatTime(summary) {
    const t = summary.relative_time;
    return typeof t === 'number' ? t.toFixed(3) : '';
}

// One field-tree node: a disclosure when it has children, a line when not.
function renderNode(node) {
    const hasBytes = typeof node.size === 'number' && node.size > 0 && typeof node.pos === 'number';
    let el;
    if (node.children.length) {
        el = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = node.label;
        el.appendChild(summary);
        const list = document.createElement('div');
        list.className = 'capture-tree-children';
        node.children.forEach(child => list.appendChild(renderNode(child)));
        el.appendChild(list);
    } else {
        el = document.createElement('div');
        el.className = 'capture-tree-leaf';
        el.textContent = node.label;
    }
    if (node.name) el.dataset.field = node.name;
    if (hasBytes) {
        el.dataset.pos = String(node.pos);
        el.dataset.size = String(node.size);
        el.title = node.size === 1
            ? `Byte ${node.pos}`
            : `Bytes ${node.pos}–${node.pos + node.size - 1}`;
    }
    return el;
}

// Open a Capture from a picker, or say in the picker why not.
function chooseTarget(node, intf, onChosen, message) {
    const analysis = appState.packetAnalysis;
    if (!analysis) return;
    const refusal = analysis.refusal(node, intf);
    if (refusal) {
        if (message) message.textContent = refusal;
        return;
    }
    if (onChosen) onChosen();
    analysis.open(node, intf);
}

function pickerButton(label, node, intf, onChosen, message) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn-secondary link-capture-end';
    button.dataset.node = node;
    button.dataset.interface = intf;
    button.textContent = label;
    button.addEventListener('click', () => chooseTarget(node, intf, onChosen, message));
    return button;
}

/**
 * Offer a Packet Capture on either end of a Link, named by its Interface.
 *
 * Rendered into the Packet Capture modal, opened from the Link menu. Each end
 * the server can capture gets its own button; choosing one closes the modal
 * and opens a pane.
 *
 * @param {HTMLElement} container - Where the buttons go; emptied first.
 * @param {object|null} link - The Topology payload's Link, or null.
 * @param {Function} onChosen - Called before the pane opens (closes the modal).
 * @param {HTMLElement} [message] - Where a refusal is announced.
 */
export function renderCaptureEnds(container, link, onChosen, message) {
    if (!container) return;
    container.textContent = '';
    if (message) message.textContent = '';
    if (!link) return;
    const ends = [
        { node: link.source, intf: link.source_intf },
        { node: link.target, intf: link.target_intf },
    ].map(end => ({
        id: typeof end.node === 'object' ? end.node.id : end.node,
        type: typeof end.node === 'object' ? end.node.type : null,
        intf: end.intf,
    }));
    let offered = 0;
    ends.forEach(end => {
        if (!end.intf || (end.type && !LINK_END_TYPES.has(end.type))) return;
        container.appendChild(pickerButton(`Capture on ${end.intf} (${end.id})`, end.id, end.intf, onChosen, message));
        offered += 1;
    });
    if (!offered) {
        const note = document.createElement('p');
        note.className = 'link-capture-none';
        note.textContent = 'Neither end of this Link is on a Router, Host, Speaker or Switch, so it cannot be captured here.';
        container.appendChild(note);
    }
}

/**
 * Offer a Packet Capture on any Interface of one Node, drawn or not.
 *
 * The Node menu's way in: it reaches Interfaces whose Link is not on the
 * Topology.
 *
 * @param {HTMLElement} container - Where the buttons go; emptied first.
 * @param {string} node - The Node's name.
 * @param {Array<object>} interfaces - The server's list for that Node.
 * @param {Function} onChosen - Called before the pane opens (closes the modal).
 * @param {HTMLElement} [message] - Where a refusal is announced.
 */
export function renderNodeInterfaces(container, node, interfaces, onChosen, message) {
    if (!container) return;
    container.textContent = '';
    if (message) message.textContent = '';
    if (!interfaces.length) {
        const note = document.createElement('p');
        note.className = 'link-capture-none';
        note.textContent = `${node} has no Interfaces to capture.`;
        container.appendChild(note);
        return;
    }
    interfaces.forEach(intf => {
        const where = intf.drawn ? `to ${intf.peer}` : 'Link not drawn';
        const addresses = intf.addresses && intf.addresses.length ? `, ${intf.addresses.join(', ')}` : '';
        const button = pickerButton(`Capture on ${intf.name} (${where}${addresses})`, node, intf.name, onChosen, message);
        if (!intf.drawn) button.dataset.undrawn = 'true';
        container.appendChild(button);
    });
}

/** One Capture's tab and pane: its packets, its filter, its pcap. */
class CapturePane {
    constructor(analysis, node, intf, filter) {
        this.analysis = analysis;
        this.target = { node, interface: intf, filter: (filter || '').trim() };
        this.ws = null;
        this.ended = false;
        this.restarting = false;
        this.packets = [];
        this.total = 0;
        this.selected = null;
        this.dropped = 0;

        const id = `capture-pane-${++paneSerial}`;
        const template = document.getElementById('capture-pane-template');
        this.el = template.content.firstElementChild.cloneNode(true);
        this.el.id = id;
        this.el.setAttribute('aria-labelledby', `${id}-tab`);
        this.header = this.el.querySelector(':scope > .packet-analysis-header');
        this.stateEl = this.el.querySelector('.packet-analysis-state');
        this.statusEl = this.el.querySelector('.packet-analysis-status');
        this.problemEl = this.el.querySelector('.packet-analysis-problem');
        this.dropEl = this.el.querySelector('.capture-drop-notice');
        this.countEl = this.el.querySelector('.packet-analysis-count');
        this.rows = this.el.querySelector('.capture-packet-rows');
        this.listRegion = this.el.querySelector('.capture-packet-list');
        this.detail = this.el.querySelector('.capture-packet-detail');
        this.form = this.el.querySelector('.capture-filter');
        this.input = this.el.querySelector('.capture-filter-input');
        this.presetsEl = this.el.querySelector('.capture-presets');

        const inputId = `${id}-filter`;
        this.input.id = inputId;
        this.el.querySelector('.capture-filter-label').setAttribute('for', inputId);

        // The explanations are there on request, not in the way: a disclosure.
        const note = this.el.querySelector('.capture-note');
        note.id = `${id}-note`;
        const info = this.el.querySelector('.capture-info');
        info.setAttribute('aria-controls', note.id);
        info.addEventListener('click', () => {
            const open = info.getAttribute('aria-expanded') !== 'true';
            info.setAttribute('aria-expanded', String(open));
            note.hidden = !open;
        });
        const pcapNote = this.el.querySelector('.capture-pcap-note');
        pcapNote.id = `${id}-pcap-note`;
        const download = this.el.querySelector('.capture-download');
        download.setAttribute('aria-describedby', pcapNote.id);
        download.addEventListener('click', () => this.download());
        this.el.querySelector('.packet-analysis-stop').addEventListener('click', () => this.analysis.close(this));

        this.presetButtons = CAPTURE_PRESETS.map(preset => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn-secondary capture-preset';
            button.textContent = preset.name;
            button.dataset.filter = preset.filter;
            button.title = preset.filter ? `${preset.name} expands to: ${preset.filter}` : 'No filter: every packet';
            button.addEventListener('click', () => this.applyFilter(preset.filter));
            this.presetsEl.appendChild(button);
            return button;
        });
        this.form.addEventListener('submit', (e) => {
            e.preventDefault();
            this.applyFilter(this.input.value);
        });

        this.rows.addEventListener('keydown', (e) => this.onRowKey(e));
        this.detail.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.selected) {
                e.preventDefault();
                this.deselect(true);
            }
        });

        this.tab = document.createElement('button');
        this.tab.type = 'button';
        this.tab.id = `${id}-tab`;
        this.tab.className = 'capture-tab';
        this.tab.setAttribute('role', 'tab');
        this.tab.setAttribute('aria-controls', id);
        this.tab.addEventListener('click', () => this.analysis.select(this, false));
        this.tab.addEventListener('keydown', (e) => this.analysis.onTabKey(e, this));

        this.showTarget();
        this.updateCount();
    }

    matches(node, intf) {
        return this.target.node === node && this.target.interface === intf;
    }

    showTarget() {
        const { node, interface: intf, filter } = this.target;
        this.tab.textContent = `${intf} (${node})`;
        this.input.value = filter;
        this.presetButtons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.filter === filter)));
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const params = new URLSearchParams({
            node: this.target.node, interface: this.target.interface, filter: this.target.filter,
        });
        const url = `${protocol}//${window.location.host}/ws/labs/${encodeURIComponent(appState.labKey || '-')}/capture?${params}`;
        const ws = new WebSocket(url);
        this.ws = ws;
        this.ended = false;
        ws.onmessage = (event) => {
            if (this.ws !== ws) return;
            let message;
            try {
                message = JSON.parse(event.data);
            } catch (e) {
                console.error('[PacketAnalysis] Unreadable message:', e);
                return;
            }
            this.handle(message);
        };
        ws.onclose = () => {
            if (this.ws !== ws) return;
            this.ws = null;
            if (!this.ended) {
                this.setState('Disconnected');
                this.setProblem('The connection to this capture was lost. Apply a filter to reconnect, or stop it and open it again.');
            }
        };
    }

    // `deliberate` tells the server the Learner closed the pane, so an
    // unwatched Capture stops at once rather than after the grace window.
    disconnect(deliberate) {
        const ws = this.ws;
        this.ws = null;
        if (!ws) return;
        if (deliberate && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'close' }));
        }
        ws.close();
    }

    // A filter is BPF, applied by the kernel as packets are captured, so a
    // change cannot reach back to packets already taken: the Capture
    // restarts, the list is emptied, and the pane says so.
    applyFilter(filter) {
        const next = (filter || '').trim();
        if (next === this.target.filter && this.ws && !this.ended) {
            this.setStatus(`Already capturing with ${describeFilter(next)}.`);
            return;
        }
        this.target.filter = next;
        this.analysis.save();
        this.showTarget();
        this.reset();
        this.restarting = true;
        this.setState('Restarting…');
        this.setProblem('');
        this.setStatus(`Restarting the capture with ${describeFilter(next)}…`);
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ended = false;
            this.ws.send(JSON.stringify({ type: 'filter', filter: next }));
        } else {
            this.disconnect(false);
            this.connect();
        }
    }

    async download() {
        const { node, interface: intf, filter } = this.target;
        let response;
        try {
            response = await getCapturePcap(node, intf, filter);
        } catch (e) {
            this.setProblem('The pcap could not be downloaded: the server did not answer.');
            return;
        }
        if (!response.ok) {
            let detail = `the server answered ${response.status}`;
            try {
                detail = (await response.json()).detail || detail;
            } catch (e) { /* not JSON */ }
            this.setProblem(`The pcap could not be downloaded: ${detail}.`);
            return;
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${appState.labKey || 'lab'}-${node}-${intf}.pcapng`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        this.setStatus(`Downloaded the pcap of ${intf}. It holds more than this list shows; the copy on the server is deleted when the Lab stops.`);
    }

    handle(message) {
        switch (message.type) {
            case 'capture': {
                const watching = `Capturing on ${message.interface} with ${describeFilter(message.filter)}.`;
                this.setState('Capturing');
                this.setProblem('');
                if (this.restarting) {
                    this.restarting = false;
                    this.setStatus(`Capture restarted with ${describeFilter(message.filter)}: the list was cleared.`);
                } else {
                    this.setStatus(watching);
                }
                if (message.rate_guard) this.guard(true);
                break;
            }
            case 'restarted':
                // Rows are cleared when the Learner applies the filter; the
                // 'capture' message that follows announces the restart.
                break;
            case 'packet':
                this.addPacket(message);
                break;
            case 'rate_guard':
                this.guard(message.active);
                break;
            case 'dropped':
                this.dropped += message.count;
                this.dropEl.hidden = false;
                this.dropEl.textContent = `${this.dropped} packet${this.dropped === 1 ? ' was' : 's were'} not shown because they arrived faster than this pane could take them. The pcap still has them.`;
                break;
            case 'ended':
                this.ended = true;
                this.restarting = false;
                this.setState('Ended');
                this.setProblem(message.filter_error
                    ? `${message.reason}. Nothing is being captured; correct the filter and apply it again.`
                    : `Capture ended: ${message.reason}.`);
                break;
            case 'error':
                this.ended = true;
                this.restarting = false;
                this.setState('Stopped');
                this.setProblem(`Cannot capture: ${message.message}`);
                break;
            default:
                break;
        }
    }

    reset() {
        this.packets = [];
        this.total = 0;
        this.selected = null;
        this.dropped = 0;
        this.dropEl.hidden = true;
        this.dropEl.textContent = '';
        this.rows.textContent = '';
        this.updateCount();
        this.detail.textContent = '';
        this.detail.hidden = true;
    }

    addPacket(message) {
        const summary = message.summary || {};
        const tree = message.pdml ? parsePacket(message.pdml) : null;
        const entry = { summary, tree, row: null };

        const region = this.listRegion;
        const atBottom = region.scrollHeight - region.scrollTop - region.clientHeight < 24;

        const row = document.createElement('tr');
        const select = document.createElement('button');
        select.type = 'button';
        select.className = 'capture-row-select';
        select.textContent = summary.number == null ? '' : String(summary.number);
        select.setAttribute('aria-pressed', 'false');
        select.setAttribute('aria-label', `Packet ${summary.number ?? ''}: ${describePacket(summary)}`);
        select.tabIndex = this.packets.length === 0 ? 0 : -1;
        const first = document.createElement('td');
        first.appendChild(select);
        row.appendChild(first);
        for (const value of [formatTime(summary), summary.source, summary.destination, summary.protocol, summary.description]) {
            const cell = document.createElement('td');
            cell.textContent = value == null ? '' : value;
            row.appendChild(cell);
        }
        row.addEventListener('click', () => this.select(entry, true));
        entry.row = row;

        this.rows.appendChild(row);
        this.packets.push(entry);
        this.total += 1;
        while (this.packets.length > RING_MAX) {
            const old = this.packets.shift();
            if (old === this.selected) this.deselect(false);
            old.row.remove();
        }
        if (!this.rows.querySelector('.capture-row-select[tabindex="0"]') && this.packets.length) {
            this.packets[0].row.querySelector('.capture-row-select').tabIndex = 0;
        }
        this.updateCount();
        if (atBottom) region.scrollTop = region.scrollHeight;
    }

    select(entry, focus) {
        if (this.selected && this.selected.row) {
            const previous = this.selected.row.querySelector('.capture-row-select');
            previous.setAttribute('aria-pressed', 'false');
            previous.tabIndex = -1;
            this.selected.row.classList.remove('selected');
        }
        this.rows.querySelectorAll('.capture-row-select[tabindex="0"]').forEach(b => { b.tabIndex = -1; });
        this.selected = entry;
        const button = entry.row.querySelector('.capture-row-select');
        button.setAttribute('aria-pressed', 'true');
        button.tabIndex = 0;
        entry.row.classList.add('selected');
        if (focus) button.focus();
        this.renderDetail(entry);
        this.detail.hidden = false;
    }

    // Hide the fields, as Wireshark's detail pane empties when nothing is
    // selected; the packet list takes the whole pane again. The row keeps
    // its place in the Tab order so focus can return to it.
    deselect(focusRow) {
        const entry = this.selected;
        this.selected = null;
        this.detail.hidden = true;
        this.detail.textContent = '';
        if (!entry || !entry.row) return;
        const button = entry.row.querySelector('.capture-row-select');
        button.setAttribute('aria-pressed', 'false');
        entry.row.classList.remove('selected');
        if (focusRow) button.focus();
    }

    renderDetail(entry) {
        this.detail.textContent = '';
        const header = document.createElement('div');
        header.className = 'capture-detail-header';
        const heading = document.createElement('h3');
        heading.className = 'capture-detail-heading';
        heading.textContent = `Packet ${entry.summary.number ?? ''}: ${describePacket(entry.summary)}`;
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'btn-icon capture-detail-close';
        close.setAttribute('aria-label', 'Hide packet fields');
        close.title = 'Hide packet fields (Escape)';
        close.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';
        close.addEventListener('click', () => this.deselect(true));
        header.appendChild(heading);
        header.appendChild(close);
        this.detail.appendChild(header);
        if (!entry.tree) {
            const note = document.createElement('p');
            note.className = 'capture-detail-empty';
            note.textContent = 'This packet arrived while the rate guard was on, so only its summary was sent.';
            this.detail.appendChild(note);
            return;
        }
        const tree = document.createElement('div');
        tree.className = 'capture-tree';
        entry.tree.protocols.forEach(proto => tree.appendChild(renderNode(proto)));
        this.detail.appendChild(tree);
    }

    // Arrow keys rove between packets, as a listbox would; the Tab key leaves
    // the list in one press, and Escape hides the selected packet's fields.
    onRowKey(event) {
        if (event.key === 'Escape' && this.selected) {
            event.preventDefault();
            this.deselect(true);
            return;
        }
        const keys = ['ArrowUp', 'ArrowDown', 'Home', 'End'];
        if (!keys.includes(event.key) || !this.packets.length) return;
        event.preventDefault();
        const current = this.packets.indexOf(this.selected);
        let next;
        if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = this.packets.length - 1;
        else if (current < 0) next = 0;
        else next = Math.max(0, Math.min(this.packets.length - 1, current + (event.key === 'ArrowUp' ? -1 : 1)));
        this.select(this.packets[next], true);
        this.packets[next].row.scrollIntoView({ block: 'nearest' });
    }

    // How many packets the list holds, and — once older ones have scrolled
    // out — how many this pane has seen, which the pcap still holds.
    updateCount() {
        const n = this.packets.length;
        this.countEl.textContent = this.total > n
            ? `last ${n} of ${this.total} packets`
            : `${n} packet${n === 1 ? '' : 's'}`;
    }

    // Too many packets a second to decode: only summaries arrive. It is a
    // state the list is in, so it stays in view until the rate falls.
    guard(active) {
        this.setState(active ? 'Summaries only' : 'Capturing');
        this.setProblem(active
            ? 'More than 200 packets a second are arriving, so only their summaries are shown. Fields return once the rate falls below 100 a second.'
            : '');
        if (!active) this.setStatus('The packet rate has fallen: fields are decoded again.');
    }

    // The badge in the header: where the Capture stands, in a word.
    setState(text) {
        this.stateEl.textContent = text;
    }

    // Spoken, not shown: the badge and the list show routine news.
    setStatus(text) {
        this.statusEl.textContent = text;
    }

    // What went wrong stays in view until it is put right, and is spoken
    // through the status once rather than twice.
    setProblem(text) {
        this.problemEl.hidden = !text;
        this.problemEl.textContent = text;
        if (text) this.setStatus(text);
    }
}

export class PacketAnalysis {
    constructor() {
        this.section = document.getElementById('packet-analysis');
        this.tabList = this.section ? this.section.querySelector('.capture-tabs') : null;
        this.paneHost = this.section ? this.section.querySelector('.capture-panes') : null;
        this.headerSlot = this.section ? this.section.querySelector('.packet-analysis-header-slot') : null;
        this.panes = [];
        this.active = null;
        this.restoredFor = null;
    }

    isOpen() {
        return Boolean(this.section && !this.section.hidden);
    }

    /** The Capture panes, leaving out the NAT Table pane beside them. */
    captures() {
        return this.panes.filter(p => p instanceof CapturePane);
    }

    /** Why this Interface cannot be opened now, or null when it can. */
    refusal(node, intf) {
        if (this.panes.some(p => p.matches(node, intf))) return null;
        return this.captures().length >= MAX_CAPTURES ? LIMIT_REASON : null;
    }

    /**
     * Open a pane on an Interface, or show the one already open on it. The
     * Capture starts on the server as the pane connects; there is no
     * separate start step.
     *
     * @param {boolean} reopen - Whether a closed Packet Analysis pane comes
     *     back. False for a Capture restored after a reload: the Learner's
     *     choice to close the pane stands.
     * @returns {boolean} False when refused: two are already open.
     */
    open(node, intf, filter = '', reopen = true) {
        if (!this.section) return false;
        const existing = this.panes.find(p => p.matches(node, intf));
        if (existing) {
            this.select(existing, false);
            this.show(reopen);
            return true;
        }
        if (this.captures().length >= MAX_CAPTURES) return false;
        const pane = new CapturePane(this, node, intf, filter);
        this.panes.push(pane);
        this.tabList.appendChild(pane.tab);
        this.paneHost.appendChild(pane.el);
        this.select(pane, false);
        this.save();
        pane.setState('Starting…');
        pane.setStatus(`Starting a capture on ${intf}…`);
        this.show(reopen);
        pane.connect();
        return true;
    }

    /**
     * Open the NAT Table tab, or show it if it is already open. It sits
     * beside the Captures and does not count against their limit.
     */
    openNatTable() {
        if (!this.section) return;
        let pane = this.panes.find(p => p instanceof NatTablePane);
        const opening = !pane;
        if (opening) {
            pane = new NatTablePane(this);
            this.panes.push(pane);
            this.tabList.appendChild(pane.tab);
            this.paneHost.appendChild(pane.el);
        }
        this.show();
        this.select(pane, true);
        if (opening) pane.connect();
    }

    // Reopen what this browser had open before a reload, once per Lab.
    restore() {
        if (!appState.labKey || this.restoredFor === appState.labKey) return;
        this.restoredFor = appState.labKey;
        if (this.panes.length) return;
        remembered().forEach(t => this.open(t.node, t.interface, t.filter || '', false));
    }

    // The selected pane's header (its state, count and actions) sits in the
    // tab row to save a row of packets; every other pane keeps its own. Moving
    // the element, not copying it, keeps its listeners.
    showHeader(pane) {
        const shown = this.headerSlot.firstElementChild;
        if (shown) {
            const owner = this.panes.find(p => p.header === shown);
            if (owner) owner.el.prepend(shown);
            else shown.remove();
        }
        if (pane) this.headerSlot.appendChild(pane.header);
    }

    select(pane, focus) {
        this.showHeader(pane);
        this.active = pane;
        this.panes.forEach(p => {
            const on = p === pane;
            p.tab.setAttribute('aria-selected', String(on));
            p.tab.tabIndex = on ? 0 : -1;
            p.el.hidden = !on;
        });
        if (focus) pane.tab.focus();
    }

    // Arrow keys move between the Capture tabs, as in any tab list.
    onTabKey(event, pane) {
        const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
        if (!keys.includes(event.key)) return;
        event.preventDefault();
        const i = this.panes.indexOf(pane);
        let next;
        if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = this.panes.length - 1;
        else next = (i + (event.key === 'ArrowLeft' ? -1 : 1) + this.panes.length) % this.panes.length;
        this.select(this.panes[next], true);
    }

    /** Close one pane on purpose: its Capture stops unless others watch it. */
    close(pane) {
        pane.disconnect(true);
        this.remove(pane);
        this.save();
        if (this.active) this.active.tab.focus();
    }

    /** Close every pane because the Lab is gone; nothing to tell the server. */
    closeAll() {
        this.panes.slice().forEach(p => {
            p.disconnect(false);
            this.remove(p);
        });
        remember([]);
        this.restoredFor = null;
    }

    remove(pane) {
        const i = this.panes.indexOf(pane);
        if (i < 0) return;
        if (this.active === pane) this.showHeader(null);
        this.panes.splice(i, 1);
        pane.tab.remove();
        pane.el.remove();
        if (this.active === pane) {
            this.active = null;
            const next = this.panes[Math.min(i, this.panes.length - 1)];
            if (next) this.select(next, false);
        }
        if (!this.panes.length) this.hide();
    }

    save() {
        remember(this.captures().map(p => ({ ...p.target })));
    }

    // The Packet Analysis pane is on show while it holds a Capture or the
    // NAT Table, and takes no room otherwise.
    show(reopen = true) {
        this.section.hidden = false;
        layoutPanes();
        if (reopen) showPacketPane();
    }

    hide() {
        if (!this.section) return;
        this.section.hidden = true;
        layoutPanes();
    }
}
