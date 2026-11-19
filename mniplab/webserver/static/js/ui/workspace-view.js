import { renderInstructorConnections, refreshInstructorConnections } from '../lab/instructor-connections.js';
// View placement only: monitoring reuses the existing tables and polling.
import { appState } from '../state/app-state.js';

let monitoring = false;
let workspaceScrollTop = 0;

export function initWorkspaceView() {
    document.getElementById('workspace-monitor-btn')?.addEventListener('click', () => {
        setMonitoring(!monitoring);
    });
    document.getElementById('back-to-topology')?.addEventListener('click', () => {
        setMonitoring(false);
    });
    syncWorkspaceView();
}

export function closeWorkspaceMonitor() {
    setMonitoring(false, false);
}

export function syncWorkspaceView() {
    const button = document.getElementById('workspace-monitor-btn');
    const instructor = appState.currentUserRole === 'instructor';
    const overview = classOverviewShown();
    document.getElementById('status').hidden = overview;
    document.getElementById('classroom-status').hidden = !overview;
    if (!instructor || overview) closeWorkspaceMonitor();
    if (button) button.hidden = !instructor || overview;
    const group = document.querySelector('#lab-tabstrip [aria-selected="true"]')?.dataset.labGroup;
    const title = document.getElementById('workspace-title');
    if (title) title.textContent = overview ? 'Class Overview'
        : group ? `Labs / ${group}` : 'Labs';
    syncLabCrumb(overview);
    const scope = document.getElementById('monitor-scope');
    if (scope) scope.textContent = appState.labMode === 'multi'
        ? `Group ${group || appState.currentGroup} · Sessions and Terminals of this Lab`
        : 'Signed-in Sessions · Active Terminals of this Lab';
}

// While a Lab runs its name is the breadcrumb's last step and carries the
// current-page marker; with no Lab (or on the Class Overview) the page title
// does. The Lab name element keeps the text and the example id other code reads.
export function syncLabCrumb(overview = classOverviewShown()) {
    const name = document.getElementById('lab-name');
    const crumb = document.getElementById('lab-crumb');
    const separator = document.getElementById('lab-crumb-separator');
    const title = document.getElementById('workspace-title')?.closest('li');
    if (!name || !crumb || !separator || !title) return;
    const running = !overview && !!appState.labKey && !!name.dataset.example;
    crumb.hidden = separator.hidden = !running;
    if (running) {
        title.removeAttribute('aria-current');
        crumb.setAttribute('aria-current', 'page');
    } else {
        crumb.removeAttribute('aria-current');
        title.setAttribute('aria-current', 'page');
    }
}

/** Whether an Instructor of a multi-Lab server is on the Class Overview tab. */
export function classOverviewShown() {
    return appState.labMode === 'multi' && appState.currentUserRole === 'instructor'
        && document.getElementById('lab-tab-overview')?.getAttribute('aria-selected') === 'true';
}

function setMonitoring(open, focus = true) {
    const panel = document.getElementById('workspace-monitor');
    const wrapper = document.getElementById('lab-tabpanel');
    const button = document.getElementById('workspace-monitor-btn');
    if (!panel || !wrapper || !button) return;
    if (open && appState.currentUserRole !== 'instructor') return;

    renderInstructorConnections();
    if (open) void refreshInstructorConnections();

    const changed = monitoring !== open;
    if (changed && open) workspaceScrollTop = wrapper.scrollTop;
    monitoring = open;
    panel.hidden = !open;
    wrapper.dataset.monitoring = String(open);
    button.setAttribute('aria-checked', String(open));
    // Visibility preserves layout dimensions, unlike display:none, so the
    // graph resize observer cannot refit zoom or restart placement on return.
    for (const element of wrapper.querySelectorAll(':scope > [data-pane]')) {
        element.inert = open;
    }
    if (changed) wrapper.scrollTop = open ? 0 : workspaceScrollTop;
    // The item lives in the account menu, closed by now: closing the monitor
    // returns focus to the menu's button.
    if (changed && focus) {
        (open ? document.getElementById('back-to-topology') : document.getElementById('account-menu-btn'))?.focus();
    }
}
