import { appState } from '../state/app-state.js';
import {
    getTopology,
    linkAction,
    configureLink,
    getRouting,
    getCaptureInterfaces
} from '../services/api.js';
import { loadTopology } from '../lab/lifecycle.js';
import { trapFocus } from '../utils/focus-trap.js';
import { Separator } from '../ui/separator.js';
import { renderCaptureEnds, renderNodeInterfaces } from '../capture/packet-analysis.js';

// How each streamed Network Tools test is named in its output and on its button.
const TOOL_NAMES = { ping: 'Ping', traceroute: 'Traceroute', iperf3: 'Iperf3' };

export class NetworkConsole {
    constructor() {
        this.nodes = [];
        this.activeToolTab = 'ping';
        this.modalTraps = new Map();
        // The open connection of a running ping or traceroute, by tool.
        this.toolRuns = new Map();
        this.init();
    }

    init() {
        this.cacheElements();
        this.attachEventListeners();
        this.setupDraggable();
        this.setupResizable();
    }

    cacheElements() {
        // Modals
        this.toolsModal = document.getElementById('tools-modal');
        this.linkModal = document.getElementById('link-modal');
        this.captureModal = document.getElementById('capture-modal');

        // Modal Triggers
        this.toolsBtn = document.getElementById('tools-btn');
        this.linkConfigBtn = document.getElementById('link-config-btn'); // In toolbar
        this.linkCaptureBtn = document.getElementById('link-capture-btn');

        // Modal Close Buttons
        this.toolsCloseBtn = document.getElementById('tools-modal-close');
        this.linkCloseBtn = document.getElementById('link-modal-close');
        this.captureCloseBtn = document.getElementById('capture-modal-close');

        // Toolbar Actions
        this.disconnectBtn = document.getElementById('link-disconnect-btn');
        this.reconnectBtn = document.getElementById('link-reconnect-btn');

        // Link Configuration (Inside Modal)
        this.configureBtn = document.getElementById('link-configure-btn'); // Apply button
        this.node1Input = document.getElementById('link-node1');
        this.node2Input = document.getElementById('link-node2');

        // Tools Tabs
        this.toolTabs = document.querySelectorAll('.tools-tab');
        this.toolContents = document.querySelectorAll('.tools-content');

        // Tool Inputs/Buttons
        this.pingSourceSelect = document.getElementById('ping-source');
        this.pingDestSelect = document.getElementById('ping-destination');
        this.pingDestCustomInput = document.getElementById('ping-destination-custom');
        this.pingCountInput = document.getElementById('ping-count');
        this.pingIpv6Check = document.getElementById('ping-ipv6');
        this.pingBtn = document.getElementById('ping-btn');
        this.pingOutput = document.getElementById('ping-output');

        this.tracerouteSourceSelect = document.getElementById('traceroute-source');
        this.tracerouteDestSelect = document.getElementById('traceroute-destination');
        this.tracerouteDestCustomInput = document.getElementById('traceroute-destination-custom');
        this.tracerouteIpv6Check = document.getElementById('traceroute-ipv6');
        this.tracerouteResolveCheck = document.getElementById('traceroute-resolve');
        this.tracerouteBtn = document.getElementById('traceroute-btn');
        this.tracerouteOutput = document.getElementById('traceroute-output');

        this.iperf3ServerSelect = document.getElementById('iperf3-server');
        this.iperf3ClientSelect = document.getElementById('iperf3-client');
        this.iperf3DurationInput = document.getElementById('iperf3-duration');
        this.iperf3ProtocolSelect = document.getElementById('iperf3-protocol');
        this.iperf3BandwidthInput = document.getElementById('iperf3-bandwidth');
        this.iperf3BandwidthUnitSelect = document.getElementById('iperf3-bandwidth-unit');
        this.iperf3JsonCheck = document.getElementById('iperf3-json');
        this.iperf3Btn = document.getElementById('iperf3-btn');
        this.iperf3Output = document.getElementById('iperf3-output');

        this.routeNodeSelect = document.getElementById('route-node');
        this.showRoutesBtn = document.getElementById('route-show-btn');
        this.showOspfBtn = document.getElementById('ospf-show-btn');
        this.showBgpBtn = document.getElementById('bgp-show-btn');
        this.routeOutput = document.getElementById('route-output');
    }

