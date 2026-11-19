// An address field that also offers the Interfaces of the Lab's other Nodes.
// One control, the ARIA editable combobox with list autocomplete: the text
// input is the value, so typing any address, even one no Node has, works as
// it always did, and picking a choice only fills in what could be typed.
//
// Keyboard: Down opens the list (Alt+Down too) and moves through it, Up moves
// back, Enter takes the highlighted choice, Escape closes the list and only
// then lets the dialog close, Tab leaves with whatever is typed.

let counter = 0;

// A fixed list closes when scrolling moves its field; delayed scroll events and
// scrolling within the list itself must not dismiss a just-opened picker.
const openLists = new Set();
document.addEventListener('scroll', () => openLists.forEach(close => close()), true);

/**
 * @typedef {object} Choice
 * @property {string} address The bare address that fills the field.
 * @property {string} label What the list shows: "Node · Interface · address".
 */

/**
 * Turn a text input into an address combobox.
 *
 * @param {HTMLInputElement} input The field; its value stays the address.
 * @param {object} options
 * @param {() => Choice[]} options.choices The Interfaces on offer now.
 * @param {string} options.buttonLabel Accessible name of the button that opens the list.
 * @returns {{node: HTMLElement, rename: (buttonLabel: string) => void}} ``node``
 *     holds the input, button and list, and replaces the bare input in the page.
 */
export function attachAddressPicker(input, { choices, buttonLabel }) {
    const id = `address-picker-${counter++}`;
    const list = document.createElement('ul');
    list.id = `${id}-list`;
    list.className = 'address-picker-list';
    list.setAttribute('role', 'listbox');
    list.hidden = true;
    const status = document.createElement('span');
    status.className = 'sr-only';
    status.setAttribute('role', 'status');
    const button = document.createElement('button');
    button.type = 'button';
    button.tabIndex = -1;
    button.className = 'btn-icon address-picker-button';
    button.textContent = '▾';
    button.setAttribute('aria-label', buttonLabel);
    button.setAttribute('aria-haspopup', 'listbox');
    const node = document.createElement('div');
    node.className = 'address-picker';
    node.append(input, button, list, status);

    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', list.id);

    let shown = [];
    let active = -1;
    let anchorBox;
    const closeWhenMoved = () => {
        const box = input.getBoundingClientRect();
        if (!input.isConnected || box.left !== anchorBox.left || box.top !== anchorBox.top) close();
    };

    const matching = () => {
        const words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
        const all = choices();
        const narrowed = all.filter(choice => words.every(word => choice.label.toLowerCase().includes(word)));
        return words.length && narrowed.length ? narrowed : words.length ? [] : all;
    };

    function close() {
        openLists.delete(closeWhenMoved);
        list.hidden = true;
        active = -1;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
        status.textContent = '';
    }

    function highlight(index) {
        active = index;
        [...list.children].forEach((option, position) =>
            option.setAttribute('aria-selected', String(position === index)));
        if (index < 0) input.removeAttribute('aria-activedescendant');
        else {
            input.setAttribute('aria-activedescendant', list.children[index].id);
            list.children[index].scrollIntoView({ block: 'nearest' });
        }
    }

    function open(items) {
        shown = items;
        if (!shown.length) { close(); return; }
        list.replaceChildren(...shown.map((choice, index) => {
            const option = document.createElement('li');
            option.id = `${id}-option-${index}`;
            option.setAttribute('role', 'option');
            option.setAttribute('aria-selected', 'false');
            option.textContent = choice.label;
            // Keep focus in the field while the mouse picks.
            option.addEventListener('mousedown', event => event.preventDefault());
            option.addEventListener('click', () => pick(choice));
            return option;
        }));
        // Fixed, from the field's own box, so no scrolling table or dialog clips the list.
        const box = input.getBoundingClientRect();
        list.style.left = `${box.left}px`;
        const bounds = input.closest('dialog')?.getBoundingClientRect()
            || { left: 0, right: window.innerWidth, top: 0, bottom: window.innerHeight, width: window.innerWidth };
        list.style.minWidth = `${box.width}px`;
        list.style.maxWidth = `${bounds.width - 8}px`;
        list.style.maxHeight = '12rem';
        list.hidden = false;
        const below = Math.max(0, bounds.bottom - box.bottom - 4);
        const above = Math.max(0, box.top - bounds.top - 4);
        const desired = Math.min(list.scrollHeight, parseFloat(getComputedStyle(list).maxHeight));
        const down = below >= desired || below >= above;
        list.style.maxHeight = `${Math.min(desired, down ? below : above)}px`;
        const popup = list.getBoundingClientRect();
        list.style.left = `${Math.max(bounds.left + 4, Math.min(box.left, bounds.right - popup.width - 4))}px`;
        list.style.top = `${down ? box.bottom + 2 : box.top - popup.height - 2}px`;
        anchorBox = box;
        openLists.add(closeWhenMoved);
        input.setAttribute('aria-expanded', 'true');
        highlight(-1);
        status.textContent = `${shown.length} matching Interface${shown.length === 1 ? '' : 's'}`;
    }

    function pick(choice) {
        input.value = choice.address;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        close();
        input.focus();
    }

    input.addEventListener('input', () => open(matching()));
    input.addEventListener('keydown', event => {
        const isOpen = !list.hidden;
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            if (!isOpen) {
                const found = matching();
                open(found.length ? found : choices());
                if (shown.length) highlight(0);
            } else highlight(Math.min(active + 1, shown.length - 1));
        } else if (event.key === 'ArrowUp' && isOpen) {
            event.preventDefault();
            highlight(Math.max(active - 1, 0));
        } else if (event.key === 'Enter' && isOpen && active >= 0) {
            event.preventDefault();
            pick(shown[active]);
        } else if (event.key === 'Escape' && isOpen) {
            event.preventDefault();
            event.stopPropagation();
            close();
        } else if (event.key === 'Tab') close();
    });
    input.addEventListener('blur', close);
    button.addEventListener('mousedown', event => event.preventDefault());
    button.addEventListener('click', () => {
        input.focus();
        if (list.hidden) open(choices()); else close();
    });
    return { node, rename: label => button.setAttribute('aria-label', label) };
}
