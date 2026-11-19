// Editable Group selection shared by Lab navigation and the classroom filter.
export function createGroupPicker({ id, label, placeholder, onSelect }) {
    const element = document.createElement('div');
    element.className = 'group-picker';
    const caption = document.createElement('label');
    caption.htmlFor = id;
    caption.textContent = label;
    const field = document.createElement('div');
    field.className = 'group-picker-field';
    const input = document.createElement('input');
    input.id = id;
    input.type = 'text';
    input.placeholder = placeholder;
    input.autocomplete = 'off';
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', `${id}-options`);
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.tabIndex = -1;
    toggle.className = 'group-picker-toggle';
    toggle.setAttribute('aria-label', `Show choices for ${label}`);
    toggle.textContent = '▾';
    const popup = document.createElement('div');
    popup.className = 'group-picker-popup';
    popup.hidden = true;
    const list = document.createElement('ul');
    list.id = `${id}-options`;
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', `${label} choices`);
    const empty = document.createElement('p');
    empty.setAttribute('role', 'status');
    empty.textContent = 'No Groups found.';
    popup.append(list, empty);
    field.append(input, toggle, popup);
    element.append(caption, field);
    let options = [];
    let matches = [];
    let selected = null;
    let active = -1;
    let query = '';
    let signature = '';

    function close() {
        popup.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
        input.value = options.find(option => option.value === selected)?.label || '';
        query = '';
        active = -1;
    }
    function highlight(index) {
        active = index;
        for (const [i, option] of Array.from(list.children).entries()) {
            option.classList.toggle('highlighted', i === active);
        }
        const item = list.children[active];
        if (item) {
            input.setAttribute('aria-activedescendant', item.id);
            item.scrollIntoView({ block: 'nearest' });
        } else input.removeAttribute('aria-activedescendant');
    }
    function choose(value) {
        selected = value;
        close();
        onSelect(value);
    }
    function render() {
        const prior = matches[active]?.value;
        matches = options.filter(option => `${option.label} ${option.description || ''}`.toLowerCase().includes(query));
        list.replaceChildren();
        for (const [index, option] of matches.entries()) {
            const item = document.createElement('li');
            item.id = `${id}-option-${index}`;
            item.setAttribute('role', 'option');
            item.setAttribute('aria-label', option.label);
            if (option.description) item.setAttribute('aria-description', option.description);
            item.setAttribute('aria-selected', String(option.value === selected));
            item.textContent = option.label;
            if (option.description) {
                const detail = document.createElement('small');
                detail.textContent = option.description;
                item.append(detail);
            }
            item.addEventListener('click', () => choose(option.value));
            list.append(item);
        }
        empty.hidden = matches.length > 0;
        highlight(matches.findIndex(option => option.value === prior));
    }
    function position() {
        const bounds = input.getBoundingClientRect();
        const width = Math.min(Math.max(bounds.width, 240), window.innerWidth - 16);
        popup.style.left = `${Math.max(8, Math.min(bounds.left, window.innerWidth - width - 8))}px`;
        popup.style.top = `${bounds.bottom + 4}px`;
        popup.style.width = `${width}px`;
        popup.style.maxHeight = `${Math.max(80, Math.min(280, window.innerHeight - bounds.bottom - 12))}px`;
    }
    function open() {
        position();
        popup.hidden = false;
        input.setAttribute('aria-expanded', 'true');
        render();
    }
    input.addEventListener('focus', () => { input.select(); });
    input.addEventListener('click', () => { if (popup.hidden) open(); });
    input.addEventListener('input', () => {
        query = input.value.trim().toLowerCase();
        active = -1;
        open();
    });
    input.addEventListener('blur', close);
    input.addEventListener('keydown', event => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (popup.hidden) open();
            const next = active < 0 ? (event.key === 'ArrowDown' ? 0 : matches.length - 1)
                : (active + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length;
            if (matches.length) highlight(next);
        } else if (event.key === 'Enter' && !popup.hidden) {
            event.preventDefault();
            const option = matches[active] || (matches.length === 1 ? matches[0] : null);
            if (option) choose(option.value);
        } else if (event.key === 'Escape' && !popup.hidden) {
            event.preventDefault();
            event.stopPropagation();
            close();
        } else if (event.key === 'Tab') close();
    });
    toggle.addEventListener('mousedown', event => event.preventDefault());
    toggle.addEventListener('click', () => {
        input.focus();
        if (popup.hidden) open();
        else close();
    });
    popup.addEventListener('mousedown', event => event.preventDefault());
    const reposition = event => {
        if (!popup.hidden && !(event.target instanceof Node && popup.contains(event.target))) position();
    };
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    return {
        element,
        get value() { return selected; },
        select(value) { selected = value; close(); },
        setOptions(choices) {
            const next = JSON.stringify(choices);
            if (signature === next) return;
            signature = next;
            options = choices;
            if (selected !== null && !options.some(option => option.value === selected)) selected = null;
            if (popup.hidden) close();
            else render();
        },
        destroy() {
            window.removeEventListener('scroll', reposition, true);
            window.removeEventListener('resize', reposition);
            element.remove();
        },
    };
}
