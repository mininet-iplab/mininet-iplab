import { appState, canManageLabs } from '../state/app-state.js';
import { getStatus, getMe, login, logout } from '../services/api.js';
import { forgetRememberedTerminals } from '../state/remembered-terminals.js';
import { connectLabsWebSocket, closeLabWebSocket } from '../services/websocket.js';
import { setTerminalToggleAvailable } from '../ui/sidebar.js';
import { showTopologyEmptyState, updateEmptyStateText, enterNoLabState } from './lifecycle.js';
import { syncRunningGuide } from './guide.js';
import { initTopology } from '../topology/renderer.js';
import { loadTopology } from './lifecycle.js';
import { updateStatus, rememberLab, syncSaveLayoutButton } from './lifecycle.js';

import { startInstructorConnections, stopInstructorConnections, refreshInstructorConnections } from './instructor-connections.js';
import { startInstructorDashboard, stopInstructorDashboard } from './instructor-dashboard.js';
import { startLabTabstrip, stopLabTabstrip } from '../ui/tabstrip.js';
import { initViewOptionsMenu } from '../ui/view-options.js';
import { closeWorkspaceMonitor, syncWorkspaceView } from '../ui/workspace-view.js';
import { setupLinkContextMenuListeners } from '../topology/interactions.js';
import { applySessionIdentity, closeSessionIdentity } from './session-identity.js';
import { applyServerSettingsAvailability, closeServerSettings } from './server-settings.js';
import { labKeyFromLocation, syncUrlToLabKey } from './routing.js';

export function showPage(pageId) {
    const pages = ['login-page', 'app'];
    pages.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        if (id === pageId) {
            el.classList.remove('hidden');
            el.removeAttribute('hidden');
            // Both the app shell (flex column) and the login page are flex
            // laid out; the inline display just overrides the .hidden rule.
            el.style.display = 'flex';
        } else {
            el.classList.add('hidden');
            el.style.display = 'none';
        }
    });
}

export function showLoginPage() {
    closeSessionIdentity();
    closeServerSettings();
    closeWorkspaceMonitor();
    showPage('login-page');
    appState.isAuthenticated = false;

    const form = document.getElementById('login-form');
    if (form) {
        form.removeEventListener('submit', handleLogin);
        form.addEventListener('submit', handleLogin);
    }
}

