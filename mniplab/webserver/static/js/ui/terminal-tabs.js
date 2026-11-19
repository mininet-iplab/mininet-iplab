import { appState } from '../state/app-state.js';
import { layoutPanes, showTerminalPane } from './pane-arrangement.js';
import { STORAGE_PREFIX } from '../state/remembered-terminals.js';
import { trapFocus } from '../utils/focus-trap.js';

export { STORAGE_PREFIX };

// Close code the server uses when another tab of this Session takes a Terminal
// over (services.py MOVED_CLOSE_CODE).  Not 1008 and not a plain drop: this
// tab must not reconnect by itself, or the two tabs would take the Terminal
// from each other for ever.
const MOVED_CLOSE_CODE = 4001;

// Seconds to wait before each try to reattach a Terminal whose connection
// dropped: a short outage is like a reload.  Spread over less than the
// default grace window (60 s) so a Terminal still waiting is found, and few
// enough to stay inside the reconnect budget.
const REATTACH_DELAYS_MS = [1000, 2000, 3000, 5000, 8000];

function storageKey() {
    return STORAGE_PREFIX + (appState.labKey || '-');
}

function remembered() {
    try {
        const saved = JSON.parse(sessionStorage.getItem(storageKey()) || 'null');
        const tabs = saved && Array.isArray(saved.tabs) ? saved.tabs : [];
        return {
            tabs: tabs.filter(t => t && typeof t.node === 'string'),
            selected: saved ? saved.selected : null,
        };
    } catch (e) {
        return { tabs: [], selected: null };
    }
}

/**
 * A Terminal's text, scrollback included, without the empty rows below the
 * last output.
 *
 * @param {Object} term - The xterm.js Terminal.
 * @returns {string} One line per row.
 */
function terminalText(term) {
    const buffer = term.buffer.active;
    const lines = [];
    for (let i = 0; i < buffer.length; i += 1) {
        const line = buffer.getLine(i);
        if (!line) continue;
        // A row that wraps on keeps its trailing spaces: they are text.
        const next = buffer.getLine(i + 1);
        const text = line.translateToString(!(next && next.isWrapped));
        if (line.isWrapped && lines.length) {
            lines[lines.length - 1] += text;
        } else {
            lines.push(text);
        }
    }
    while (lines.length && lines[lines.length - 1] === '') lines.pop();
    return lines.join('\n');
}

/** Open a Node's Terminal in the Terminal pane, or show it if open. */
export function openTerminal(nodeName) {
    if (appState.tabManager) {
        appState.tabManager.openTerminal(nodeName);
    } else {
        console.error('Tab manager not initialized');
    }
}

export class TabManager {
    constructor() {
        this.terminals = new Map(); // nodeName -> { term, element, tabElement, ws, id }
        this.activeTab = null;
        this.restoredFor = null;
        this.containerSelector = '.terminal-content';
        this.tabBarSelector = '.terminal-tabs';

        this.init();
    }

    init() {
        this.setupEventListeners();
        this.setupResizeObserver();
        this.setupTabDragDrop();
        this.setupToolbar();
    }

    setupEventListeners() {
        // Close all terminals button
        const closeAllBtn = document.getElementById('close-all-terminals');
        if (closeAllBtn) {
            closeAllBtn.addEventListener('click', () => this.closeAllTerminals());
        }

        // Lab prompt button in header
        const labPromptBtn = document.getElementById('lab-prompt-btn');
        if (labPromptBtn) {
            labPromptBtn.addEventListener('click', () => this.openLabPrompt());
        }
    }

    setupTabDragDrop() {
        this.draggedTab = null;
        this.dragPlaceholder = null;
    }

    setupResizeObserver() {
        const container = document.querySelector(this.containerSelector);
        if (!container) return;

        let resizeTimeout;

        const observer = new ResizeObserver(() => {
            if (resizeTimeout) clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(() => {
                this.fitActiveTerminal();
            }, 100);
        });

        observer.observe(container);
    }

    fitActiveTerminal() {
        if (!this.activeTab || !this.terminals.has(this.activeTab)) return;

        const termData = this.terminals.get(this.activeTab);
        if (termData.term && termData.term.fitAddon) {
            try {
                termData.term.fitAddon.fit();

                // Send new size to backend
                if (termData.ws && termData.ws.readyState === WebSocket.OPEN) {
                    termData.ws.send(JSON.stringify({
                        type: 'resize',
                        cols: termData.term.cols,
                        rows: termData.term.rows
                    }));
                }
            } catch (e) {
                console.warn('[TabManager] Error fitting terminal:', e);
            }
        }
    }

