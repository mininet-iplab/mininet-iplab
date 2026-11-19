// Centralized application state
// All modules import this object directly. Keep it flat and simple.

export const appState = {
    // D3 / topology rendering
    simulation: null,
    svg: null,
    g: null,
    zoom: null,
    nodeElements: null,
    linkElements: null,
    labelElements: null,
    textElements: null,
    showLabels: true,
    showInterfaceLabels: false,
    showVlanLabels: true,
    showGrid: true,
    transform: (typeof d3 !== 'undefined') ? d3.zoomIdentity : null,
    gridPattern: null,
    graphResizeObserver: null,
    // True while a separator is being dragged: the Topology refits when it ends.
    paneResizing: false,
    groupElements: null,

    // Topology data
    currentTopology: null,
    // Authored Layout for the current lab: the canonical {version, nodes:{id:{x,y}}}
    // fetched fresh from the server on every load. Null when the lab has none.
    authoredLayout: null,
    groups: [],
    selectedNodes: new Set(),
    selectedGroup: null,
    activeRelayPath: null,
    pinnedRelayNode: null,
    isInitialLoad: true,

    // Auth / user
    isAuthenticated: false,
    isLabRunning: false,
    // The running Lab's key: every Lab route and feed is addressed by it,
    // and it is kept in lockstep with the page URL's `/labs/{key}` segment
    // (see lab/routing.js and lab/lifecycle.js's rememberLab).
    labKey: null,
    // The Group whose Lab the Instructor has bound the page to (Multi-Lab
    // Mode). Null when the page is on the Instructor's own Lab (Overview /
    // teacher tab) or in Single-Lab Mode. While set, the Labs feed — which
    // reports only the viewer's OWN Lab — must not clobber this binding.
    boundGroup: null,
    currentUserRole: null,
    currentUsername: null,
    // The manager's mode ('single' or 'multi'), the caller's Group and the
    // cap on alive Labs, from /api/me.
    labMode: 'single',
    currentGroup: null,
    labCap: 1,
    // Whether the server has Server settings: under `serve` only, from /api/me.
    serverSettings: false,

    // Sidebar / UI view state
    sidebarView: 'labs',

    // The catalog of Lab Examples, for the sidebar list
    allExamples: [],
    catalogAreas: [],
    labSearchDebounce: null,

    // Guide
    currentGuidePath: null,
    loadedGuideLabId: null,
    hasGuide: null,

    // View options / menus
    viewOptionsMenuInitialized: false,
    linkContextMenuListenersInitialized: false,
    nodeContextMenuListenersInitialized: false,

    // WebSockets: the Labs feed (always, once signed in) and the Lab's feed
    labsWs: null,
    labWs: null,
    wsReconnectAttempts: 0,
    wsMaxReconnectAttempts: 5,
    wsReconnectDelay: 1000,
};

// Whether the signed-in login may start and stop Labs from this page: the
// Instructor always; in Multi-Lab Mode every Group manages its own Lab.
export function canManageLabs() {
    return appState.currentUserRole === 'instructor' || appState.labMode === 'multi';
}
