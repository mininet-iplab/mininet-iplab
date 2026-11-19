// Rename a Lab draft where its name is shown: a text field takes the name's
// place; Enter or leaving it renames, Escape keeps the name, and focus goes
// back to the name either way.

/**
 * Edit a name in place of the control that shows it.
 *
 * @param {HTMLElement} anchor The control showing the name; hidden while editing.
 * @param {object} options
 * @param {string} options.name The current name.
 * @param {(name: string) => Promise<void>} options.rename Save a changed,
 *     non-empty name.
 */
export function renameInPlace(anchor, { name, rename }) {
    const field = document.createElement('input');
    field.type = 'text';
    field.maxLength = 120;
    field.value = name;
    field.className = 'authoring-rename-field';
    field.setAttribute('aria-label', 'Lab draft name');
    anchor.hidden = true;
    anchor.after(field);
    field.focus();
    field.select();

    let done = false;
    async function finish(keep) {
        if (done) return;
        done = true;
        const next = field.value.trim();
        field.remove();
        anchor.hidden = false;
        anchor.focus();
        if (keep && next && next !== name) await rename(next);
    }

    field.addEventListener('keydown', event => {
        if (event.key !== 'Enter' && event.key !== 'Escape') return;
        // Neither key reaches the form or the canvas behind the field.
        event.preventDefault();
        event.stopPropagation();
        finish(event.key === 'Enter');
    });
    field.addEventListener('blur', () => finish(true));
}
