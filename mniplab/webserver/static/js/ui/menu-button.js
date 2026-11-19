// A menu button (WAI-ARIA menu button pattern) whose list sits in the top
// layer, placed from the button's box, so no dialog's or Pane's clipping can
// cut it off. Shared by the header's account menu and the login rows of
// Server settings; it looks like the Pane actions menu (.pane-menu-list).
//
// The list is written in the markup (or by the caller) with its items in
// place; hidden items are skipped. Choosing an item closes the menu and puts
// focus on the button first, so a dialog the item opens returns focus there,
// not to an item that is no longer visible. An item marked
// data-menu-keep-open (a setting like Dark mode) leaves the menu open.

const ITEMS = '[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]';

/**
 * Make `button` open and close `list` as a menu.
 *
 * @param {HTMLButtonElement} button - The menu button.
 * @param {HTMLElement} list - The element with role="menu".
 * @returns {{close: Function}} Closes the menu without moving focus.
 */
export function bindMenuButton(button, list) {
    button.setAttribute('aria-haspopup', 'menu');
    button.setAttribute('aria-expanded', 'false');
    if (list.id) button.setAttribute('aria-controls', list.id);
    list.hidden = true;
    list.popover = 'manual';
    for (const item of list.querySelectorAll(ITEMS)) item.tabIndex = -1;

    const items = () => [...list.querySelectorAll(ITEMS)].filter((item) => !item.hidden);
    const isOpen = () => !list.hidden;

    // Right-aligned under the button and kept inside the window; above it
    // when it does not fit below.
    const place = () => {
        const edge = 8;
        const at = button.getBoundingClientRect();
        const left = Math.min(Math.max(at.right - list.offsetWidth, edge),
            Math.max(window.innerWidth - list.offsetWidth - edge, edge));
        const fitsBelow = at.bottom + 4 + list.offsetHeight <= window.innerHeight - edge;
        list.style.left = `${left}px`;
        list.style.top = `${fitsBelow || at.top < list.offsetHeight ? at.bottom + 4 : at.top - 4 - list.offsetHeight}px`;
    };
    const close = (focus = false) => {
        if (!isOpen()) return;
        if (list.matches(':popover-open')) list.hidePopover();
        list.hidden = true;
        button.setAttribute('aria-expanded', 'false');
        window.removeEventListener('resize', onResize);
        if (focus) button.focus();
    };
    const onResize = () => close();
    const open = (last = false) => {
        const visible = items();
        if (!visible.length) return;
        list.hidden = false;
        list.showPopover();
        place();
        button.setAttribute('aria-expanded', 'true');
        window.addEventListener('resize', onResize);
        visible[last ? visible.length - 1 : 0].focus();
    };

    button.addEventListener('click', () => (isOpen() ? close(true) : open()));
    button.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        event.preventDefault();
        if (!isOpen()) open(event.key === 'ArrowUp');
    });
    list.addEventListener('keydown', (event) => {
        const visible = items();
        const at = visible.indexOf(document.activeElement);
        const go = (index) => {
            event.preventDefault();
            visible[(index + visible.length) % visible.length].focus();
        };
        if (event.key === 'ArrowDown') go(at + 1);
        else if (event.key === 'ArrowUp') go(at - 1);
        else if (event.key === 'Home') go(0);
        else if (event.key === 'End') go(visible.length - 1);
        else if (event.key === 'Tab') close();
    });
    // Escape closes the menu, and only the menu: a dialog's focus trap
    // listens on document in the capture phase, so this listens earlier.
    window.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !isOpen()) return;
        event.preventDefault();
        event.stopPropagation();
        close(true);
    }, true);
    // Runs before the item's own click handlers (capture on the list).
    list.addEventListener('click', (event) => {
        const item = event.target.closest(ITEMS);
        if (!item || item.hasAttribute('data-menu-keep-open')) return;
        close(true);
    }, true);
    document.addEventListener('pointerdown', (event) => {
        if (isOpen() && !list.contains(event.target) && !button.contains(event.target)) close();
    });
    return { close };
}
