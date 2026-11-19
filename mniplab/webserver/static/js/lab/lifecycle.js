import { refreshInstructorConnections } from './instructor-connections.js';
import { classOverviewShown, syncLabCrumb } from '../ui/workspace-view.js';
import { appState, canManageLabs } from '../state/app-state.js';
import { getTopology, getStatus, startLab as apiStartLab, stopLab as apiStopLab, loadLayout } from '../services/api.js';
import { connectLabsWebSocket, connectLabWebSocket, closeLabWebSocket, sendPong } from '../services/websocket.js';
import { syncUrlToLabKey } from './routing.js';
import { initTopology, renderTopology, ticked } from '../topology/renderer.js';
import { loadGroups, updateGroups } from '../topology/groups.js';
import { applyAuthoredLayout, fitTopology, captureAndSaveLayout, releaseAllNodes } from '../topology/layout.js';
import { showSidebarLabs, setTerminalToggleAvailable, closeSidebar } from '../ui/sidebar.js';
import { layoutPanes } from '../ui/pane-arrangement.js';
import { showLearnerStartScreen, renderStartScreen } from './start-screen.js';
import { syncRunningGuide } from './guide.js';

import { renderSidebarLabList } from './sidebar-labs.js';
import { showConfirmModal } from '../utils/modal.js';
import { getCurrentLabName, formatResponseError } from '../utils/dom.js';
import { clearLabGuide } from './guide.js';
// handleRoleDesync is imported dynamically below to avoid circular dependency with auth.js

export function showTopologyEmptyState(show) {
    const emptyState = document.getElementById('topology-empty-state');
    const topologySvg = document.getElementById('topology');
    const startScreen = document.getElementById('start-screen');
    if (emptyState) emptyState.style.display = show ? '' : 'none';
    if (topologySvg) topologySvg.style.display = show ? 'none' : '';
    const canvasControls = document.getElementById('topology-canvas-controls');
    if (canvasControls) canvasControls.hidden = show;
    if (startScreen) startScreen.style.display = 'none';
    // Whether a Lab runs decides which Panes are on show.
    layoutPanes();
}

export function updateEmptyStateText() {
    const hint = document.getElementById('empty-state-hint');
    if (!hint) return;
    if (appState.labMode === 'multi') {
        hint.textContent = 'This Group has no Lab running.';
    } else if (canManageLabs()) {
        hint.textContent = 'Select a lab from the sidebar to get started.';
    } else {
        hint.textContent = 'Waiting for a lab to be started.';
    }
}

// The no-Lab state, entered from every route that lands here: login, a Lab
// stopping, and a cross-session poll. Owns which surface carries the
// no-Lab message — the Learner's start screen in Multi-Lab Mode, the
// sidebar Lab list for a manager, the plain empty state otherwise.
export async function enterNoLabState() {
    showTopologyEmptyState(true);
    if (appState.labMode === 'multi') {
        closeSidebar();
        if (appState.currentUserRole === 'learner') {
            showLearnerStartScreen(true);
            await renderStartScreen();
        } else {
            showLearnerStartScreen(false);
            updateEmptyStateText();
        }
    } else if (canManageLabs()) {
        showSidebarLabs();
        await renderSidebarLabList();
        updateEmptyStateText();
    } else {
        closeSidebar();
        updateEmptyStateText();
    }
}

export function clearTopologyState() {
    if (appState.svg) { appState.svg.selectAll('*').remove(); }
    appState.svg = null;
    appState.g = null;
    if (appState.simulation) { appState.simulation.stop(); appState.simulation = null; }
    appState.nodeElements = null;
    appState.linkElements = null;
    appState.labelElements = null;
    appState.textElements = null;
    appState.groupElements = null;
    appState.groups = [];
    appState.selectedNodes = new Set();
    appState.selectedGroup = null;
    appState.gridPattern = null;
    appState.currentTopology = null;
    // Drop the previous lab's Authored Layout and re-arm the bootstrap flag, so
    // the next lab fetches its own layout fresh (the first-render guard keys off
    // authoredLayout === null) and its force simulation can run if it needs one.
    appState.authoredLayout = null;
    appState.isInitialLoad = true;
    // hideLinkContextMenu will be handled by the caller or keyboard shortcut
}