    setupToolbar() {
        const fontDecBtn = document.getElementById('terminal-font-dec');
        if (fontDecBtn) {
            fontDecBtn.addEventListener('click', () => this.adjustActiveFontSize(-1));
        }

        const fontIncBtn = document.getElementById('terminal-font-inc');
        if (fontIncBtn) {
            fontIncBtn.addEventListener('click', () => this.adjustActiveFontSize(1));
        }

        const clearBtn = document.getElementById('terminal-clear');
        if (clearBtn) {
            clearBtn.addEventListener('click', () => this.clearActiveTerminal());
        }

        this.setupPasteDialog();
        this.setupCopyDialog();
    }

    /**
     * Wire the touch-screen Copy control. A finger cannot select text on
     * the Terminal canvas, so the active Terminal's text is shown in a
     * read-only field, where the system selection and Copy work.
     */
    setupCopyDialog() {
        const copyBtn = document.getElementById('terminal-copy');
        const modal = document.getElementById('terminal-copy-modal');
        const field = document.getElementById('terminal-copy-text');
        if (!copyBtn || !modal || !field) return;

        let trap = null;
        const close = () => {
            modal.style.display = 'none';
            field.value = '';
            if (trap) {
                trap.release();
                trap = null;
            }
        };

        copyBtn.addEventListener('click', () => {
            if (!this.activeTab || !this.terminals.has(this.activeTab)) return;
            const termData = this.terminals.get(this.activeTab);
            if (!termData.term) return;
            const label = termData.isPrompt ? 'Lab Prompt' : this.activeTab;
            document.getElementById('terminal-copy-title').textContent = `Copy from ${label}`;
            field.value = terminalText(termData.term);
            modal.style.display = 'flex';
            trap = trapFocus(modal, { onClose: close });
            // The latest output is at the end, where the reader looks first.
            field.scrollTop = field.scrollHeight;
            field.focus();
        });
        document.getElementById('terminal-copy-close')?.addEventListener('click', close);
        modal.addEventListener('click', (event) => {
            if (event.target === modal) close();
        });
    }

    /**
     * Wire the touch-screen Paste control. A Terminal draws on a canvas, so
     * iOS offers no Paste on it: the text is pasted into a plain field here,
     * where the system Paste works, and sent to the active Terminal as if
     * typed, so each line runs without a Return the phone has no key for.
     */
    setupPasteDialog() {
        const pasteBtn = document.getElementById('terminal-paste');
        const modal = document.getElementById('terminal-paste-modal');
        const form = document.getElementById('terminal-paste-form');
        const field = document.getElementById('terminal-paste-text');
        if (!pasteBtn || !modal || !form || !field) return;

        let trap = null;
        let target = null;
        const close = () => {
            modal.style.display = 'none';
            field.value = '';
            if (trap) {
                trap.release();
                trap = null;
            }
        };

        pasteBtn.addEventListener('click', () => {
            if (!this.activeTab || !this.terminals.has(this.activeTab)) return;
            target = this.activeTab;
            const label = this.terminals.get(target).isPrompt ? 'Lab Prompt' : target;
            document.getElementById('terminal-paste-title').textContent = `Paste into ${label}`;
            modal.style.display = 'flex';
            trap = trapFocus(modal, { onClose: close });
            field.focus();
        });

        form.addEventListener('submit', (event) => {
            event.preventDefault();
            const text = field.value;
            const termData = this.terminals.get(target);
            close();
            if (!text || !termData || !termData.ws || termData.ws.readyState !== WebSocket.OPEN) return;
            // Every line ends in a Return, the last one included: a block
            // copied from a Guide rarely ends in a newline.
            const lines = text.replace(/\r?\n$/, '').replace(/\r?\n/g, '\r');
            termData.ws.send(JSON.stringify({ type: 'input', data: lines + '\r' }));
        });
        document.getElementById('terminal-paste-cancel')?.addEventListener('click', close);
        document.getElementById('terminal-paste-close')?.addEventListener('click', close);
        modal.addEventListener('click', (event) => {
            if (event.target === modal) close();
        });
    }

    adjustActiveFontSize(delta) {
        if (!this.activeTab || !this.terminals.has(this.activeTab)) return;

        const termData = this.terminals.get(this.activeTab);
        if (!termData.term) return;

        const current = termData.term.options.fontSize || 14;
        termData.term.options.fontSize = Math.min(24, Math.max(10, current + delta));

        if (termData.term.fitAddon) {
            try {
                termData.term.fitAddon.fit();
            } catch (e) {
                console.warn('[TabManager] Error fitting terminal after font change:', e);
            }
        }
    }

