// The title bar of each Pane: a grip and a "<Pane> pane actions" menu button
// following the menu-button pattern. The menu is built when it opens, from
// the moves pane-arrangement.js offers for that Pane at that moment, and
// focus returns to the button after a choice, so a keyboard user keeps their
// place while the Panes move around them. The list sits in the top layer and
// is placed from the button's box, so no Pane's clipping can cut it off.

import { PANE_NAMES, paneActions, resetPaneArrangement, setFullView } from './pane-arrangement.js';

const BARS = {
    topology: '.controls',
    guide: '.lab-guide-toolbar',
    terminal: '.terminal-tab-bar',
    packet: '.packet-analysis-tabbar',
};

function grip() {
    const el = document.createElement('span');
    el.className = 'pane-grip';
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = '<svg width="12" height="16" viewBox="0 0 12 16" fill="currentColor">'
        + '<circle cx="3" cy="3" r="1.5"/><circle cx="9" cy="3" r="1.5"/>'
        + '<circle cx="3" cy="8" r="1.5"/><circle cx="9" cy="8" r="1.5"/>'
        + '<circle cx="3" cy="13" r="1.5"/><circle cx="9" cy="13" r="1.5"/></svg>';
    return el;
}

function menu(id) {
    const name = `${PANE_NAMES[id]} pane actions`;
    const wrap = document.createElement('div');
    wrap.className = 'pane-menu';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn-icon pane-menu-button';
    button.setAttribute('aria-haspopup', 'menu');
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-label', name);
    button.title = name;
    button.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
        + '<circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
    // Shown only while the Pane has the whole page.
    const exit = document.createElement('button');
    exit.type = 'button';
    exit.className = 'btn-secondary pane-exit-full';
    exit.textContent = 'Exit full view';
    exit.addEventListener('click', () => setFullView(null));
    const list = document.createElement('div');
    list.className = 'pane-menu-list';
    list.setAttribute('role', 'menu');
    list.setAttribute('aria-label', name);
    list.hidden = true;
    list.popover = 'manual';

    // Right-aligned under the button, kept inside the window; above it when
    // there is more room there, and scrollable when neither side fits it.
    const place = () => {
        const edge = 8;
        const at = button.getBoundingClientRect();
        const below = window.innerHeight - at.bottom - edge;
        const above = at.top - edge;
        const up = list.scrollHeight > below && above > below;
        const room = Math.max(up ? above : below, 96);
        list.style.maxHeight = `${room}px`;
        const height = Math.min(list.scrollHeight, room);
        const left = Math.min(Math.max(at.right - list.offsetWidth, edge),
            Math.max(window.innerWidth - list.offsetWidth - edge, edge));
        list.style.left = `${left}px`;
        list.style.top = `${up ? at.top - 4 - height : at.bottom + 4}px`;
    };
    const close = (focus = true) => {
        if (list.matches(':popover-open')) list.hidePopover();
        window.removeEventListener('resize', close);
        list.hidden = true;
        list.replaceChildren();
        button.setAttribute('aria-expanded', 'false');
        if (focus) button.focus();
    };
    const open = () => {
        const moves = paneActions(id);
        if (!moves.length) return;
        for (const move of moves) {
            const item = document.createElement('button');
            item.type = 'button';
            item.setAttribute('role', 'menuitem');
            item.tabIndex = -1;
            item.textContent = move.label;
            if (move.disabled) item.setAttribute('aria-disabled', 'true');
            item.addEventListener('click', () => {
                if (move.disabled) return;
                close();
                move.run();
            });
            list.appendChild(item);
        }
        list.hidden = false;
        list.showPopover();
        place();
        window.addEventListener('resize', close, { once: true });
        button.setAttribute('aria-expanded', 'true');
        (list.querySelector('[role="menuitem"]:not([aria-disabled])')
            || list.querySelector('[role="menuitem"]')).focus();
    };
    button.addEventListener('click', () => (list.hidden ? open() : close()));
    button.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (list.hidden) open();
        }
    });
    list.addEventListener('keydown', (event) => {
        const items = [...list.querySelectorAll('[role="menuitem"]')];
        const at = items.indexOf(document.activeElement);
        const go = (index) => { event.preventDefault(); items[(index + items.length) % items.length].focus(); };
        if (event.key === 'ArrowDown') go(at + 1);
        else if (event.key === 'ArrowUp') go(at - 1);
        else if (event.key === 'Home') go(0);
        else if (event.key === 'End') go(items.length - 1);
        else if (event.key === 'Escape') {
            // Escape belongs to the menu here, not to the keyboard shortcuts.
            event.preventDefault();
            event.stopPropagation();
            close();
        } else if (event.key === 'Tab') close(false);
    });
    document.addEventListener('pointerdown', (event) => {
        if (!list.hidden && !wrap.contains(event.target)) close(false);
    });
    wrap.append(exit, button, list);
    return wrap;
}

/** Add the grip and the Pane actions menu to each Pane's title bar. */
export function initPaneMenus() {
    for (const [id, selector] of Object.entries(BARS)) {
        const bar = document.querySelector(`[data-pane="${id}"] ${selector}`);
        if (!bar || bar.querySelector('.pane-menu')) continue;
        bar.classList.add('pane-bar');
        bar.prepend(grip());
        bar.append(menu(id));
    }
    document.getElementById('reset-pane-arrangement-btn')
        ?.addEventListener('click', resetPaneArrangement);
}