// The Lab Prompt is the Instructor's: one screen shared by everyone attached
// to a Lab, addressing the Lab as a whole rather than any Node. A Learner
// drives their Lab through Node Terminals and the Topology, so the control
// is not offered to them at all.
function showLabPrompt(running) {
    return !!running && appState.currentUserRole === 'instructor';
}

// Save as Authored Layout writes a shipped Lab Example's
// examples/layouts/<lab>.json. An authored Lab Example's Authored Layout lives
// in its Draft and its published versions, which /api/save_layout refuses
// (409): it is edited on the authoring canvas, so the control is not offered
// for an authored or Preview Lab.
export function syncSaveLayoutButton() {
    const saveLayoutBtn = document.getElementById('save-layout-btn');
    if (!saveLayoutBtn) return;
    const lab = getCurrentLabName();
    const authored = lab.startsWith('authored-') || lab.startsWith('preview-');
    saveLayoutBtn.style.display =
        appState.currentUserRole === 'instructor' && !authored ? '' : 'none';
}

export async function updateStatus() {
    if (!appState.isAuthenticated) return;

    try {
        const response = await getStatus();
        const data = await response.json();

        rememberLab(data);
        const labName = document.getElementById('lab-name');
        labName.textContent = data.example_name || data.example || '-';
        labName.title = labName.textContent;
        document.getElementById('node-count').textContent = data.nodes || '-';
        document.getElementById('terminal-count').textContent = data.active_terminals || '0';
        const labPromptBtn = document.getElementById('lab-prompt-btn');
        if (labPromptBtn) labPromptBtn.style.display = showLabPrompt(data.running) ? '' : 'none';
        syncSaveLayoutButton();
    } catch (error) {
        console.error('Failed to update status:', error);
    }
}

// Bind the page to the Lab the server reports: its key addresses every
// Lab route, its feed is opened or closed to match, and the URL is kept in
// lockstep so a reload or a shared link lands back on the same Lab.
export function rememberLab(data) {
    // The Labs feed reports only the viewer's OWN Lab. While the Instructor
    // is bound to another Group's Lab (boundGroup set), that feed is about a
    // different Lab and must not clobber the binding — otherwise the page
    // drops the key and every Lab route (terminals, prompt, topology) 404s
    // with the '-' placeholder.
    if (appState.boundGroup && data && !data.running) {
        return;
    }
    // The feed can render before Guide loading completes. Bind its example
    // first so that first render fetches this Lab's Authored Layout.
    if (data && data.running) {
        const labName = document.getElementById('lab-name');
        if (labName) {
            labName.dataset.example = data.example || '';
            // A payload that carries only the id (the start response) must
            // not replace a display name with it.
            if (data.example_name || !labName.textContent || labName.textContent === '-') {
                labName.textContent = data.example_name || data.example || '-';
            }
            labName.title = labName.textContent;
        }
        syncSaveLayoutButton();
    }
    appState.labKey = data && data.running ? (data.key || null) : null;
    appState.hasGuide = data && data.running ? (data.has_guide ?? null) : null;
    const overview = classOverviewShown();
    syncUrlToLabKey(overview ? null : appState.labKey);
    syncLabCrumb(overview);
    if (appState.labKey) {
        connectLabWebSocket();
    } else {
        closeLabWebSocket();
    }
}