    clearActiveTerminal() {
        if (!this.activeTab || !this.terminals.has(this.activeTab)) return;

        const termData = this.terminals.get(this.activeTab);
        if (termData.term) {
            termData.term.clear();
        }
    }

    /**
     * Open a Node's Terminal, or show it if open.
     *
     * @param {string} nodeName - The Node.
     * @param {string|null} terminalId - A Terminal this tab had before a
     *     reload, to reattach rather than open a new one.
     */
    openTerminal(nodeName, terminalId = null) {
        // If terminal already exists, just switch to it
        if (this.terminals.has(nodeName)) {
            this.switchToTab(nodeName);
            return;
        }

        // Create terminal instance
        const terminalInstance = this.createTerminalInstance(nodeName);

        // Store terminal data
        this.terminals.set(nodeName, {
            term: null,
            element: terminalInstance,
            tabElement: null,
            ws: null,
            isPrompt: false,
            id: terminalId
        });

        // Create and add tab
        this.createTab(nodeName);

        // Switch to new terminal
        this.switchToTab(nodeName);
    }

    openLabPrompt() {
        const promptId = 'lab-prompt';
        if (this.terminals.has(promptId)) {
            this.switchToTab(promptId);
            return;
        }

        const terminalInstance = this.createTerminalInstance(promptId);

        this.terminals.set(promptId, {
            term: null,
            element: terminalInstance,
            tabElement: null,
            ws: null,
            isPrompt: true
        });

        this.createTab(promptId, 'Lab Prompt');
        this.switchToTab(promptId);
        this.save();
    }

    createTerminalInstance(nodeName) {
        const container = document.querySelector(this.containerSelector);
        const instance = document.createElement('div');
        instance.className = 'terminal-instance';
        instance.setAttribute('data-node', nodeName);
        instance.setAttribute('data-active', 'false');
        instance.id = `terminal-${nodeName}`;

        const termDiv = document.createElement('div');
        termDiv.className = 'xterm';
        instance.appendChild(termDiv);

        container.appendChild(instance);
        return instance;
    }