    attachEventListeners() {
        // Modals
        if (this.toolsBtn) this.toolsBtn.addEventListener('click', () => {
            this.fetchNodes(); // nodes may have changed since page load
            this.openModal(this.toolsModal);
        });
        if (this.toolsCloseBtn) this.toolsCloseBtn.addEventListener('click', () => this.closeModal(this.toolsModal));

        if (this.linkConfigBtn) this.linkConfigBtn.addEventListener('click', () => {
            this.prepareLinkModal();
            this.openModal(this.linkModal);
        });
        if (this.linkCloseBtn) this.linkCloseBtn.addEventListener('click', () => this.closeModal(this.linkModal));

        if (this.linkCaptureBtn) this.linkCaptureBtn.addEventListener('click', () => {
            this.prepareCaptureModal();
            this.openModal(this.captureModal);
        });
        if (this.captureCloseBtn) this.captureCloseBtn.addEventListener('click', () => this.closeModal(this.captureModal));

        // Click outside to close
        window.addEventListener('click', (e) => {
            if (e.target === this.toolsModal) this.closeModal(this.toolsModal);
            if (e.target === this.linkModal) this.closeModal(this.linkModal);
            if (e.target === this.captureModal) this.closeModal(this.captureModal);
        });

        // Tabs
        this.toolTabs.forEach(tab => {
            tab.addEventListener('click', () => this.switchToolTab(tab.dataset.tab));
        });

        // Link Actions
        if (this.disconnectBtn) this.disconnectBtn.addEventListener('click', () => this.handleLinkStatus('disconnect'));
        if (this.reconnectBtn) this.reconnectBtn.addEventListener('click', () => this.handleLinkStatus('reconnect'));
        if (this.configureBtn) this.configureBtn.addEventListener('click', () => this.configureLink());

        // Tool Actions
        if (this.pingDestSelect) {
            this.pingDestSelect.addEventListener('change', () => {
                this.toggleCustomDestinationInput(this.pingDestSelect, this.pingDestCustomInput);
            });
        }
        if (this.tracerouteDestSelect) {
            this.tracerouteDestSelect.addEventListener('change', () => {
                this.toggleCustomDestinationInput(this.tracerouteDestSelect, this.tracerouteDestCustomInput);
            });
        }
        if (this.pingBtn) this.pingBtn.addEventListener('click', () => this.runPing());
        if (this.tracerouteBtn) this.tracerouteBtn.addEventListener('click', () => this.runTraceroute());
        if (this.iperf3Btn) this.iperf3Btn.addEventListener('click', () => this.runIperf3());
        if (this.showRoutesBtn) this.showRoutesBtn.addEventListener('click', () => this.fetchRouting('routes'));
        if (this.showOspfBtn) this.showOspfBtn.addEventListener('click', () => this.fetchRouting('ospf'));
        if (this.showBgpBtn) this.showBgpBtn.addEventListener('click', () => this.fetchRouting('bgp'));
    }