export async function updateStatusFromData(data) {
    const labName = document.getElementById('lab-name');
    const nodeCount = document.getElementById('node-count');
    const terminalCount = document.getElementById('terminal-count');
    const labPromptBtn = document.getElementById('lab-prompt-btn');

    // rememberLab lets go of the key when the Lab has stopped; the Terminals
    // remembered under it must still be forgotten.
    const previousLabKey = appState.labKey;
    rememberLab(data);
    if (data.running) {
        // Cross-session transition: another browser session started a lab.
        // Move this session from labs view to nodes/topology view.
        if (!appState.isLabRunning) {
            appState.isLabRunning = true;
            await syncRunningGuide(data.example || null);
            showTopologyEmptyState(false);

            const stopLabBtn = document.getElementById('stop-lab-btn');
            if (stopLabBtn && canManageLabs()) stopLabBtn.style.display = '';
            setTerminalToggleAvailable(true);

            refreshInstructorConnections();
            if (!appState.svg) {
                initTopology();
            }
        } else if (appState.loadedGuideLabId !== (data.example || null)) {
            await clearLabGuide(); // will reload via syncRunningGuide
        }

        if (labName) {
            labName.textContent = data.example_name || data.example || '-';
            labName.title = labName.textContent;
        }
        if (nodeCount) nodeCount.textContent = data.nodes || '0';
        if (terminalCount) terminalCount.textContent = data.active_terminals || '0';
        if (labPromptBtn) labPromptBtn.style.display = showLabPrompt(true) ? '' : 'none';
    } else {
        // Cross-session transition: another browser session stopped the lab.
        // Return to labs view and clear topology state.
        if (appState.isLabRunning) {
            appState.isLabRunning = false;

            if (appState.packetAnalysis) {
                appState.packetAnalysis.closeAll();
            }
            if (appState.tabManager) {
                appState.tabManager.closeAllTerminals(previousLabKey);
            }

            clearTopologyState();
            clearLabGuide();
            refreshInstructorConnections();
            await enterNoLabState();

            const stopLabBtn = document.getElementById('stop-lab-btn');
            if (stopLabBtn) stopLabBtn.style.display = 'none';
            setTerminalToggleAvailable(false);
        }

        if (labName) {
            labName.dataset.example = '';
            labName.textContent = 'No lab running';
            labName.title = labName.textContent;
        }
        if (nodeCount) nodeCount.textContent = '-';
        if (terminalCount) terminalCount.textContent = '-';
        if (labPromptBtn) labPromptBtn.style.display = 'none';
    }
    syncSaveLayoutButton();
}

export async function loadTopologyFromData(data) {
    if (data.error) {
        // If server reports no running lab, keep UI in labs mode.
        if (appState.isLabRunning) {
            void updateStatusFromData({ running: false });
        }
        console.error('Topology error:', data.error);
        return;
    }

    // Ensure topology canvas exists if this update arrived due to
    // another session starting a lab.
    if (!appState.svg) {
        initTopology();
    }

    // If this WebSocket update is the first render for the lab (no simulation
    // yet) and loadTopology has not already fetched the Authored Layout, fetch
    // it now so the nodes are placed statically instead of bootstrapping the
    // force simulation — otherwise the update could race ahead and flicker.
    if (!appState.simulation && appState.authoredLayout === null) {
        appState.authoredLayout = await fetchAuthoredLayout(getCurrentLabName());
    }

    appState.currentTopology = data;
    renderTopology(data);
    updateGroups();
    // A reload reopens the Packet Capture and the Terminals this page had open.
    if (appState.packetAnalysis) appState.packetAnalysis.restore();
    if (appState.tabManager) appState.tabManager.restore();
}

// The no-Lab surface that owns the Start button being pressed: the Learner's
// start screen in Multi-Lab Mode, the sidebar Lab list otherwise.
function noLabSurface() {
    const startScreen = document.getElementById('start-screen');
    if (startScreen && startScreen.style.display !== 'none') {
        return {
            statusEl: document.getElementById('start-screen-status'),
            statusText: document.getElementById('start-screen-status-text'),
            busyEl: document.getElementById('start-screen-cards'),
            startButtons: document.querySelectorAll('.start-screen-start, .start-screen-start-clean'),
        };
    }
    return {
        statusEl: document.getElementById('sidebar-lab-status'),
        statusText: document.getElementById('sidebar-lab-status-text'),
        busyEl: document.getElementById('sidebar-lab-list'),
        startButtons: document.querySelectorAll('.sidebar-lab-start'),
    };
}