export async function showMainApp() {
    showPage('app');

    // Keep all authenticated sessions subscribed to the Labs feed, even when
    // no lab is currently running (labs sidebar view). This ensures
    // cross-session start/stop events are reflected immediately.
    connectLabsWebSocket();

    // Remove old listeners and add new ones to avoid duplicates
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.removeEventListener('click', handleLogout);
        logoutBtn.addEventListener('click', handleLogout);
    }

    const autoArrangeBtn = document.getElementById('auto-arrange-nodes');
    if (autoArrangeBtn) {
        autoArrangeBtn.removeEventListener('click', handleAutoArrangeClick);
        autoArrangeBtn.addEventListener('click', handleAutoArrangeClick);
    }

    const fitBtn = document.getElementById('fit-view');
    if (fitBtn) {
        fitBtn.removeEventListener('click', fitTopology);
        fitBtn.addEventListener('click', fitTopology);
    }

    const zoomInBtn = document.getElementById('zoom-in');
    if (zoomInBtn) {
        zoomInBtn.removeEventListener('click', handleZoomIn);
        zoomInBtn.addEventListener('click', handleZoomIn);
    }

    const zoomOutBtn = document.getElementById('zoom-out');
    if (zoomOutBtn) {
        zoomOutBtn.removeEventListener('click', handleZoomOut);
        zoomOutBtn.addEventListener('click', handleZoomOut);
    }

    const toggleGrid = document.getElementById('toggle-grid');
    if (toggleGrid) {
        toggleGrid.removeEventListener('change', handleGridToggle);
        toggleGrid.addEventListener('change', handleGridToggle);
    }

    const toggleLabels = document.getElementById('toggle-labels');
    if (toggleLabels) {
        toggleLabels.removeEventListener('change', handleLabelsToggle);
        toggleLabels.addEventListener('change', handleLabelsToggle);
    }

    const toggleInterfaces = document.getElementById('toggle-interfaces');
    if (toggleInterfaces) {
        toggleInterfaces.removeEventListener('change', handleInterfacesToggle);
        toggleInterfaces.addEventListener('change', handleInterfacesToggle);
    }

    const toggleVlanLabels = document.getElementById('toggle-vlan-labels');
    if (toggleVlanLabels) {
        toggleVlanLabels.removeEventListener('change', handleVlanLabelsToggle);
        toggleVlanLabels.addEventListener('change', handleVlanLabelsToggle);
    }

    const restoreBtn = document.getElementById('restore-layout-btn');
    if (restoreBtn) {
        restoreBtn.removeEventListener('click', handleRestoreClick);
        restoreBtn.addEventListener('click', handleRestoreClick);
    }

    const saveLayoutBtn = document.getElementById('save-layout-btn');
    if (saveLayoutBtn) {
        saveLayoutBtn.removeEventListener('click', handleSaveLayoutClick);
        saveLayoutBtn.addEventListener('click', handleSaveLayoutClick);
    }

    const createGroupBtn = document.getElementById('create-group-btn');
    if (createGroupBtn) {
        createGroupBtn.removeEventListener('click', handleCreateGroup);
        createGroupBtn.addEventListener('click', handleCreateGroup);
    }

    const deleteGroupBtn = document.getElementById('delete-group-btn');
    if (deleteGroupBtn) {
        deleteGroupBtn.removeEventListener('click', handleDeleteGroup);
        deleteGroupBtn.addEventListener('click', handleDeleteGroup);
    }

    initViewOptionsMenu();
    setupLinkContextMenuListeners();
}

// The login shell can be served at /authoring after session expiry. Reload
// that same URL once authenticated so its Instructor-only page is served.
function returnToAuthoring() {
    if (window.location.pathname !== '/authoring') return false;
    window.location.reload();
    return true;
}

export async function checkAuthentication() {
    const requestedKey = labKeyFromLocation();
    const main = document.getElementById('main');
    try {
        const response = await getStatus();

        if (response.status === 401) {
            showLoginPage();
        } else {
            appState.isAuthenticated = true;
            await fetchAndApplyRole();
            if (returnToAuthoring()) return;
            // Keep layout dimensions for own-Lab initialization, but do not
            // paint its workspace while the root URL restores Overview.
            const startOnOverview = appState.labMode === 'multi'
                && appState.currentUserRole === 'instructor' && !requestedKey;
            if (startOnOverview && main) main.style.visibility = 'hidden';
            // Check whether a lab is already running
            try {
                const statusRes = await getStatus();
                const statusData = statusRes.ok ? await statusRes.json() : { running: false };
                rememberLab(statusData);
                if (statusData.running) {
                    appState.isLabRunning = true;
                    showMainApp();
                    const stopLabBtn = document.getElementById('stop-lab-btn');
                    if (stopLabBtn && canManageLabs()) stopLabBtn.style.display = '';
                    setTerminalToggleAvailable(true);
                    await syncRunningGuide(statusData.example || null);
                    showTopologyEmptyState(false);
                    refreshInstructorConnections();
                    initTopology();
                    await updateStatus();
                    await loadTopology();
                    connectLabsWebSocket();
                } else {
                    showMainApp();
                    await enterNoLabState();
                }
            } catch (_statusErr) {
                // If status check fails, assume lab may be running
                appState.isLabRunning = true;
                showMainApp();
                const stopLabBtn = document.getElementById('stop-lab-btn');
                if (stopLabBtn && canManageLabs()) stopLabBtn.style.display = '';
                setTerminalToggleAvailable(true);
                showTopologyEmptyState(false);
                refreshInstructorConnections();
                initTopology();
                await updateStatus();
                await loadTopology();
                connectLabsWebSocket();
            }
            // Restore a saved Group only after the initial own-Lab status
            // work finishes, so it cannot overwrite that Group's binding.
            if (appState.labMode === 'multi' && appState.currentUserRole === 'instructor') {
                syncUrlToLabKey(requestedKey);
            }
            applyRoleToUI(appState.currentUserRole);
        }
    } catch (error) {
        console.error('Authentication check failed:', error);
        showLoginPage();
    } finally {
        if (main) main.style.visibility = '';
    }
}

