import { appState } from '../state/app-state.js';
import { handleGraphResize } from '../topology/layout.js';
import { toggleTerminalPane } from './pane-arrangement.js';

export function initSidebar() {
    // The sidebar is only the Labs list: open ('labs') or 'closed'.
    // checkAuthentication sets the right one for the page.
    appState.sidebarView = localStorage.getItem('mniplab-sidebar-view') === 'closed' ? 'closed' : 'labs';

    // Setup graph resize observer
    setupGraphResizeObserver();

    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        sidebar.setAttribute('data-sidebar-view', appState.sidebarView);
    }

    setupSidebarResizeHandle();

    const openGuideBtn = document.getElementById('lab-guide-open-btn');
    if (openGuideBtn) {
        openGuideBtn.addEventListener('click', () => {
            if (!appState.loadedGuideLabId) return;
            const guidePageUrl = `/guide?example=${encodeURIComponent(appState.loadedGuideLabId)}`
                + (appState.labKey ? `&key=${encodeURIComponent(appState.labKey)}` : '');
            window.open(guidePageUrl, '_blank', 'noopener,noreferrer');
        });
    }

    // Setup sidebar toggle button
    const toggleBtn = document.getElementById('toggle-sidebar');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleSidebar);
    }

    // The toolbar's Terminal button shows and hides the Terminal pane.
    const terminalToggleBtn = document.getElementById('toggle-terminal-pane');
    if (terminalToggleBtn) {
        terminalToggleBtn.addEventListener('click', toggleTerminalPane);
    }
}

export function toggleSidebar() {
    // While a Lab runs the sidebar is not shown, so its button is hidden and
    // Ctrl+B changes nothing.
    if (appState.isLabRunning) return;
    if (appState.sidebarView === 'closed') {
        appState.sidebarView = 'labs';
    } else {
        appState.sidebarView = 'closed';
    }

    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        sidebar.setAttribute('data-sidebar-view', appState.sidebarView);
    }

    // Save state to localStorage
    localStorage.setItem('mniplab-sidebar-view', appState.sidebarView);

    // Trigger graph resize
    if (appState.simulation && appState.svg) {
        // Small delay to allow CSS transition to complete
        setTimeout(() => {
            handleGraphResize();
        }, 310);
    }
}

export function closeSidebar() {
    appState.sidebarView = 'closed';
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        sidebar.setAttribute('data-sidebar-view', 'closed');
    }
    localStorage.setItem('mniplab-sidebar-view', 'closed');
}

function setupSidebarResizeHandle() {
    const handle = document.getElementById('sidebar-resize-handle');
    const sidebar = document.querySelector('.sidebar');
    if (!handle || !sidebar) return;

    const savedWidth = Number(localStorage.getItem('mniplab-sidebar-width'));
    if (Number.isFinite(savedWidth) && savedWidth >= 180) {
        sidebar.style.setProperty('--sidebar-width', `${savedWidth}px`);
    }

    let isResizing = false;
    let startX = 0;
    let startWidth = 0;

    const clampWidth = (value) => {
        const minWidth = 180;
        const maxWidth = Math.max(minWidth, Math.min(window.innerWidth * 0.55, 560));
        return Math.max(minWidth, Math.min(value, maxWidth));
    };

    const applyWidth = (widthPx) => {
        const width = clampWidth(widthPx);
        sidebar.style.setProperty('--sidebar-width', `${width}px`);
        handleGraphResize();
        handle.setAttribute('aria-valuenow', String(Math.round(width)));
    };

    const startResize = (clientX) => {
        if (appState.sidebarView === 'closed') return;
        isResizing = true;
        startX = clientX;
        startWidth = sidebar.getBoundingClientRect().width;
        handle.setAttribute('data-resizing', 'true');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    };

    const doResize = (clientX) => {
        if (!isResizing) return;
        const deltaX = clientX - startX;
        applyWidth(startWidth + deltaX);
    };

    const endResize = () => {
        if (!isResizing) return;
        isResizing = false;
        handle.setAttribute('data-resizing', 'false');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        const currentWidth = Math.round(sidebar.getBoundingClientRect().width);
        localStorage.setItem('mniplab-sidebar-width', String(currentWidth));
    };

    handle.addEventListener('mousedown', (event) => {
        event.preventDefault();
        startResize(event.clientX);
    });

    document.addEventListener('mousemove', (event) => doResize(event.clientX));
    document.addEventListener('mouseup', endResize);

    handle.addEventListener('touchstart', (event) => {
        if (event.touches.length !== 1) return;
        startResize(event.touches[0].clientX);
        event.preventDefault();
    }, { passive: false });

    document.addEventListener('touchmove', (event) => {
        if (event.touches.length !== 1) return;
        doResize(event.touches[0].clientX);
    }, { passive: true });

    document.addEventListener('touchend', endResize);

    handle.addEventListener('keydown', (event) => {
        if (appState.sidebarView === 'closed') return;
        const step = 16;
        if (event.key === 'ArrowLeft') {
            event.preventDefault();
            applyWidth(sidebar.getBoundingClientRect().width - step);
            localStorage.setItem('mniplab-sidebar-width', String(Math.round(sidebar.getBoundingClientRect().width)));
        } else if (event.key === 'ArrowRight') {
            event.preventDefault();
            applyWidth(sidebar.getBoundingClientRect().width + step);
            localStorage.setItem('mniplab-sidebar-width', String(Math.round(sidebar.getBoundingClientRect().width)));
        }
    });
}

function setupGraphResizeObserver() {
    const graphContainer = document.querySelector('.topology-container');

    if (!graphContainer || !('ResizeObserver' in window)) {
        return;
    }

    appState.graphResizeObserver = new ResizeObserver(() => {
        if (appState.paneResizing) return;
        handleGraphResize();
    });

    appState.graphResizeObserver.observe(graphContainer);
}

/** Show the Labs list in the sidebar, as the page with no Lab running does. */
export function showSidebarLabs() {
    const sidebarEl = document.querySelector('.sidebar');
    appState.sidebarView = 'labs';
    if (sidebarEl) sidebarEl.setAttribute('data-sidebar-view', 'labs');
    localStorage.setItem('mniplab-sidebar-view', 'labs');
}

export function setTerminalToggleAvailable(available) {
    const btn = document.getElementById('toggle-terminal-pane');
    if (btn) btn.disabled = !available;
}