// `clean` is "start clean": the Group's copy of the Lab Example's Persistent
// Config is discarded first, so the Lab starts from the seed.
export async function startLab(exampleId, clean = false) {
    const surface = noLabSurface();
    const statusEl = surface.statusEl;
    const statusText = surface.statusText;
    const list = surface.busyEl;

    if (statusEl) statusEl.classList.remove('hidden');
    if (list) list.setAttribute('aria-busy', 'true');
    if (statusText) statusText.textContent = 'Starting lab… this may take a few seconds.';

    surface.startButtons.forEach(btn => { btn.disabled = true; });

    try {
        const res = await apiStartLab(exampleId, null, clean);

        if (!res.ok) {
            if (res.status === 403) {
                const authModule = await import('./auth.js');
                await authModule.handleRoleDesync();
                if (statusText) statusText.textContent = 'Permission denied — your session role has changed.';
                surface.startButtons.forEach(btn => { btn.disabled = false; });
                return;
            }
            const data = await res.json().catch(() => ({}));
            if (res.status === 409 && appState.currentUserRole === 'learner'
                && appState.labMode === 'multi') {
                await renderStartScreen();
            }
            throw new Error(data.detail || `Server error ${res.status}`);
        }

        const started = await res.json().catch(() => ({}));
        if (started.key) rememberLab({ running: true, key: started.key, example: exampleId });

        if (statusText) statusText.textContent = 'Waiting for lab to become ready…';
        await pollUntilRunning(60000);

        // Transition: switch sidebar to running-lab mode and reveal topology
        appState.isLabRunning = true;
        await syncRunningGuide(exampleId);
        const stopLabBtn = document.getElementById('stop-lab-btn');
        if (stopLabBtn && canManageLabs()) stopLabBtn.style.display = '';
        setTerminalToggleAvailable(true);
        showTopologyEmptyState(false);
        refreshInstructorConnections();
        initTopology();
        await updateStatus();
        loadTopology();
        connectLabsWebSocket();

    } catch (err) {
        console.error('Failed to start lab:', err);
        if (statusText) statusText.textContent = `Error: ${err.message}`;
        surface.startButtons.forEach(btn => { btn.disabled = false; });
        return;
    }

    if (statusEl) statusEl.classList.add('hidden');
    if (list) list.removeAttribute('aria-busy');
    surface.startButtons.forEach(btn => { btn.disabled = false; });
}

export async function stopLab() {
    const key = appState.labKey;
    if (appState.boundGroup && document.getElementById('lab-tab-current')?.dataset.labKey !== key) return;
    const name = document.getElementById('lab-name').textContent;
    const group = appState.boundGroup || appState.currentGroup;
    const target = appState.labMode === 'multi' ? `${name} for Group ${group}` : name;
    showConfirmModal({
        title: 'Stop Lab',
        message: `Stop ${target}?\n\nAll Terminals in this Lab will be closed.`,
        confirmText: 'Stop Lab',
        cancelText: 'Cancel',
        onConfirm: () => {
            // A changed binding requires a new confirmation for that Lab.
            if (appState.labKey === key && (appState.boundGroup || appState.currentGroup) === group) {
                void stopLabConfirmed();
            }
        }
    });
}

