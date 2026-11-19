// Switch panel — the modal opened when clicking a ManagedSwitch in the topology.
//
// Allows learners and instructors to inspect switch ports, view connected peer devices,
// dynamically reconfigure ports as Access, Trunk, or Untagged/None, apply changes live,
// inspect learned MACs and spanning tree, and export declarative Python/CLI snippets.

import { getSwitchVlans, setSwitchPortVlan, getSwitchMacTable, flushSwitchMacTable, getSwitchSpanningTree } from '../services/api.js';
import { formatResponseError, setElementText } from '../utils/dom.js';
import { trapFocus } from '../utils/focus-trap.js';

let currentSwitch = null;
let switchPortsData = {};
let trap = null;
let lastFocusedElement = null;

export function initSwitchPanel() {
    const modal = document.getElementById('switch-modal');
    if (!modal) return;

    const closeBtn = document.getElementById('switch-modal-close');
    if (closeBtn) closeBtn.addEventListener('click', closeSwitchPanel);

    document.getElementById('switch-refresh-btn')?.addEventListener('click', () => {
        if (currentSwitch) void loadSwitchData(currentSwitch);
    });
    document.getElementById('switch-flush-btn')?.addEventListener('click', () => {
        if (currentSwitch) void flushMacTable(currentSwitch);
    });

    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeSwitchPanel();
    });

    initHints(modal);

    modal.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeSwitchPanel();
        }
    });

    // Export drawer toggle
    const exportToggleBtn = document.getElementById('switch-export-toggle-btn');
    const exportDrawer = document.getElementById('switch-export-drawer');
    if (exportToggleBtn && exportDrawer) {
        exportToggleBtn.addEventListener('click', () => {
            const isExpanded = exportToggleBtn.getAttribute('aria-expanded') === 'true';
            exportToggleBtn.setAttribute('aria-expanded', String(!isExpanded));
            exportDrawer.classList.toggle('hidden', isExpanded);
            exportDrawer.setAttribute('aria-hidden', String(isExpanded));
            if (!isExpanded) {
                updateExportSnippets();
            }
        });
    }

    // Copy export snippets
    const copyBtn = document.getElementById('switch-copy-export-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', async () => {
            const pyCode = document.getElementById('switch-export-python')?.textContent || '';
            const cliCode = document.getElementById('switch-export-cli')?.textContent || '';
            const fullText = `# Python addLink declarations:\n${pyCode}\n\n# CLI commands:\n${cliCode}`;
            const statusEl = document.getElementById('switch-copy-status');
            try {
                await navigator.clipboard.writeText(fullText);
                if (statusEl) setElementText(statusEl, 'Copied to clipboard!');
            } catch (e) {
                if (statusEl) setElementText(statusEl, 'Failed to copy to clipboard');
            }
            setTimeout(() => {
                if (statusEl) setElementText(statusEl, '');
            }, 3000);
        });
    }
}