    createTab(nodeName, label = null) {
        const displayLabel = label || nodeName;
        const tabBar = document.querySelector(this.tabBarSelector);

        const tab = document.createElement('button');
        tab.className = 'terminal-tab';
        tab.setAttribute('role', 'tab');
        tab.setAttribute('aria-selected', 'false');
        // Roving tabindex: only the selected tab sits in the Tab order, so a
        // keyboard user reaches the strip in one Tab press and then roves with
        // the arrow keys — matching the sidebar's running-Lab tabs.
        tab.setAttribute('tabindex', '-1');
        tab.setAttribute('aria-controls', `terminal-${nodeName}`);
        tab.setAttribute('draggable', 'true');
        tab.setAttribute('data-node', nodeName);
        tab.id = `tab-${nodeName}`;

        const nameSpan = document.createElement('span');
        nameSpan.className = 'tab-name';
        nameSpan.textContent = displayLabel;

        const closeBtn = document.createElement('button');
        closeBtn.className = 'terminal-tab-close';
        closeBtn.setAttribute('aria-label', `Close ${displayLabel} terminal`);
        closeBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';

        closeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.closeTab(nodeName);
        });

        tab.appendChild(nameSpan);

        // The close control sits beside its tab: a control nested inside a
        // tab is announced as part of it and cannot be reached on its own.
        const item = document.createElement('div');
        item.className = 'terminal-tab-item';
        item.setAttribute('role', 'presentation');
        item.appendChild(tab);
        item.appendChild(closeBtn);

        tab.addEventListener('click', () => this.switchToTab(nodeName));

        tab.addEventListener('keydown', (e) => {
            // Arrow keys, Home and End rove between tabs; Escape is owned by
            // the page-level shortcut handler, which asks for confirmation
            // before closing the terminal (a silent close here would destroy
            // the session with no warning or undo).
            if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                e.preventDefault();
                this.focusAdjacentTab(e.key === 'ArrowLeft', tab);
            } else if (e.key === 'Home' || e.key === 'End') {
                e.preventDefault();
                this.focusEdgeTab(e.key === 'Home');
            }
        });

        // Drag-and-drop event handlers for tab reordering
        tab.addEventListener('dragstart', (e) => {
            this.draggedTab = tab;
            tab.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', nodeName);
        });

        tab.addEventListener('dragend', () => {
            tab.classList.remove('dragging');
            this.draggedTab = null;
            document.querySelectorAll('.terminal-tab.drag-over').forEach(t => {
                t.classList.remove('drag-over', 'drag-over-left', 'drag-over-right');
            });
        });

        tab.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';

            if (this.draggedTab && this.draggedTab !== tab) {
                const rect = tab.getBoundingClientRect();
                const midpoint = rect.left + rect.width / 2;
                const isLeft = e.clientX < midpoint;

                tab.classList.remove('drag-over-left', 'drag-over-right');
                tab.classList.add('drag-over', isLeft ? 'drag-over-left' : 'drag-over-right');
            }
        });

        tab.addEventListener('dragleave', () => {
            tab.classList.remove('drag-over', 'drag-over-left', 'drag-over-right');
        });

        tab.addEventListener('drop', (e) => {
            e.preventDefault();
            tab.classList.remove('drag-over', 'drag-over-left', 'drag-over-right');

            if (this.draggedTab && this.draggedTab !== tab) {
                const rect = tab.getBoundingClientRect();
                const midpoint = rect.left + rect.width / 2;
                const insertBefore = e.clientX < midpoint;

                const draggedItem = this.draggedTab.parentElement;
                if (insertBefore) {
                    tabBar.insertBefore(draggedItem, item);
                } else {
                    tabBar.insertBefore(draggedItem, item.nextSibling);
                }
                this.syncTabList();
                this.save();
            }
        });

        tabBar.appendChild(item);
        this.terminals.get(nodeName).tabElement = tab;
        this.syncTabList();
    }

    async switchToTab(nodeName) {
        if (!this.terminals.has(nodeName)) {
            return;
        }

        showTerminalPane();

        const termData = this.terminals.get(nodeName);

        document.querySelectorAll('.terminal-instance').forEach(inst => {
            inst.setAttribute('data-active', 'false');
        });

        document.querySelectorAll('.terminal-tab').forEach(tab => {
            tab.setAttribute('aria-selected', 'false');
            tab.setAttribute('tabindex', '-1');
        });

        termData.element.setAttribute('data-active', 'true');
        termData.tabElement.setAttribute('aria-selected', 'true');
        termData.tabElement.setAttribute('tabindex', '0');
        termData.tabElement.focus();

        this.activeTab = nodeName;
        this.save();

        if (!termData.term) {
            await this.initializeTerminal(nodeName);
        }

        if (termData.term && termData.term.fitAddon) {
            try {
                setTimeout(() => {
                    termData.term.fitAddon.fit();
                }, 50);
            } catch (e) {
                console.error('Error fitting terminal:', e);
            }
        }

        this.labelPanelBySelectedTab();
    }

    // The tablist owns exactly the tabs, in the order they are shown, so the
    // close controls beside them stay out of its accessibility subtree.
    syncTabList() {
        const tabList = document.querySelector('.terminal-tablist');
        if (!tabList) return;
        const ids = Array.from(document.querySelectorAll('.terminal-tab')).map(tab => tab.id);
        if (ids.length) tabList.setAttribute('aria-owns', ids.join(' '));
        else tabList.removeAttribute('aria-owns');
    }

    // The Terminal tab panel takes its name from the selected tab, so a screen
    // reader says which Node's Terminal it is in; with no tab left it has none.
    labelPanelBySelectedTab() {
        const panel = document.querySelector('.terminal-content');
        if (!panel) return;
        const termData = this.activeTab && this.terminals.get(this.activeTab);
        if (termData) {
            panel.setAttribute('aria-labelledby', termData.tabElement.id);
        } else {
            panel.removeAttribute('aria-labelledby');
        }
    }

    async initializeTerminal(nodeName) {
        const termData = this.terminals.get(nodeName);

        if (typeof Terminal === 'undefined') {
            console.error('[TabManager] xterm.js not loaded');
            return;
        }

        try {
            const term = new Terminal({
                cols: 80,
                rows: 24,
                fontSize: 14,
                fontFamily: '"Cascadia Code", "Courier New", monospace',
                theme: {
                    background: '#000000',
                    foreground: '#f0f0f0',
                    cursor: '#ffffff',
                    cursorAccent: '#000000',
                    selection: 'rgba(255, 255, 255, 0.3)',
                    black: '#000000',
                    red: '#cd3131',
                    green: '#0dbc79',
                    yellow: '#e5e510',
                    blue: '#2472c8',
                    magenta: '#bc3fbc',
                    cyan: '#11a8cd',
                    white: '#e5e5e5'
                },
                cursorBlink: true,
                scrollback: 1000,
                allowTransparency: false
            });

            const FitAddonConstructor = window.FitAddon?.FitAddon || window.FitAddon;
            if (!FitAddonConstructor) {
                console.error('[TabManager] xterm-addon-fit not loaded');
                term.write('\r\n\x1b[1;31m✗ Terminal fit addon not loaded\x1b[0m\r\n');
                return;
            }

            const fitAddon = new FitAddonConstructor();
            term.loadAddon(fitAddon);

            const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
            if (isSafari) {
                const CanvasAddonConstructor = window.CanvasAddon?.CanvasAddon || window.CanvasAddon;
                if (CanvasAddonConstructor) {
                    term.loadAddon(new CanvasAddonConstructor());
                } else {
                    console.warn('[TabManager] xterm-addon-canvas not loaded for Safari');
                }
            }

            const container = termData.element.querySelector('.xterm');
            if (!container) {
                console.error('[TabManager] xterm container not found');
                return;
            }

            if (isSafari && container) {
                this.setupSafariInputProxy(termData, container);
            }

            term.open(container);
            const displayName = termData.isPrompt ? 'Lab Prompt' : nodeName;
            term.write('\x1b[1;34m[Mininet-IPLab]\x1b[0m Connecting to ' + displayName + '...');

            setTimeout(() => {
                const textarea = container.querySelector('.xterm-helper-textarea');
                if (textarea) {
                    textarea.setAttribute('id', `terminal-input-${nodeName}`);
                    textarea.setAttribute('name', `terminal-input-${nodeName}`);
                }
            }, 50);

            termData.term = term;
            termData.term.fitAddon = fitAddon;

            this.connectWebSocket(nodeName);

            const forwardInput = (data) => {
                if (termData.ws && termData.ws.readyState === WebSocket.OPEN) {
                    termData.ws.send(JSON.stringify({
                        type: 'input',
                        data: data
                    }));
                }
            };

            if (isSafari) {
                // The key proxy owns Safari typing. Listen to xterm data only
                // during its synchronous paste processing, never for keys.
                const pasteHandler = (ev) => {
                    ev.preventDefault();
                    ev.stopImmediatePropagation();
                    if (!ev.clipboardData) return;
                    const subscription = term.onData(forwardInput);
                    try {
                        term.paste(ev.clipboardData.getData('text/plain'));
                    } finally {
                        subscription.dispose();
                    }
                };
                container.addEventListener('paste', pasteHandler, true);
                termData.safariPasteHandler = pasteHandler;
            } else {
                term.onData(forwardInput);
            }

            term.onResize((dims) => {
                if (termData.ws && termData.ws.readyState === WebSocket.OPEN) {
                    termData.ws.send(JSON.stringify({
                        type: 'resize',
                        cols: dims.cols,
                        rows: dims.rows
                    }));
                }
            });

            setTimeout(() => {
                try {
                    fitAddon.fit();
                } catch (e) {
                    console.warn('Error fitting terminal:', e);
                }
            }, 100);
        } catch (err) {
            console.error('Error initializing terminal:', err);
        }
    }

    setupSafariInputProxy(termData, container) {
        const handler = (ev) => {
            if (this.activeTab !== termData.element.getAttribute('data-node')) {
                return;
            }

            const seq = this.safariKeyEventToSequence(ev);
            if (!seq) return;

            if (termData.ws && termData.ws.readyState === WebSocket.OPEN) {
                termData.ws.send(JSON.stringify({
                    type: 'input',
                    data: seq
                }));
            }

            ev.preventDefault();
            ev.stopImmediatePropagation();
        };

        container.addEventListener('keydown', handler, true);
        termData.safariKeyHandler = handler;
    }

    safariKeyEventToSequence(ev) {
        // Ctrl+<letter> maps to a C0 control byte (Ctrl+C -> \x03, Ctrl+D -> \x04, ...)
        if (ev.ctrlKey && !ev.altKey && !ev.metaKey && /^[a-z]$/i.test(ev.key)) {
            return String.fromCharCode(ev.key.toUpperCase().charCodeAt(0) - 64);
        }
        // Alt/Meta chords keep falling through to native browser handling (e.g. Cmd+C copy)
        if (ev.ctrlKey || ev.altKey || ev.metaKey) return null;
        if (ev.shiftKey && ev.key.length !== 1) return null;
        if (ev.key.length === 1) {
            return ev.key;
        }

        switch (ev.key) {
            case 'Enter': return '\r';
            case 'Backspace': return '\x7f';
            case 'Tab': return '\t';
            case 'Escape': return '\x1b';
            case 'ArrowUp': return '\x1b[A';
            case 'ArrowDown': return '\x1b[B';
            case 'ArrowRight': return '\x1b[C';
            case 'ArrowLeft': return '\x1b[D';
            case 'Home': return '\x1b[H';
            case 'End': return '\x1b[F';
            case 'Delete': return '\x1b[3~';
            case 'Insert': return '\x1b[2~';
            case 'PageUp': return '\x1b[5~';
            case 'PageDown': return '\x1b[6~';
            default: return null;
        }
    }

    connectWebSocket(nodeName, isReconnect = false) {
        const termData = this.terminals.get(nodeName);
        if (!termData) return;

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        let wsUrl;
        if (termData.isPrompt) {
            wsUrl = `${protocol}//${window.location.host}/ws/labs/${encodeURIComponent(appState.labKey || '-')}/prompt`;
        } else {
            wsUrl = `${protocol}//${window.location.host}/ws/labs/${encodeURIComponent(appState.labKey || '-')}/terminal?node=${nodeName}`;
            // Naming the Terminal this tab had asks the server for it back.
            if (termData.id) wsUrl += `&terminal=${encodeURIComponent(termData.id)}`;
        }

        if (termData.reconnectDisposable) {
            termData.reconnectDisposable.dispose();
            termData.reconnectDisposable = null;
        }
        termData.moved = false;

        if (isReconnect && termData.term) {
            termData.term.write('\r\n\x1b[1;34m[Mininet-IPLab]\x1b[0m Reconnecting...');
        }

        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            if (termData.term) {
                termData.term.write(' \x1b[1;32m✓\x1b[0m\r\n');
            }
            termData.isDisconnected = false;
        };

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);

                if (msg.type === 'output' && termData.term) {
                    termData.term.write(msg.data);
                } else if (msg.type === 'attached') {
                    this.onAttached(nodeName, msg);
                } else if (msg.type === 'ended') {
                    // The Terminal this tab named is gone; the prompt that
                    // follows the close opens a new one on the same Node.
                    termData.id = null;
                    this.save();
                    if (termData.term) {
                        termData.term.write('\r\n\x1b[1;33m⚠ ' + msg.reason + '\x1b[0m');
                    }
                } else if (msg.type === 'moved') {
                    // Another tab of this Session took the Terminal.  Dropped
                    // from this tab's list so a reload here does not take it back.
                    termData.id = null;
                    termData.moved = true;
                    this.save();
                    if (termData.term) {
                        termData.term.write('\r\n\x1b[1;33m⚠ ' + msg.reason + '\x1b[0m');
                    }
                } else if (msg.type === 'pong') {
                    // Heartbeat response
                } else if (msg.type === 'error') {
                    console.error('[TabManager] Terminal error:', msg.message);
                    if (termData.term) {
                        termData.term.write('\r\n\x1b[1;31m✗ Error: ' + msg.message + '\x1b[0m\r\n');
                    }
                }
            } catch (e) {
                console.error('[TabManager] Error parsing WebSocket message:', e);
            }
        };

        ws.onerror = (error) => {
            console.error('[TabManager] WebSocket error:', error);
            if (termData.term) {
                termData.term.write('\r\n\x1b[1;31m✗ WebSocket error: Connection failed\x1b[0m\r\n');
            }
        };

        ws.onclose = (event) => {
            console.log('[TabManager] WebSocket closed:', nodeName, 'code:', event.code);

            // A close the server gave a reason for ended the Terminal; only a
            // dropped connection leaves one to reattach.
            if (event.code === 1008 || event.code === MOVED_CLOSE_CODE || event.reason) {
                termData.id = null;
                this.save();
            }

            if (termData.term && this.terminals.has(nodeName)) {
                if (this.reattachAfterDrop(nodeName, termData)) return;
                termData.isDisconnected = true;
                this.showReconnectPrompt(nodeName, event);
            }
        };

        termData.ws = ws;
    }

    /**
     * Try to get back a Terminal whose connection dropped, after a delay.
     *
     * @param {string} nodeName - The Terminal's Node.
     * @param {object} termData - The Terminal's entry in this.terminals.
     * @returns {boolean} True when a try is scheduled; false when the Terminal
     *     has no id to name or the tries are spent, so the prompt is shown.
     */
    reattachAfterDrop(nodeName, termData) {
        const tries = termData.reattachTries || 0;
        if (!termData.id || termData.isPrompt || tries >= REATTACH_DELAYS_MS.length) {
            termData.reattachTries = 0;
            return false;
        }
        termData.reattachTries = tries + 1;
        setTimeout(() => {
            // Closed, or reconnected some other way, in the meantime.
            if (this.terminals.get(nodeName) !== termData || !termData.id) return;
            this.connectWebSocket(nodeName, true);
        }, REATTACH_DELAYS_MS[tries]);
        return true;
    }

    /**
     * Keep the id the server gave a Terminal, replay what a reattached one
     * printed, and tell the shell the size it is shown at.
     *
     * @param {string} nodeName - The Terminal's Node.
     * @param {{id: string, reattached: boolean, replay?: string}} msg - The
     *     server's word that the Terminal is attached.
     */
    onAttached(nodeName, msg) {
        const termData = this.terminals.get(nodeName);
        if (!termData) return;
        termData.id = msg.id;
        termData.reattachTries = 0;
        this.save();
        const term = termData.term;
        if (!term) return;
        if (msg.reattached) {
            term.write('\x1b[2m[Mininet-IPLab] Reattached to the running shell\x1b[0m\r\n');
            term.write(msg.replay || '');
        }
        // A size sent before the connection opened was lost; full-screen
        // programs redraw to this one.
        if (termData.ws && termData.ws.readyState === WebSocket.OPEN) {
            termData.ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
        }
    }

    // Remember this tab's open Terminals, in tab order, and the selected one.
    // The Lab Prompt is kept as a marker entry, and only for an Instructor: it
    // is shared on the server, so reopening it needs no id.
    save() {
        const instructor = appState.currentUserRole === 'instructor';
        const tabs = Array.from(document.querySelectorAll('.terminal-tab'))
            .map(tab => tab.getAttribute('data-node'))
            .filter(node => this.terminals.has(node))
            .filter(node => {
                const termData = this.terminals.get(node);
                return termData.isPrompt ? instructor : !termData.moved;
            })
            .map(node => (this.terminals.get(node).isPrompt
                ? { node, prompt: true }
                : { node, id: this.terminals.get(node).id || null }));
        try {
            if (tabs.length) {
                sessionStorage.setItem(storageKey(), JSON.stringify({ tabs, selected: this.activeTab }));
            } else {
                sessionStorage.removeItem(storageKey());
            }
        } catch (e) {
            // Storage may be unavailable (private mode); a reload then loses the tabs.
        }
    }

    // After a reload, reopen this tab's Terminals for the Lab, once per Lab,
    // in their old order with the same one selected; each reattaches.
    restore() {
        if (!appState.labKey || this.restoredFor === appState.labKey) return;
        this.restoredFor = appState.labKey;
        if (this.terminals.size) return;
        const saved = remembered();
        saved.tabs.forEach(t => {
            if (!t.prompt) {
                this.openTerminal(t.node, t.id || null);
            } else if (appState.currentUserRole === 'instructor') {
                this.openLabPrompt();
            }
        });
        if (saved.selected && this.terminals.has(saved.selected)) {
            this.switchToTab(saved.selected);
        }
    }

    showReconnectPrompt(nodeName, closeEvent) {
        const termData = this.terminals.get(nodeName);
        if (!termData || !termData.term) return;

        // 1008 is the server refusing this terminal on purpose — the per-session
        // cap, the reconnect budget, or a session that has ended. Reconnecting on
        // any keystroke turns one refusal into a burst of them, which is how a
        // learner who was over the cap ended up being told they were reconnecting
        // too fast instead. A refusal gets the deliberate click only.
        // A Terminal moved to another tab gets the click only too: a keystroke
        // here would open a new shell the Learner did not ask for.
        const moved = Boolean(closeEvent) && closeEvent.code === MOVED_CLOSE_CODE;
        const wasRefused = moved || (Boolean(closeEvent) && closeEvent.code === 1008);

        termData.term.write('\r\n\x1b[1;33m⚠ Disconnected\x1b[0m');
        termData.term.write(moved
            ? '\r\n\x1b[90m\x1b[1;36mClick here\x1b[0m\x1b[90m to open a new Terminal here.\x1b[0m\r\n'
            : wasRefused
            ? '\r\n\x1b[90m\x1b[1;36mClick here\x1b[0m\x1b[90m to try again.\x1b[0m\r\n'
            : '\r\n\x1b[90mPress any key or \x1b[1;36mclick here\x1b[0m\x1b[90m to reconnect...\x1b[0m\r\n');

        if (!wasRefused) {
            const reconnectOnKey = (data) => {
                if (termData.isDisconnected) {
                    if (termData.reconnectDisposable) {
                        termData.reconnectDisposable.dispose();
                        termData.reconnectDisposable = null;
                    }

                    this.connectWebSocket(nodeName, true);
                }
            };

            termData.reconnectDisposable = termData.term.onData(reconnectOnKey);
        }

        const terminalElement = termData.element.querySelector('.xterm');
        if (terminalElement) {
            const clickHandler = () => {
                if (termData.isDisconnected) {
                    terminalElement.removeEventListener('click', clickHandler);

                    if (termData.reconnectDisposable) {
                        termData.reconnectDisposable.dispose();
                        termData.reconnectDisposable = null;
                    }

                    this.connectWebSocket(nodeName, true);
                }
            };
            terminalElement.addEventListener('click', clickHandler, { once: true });
        }
    }

    closeTab(nodeName) {
        if (!this.terminals.has(nodeName)) {
            return;
        }

        const termData = this.terminals.get(nodeName);

        if (termData.reconnectDisposable) {
            termData.reconnectDisposable.dispose();
            termData.reconnectDisposable = null;
        }

        if (termData.safariKeyHandler) {
            const container = termData.element.querySelector('.xterm');
            if (container) {
                container.removeEventListener('keydown', termData.safariKeyHandler, true);
            }
            termData.safariKeyHandler = null;
        }

        if (termData.safariPasteHandler) {
            const container = termData.element.querySelector('.xterm');
            if (container) {
                container.removeEventListener('paste', termData.safariPasteHandler, true);
            }
            termData.safariPasteHandler = null;
        }

        if (termData.ws) {
            // Said first so the server ends the shell now instead of keeping
            // it for a reload; a connection still opening says it once open.
            const ws = termData.ws;
            ws.onmessage = null;
            const closeOnPurpose = () => {
                if (!termData.isPrompt) ws.send(JSON.stringify({ type: 'close' }));
                ws.close();
            };
            if (ws.readyState === WebSocket.OPEN) closeOnPurpose();
            else if (ws.readyState === WebSocket.CONNECTING) ws.onopen = closeOnPurpose;
            else if (termData.id && !termData.isPrompt) this.endDetachedTerminal(nodeName, termData.id);
        }

        if (termData.term) {
            termData.term.dispose();
        }

        termData.element.remove();
        termData.tabElement.parentElement.remove();
        this.syncTabList();

        this.terminals.delete(nodeName);
        this.save();

        if (this.activeTab === nodeName) {
            const remainingNodes = Array.from(this.terminals.keys());
            if (remainingNodes.length > 0) {
                this.switchToTab(remainingNodes[0]);
            } else {
                this.activeTab = null;
                this.labelPanelBySelectedTab();
            }
        }

        // The Terminal pane takes no room once its last Terminal closes.
        layoutPanes();
    }

    /**
     * End a Terminal whose connection dropped: its shell is detached on the
     * server, so reattach on a short-lived connection just to close it.
     *
     * @param {string} nodeName - The Terminal's Node.
     * @param {string} id - The Terminal's id.
     */
    endDetachedTerminal(nodeName, id) {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}/ws/labs/${encodeURIComponent(appState.labKey || '-')}`
            + `/terminal?node=${nodeName}&terminal=${encodeURIComponent(id)}`;
        const ws = new WebSocket(url);
        ws.onmessage = (event) => {
            try {
                if (JSON.parse(event.data).type === 'attached') {
                    ws.send(JSON.stringify({ type: 'close' }));
                }
            } catch (e) {
                // Anything else means there is nothing left to close.
            }
            ws.close();
        };
    }

    /**
     * Close every Terminal and forget them for the Lab.
     *
     * @param {string|null} [labKey] - The Lab whose remembered list to clear;
     *     the caller passes the old key when the page has already let go of it.
     */
    closeAllTerminals(labKey = appState.labKey) {
        const nodeNames = Array.from(this.terminals.keys());
        nodeNames.forEach(nodeName => this.closeTab(nodeName));
        this.restoredFor = null;
        try {
            sessionStorage.removeItem(STORAGE_PREFIX + (labKey || '-'));
        } catch (e) {
            // Storage may be unavailable (private mode).
        }
    }

    focusAdjacentTab(goLeft, currentTab) {
        const tabs = Array.from(document.querySelectorAll('.terminal-tab'));
        if (!currentTab) return;

        const currentIndex = tabs.indexOf(currentTab);
        let nextIndex;

        if (goLeft) {
            nextIndex = currentIndex > 0 ? currentIndex - 1 : tabs.length - 1;
        } else {
            nextIndex = currentIndex < tabs.length - 1 ? currentIndex + 1 : 0;
        }

        if (tabs[nextIndex]) {
            const nextNodeName = tabs[nextIndex].getAttribute('aria-controls').replace('terminal-', '');
            this.switchToTab(nextNodeName);
        }
    }

    focusEdgeTab(first) {
        const tabs = Array.from(document.querySelectorAll('.terminal-tab'));
        const edge = first ? tabs[0] : tabs[tabs.length - 1];
        if (edge) this.switchToTab(edge.getAttribute('aria-controls').replace('terminal-', ''));
    }

    getTerminalCount() {
        return this.terminals.size;
    }
}