    setupDraggable() {
        if (!this.toolsModal) return;
        const content = this.toolsModal.querySelector('.modal-content');
        const header = this.toolsModal.querySelector('.modal-header');
        if (!content || !header) return;

        let isDragging = false;
        let diffX = 0;
        let diffY = 0;

        header.addEventListener('mousedown', (e) => {
            // Ignore if clicking on button/input
            if (e.target.tagName.toLowerCase() === 'button' || e.target.closest('button')) return;
            isDragging = true;
            const rect = content.getBoundingClientRect();
            diffX = e.clientX - rect.left;
            diffY = e.clientY - rect.top;
            document.body.style.userSelect = 'none'; // Prevent text selection
        });

        window.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            // Clear flex alignment dependencies
            content.style.position = 'absolute';
            content.style.right = 'auto';
            content.style.bottom = 'auto';

            content.style.left = `${e.clientX - diffX}px`;
            content.style.top = `${e.clientY - diffY}px`;
        });

        window.addEventListener('mouseup', () => {
            isDragging = false;
            document.body.style.userSelect = '';
        });
    }

    /**
     * Let the Network Tools dialog be widened and heightened, so long ping
     * and traceroute lines fit, by its left and bottom edges: a pointer
     * drag, or the arrow keys and Home/End on the focused edge. It opens in
     * the window's top-right corner, so it widens leftwards, keeping its
     * right edge, until its left edge meets the window's; then it widens
     * rightwards.
     */
    setupResizable() {
        this.toolsSeparators = [];
        const content = this.toolsModal?.querySelector('.modal-content');
        if (!content) return;
        // The gap the dialog keeps from the window's edges.
        const gap = 8;
        // What differs between the two edges: the window length a percent
        // counts in, the smallest and largest size, and where the dialog
        // goes for a new size, as viewport left and top.
        const axes = {
            width: {
                direction: -1,
                total: () => window.innerWidth,
                minimum: 360,
                largest: () => window.innerWidth - 2 * gap,
                place: (box, size) => [Math.max(gap, box.right - size), box.top],
                word: 'wide',
            },
            height: {
                direction: 1,
                total: () => window.innerHeight,
                minimum: 240,
                largest: (box) => window.innerHeight - box.top - gap,
                place: (box) => [box.left, box.top],
                word: 'tall',
            },
        };
        for (const [axis, edge] of Object.entries(axes)) {
            const handle = document.getElementById(`tools-resize-${axis}`);
            if (!handle) continue;
            const separator = new Separator(handle, {
                axis,
                direction: edge.direction,
                step: 2,
                read: () => {
                    const box = content.getBoundingClientRect();
                    if (!box.width) return null;
                    const total = edge.total();
                    const largest = edge.largest(box);
                    return {
                        value: (box[axis] / total) * 100,
                        min: (Math.min(edge.minimum, largest) / total) * 100,
                        max: (largest / total) * 100,
                        text: `${Math.round(box[axis])} pixels ${edge.word}`,
                        total,
                    };
                },
                write: (percent) => {
                    const box = content.getBoundingClientRect();
                    const size = Math.round((percent * edge.total()) / 100);
                    const [left, top] = edge.place(box, size);
                    // left and top count from the overlay, not the window.
                    content.style.left = `${content.offsetLeft + left - box.left}px`;
                    content.style.top = `${content.offsetTop + top - box.top}px`;
                    content.style.right = 'auto';
                    content.style[axis] = `${size}px`;
                    content.classList.add(`has-set-${axis}`);
                },
                commit: () => separator.describe(),
            });
            this.toolsSeparators.push(separator);
        }
    }

    openModal(modal) {
        if (!modal) return;
        modal.style.display = 'flex';
        if (modal === this.toolsModal) {
            this.toolsSeparators.forEach(separator => separator.describe());
        }
        // Confine focus to this modal while it is open; Escape closes it. One
        // trap per modal so the two modals (tools, link config) can't clobber
        // each other's trap if both are ever open.
        this.modalTraps.set(modal, trapFocus(modal, { onClose: () => this.closeModal(modal) }));
    }

    closeModal(modal) {
        if (modal) modal.style.display = 'none';
        // Nobody is left to read a run's output or press Stop; dropping the
        // connection makes the server end the tool.
        if (modal === this.toolsModal) {
            this.toolRuns.forEach(ws => ws.close());
        }
        const trap = this.modalTraps.get(modal);
        if (trap) {
            // Returns focus to the element that opened the modal.
            trap.release();
            this.modalTraps.delete(modal);
        }
    }

    showModalError(message) {
        const el = document.getElementById('link-modal-error');
        if (!el) return;
        el.textContent = message;
        // .error-message is display:none by default, .show reveals it, and the
        // element's initial .hidden (display:none !important) must go too —
        // so a visible message needs .show added and .hidden removed.
        el.classList.add('show');
        el.classList.remove('hidden');
    }

    clearModalError() {
        const el = document.getElementById('link-modal-error');
        if (!el) return;
        el.textContent = '';
        el.classList.remove('show');
        el.classList.add('hidden');
    }

    switchToolTab(tabId) {
        this.activeToolTab = tabId;
        this.toolTabs.forEach(tab => {
            tab.classList.toggle('active', tab.dataset.tab === tabId);
            tab.setAttribute('aria-selected', tab.dataset.tab === tabId);
        });
        this.toolContents.forEach(content => {
            content.classList.toggle('active', content.id === `content-${tabId}`);
        });
    }

    async fetchNodes() {
        try {
            const response = await getTopology();
            const data = await response.json();
            // No lab running answers with no "nodes" key at all; treat that
            // the same as an empty topology instead of keeping stale ones.
            // The Internet cloud is not a Node the Tools run from or to.
            this.nodes = (data.nodes || []).filter(n => n.type !== 'switch' && n.type !== 'internet');
            this.populateDropdowns();
        } catch (error) {
            console.error('Failed to fetch nodes:', error);
        }
    }

    populateDropdowns() {
        const selects = [
            this.pingSourceSelect,
            this.tracerouteSourceSelect,
            this.iperf3ServerSelect, this.iperf3ClientSelect,
            this.routeNodeSelect
        ];

        selects.forEach(select => {
            if (!select) return;
            const currentValue = select.value;
            select.innerHTML = `<option value="">Select Node</option>`;
            this.nodes.forEach(node => {
                const option = document.createElement('option');
                option.value = node.id;
                option.textContent = node.id;
                select.appendChild(option);
            });
            select.value = currentValue;
        });

        this.populateDestinationSelect(this.pingDestSelect, this.pingDestCustomInput);
        this.populateDestinationSelect(this.tracerouteDestSelect, this.tracerouteDestCustomInput);
    }

    populateDestinationSelect(select, customInput) {
        if (!select) return;

        const previousValue = select.value;
        const previousCustom = customInput ? customInput.value.trim() : '';

        select.innerHTML = '<option value="">Select Node</option>';
        this.nodes.forEach(node => {
            const option = document.createElement('option');
            option.value = node.id;
            option.textContent = node.id;
            select.appendChild(option);
        });

        const customOption = document.createElement('option');
        customOption.value = '__custom__';
        customOption.textContent = 'Custom IP/Hostname…';
        select.appendChild(customOption);

        const hasPreviousNode = this.nodes.some(node => node.id === previousValue);
        if (hasPreviousNode) {
            select.value = previousValue;
        } else if (previousValue === '__custom__' || previousCustom) {
            select.value = '__custom__';
            if (customInput) {
                customInput.value = previousCustom;
            }
        } else {
            select.value = '';
        }

        this.toggleCustomDestinationInput(select, customInput);
    }

    toggleCustomDestinationInput(select, customInput) {
        if (!select || !customInput) return;
        const isCustom = select.value === '__custom__';
        customInput.classList.toggle('hidden', !isCustom);
        customInput.disabled = !isCustom;
        customInput.setAttribute('aria-required', isCustom ? 'true' : 'false');
    }

    getDestinationValue(select, customInput) {
        if (!select) return '';
        if (select.value === '__custom__') {
            return customInput ? customInput.value.trim() : '';
        }
        return select.value.trim();
    }

    showOutput(element, message, isError = false) {
        if (!element) return;
        element.textContent = message;
        element.style.color = isError ? '#ff4d4d' : '#0f0';
        element.scrollTop = element.scrollHeight;
    }

    // --- Link Management ---

    prepareLinkModal() {
        this.clearModalError();
        const node1 = this.node1Input.value;
        const node2 = this.node2Input.value;

        if (appState.currentTopology && appState.currentTopology.links) {
            const link = appState.currentTopology.links.find(l =>
                (l.source.id === node1 && l.target.id === node2) ||
                (l.source.id === node2 && l.target.id === node1)
            );

            if (link && link.params) {
                document.getElementById('link-delay').value = link.params.delay || '';
                document.getElementById('link-jitter').value = link.params.jitter || '';
                document.getElementById('link-loss').value = link.params.loss || '';
                document.getElementById('link-bandwidth').value = link.params.bw || '';
            } else {
                document.getElementById('link-delay').value = '';
                document.getElementById('link-jitter').value = '';
                document.getElementById('link-loss').value = '';
                document.getElementById('link-bandwidth').value = '';
            }
        }
    }

    // The Packet Capture modal names both ends of the selected Link by
    // Interface; choosing one closes it and opens the pane in Packet Analysis.
    prepareCaptureModal() {
        this.setCaptureHint('Watch the packets crossing one end of this Link. A capture happens at a point, so choose the Interface to watch from.');
        const node1 = this.node1Input.value;
        const node2 = this.node2Input.value;
        const links = (appState.currentTopology && appState.currentTopology.links) || [];
        const link = links.find(l =>
            (l.source.id === node1 && l.target.id === node2) ||
            (l.source.id === node2 && l.target.id === node1)
        );
        renderCaptureEnds(
            document.getElementById('link-capture-ends'),
            link || null,
            () => this.closeModal(this.captureModal),
            document.getElementById('capture-modal-message')
        );
    }

    setCaptureHint(text) {
        const hint = document.getElementById('capture-modal-hint');
        if (hint) hint.textContent = text;
    }

    // The same modal from the Node menu, listing every Interface the Node
    // has — including one whose Link is not drawn, which no Link menu reaches.
    async openNodeCaptureModal(node) {
        const container = document.getElementById('link-capture-ends');
        const message = document.getElementById('capture-modal-message');
        this.setCaptureHint(`Watch the packets crossing one of ${node}'s Interfaces. Interfaces whose Link is not drawn on the Topology are listed too.`);
        if (container) container.textContent = '';
        if (message) message.textContent = '';
        this.openModal(this.captureModal);
        let interfaces;
        try {
            const response = await getCaptureInterfaces(node);
            const body = await response.json();
            if (!response.ok) throw new Error(body.detail || `the server answered ${response.status}`);
            interfaces = body.interfaces || [];
        } catch (e) {
            if (message) message.textContent = `Cannot list ${node}'s Interfaces: ${e.message}`;
            return;
        }
        renderNodeInterfaces(container, node, interfaces, () => this.closeModal(this.captureModal), message);
    }

    async handleLinkStatus(action) {
        const node1 = this.node1Input.value;
        const node2 = this.node2Input.value;

        if (!node1 || !node2) {
            console.error('No nodes selected for link action');
            return;
        }

        try {
            const response = await linkAction(action, node1, node2);
            const data = await response.json();
            if (data.success) {
                loadTopology();
            } else {
                this.reportLinkActionError(`Error: ${data.detail || 'Failed'}`);
            }
        } catch (error) {
            this.reportLinkActionError(`Error: ${error.message}`);
        }
    }

    // A failed disconnect/reconnect is reported where the user is working —
    // the Link Config modal's own error region — rather than by overwriting
    // the persistent header status readout (Lab / Nodes / Active Terminals)
    // with unrelated text. The modal is opened first so the message has a
    // visible, modal-scoped home.
    reportLinkActionError(message) {
        if (!this.linkModal) return;
        this.prepareLinkModal();
        this.openModal(this.linkModal);
        this.showModalError(message);
    }

    async configureLink() {
        const node1 = this.node1Input.value;
        const node2 = this.node2Input.value;
        const delay = document.getElementById('link-delay').value;
        const jitter = document.getElementById('link-jitter').value;
        const loss = document.getElementById('link-loss').value;
        const bandwidth = document.getElementById('link-bandwidth').value;
        const scheduler = document.getElementById('link-scheduler').value;

        if (!node1 || !node2) {
            this.showModalError('Select two nodes via the topology first');
            return;
        }

        if (jitter && !delay) {
            this.showModalError('Jitter requires a delay value');
            return;
        }

        const btn = this.configureBtn;
        const originalText = btn.textContent;
        btn.textContent = 'Applying...';
        btn.disabled = true;

        try {
            const response = await configureLink({
                node1, node2, delay, jitter, loss,
                bandwidth, scheduler
            });
            const data = await response.json();
            if (data.success) {
                this.closeModal(this.linkModal);
                loadTopology();
            } else {
                this.showModalError(`Error: ${data.detail || 'Failed'}`);
            }
        } catch (error) {
            this.showModalError(`Error: ${error.message}`);
        } finally {
            btn.textContent = originalText;
            btn.disabled = false;
        }
    }

    // --- Tools ---

    async runPing() {
        if (this.toolRuns.has('ping')) {
            this.stopTool('ping');
            return;
        }
        const source = this.pingSourceSelect.value;
        const destination = this.getDestinationValue(this.pingDestSelect, this.pingDestCustomInput);
        const count = this.pingCountInput ? parseInt(this.pingCountInput.value) || 4 : 4;
        const ipv6 = Boolean(this.pingIpv6Check?.checked);

        if (!source || !destination) {
            this.showOutput(this.pingOutput, 'Error: Select source and enter destination', true);
            return;
        }

        this.streamTool('ping', { source, destination, count, ipv6 }, this.pingBtn, this.pingOutput,
            `Pinging ${destination} from ${source} (${count} packets${ipv6 ? ', IPv6' : ''})...\n`);
    }

    async runTraceroute() {
        if (this.toolRuns.has('traceroute')) {
            this.stopTool('traceroute');
            return;
        }
        const source = this.tracerouteSourceSelect.value;
        const destination = this.getDestinationValue(this.tracerouteDestSelect, this.tracerouteDestCustomInput);
        const ipv6 = this.tracerouteIpv6Check.checked;
        const resolve = Boolean(this.tracerouteResolveCheck?.checked);

        if (!source || !destination) {
            this.showOutput(this.tracerouteOutput, 'Error: Select source and enter destination', true);
            return;
        }

        this.streamTool('traceroute', { source, destination, ipv6, resolve }, this.tracerouteBtn,
            this.tracerouteOutput,
            `Tracing route to ${destination} from ${source}${ipv6 ? ' (IPv6)' : ''}...\n`);
    }

    /**
     * Run a ping, traceroute or iperf3 test on the server, showing each
     * line as it is printed. While it runs, the tool's Run button becomes its
     * Stop button. An iperf3 run with JSON output shows its summary once, at the end.
     *
     * @param {'ping'|'traceroute'|'iperf3'} tool - The test to run.
     * @param {object} params - The test's source, destination or servers, and options.
     * @param {HTMLButtonElement} button - The tool's Run button.
     * @param {HTMLElement} output - Where the tool's output goes.
     * @param {string} heading - The first line shown, before any output.
     */
    streamTool(tool, params, button, output, heading) {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const key = encodeURIComponent(appState.labKey || '-');
        const ws = new WebSocket(`${protocol}//${window.location.host}/ws/labs/${key}/network-tool`);
        const runLabel = button.textContent;
        const name = TOOL_NAMES[tool];
        this.toolRuns.set(tool, ws);
        this.showOutput(output, heading);
        button.textContent = `Stop ${name}`;
        button.classList.replace('btn-primary', 'btn-danger');

        let failed = false;
        ws.onopen = () => {
            // Stop pressed before the connection opened: never start the test.
            if (ws.stopBeforeStart) ws.close(1000);
            else ws.send(JSON.stringify({ type: 'start', tool, ...params }));
        };
        ws.onmessage = (event) => {
            let message;
            try {
                message = JSON.parse(event.data);
            } catch (e) {
                return;
            }
            if (message.type === 'output') {
                this.appendOutput(output, message.data);
            } else if (message.type === 'exit' && message.stopped) {
                this.appendOutput(output, `\n${name} stopped.\n`);
            } else if (message.type === 'exit' && message.code !== 0) {
                this.appendOutput(output, `\n${name} exited with status ${message.code}.\n`);
            } else if (message.type === 'error') {
                failed = true;
                this.showOutput(output, `Error: ${message.message}`, true);
            }
        };
        ws.onclose = (event) => {
            if (this.toolRuns.get(tool) === ws) this.toolRuns.delete(tool);
            button.textContent = runLabel;
            button.classList.replace('btn-danger', 'btn-primary');
            if (ws.stopBeforeStart) {
                this.appendOutput(output, `${name} stopped before it started.\n`);
            } else if (!failed && event.code !== 1000 && this.toolsModal?.style.display !== 'none') {
                this.appendOutput(output, `\nConnection lost; ${name.toLowerCase()} ended.\n`);
            }
        };
    }

    /**
     * Stop a running ping, traceroute or iperf3 test as Ctrl+C would, so
     * a ping or an iperf3 client still prints its summary.
     *
     * @param {'ping'|'traceroute'|'iperf3'} tool - The test to stop.
     */
    stopTool(tool) {
        const ws = this.toolRuns.get(tool);
        if (!ws) return;
        if (ws.readyState === WebSocket.CONNECTING) ws.stopBeforeStart = true;
        else if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'stop' }));
    }

    /**
     * Add text to the end of a tool's output, keeping it scrolled to the
     * bottom unless the reader has scrolled up. The text goes in as a new
     * node, so a screen reader announces only what was added to the log.
     *
     * @param {HTMLElement} element - The tool's output.
     * @param {string} text - The text to add.
     */
    appendOutput(element, text) {
        if (!element) return;
        const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 8;
        element.appendChild(document.createTextNode(text));
        element.style.color = '#0f0';
        if (atBottom) element.scrollTop = element.scrollHeight;
    }

    /**
     * Read a Bandwidth field as the server takes it: its number and unit.
     * A blank field is the tool's default. A number that is not above zero is
     * shown as an error in the tab's output.
     *
     * @param {HTMLInputElement} input - The number.
     * @param {HTMLSelectElement} unit - The unit, K, M or G.
     * @param {HTMLElement} output - The tab's output, where an error is shown.
     * @returns {string|null} '' for the default, '<number><unit>', or null when refused.
     */
    readBandwidth(input, unit, output) {
        // A partly typed number reads as blank, so a bad entry is refused, not defaulted.
        if (input.value === '' && !input.validity.badInput) return '';
        const number = input.validity.badInput ? NaN : Number(input.value);
        if (!(number > 0)) {
            this.showOutput(output, 'Error: Bandwidth must be above zero', true);
            return null;
        }
        return `${input.value}${unit.value}`;
    }

    async runIperf3() {
        if (this.toolRuns.has('iperf3')) {
            this.stopTool('iperf3');
            return;
        }
        const server = this.iperf3ServerSelect.value;
        const client = this.iperf3ClientSelect.value;
        const seconds = parseInt(this.iperf3DurationInput.value) || 10;
        const protocol = this.iperf3ProtocolSelect.value;
        const json_output = this.iperf3JsonCheck.checked;

        if (!server || !client) {
            this.showOutput(this.iperf3Output, 'Error: Select server and client', true);
            return;
        }

        if (seconds < 1 || seconds > 60) {
            this.showOutput(this.iperf3Output, 'Error: Duration must be between 1 and 60 seconds', true);
            return;
        }

        const bandwidth = this.readBandwidth(this.iperf3BandwidthInput,
            this.iperf3BandwidthUnitSelect, this.iperf3Output);
        if (bandwidth === null) return;

        const params = { server, client, seconds, protocol, json_output };
        const rate = bandwidth ? `, ${bandwidth}` : '';
        if (bandwidth) params.bandwidth = bandwidth;

        this.streamTool('iperf3', params, this.iperf3Btn, this.iperf3Output,
            `Running iperf3 between ${server} and ${client} (${seconds}s, ${protocol.toUpperCase()}${rate})...\n`);
    }

    async fetchRouting(type) {
        const node = this.routeNodeSelect.value;

        if (!node) {
            this.showOutput(this.routeOutput, 'Error: Select a node', true);
            return;
        }

        this.showOutput(this.routeOutput, `Fetching ${type.toUpperCase()} info for ${node}...`);

        try {
            const response = await getRouting(type, node);
            const data = await response.json();
            if (data.success) {
                this.showOutput(this.routeOutput, data.output);
            } else {
                this.showOutput(this.routeOutput, `Error: ${data.detail || 'Failed'}`, true);
            }
        } catch (error) {
            this.showOutput(this.routeOutput, `Error: ${error.message}`, true);
        }
    }
}