export async function handleLogin(event) {
    event.preventDefault();

    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const errorDiv = document.getElementById('login-error');
    const loginBtn = document.getElementById('login-btn');

    errorDiv.textContent = '';
    errorDiv.classList.add('hidden');
    errorDiv.classList.remove('show');
    loginBtn.disabled = true;
    loginBtn.textContent = 'Logging in...';

    try {
        const credentials = btoa(`${username}:${password}`);
        const response = await login(credentials);

        if (response.ok) {
            const loginData = await response.json();
            appState.currentUserRole = loginData.role || 'learner';
            appState.currentUsername = loginData.username || null;
            // The mode and Group come with /api/me, not the login answer.
            await fetchAndApplyRole();
            if (returnToAuthoring()) return;
            // A fresh instructor Session must choose its own Group context.
            // Authenticated reloads use checkAuthentication and retain links.
            if (appState.labMode === 'multi' && appState.currentUserRole === 'instructor') {
                stopInstructorDashboard();
                stopLabTabstrip();
                appState.boundGroup = null;
                syncUrlToLabKey(null);
            }
            applyRoleToUI(appState.currentUserRole);
            appState.isAuthenticated = true;

            // Check whether a lab is already running
            try {
                const statusRes = await getStatus();
                const statusData = statusRes.ok ? await statusRes.json() : { running: false };
                rememberLab(statusData);
                if (statusData.running) {
                    appState.isLabRunning = true;
                    showMainApp();
                    const stopLabBtn = document.getElementById('stop-lab-btn');
                    if (stopLabBtn && canManageLabs()) stopLabBtn.style.display = '';
                    setTerminalToggleAvailable(true);
                    // Move focus to the main content area (via skip link)
                    const skipLink = document.querySelector('.skip-link');
                    if (skipLink) skipLink.focus();
                    await syncRunningGuide(statusData.example || null);
                    showTopologyEmptyState(false);
                    refreshInstructorConnections();
                    initTopology();
                    loadTopology();
                    updateStatus();
                    connectLabsWebSocket();
                } else {
                    showMainApp();
                    await enterNoLabState();
                }
            } catch (_) {
                // Fallback to topology view if status check fails
                appState.isLabRunning = true;
                showMainApp();
                const stopLabBtn = document.getElementById('stop-lab-btn');
                if (stopLabBtn && canManageLabs()) stopLabBtn.style.display = '';
                setTerminalToggleAvailable(true);
                const skipLink = document.querySelector('.skip-link');
                if (skipLink) skipLink.focus();
                showTopologyEmptyState(false);
                refreshInstructorConnections();
                initTopology();
                loadTopology();
                updateStatus();
                connectLabsWebSocket();
            }
        } else {
            const data = await response.json();
            errorDiv.textContent = data.detail || 'Login failed. Please try again.';
            errorDiv.classList.remove('hidden');
            errorDiv.classList.add('show');
        }
    } catch (error) {
        console.error('Login error:', error);
        errorDiv.textContent = 'Connection error. Please try again.';
        errorDiv.classList.remove('hidden');
        errorDiv.classList.add('show');
    } finally {
        loginBtn.disabled = false;
        loginBtn.textContent = 'Login';
    }
}

