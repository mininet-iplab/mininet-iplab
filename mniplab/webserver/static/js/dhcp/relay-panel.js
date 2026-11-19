// DHCP Relay panel — the modal a Learner opens from a Node's Relay badge to
// watch what the Relay has forwarded.
//
// The panel is deliberately read-only: a Relay is Lab shape,
// not Exercise Configuration, so there is nothing here to edit or remove. It
// shows the one pair that is the lesson — the source address the Relay used
// and the Service it forwarded to — and defers to the Node's Terminal for
// the relay's own unabridged log.
//
// Forwarded requests are read from the relay's log behind
// RelayActivityQueryService (see mniplab/dhcp/relay_activity.py), which is
// poll-only; the interval matches the DHCP Leases panel and the instructor
// Sessions/Terminals panels.

import { appState } from '../state/app-state.js';
import { getRelayActivity } from '../services/api.js';
import { patchTableRows } from '../utils/dom.js';
import { trapFocus } from '../utils/focus-trap.js';
import { pinRelayPath } from '../topology/relay-paths.js';

const POLL_INTERVAL_MS = 3000;

let currentNode = null;
let pollTimer = null;
let trap = null;
// Row count from the last render, so a poll that changes the table announces
// the new count once through the polite live region instead of staying
// silent (WCAG 4.1.3) or re-announcing an unchanged table every 3 seconds.
let lastRowCount = null;

export function initRelayPanel() {
    const modal = document.getElementById('dhcp-relay-modal');
    const closeBtn = document.getElementById('dhcp-relay-modal-close');
    if (!modal) return;

    if (closeBtn) closeBtn.addEventListener('click', closeRelayPanel);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeRelayPanel();
    });

    const viewCanvasBtn = document.getElementById('dhcp-relay-view-canvas');
    if (viewCanvasBtn) {
        viewCanvasBtn.addEventListener('click', () => {
            const nodeToPin = currentNode;
            closeRelayPanel();
            if (nodeToPin) {
                pinRelayPath(nodeToPin);
            }
        });
    }

    const terminalBtn = document.getElementById('dhcp-relay-terminal-btn');
    if (terminalBtn) {
        terminalBtn.addEventListener('click', () => {
            // The Terminal, not this panel, is the full record: the relay's
            // log says everything dnsmasq reported, not just the forwarded
            // lines the parser recognised.
            if (currentNode && appState.tabManager) {
                appState.tabManager.openTerminal(currentNode);
            }
        });
    }
}

export function openRelayPanel(nodeName) {
    const modal = document.getElementById('dhcp-relay-modal');
    const title = document.getElementById('dhcp-relay-modal-title');
    if (!modal) return;

    currentNode = nodeName;
    if (title) title.textContent = `${nodeName} — DHCP Relay`;
    renderCoveredSubnets(nodeName);
    setStatus('Loading relay activity…', false);
    clearRows();

    modal.style.display = 'flex';
    // Re-opening while already open (another badge press) must not leak the
    // previous trap's document-level listener.
    if (trap) trap.release();
    lastRowCount = null;
    trap = trapFocus(modal, { onClose: closeRelayPanel });

    void refreshActivity();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => { void refreshActivity(); }, POLL_INTERVAL_MS);
}

export function closeRelayPanel() {
    const modal = document.getElementById('dhcp-relay-modal');
    if (modal) modal.style.display = 'none';
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
    currentNode = null;
    lastRowCount = null;
    if (trap) {
        trap.release();
        trap = null;
    }
}

/** Name the Subnets the Relay covers, from the current Topology payload. */
function renderCoveredSubnets(nodeName) {
    const el = document.getElementById('dhcp-relay-subnets');
    if (!el) return;
    const nodes = appState.currentTopology?.nodes || [];
    const node = nodes.find((n) => n.id === nodeName);
    const subnets = (node?.relays || []).flatMap((r) => r.subnets || []);
    el.textContent = subnets.length > 0
        ? `This Node relays DHCP requests for: ${subnets.join(', ')}`
        : '';
}

async function refreshActivity() {
    const node = currentNode;
    if (!node) return;

    let response;
    try {
        response = await getRelayActivity(node);
    } catch (error) {
        if (node === currentNode) {
            setStatus(`Can't reach the server: ${error.message}`, true);
            clearRows();
        }
        return;
    }

    // The panel may have been closed (or reopened for a different Node)
    // while this request was in flight.
    if (node !== currentNode) return;

    let data = null;
    try {
        data = await response.json();
    } catch (_) { /* fall through with data === null */ }

    if (!response.ok) {
        // The log cannot be read — the Relay process is not running. This is
        // deliberately a different state from "running, forwarded nothing":
        // one is a fault, the other is a Relay waiting for a client.
        const detail = (data && data.detail) || `Request failed (${response.status})`;
        setStatus(detail, true);
        clearRows();
        return;
    }

    const activity = (data && data.activity) || [];
    if (activity.length === 0) {
        setStatus('This Relay has forwarded nothing yet.', false);
        lastRowCount = 0;
    } else if (activity.length !== lastRowCount) {
        // Announce only on change: the table's arrival or growth is the
        // thing a screen-reader user needs; an unchanged poll is silence.
        setStatus(
            activity.length === 1 ? '1 forwarded request' : `${activity.length} forwarded requests`,
            false,
        );
        lastRowCount = activity.length;
    } else {
        setStatus('', false);
    }

    const tbody = document.getElementById('dhcp-relay-activity-tbody');
    if (!tbody) return;
    // Keys include the row index: the same source/server pair legitimately
    // appears once per forwarded request, so the pair alone is not unique.
    patchTableRows(tbody, activity.map((forwarded, index) => ({
        key: `${index}:${forwarded.source}->${forwarded.server}`,
        cells: [forwarded.source || '—', forwardedToCell(forwarded.server)],
    })));
}

/** The "Forwarded to" cell: the Service's address, naming the Node that
 * carries it when the current Topology resolves it — the address alone
 * makes the Learner infer which Service the path ends at. */
function forwardedToCell(server) {
    if (!server) return '—';
    const nodes = appState.currentTopology?.nodes || [];
    const target = server.split('/')[0].toLowerCase();
    const match = nodes.find((n) =>
        (n.ips || [n.ip]).some((ip) => (ip || '').split('/')[0].toLowerCase() === target)
    );
    return match ? `${server} (${match.label || match.id})` : server;
}

function setStatus(message, isError) {
    const el = document.getElementById('dhcp-relay-status');
    if (!el) return;
    el.textContent = message;
    el.classList.toggle('dhcp-leases-status-error', Boolean(isError && message));
}

function clearRows() {
    const tbody = document.getElementById('dhcp-relay-activity-tbody');
    if (tbody) patchTableRows(tbody, []);
}
