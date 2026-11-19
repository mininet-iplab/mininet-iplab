// The Lab Authoring page frame: the collapsible sidebar and ARIA tabs.

// Below this width the palette is a row above the canvas, which opens at a
// readable zoom rather than fitting everything.
export const NARROW_LAYOUT = '(max-width: 600px)';

function tabsOf(list) {
    return [...list.querySelectorAll('[role="tab"]')].filter(tab => !tab.hidden);
}

// Selecting a tab is the only thing that changes it; selecting a Node never does.
export function selectTab(tabId, focus = false) {
    const chosen = document.getElementById(tabId);
    for (const tab of chosen.closest('[role="tablist"]').querySelectorAll('[role="tab"]')) {
        const selected = tab === chosen;
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
        document.getElementById(tab.getAttribute('aria-controls')).hidden = !selected;
    }
    if (focus) chosen.focus();
}

// Arrow keys, Home and End move between tabs and select them (APG tabs).
export function bindTabs(list, onSelect = () => {}) {
    list.addEventListener('click', event => {
        const tab = event.target.closest('[role="tab"]');
        if (!tab) return;
        selectTab(tab.id);
        onSelect(tab.id);
    });
    list.addEventListener('keydown', event => {
        const available = tabsOf(list);
        const index = available.indexOf(document.activeElement);
        if (index < 0) return;
        const next = {
            ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: available.length - 1,
        }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        const tab = available[(next + available.length) % available.length];
        selectTab(tab.id, true);
        onSelect(tab.id);
    });
}

function bindSidebarToggle() {
    const toggle = document.getElementById('sidebar-toggle');
    toggle.addEventListener('click', () => {
        const open = toggle.getAttribute('aria-expanded') !== 'true';
        toggle.setAttribute('aria-expanded', String(open));
        document.getElementById('sidebar-body').hidden = !open;
        document.querySelector('.authoring-workspace').classList.toggle('sidebar-collapsed', !open);
    });
}

function bindSidebarResize() {
    const workspace = document.querySelector('.authoring-workspace');
    const divider = document.getElementById('sidebar-resize');
    const narrower = document.getElementById('sidebar-narrower');
    const wider = document.getElementById('sidebar-wider');
    const minimum = 280;
    const step = 40;
    let width = 340;
    let preferredWidth = width;

    const maximum = () => Math.max(minimum, Math.min(680, Math.floor(workspace.clientWidth * .55)));
    function renderWidth() {
        if (!workspace.clientWidth) return;
        width = Math.max(minimum, Math.min(maximum(), preferredWidth));
        workspace.style.setProperty('--authoring-sidebar-open-width', `${width}px`);
        divider.setAttribute('aria-valuenow', String(width));
        divider.setAttribute('aria-valuemax', String(maximum()));
        narrower.disabled = width === minimum;
        wider.disabled = width === maximum();
    }
    function setWidth(next) {
        preferredWidth = Math.round(next);
        renderWidth();
    }

    narrower.addEventListener('click', () => setWidth(width - step));
    wider.addEventListener('click', () => setWidth(width + step));
    divider.addEventListener('keydown', event => {
        const change = {
            ArrowLeft: step, ArrowRight: -step, Home: minimum - width,
            End: maximum() - width,
        }[event.key];
        if (change === undefined) return;
        event.preventDefault();
        setWidth(width + change);
    });

    divider.addEventListener('pointerdown', event => {
        if (event.button !== 0 || workspace.classList.contains('sidebar-collapsed')) return;
        divider.setPointerCapture(event.pointerId);
        event.preventDefault();
    });
    divider.addEventListener('pointermove', event => {
        if (!divider.hasPointerCapture(event.pointerId)) return;
        setWidth(workspace.getBoundingClientRect().right - event.clientX);
    });
    const endResize = event => {
        if (!divider.hasPointerCapture(event.pointerId)) return;
        divider.releasePointerCapture(event.pointerId);
    };
    divider.addEventListener('pointerup', endResize);
    divider.addEventListener('pointercancel', endResize);
    new ResizeObserver(renderWidth).observe(workspace);
}

/** Whether the sidebar shows its tabs rather than only its rail. */
export function sidebarIsOpen() {
    return document.getElementById('sidebar-toggle').getAttribute('aria-expanded') === 'true';
}

/** Open the sidebar, or fold it to its rail, as its toggle does. */
export function setSidebarOpen(open) {
    if (sidebarIsOpen() !== open) document.getElementById('sidebar-toggle').click();
}

// Open a collapsed sidebar, so a fix can reach a field inside it.
export function openSidebar() {
    setSidebarOpen(true);
}

export function initShell() {
    bindTabs(document.querySelector('#authoring-sidebar [role="tablist"]'));
    bindSidebarToggle();
    bindSidebarResize();
}