export async function handleLogout() {
    try {
        await logout();
    } catch (error) {
        console.error('Logout error:', error);
    }

    // Terminals do not outlive their Session, so none should come back.
    forgetRememberedTerminals();

    // Disconnect the feeds
    closeLabWebSocket();
    if (appState.labsWs) {
        appState.labsWs.close();
        appState.labsWs = null;
    }

    appState.isAuthenticated = false;
    if (appState.labMode === 'multi' && appState.currentUserRole === 'instructor') {
        window.location.replace('/');
    } else {
        window.location.reload();
    }
}

export async function fetchAndApplyRole() {
    try {
        const res = await getMe();
        if (res.ok) {
            const data = await res.json();
            appState.currentUserRole = data.role || 'learner';
            appState.currentUsername = data.username || null;
            appState.labMode = data.mode || 'single';
            appState.currentGroup = data.group || null;
            appState.labCap = data.cap || 1;
            appState.serverSettings = data.server_settings === true;
            applySessionIdentity(data);
        }
    } catch (_) {
        appState.currentUserRole = 'learner';
    }
}

export function applyRoleToUI(role) {
    syncWorkspaceView();
    const badge = document.getElementById('role-badge');
    const usernameEl = document.getElementById('header-username');
    const stopLabBtn = document.getElementById('stop-lab-btn');
    const authoringLink = document.getElementById('authoring-link');

    if (authoringLink) authoringLink.hidden = role !== 'instructor';
    applyServerSettingsAvailability(role);

    if (badge) {
        badge.textContent = role === 'instructor' ? 'Instructor' : 'Learner';
        badge.dataset.role = role || 'learner';
    }
    // The account menu names the login in both Lab modes, so the header
    // reads the same whichever mode the server runs.
    if (usernameEl) usernameEl.textContent = appState.currentUsername || '';

    // Stop Lab is shown to whoever may stop this page's Lab, while one runs.
    if (stopLabBtn) {
        stopLabBtn.style.display = (canManageLabs() && appState.isLabRunning) ? '' : 'none';
    }

    setTerminalToggleAvailable(appState.isLabRunning);

    // Save Layout control is instructor-only: the /api/save_layout route
    // itself is gated with require_role("instructor"); hiding the control
    // for Learners keeps the UI consistent with what they're allowed to do.
    syncSaveLayoutButton();

    // The Sessions panel is driven from the role rather than from the lab
    // lifecycle, unlike the Terminal dashboard: people are signed in whether
    // or not a Lab is running, and that is exactly when an Instructor asks
    // who is stuck.
    if (role === 'instructor') {
        startInstructorConnections();
    } else {
        stopInstructorConnections();
    }
    // The dashboard and the Lab tab strip exist in Multi-Lab Mode only: in
    // Single-Lab Mode the one Lab is on the page already and there is no
    // Group to assign.
    if (appState.labMode === 'multi') {
        if (role === 'instructor') {
            startInstructorDashboard();
        } else {
            stopInstructorDashboard();
        }
        startLabTabstrip();
    } else {
        stopInstructorDashboard();
        stopLabTabstrip();
    }

    updateEmptyStateText();
}

export async function handleRoleDesync() {
    await fetchAndApplyRole();
    applyRoleToUI(appState.currentUserRole);
    if (appState.isLabRunning) {
        if (appState.currentUserRole === 'instructor') {
            refreshInstructorConnections();
        } else {
            refreshInstructorConnections();
        }
    }
}

// Import after to avoid circular dependency issues with showMainApp
import { fitTopology, zoomStep } from '../topology/layout.js';
import { handleGridToggle, handleLabelsToggle, handleInterfacesToggle, handleVlanLabelsToggle, handleAutoArrangeClick, handleRestoreClick, handleSaveLayoutClick } from './lifecycle.js';
import { handleCreateGroup, handleDeleteGroup } from '../topology/groups.js';

function handleZoomIn() {
    zoomStep(1);
}

function handleZoomOut() {
    zoomStep(-1);
}
