import { refreshInstructorConnections } from './lab/instructor-connections.js';
import { appState } from './state/app-state.js';
import { onThemeChange } from './ui/theme.js';
import { initHeader } from './ui/header.js';
import { updateTopologyColors } from './topology/renderer.js';
import { initSidebar } from './ui/sidebar.js';
import { initPaneArrangement } from './ui/pane-arrangement.js';
import { initPaneMenus } from './ui/pane-menu.js';
import { initPaneDrag } from './ui/pane-drag.js';
import { initWorkspaceView } from './ui/workspace-view.js';
import { setupKeyboardShortcuts } from './ui/keyboard-shortcuts.js';
import { checkAuthentication, fetchAndApplyRole, applyRoleToUI, handleLogout } from './lab/auth.js';
import { stopLab, enterNoLabState } from './lab/lifecycle.js';
import { updateStatusFromData, loadTopologyFromData } from './lab/lifecycle.js';
import { updateStatus } from './lab/lifecycle.js';
import { loadTopology } from './lab/lifecycle.js';

import { syncRunningGuide } from './lab/guide.js';
import { getCurrentLabName } from './utils/dom.js';
import { TabManager } from './ui/terminal-tabs.js';
import { PacketAnalysis } from './capture/packet-analysis.js';
import { NetworkConsole } from './console/network-tools.js';
import { handleGraphResize } from './topology/layout.js';
import { updateNodeServiceFromPools } from './topology/renderer.js';
import { initLeasesPanel, handlePoolBroadcast } from './dhcp/leases-panel.js';
import { initRelayPanel } from './dhcp/relay-panel.js';
import { initDnsPanel, handleDnsRecordBroadcast } from './dns/dns-panel.js';
import { initResolverPanel, handleResolverAccessBroadcast } from './resolver/resolver-panel.js';
import { initRpkiCaPanel, handleRpkiObjectsBroadcast } from './rpki/ca-panel.js';
import { initValidatorPanel } from './rpki/validator-panel.js';
import { initSpeakerPanel } from './speaker/speaker-panel.js';
import { initSwitchPanel } from './switch/switch-panel.js';
import { labKeyFromLocation } from './lab/routing.js';

// Seed the binding from whatever `/labs/{key}` (or `/`) the page was loaded
// with, so a shared link or a reload targets that Lab from the first paint
// rather than only after the status round-trip resolves. `checkAuthentication`
// confirms or corrects this against the caller's actual Lab immediately after.
appState.labKey = labKeyFromLocation();

// "Skip to main content" must move keyboard focus to a real, focusable region
// in both states: the login screen (whose target is the login <main>) and the
// post-login app shell (whose target is the app's <main>). Without a handler
// the link only scrolls; without tabindex="-1" on the target, focus would be
// stranded on <body>.
function initSkipLink() {
    const skipLink = document.querySelector('.skip-link');
    if (!skipLink) return;
    skipLink.addEventListener('click', (event) => {
        event.preventDefault();
        const targetId = document.getElementById('app').classList.contains('hidden')
            ? 'login-page'
            : 'main';
        const target = document.getElementById(targetId);
        if (target) target.focus();
    });
}

// Set up WebSocket message handler
appState.wsMessageHandler = (message) => {
    const { type, data } = message;
    switch (type) {
        case 'status_update':
            // The Labs feed reports only the viewer's OWN Lab. While the
            // Instructor is bound to another Group's Lab, this message is
            // about a different Lab and must not drive the page — it would
            // drop the bound key and tear down the running Lab's UI. The
            // bound Lab's own lifecycle arrives on its topology feed and the
            // dashboard poll instead.
            if (!appState.boundGroup) {
                void updateStatusFromData(data);
            }
            // A Terminal detached, reattached or ended shows in the open
            // Sessions and Terminals view at once, not at its next poll.
            if (!document.getElementById('workspace-monitor')?.hidden) {
                refreshInstructorConnections();
            }
            break;
        case 'topology_update':
            loadTopologyFromData(data);
            break;
        case 'dhcp_pool_update':
            handlePoolBroadcast(data);
            if (data && data.node && data.pools) {
                updateNodeServiceFromPools(data.node, data.pools);
            }
            break;
        case 'dns_record_update':
        case 'DNS_RECORD_UPDATED':
            handleDnsRecordBroadcast(data);
            break;
        case 'RESOLVER_ACCESS_UPDATED':
            handleResolverAccessBroadcast(data);
            break;
        case 'RPKI_OBJECTS_UPDATED':
            handleRpkiObjectsBroadcast(data);
            break;
        case 'ping':
            import('./services/websocket.js').then(({ sendPong }) => sendPong());
            break;
        default:
            console.warn('Unknown WebSocket message type:', type);
    }
};