// Info hints (WCAG 1.4.13): shown on hover and keyboard focus, kept while the
// pointer is over the hint, dismissed with Escape, toggled by a tap.
function initHints(modal) {
    for (const wrap of modal.querySelectorAll('.switch-hint-wrap')) {
        const button = wrap.querySelector('.switch-hint-btn');
        const hint = wrap.querySelector('.switch-hint');
        if (!button || !hint) continue;
        const open = () => {
            const box = button.getBoundingClientRect();
            hint.style.top = `${box.bottom + 4}px`;
            hint.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - 328))}px`;
            wrap.setAttribute('data-open', '');
        };
        const close = () => wrap.removeAttribute('data-open');
        let touched = false;
        wrap.addEventListener('pointerdown', (event) => { touched = event.pointerType === 'touch'; });
        wrap.addEventListener('mouseenter', () => { if (!touched) open(); });
        wrap.addEventListener('mouseleave', close);
        button.addEventListener('focus', () => { if (button.matches(':focus-visible')) open(); });
        button.addEventListener('blur', close);
        button.addEventListener('click', () => {
            if (!touched) return;
            if (wrap.hasAttribute('data-open')) close(); else open();
        });
        wrap.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && wrap.hasAttribute('data-open')) {
                event.preventDefault();
                event.stopPropagation();
                close();
            }
        });
    }
}

function describeSpanningTree(tree) {
    if (!tree.protocol) return 'Spanning tree is off.';
    const protocol = tree.protocol.toUpperCase();
    if (!tree.root_bridge) return `${protocol} · Electing a root…`;
    const priority = tree.root_priority === null || tree.root_priority === undefined
        ? '' : ` (priority ${tree.root_priority})`;
    if (tree.is_root) return `${protocol} · This Switch is the root${priority}`;
    return `${protocol} · Root: ${tree.root_name || tree.root_mac || 'unknown'}${priority}`;
}

function describeThisSwitch(tree) {
    if (!tree.protocol) return '';
    const parts = [];
    if (tree.priority !== null && tree.priority !== undefined) parts.push(`priority ${tree.priority}`);
    if (tree.mac) parts.push(`MAC ${tree.mac}`);
    return parts.length ? `This Switch: ${parts.join(' · ')}` : '';
}

export function openSwitchPanel(switchName) {
    const modal = document.getElementById('switch-modal');
    if (!modal) return;

    currentSwitch = switchName;
    document.getElementById('switch-mac-tbody').replaceChildren();
    document.getElementById('switch-tree-tbody').replaceChildren();
    document.getElementById('switch-tree-summary').textContent = '';
    document.getElementById('switch-tree-detail').textContent = '';
    lastFocusedElement = document.activeElement;

    const titleEl = document.getElementById('switch-modal-title');
    if (titleEl) titleEl.textContent = `Switch Management — ${switchName}`;

    const nodePill = document.getElementById('switch-modal-node');
    if (nodePill) nodePill.textContent = switchName;

    const summaryEl = document.getElementById('switch-summary');
    if (summaryEl) setElementText(summaryEl, `Manage 802.1Q VLAN memberships and trunking on ${switchName}.`);

    const statusEl = document.getElementById('switch-status');
    if (statusEl) {
        setElementText(statusEl, 'Loading port configurations...');
        statusEl.className = 'switch-status';
    }

    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');

    trap = trapFocus(modal, closeSwitchPanel);

    void loadSwitchData(switchName);
}

export function closeSwitchPanel() {
    const modal = document.getElementById('switch-modal');
    if (!modal || modal.style.display === 'none') return;

    if (trap) {
        trap.release();
        trap = null;
    }

    currentSwitch = null;
    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');

    // Reset export drawer
    const exportToggleBtn = document.getElementById('switch-export-toggle-btn');
    const exportDrawer = document.getElementById('switch-export-drawer');
    if (exportToggleBtn) exportToggleBtn.setAttribute('aria-expanded', 'false');
    if (exportDrawer) {
        exportDrawer.classList.add('hidden');
        exportDrawer.setAttribute('aria-hidden', 'true');
    }

    if (lastFocusedElement && typeof lastFocusedElement.focus === 'function') {
        lastFocusedElement.focus();
    }
}

async function loadSwitchData(switchName) {
    const statusEl = document.getElementById('switch-status');
    const tbody = document.getElementById('switch-ports-tbody');
    if (!tbody) return;

    setReadButtonsDisabled(true);
    try {
        const [res, macResponse, treeResponse] = await Promise.all([
            getSwitchVlans(switchName), getSwitchMacTable(switchName),
            getSwitchSpanningTree(switchName),
        ]);
        if (currentSwitch !== switchName) return;
        if (!res.ok) {
            const err = await formatResponseError(res);
            if (statusEl) {
                setElementText(statusEl, `Error loading ports: ${err}`);
                statusEl.className = 'switch-status switch-status-error';
            }
            return;
        }

        const data = await res.json();
        switchPortsData = data.ports || {};
        renderPortsTable(switchName, switchPortsData);

        if (statusEl) {
            setElementText(statusEl, '');
            statusEl.className = 'switch-status';
        }
        for (const response of [macResponse, treeResponse]) {
            if (!response.ok) throw new Error(formatResponseError(
                await response.json().catch(() => null), response.statusText || 'Switch request failed',
            ));
        }
        renderReadTable('switch-mac-tbody', (await macResponse.json()).entries,
            ['mac', 'port', 'vlan', 'age'], 'No learned MAC addresses.');
        const tree = await treeResponse.json();
        document.getElementById('switch-tree-summary').textContent = describeSpanningTree(tree);
        document.getElementById('switch-tree-detail').textContent = describeThisSwitch(tree);
        renderReadTable('switch-tree-tbody', tree.ports, ['port', 'role', 'state'],
            tree.protocol ? 'No spanning-tree ports.' : 'Spanning tree is off.');
        updateExportSnippets();
    } catch (err) {
        if (statusEl) {
            setElementText(statusEl, `Failed to fetch switch data: ${err.message || err}`);
            statusEl.className = 'switch-status switch-status-error';
        }
    } finally {
        if (currentSwitch === switchName) setReadButtonsDisabled(false);
    }
}

function setReadButtonsDisabled(disabled) {
    for (const id of ['switch-refresh-btn', 'switch-flush-btn']) {
        document.getElementById(id).disabled = disabled;
    }
}

function renderReadTable(id, entries, columns, emptyMessage) {
    const body = document.getElementById(id);
    body.replaceChildren();
    for (const entry of entries || []) {
        const row = document.createElement('tr');
        for (const column of columns) {
            const cell = document.createElement('td');
            cell.textContent = entry[column] ?? '—';
            row.appendChild(cell);
        }
        body.appendChild(row);
    }
    if (!body.children.length) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = columns.length;
        cell.textContent = emptyMessage;
        row.appendChild(cell);
        body.appendChild(row);
    }
}

async function flushMacTable(switchName) {
    setReadButtonsDisabled(true);
    const status = document.getElementById('switch-status');
    try {
        const response = await flushSwitchMacTable(switchName);
        if (currentSwitch !== switchName) return;
        if (!response.ok) throw new Error(formatResponseError(
            await response.json().catch(() => null), response.statusText || 'Switch request failed',
        ));
        renderReadTable('switch-mac-tbody', (await response.json()).entries,
            ['mac', 'port', 'vlan', 'age'], 'No learned MAC addresses.');
        status.textContent = 'MAC address table flushed.';
        status.className = 'switch-status switch-status-success';
    } catch (error) {
        if (currentSwitch !== switchName) return;
        status.textContent = `Could not flush MAC address table: ${error.message || error}`;
        status.className = 'switch-status switch-status-error';
    } finally {
        if (currentSwitch === switchName) setReadButtonsDisabled(false);
    }
}

function renderPortsTable(switchName, ports) {
    const tbody = document.getElementById('switch-ports-tbody');
    if (!tbody) return;

    tbody.innerHTML = '';
    const portNames = Object.keys(ports).sort();

    if (portNames.length === 0) {
        const emptyRow = document.createElement('tr');
        emptyRow.innerHTML = '<td colspan="5" class="switch-empty-cell">No ports found on this switch.</td>';
        tbody.appendChild(emptyRow);
        return;
    }

    for (const portName of portNames) {
        const portInfo = ports[portName] || {};
        const mode = portInfo.mode || 'none';
        const tag = portInfo.tag != null ? portInfo.tag : '';
        const trunks = Array.isArray(portInfo.trunks) ? portInfo.trunks.join(', ') : '';
        const peerNode = portInfo.peer_node || '';
        const peerIntf = portInfo.peer_intf || '';
        const connectedText = peerNode ? (peerIntf ? `${peerNode} (${peerIntf})` : peerNode) : '—';

        const tr = document.createElement('tr');
        tr.dataset.port = portName;

        // Port Name
        const tdPort = document.createElement('td');
        tdPort.className = 'switch-port-name-cell';
        tdPort.textContent = portName;
        tr.appendChild(tdPort);

        // Connected To
        const tdPeer = document.createElement('td');
        tdPeer.className = 'switch-peer-cell';
        tdPeer.textContent = connectedText;
        tr.appendChild(tdPeer);

        // Mode Selector
        const tdMode = document.createElement('td');
        const select = document.createElement('select');
        select.className = 'switch-mode-select';
        select.setAttribute('aria-label', `Port ${portName} mode`);
        select.innerHTML = `
            <option value="none"${mode === 'none' ? ' selected' : ''}>Untagged (None)</option>
            <option value="access"${mode === 'access' ? ' selected' : ''}>Access</option>
            <option value="trunk"${mode === 'trunk' ? ' selected' : ''}>Trunk</option>
        `;
        tdMode.appendChild(select);
        tr.appendChild(tdMode);

        // VLAN Input(s)
        const tdVlan = document.createElement('td');
        tdVlan.className = 'switch-vlan-input-cell';

        const vlanInput = document.createElement('input');
        vlanInput.className = 'switch-vlan-input';
        vlanInput.setAttribute('aria-label', `Port ${portName} VLAN ID(s)`);
        vlanInput.autocomplete = 'off';
        vlanInput.spellcheck = false;

        updateInputForMode(vlanInput, mode, tag, trunks);

        tdVlan.appendChild(vlanInput);
        tr.appendChild(tdVlan);

        // Mode change updates input type & placeholder
        select.addEventListener('change', () => {
            const newMode = select.value;
            updateInputForMode(vlanInput, newMode, tag, trunks);
        });

        // Action / Apply Button
        const tdAction = document.createElement('td');
        const applyBtn = document.createElement('button');
        applyBtn.className = 'btn-primary btn-sm switch-apply-btn';
        applyBtn.type = 'button';
        applyBtn.textContent = 'Apply';
        applyBtn.setAttribute('aria-label', `Apply VLAN settings for port ${portName}`);

        applyBtn.addEventListener('click', () => {
            void applyPortVlan(switchName, portName, select.value, vlanInput.value, applyBtn);
        });

        tdAction.appendChild(applyBtn);
        tr.appendChild(tdAction);

        tbody.appendChild(tr);
    }
}

function updateInputForMode(input, mode, tagVal, trunksVal) {
    if (mode === 'none') {
        input.disabled = true;
        input.type = 'text';
        input.value = '';
        input.placeholder = 'None';
    } else if (mode === 'access') {
        input.disabled = false;
        input.type = 'number';
        input.min = '1';
        input.max = '4094';
        input.placeholder = 'e.g. 10';
        input.value = tagVal || '';
    } else if (mode === 'trunk') {
        input.disabled = false;
        input.type = 'text';
        input.removeAttribute('min');
        input.removeAttribute('max');
        input.placeholder = 'e.g. 10, 20';
        input.value = trunksVal || '';
    }
}

async function applyPortVlan(switchName, portName, mode, inputValue, button) {
    const statusEl = document.getElementById('switch-status');
    const liveEl = document.getElementById('switch-live');

    // Validation
    const payload = { mode };

    if (mode === 'access') {
        const tag = parseInt(inputValue.trim(), 10);
        if (isNaN(tag) || tag < 1 || tag > 4094) {
            if (statusEl) {
                setElementText(statusEl, `Invalid VLAN ID for ${portName}. Must be an integer between 1 and 4094.`);
                statusEl.className = 'switch-status switch-status-error';
            }
            return;
        }
        payload.vlan = tag;
    } else if (mode === 'trunk') {
        const tokens = inputValue.split(',').map(s => s.trim()).filter(Boolean);
        if (tokens.length === 0) {
            if (statusEl) {
                setElementText(statusEl, `At least one trunk VLAN ID must be specified for ${portName}.`);
                statusEl.className = 'switch-status switch-status-error';
            }
            return;
        }
        const parsedTrunks = [];
        for (const token of tokens) {
            const val = parseInt(token, 10);
            if (isNaN(val) || val < 1 || val > 4094) {
                if (statusEl) {
                    setElementText(statusEl, `Invalid trunk VLAN ID "${token}" for ${portName}. Must be between 1 and 4094.`);
                    statusEl.className = 'switch-status switch-status-error';
                }
                return;
            }
            parsedTrunks.push(val);
        }
        payload.trunks = parsedTrunks;
    }

    button.disabled = true;
    if (statusEl) {
        setElementText(statusEl, `Applying configuration to ${portName}...`);
        statusEl.className = 'switch-status';
    }

    try {
        const res = await setSwitchPortVlan(switchName, portName, payload);
        if (!res.ok) {
            const err = await formatResponseError(res);
            if (statusEl) {
                setElementText(statusEl, `Failed to configure ${portName}: ${err}`);
                statusEl.className = 'switch-status switch-status-error';
            }
            return;
        }

        const data = await res.json();
        if (data.ports) {
            switchPortsData = data.ports;
        } else if (data.config) {
            switchPortsData[portName] = data.config;
        }

        const successMsg = mode === 'access'
            ? `Port ${portName} set to Access VLAN ${payload.vlan}`
            : mode === 'trunk'
            ? `Port ${portName} set to Trunk [${payload.trunks.join(', ')}]`
            : `Port ${portName} set to Untagged`;

        if (statusEl) {
            setElementText(statusEl, successMsg);
            statusEl.className = 'switch-status switch-status-success';
        }
        if (liveEl) setElementText(liveEl, successMsg);

        updateExportSnippets();
    } catch (err) {
        if (statusEl) {
            setElementText(statusEl, `Request error: ${err.message || err}`);
            statusEl.className = 'switch-status switch-status-error';
        }
    } finally {
        button.disabled = false;
    }
}

function updateExportSnippets() {
    const pyEl = document.getElementById('switch-export-python');
    const cliEl = document.getElementById('switch-export-cli');
    if (!pyEl || !cliEl || !currentSwitch) return;

    const pyLines = [];
    const cliLines = [];
    const portNames = Object.keys(switchPortsData).sort();

    for (const port of portNames) {
        const info = switchPortsData[port] || {};
        const mode = info.mode || 'none';
        const peerNode = info.peer_node || 'peer';

        if (mode === 'access' && info.tag != null) {
            pyLines.push(`net.addLink(${peerNode}, ${currentSwitch}, params2={'vlan': ${info.tag}})`);
            cliLines.push(`vlan ${currentSwitch} ${port} access ${info.tag}`);
        } else if (mode === 'trunk' && Array.isArray(info.trunks) && info.trunks.length > 0) {
            const trunksStr = JSON.stringify(info.trunks);
            pyLines.push(`net.addLink(${peerNode}, ${currentSwitch}, params2={'trunks': ${trunksStr}})`);
            cliLines.push(`vlan ${currentSwitch} ${port} trunk ${info.trunks.join(',')}`);
        } else {
            pyLines.push(`net.addLink(${peerNode}, ${currentSwitch})`);
            cliLines.push(`vlan ${currentSwitch} ${port} none`);
        }
    }

    pyEl.textContent = pyLines.length > 0 ? pyLines.join('\n') : '# No ports configured';
    cliEl.textContent = cliLines.length > 0 ? cliLines.join('\n') : '# No ports configured';
}