export async function stopLabConfirmed() {
    const stopLabBtn = document.getElementById('stop-lab-btn');
    if (stopLabBtn) { stopLabBtn.disabled = true; stopLabBtn.textContent = 'Stopping…'; }

    try {
        const res = await apiStopLab();
        if (res.status === 403) {
            const authModule = await import('./auth.js');
            await authModule.handleRoleDesync();
            if (stopLabBtn) {
                stopLabBtn.disabled = false;
                stopLabBtn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect></svg> Stop Lab`;
            }
            return;
        }
        if (!res.ok && res.status !== 409) {
            throw new Error(`Stop failed: ${res.status}`);
        }
    } catch (err) {
        console.error('Failed to stop lab:', err);
    }

    // Close the Packet Capture pane and all open terminal tabs
    if (appState.packetAnalysis) {
        appState.packetAnalysis.closeAll();
    }
    if (appState.tabManager) {
        appState.tabManager.closeAllTerminals();
    }

    // Restore Stop Lab button state
    if (stopLabBtn) {
        stopLabBtn.disabled = false;
        stopLabBtn.style.display = 'none';
        stopLabBtn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect></svg> Stop Lab`;
    }

    const labPromptBtn = document.getElementById('lab-prompt-btn');
    if (labPromptBtn) labPromptBtn.style.display = 'none';

    // Clear the topology graph
    clearTopologyState();
    clearLabGuide();

    appState.isLabRunning = false;
    rememberLab({ running: false });
    setTerminalToggleAvailable(false);
    refreshInstructorConnections();
    await enterNoLabState();
}

export async function pollUntilRunning(maxWaitMs = 30000) {
    const start = Date.now();
    while (Date.now() - start < maxWaitMs) {
        try {
            const res = await getStatus();
            if (res.ok) {
                const data = await res.json();
                if (data.running) {
                    rememberLab(data);
                    return;
                }
            }
        } catch (_) { /* network blip — keep trying */ }
        await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw new Error('Lab did not become ready within the timeout period.');
}

// Fetch the current lab's Authored Layout fresh from the server. Returns the
// canonical {version, nodes:{id:{x,y}}} document, or null when the lab has no
// Authored Layout (load_layout responds 200 with layout: null). A malformed
// layout file fails loud server-side (500, per load_layout's docstring), so
// that and any other request failure is surfaced to the Instructor here too —
// bootstrapping the force simulation silently would hide a real problem.
async function fetchAuthoredLayout(labName) {
    const statusEl = document.getElementById('layout-status');
    try {
        const resp = await loadLayout(labName, appState.labKey);
        if (!resp.ok) {
            const body = await resp.json().catch(() => ({}));
            console.error('Authored layout fetch failed:', resp.status, body.detail || body);
            flashStatus(
                statusEl,
                `Authored layout error (${resp.status}) — starting from an auto-arranged layout.`,
                6000
            );
            return null;
        }
        const data = await resp.json();
        if (data.layout && data.layout.nodes) return data.layout;
    } catch (e) {
        console.error('Failed to fetch authored layout:', e);
        flashStatus(statusEl, 'Could not reach the server for the authored layout.', 6000);
    }
    return null;
}

export async function loadTopology() {
    if (!appState.isAuthenticated) return;

    try {
        const response = await getTopology();

        if (response.status === 401) {
            // apiFetch has already sent the page back to the login page.
            return;
        }

        const data = await response.json();

        if (data.error) {
            console.error('Topology error:', data.error);
            return;
        }

        // The Authored Layout is the source of truth: fetch it fresh on every
        // load/switch and hand it to renderTopology, which applies it statically
        // so a returning viewer always sees the intended arrangement and no
        // stale client copy can shadow a shipped update.
        appState.authoredLayout = await fetchAuthoredLayout(getCurrentLabName());

        loadGroups();
        renderTopology(data);
        updateGroups();
    } catch (error) {
        console.error('Failed to load topology:', error);
    }
}

// Set statusEl's text, then clear it after `delay`ms. Shared by every
// layout-control handler below so the flash-then-clear behaviour stays
// consistent instead of being reimplemented per handler.
function flashStatus(statusEl, text, delay = 3000) {
    if (statusEl) statusEl.textContent = text;
    setTimeout(() => {
        if (statusEl) statusEl.textContent = '';
    }, delay);
}

// Unpin every node and let the bootstrap force simulation re-arrange them.
// Confirmed first: unlike "Reset to Authored Layout" this is not reversible —
// a Personal Layout is never persisted, so the arrangement it
// discards has no other route back, and the one that replaces it is not one the
// Learner chose or can predict.
export async function handleAutoArrangeClick() {
    showConfirmModal({
        title: 'Auto-Arrange Nodes',
        message: 'Re-arrange every node automatically?\n\nYour current arrangement will be discarded, and cannot be recovered.',
        confirmText: 'Re-arrange',
        cancelText: 'Cancel',
        onConfirm: releaseAllNodes
    });
}

// Reset the canvas to the lab's Authored Layout, in place and without a reload:
// re-fetch it fresh and re-apply it statically over any Personal Layout drags.
export async function handleRestoreClick() {
    const statusEl = document.getElementById('layout-status');
    if (statusEl) statusEl.textContent = 'Restoring layout...';

    const labName = getCurrentLabName();
    if (!labName) {
        flashStatus(statusEl, 'Unknown lab; cannot load layout.');
        return;
    }

    try {
        const authored = await fetchAuthoredLayout(labName);
        if (!authored) {
            flashStatus(statusEl, 'No authored layout on server.');
            return;
        }

        appState.authoredLayout = authored;

        if (appState.simulation && appState.simulation.nodes) {
            applyAuthoredLayout(appState.simulation.nodes(), authored);
            appState.simulation.stop();
            if (appState.nodeElements) appState.nodeElements.classed('pinned', true);
            ticked();
            fitTopology();
        }

        flashStatus(statusEl, 'Restored authored layout.');
    } catch (e) {
        console.error('Failed to restore authored layout', e);
        flashStatus(statusEl, 'Restore layout failed');
    }
}

// Instructor-only: capture the current canvas arrangement into the Lab
// Example. The route itself is gated with require_role("instructor"), so a
// Learner reaching this handler (e.g. via a stale UI state) still gets a 403
// from the server; the button is hidden for learners in applyRoleToUI.
export async function handleSaveLayoutClick() {
    const statusEl = document.getElementById('layout-status');
    if (statusEl) statusEl.textContent = 'Saving layout...';

    try {
        const resp = await captureAndSaveLayout();
        if (!resp) {
            flashStatus(statusEl, 'No topology to save.');
        } else if (!resp.ok) {
            const body = await resp.json().catch(() => null);
            flashStatus(statusEl, formatResponseError(body, `Save failed (${resp.status})`), 6000);
        } else {
            flashStatus(statusEl, 'Layout saved.');
        }
    } catch (e) {
        console.error('Failed to save layout to server', e);
        flashStatus(statusEl, 'Save layout failed');
    }
}

export function handleGridToggle(e) {
    appState.showGrid = e.target.checked;
    if (appState.svg) {
        appState.svg.select('.grid-background')
            .style('display', appState.showGrid ? 'block' : 'none');
    }
}

export function handleLabelsToggle(e) {
    appState.showLabels = e.target.checked;
    if (appState.textElements) {
        appState.textElements.style('display', appState.showLabels ? 'block' : 'none');
    }
}

export function handleVlanLabelsToggle(e) {
    appState.showVlanLabels = e.target.checked;
    applyVlanLabelsVisibility();
}

/** Hide or show the VLAN and trunk badges per the View menu's option. */
export function applyVlanLabelsVisibility() {
    if (!appState.g) return;
    appState.g.select('.layer-labels').classed('hide-vlan-labels', !appState.showVlanLabels);
}

export function handleInterfacesToggle(e) {
    appState.showInterfaceLabels = e.target.checked;
    if (appState.labelElements) {
        appState.labelElements.selectAll('.link-label')
            .style('display', appState.showInterfaceLabels ? 'block' : 'none');
    }
}