// Fallback handler when WebSocket max reconnects reached
appState.wsFallbackHandler = () => {
    updateStatus();
    loadTopology();
};

// The Authored Layout is now the source of truth and the browser no longer
// persists layout. Purge any layout keys an older client left in localStorage,
// so a stale copy can never shadow the authored arrangement.
function purgeLegacyLayoutKeys() {
    try {
        localStorage.removeItem('topologyLayout');
        Object.keys(localStorage)
            .filter(key => key.startsWith('mniplab.topologyLayout.'))
            .forEach(key => localStorage.removeItem(key));
    } catch (e) {
        // localStorage may be unavailable (private mode); nothing to purge.
    }
}

// Initialize application on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    purgeLegacyLayoutKeys();

    // Core UI shell
    initHeader({ onLogout: handleLogout });
    onThemeChange(() => {
        if (appState.simulation) {
            updateTopologyColors();
        }
    });
    initSidebar();
    initWorkspaceView();
    setupKeyboardShortcuts();
    initSkipLink();

    // Terminal tabs
    appState.tabManager = new TabManager();

    // Packet Analysis, the Captures and the NAT Table
    appState.packetAnalysis = new PacketAnalysis();

    // The four Panes of the running-Lab page, placed in their Slots
    initPaneArrangement();
    initPaneMenus();
    initPaneDrag();

    // Network tools console
    appState.networkConsole = new NetworkConsole();

    // DHCP Leases panel, opened from a Node's DHCP badge in the Topology
    initLeasesPanel();

    // DHCP Relay panel, opened from a Node's Relay badge in the Topology
    initRelayPanel();

    // DNS panel, opened from a Node's DNS badge in the Topology
    initDnsPanel();

    // Resolver panel, opened from a Node's RESOLVER badge in the Topology
    initResolverPanel();

    // RPKI CA panel, opened from an RPKI CA Node's badge in the Topology
    initRpkiCaPanel();

    // Validator panel, opened from a Validator Node's badge in the Topology
    initValidatorPanel();

    // Speaker panel, opened from a Speaker's node context menu
    initSpeakerPanel();

    // Switch management panel, opened when clicking a switch
    initSwitchPanel();

    // Wire Stop Lab button
    const stopLabBtn = document.getElementById('stop-lab-btn');
    if (stopLabBtn) {
        stopLabBtn.addEventListener('click', stopLab);
    }

    // Window resize handler for topology
    window.addEventListener('resize', handleGraphResize);

    // Check auth and initial state
    checkAuthentication();
});

// Re-check role when the tab regains focus (another tab may have logged in
// as a different user, overwriting the shared session cookie).
document.addEventListener('visibilitychange', async () => {
    if (!document.hidden && appState.isAuthenticated) {
        const prevRole = appState.currentUserRole;
        await fetchAndApplyRole();
        if (appState.currentUserRole !== prevRole) {
            applyRoleToUI(appState.currentUserRole);
            if (!appState.isLabRunning) {
                await enterNoLabState();
            } else {
                if (appState.currentUserRole === 'instructor') {
                    refreshInstructorConnections();
                } else {
                    refreshInstructorConnections();
                }
                await syncRunningGuide(getCurrentLabName() || null);
            }
        }
    }
});

// Auto-open terminal from redirect (if any)
const autoOpenNode = sessionStorage.getItem('mniplab-auto-open-node');
if (autoOpenNode) {
    sessionStorage.removeItem('mniplab-auto-open-node');
    document.addEventListener('DOMContentLoaded', () => {
        setTimeout(() => {
            if (appState.tabManager) {
                appState.tabManager.openTerminal(autoOpenNode);
            }
        }, 500);
    });
}
