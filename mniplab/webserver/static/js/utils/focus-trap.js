/**
 * Focus-trap helper for modal dialogs.
 *
 * While a trap is active, keyboard focus is confined to the modal's own
 * focusable elements: Tab/Shift+Tab cycle within the modal and never walk out
 * into the page rendered behind the (still visible, translucent) overlay. On
 * release, focus returns to whatever had focus when the trap was taken. This is
 * the shared mechanism behind every modal (Help, Network Tools, Link Config,
 * Create-Group, Confirm) keeping keyboard focus inside itself while open.
 *
 * The trap listens at the document level in the capture phase (matching the
 * existing confirm-modal pattern), so it runs before the page-level shortcut
 * handler and closes the modal first; for the keys it handles it also calls
 * `event.preventDefault()` and `event.stopPropagation()`.
 *
 * The Tab/Shift+Tab redirect is what confines keyboard focus; the modals' own
 * `role="dialog"` + `aria-modal="true"` (already in the markup) is what tells
 * assistive technology the background is not interactive.
 *
 * Traps stack: a confirmation opened from inside a panel takes its own trap,
 * and only the most recent one handles keys, so Escape dismisses the
 * confirmation rather than the panel beneath it. Releasing the top trap hands
 * the keys back to the one below.
 */

// Active traps, oldest first; the last one owns the keyboard.
const activeTraps = [];

const FOCUSABLE_SELECTOR = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
].join(',');

// Elements that accept focus but are not part of a dialog's tab order:
// hidden inputs (link-node1/link-node2), and anything not visible, itself or
// through a hidden ancestor such as a tab panel not selected.
function isFocusable(el) {
    if (!el || !el.isConnected) return false;
    if (el.tabIndex < 0) return false;
    if (el.type === 'hidden') return false;
    if (el.getClientRects().length === 0) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    return true;
}

function focusableIn(container) {
    return Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR))
        .filter(isFocusable);
}

// A previously-focused element is safe to restore focus to only if it is still
// in the document and actually rendered (focusing a hidden element is a silent
// no-op that would strand focus on whatever the browser chose).
function isRestorable(el) {
    if (!el || !el.isConnected) return false;
    if (window.getComputedStyle(el).display === 'none') return false;
    return true;
}

/**
 * Take a focus trap on `modalElement`.
 *
 * @param {HTMLElement} modalElement - The dialog element to trap focus in.
 * @param {Object} [options]
 * @param {Function} [options.onClose] - Called when Escape is pressed (the
 *     modal's own close handler). The trap is released by the close handler via
 *     the returned `release`, so this callback should not release the trap
 *     itself.
 * @returns {{release: Function}} - Call `release()` to end the trap and return
 *     focus to the previously-focused element. Safe to call more than once.
 */
export function trapFocus(modalElement, { onClose } = {}) {
    if (!modalElement) return { release() {} };

    const previousFocus = document.activeElement;

    let disposed = false;
    const trap = { release };

    function release() {
        if (disposed) return;
        disposed = true;

        document.removeEventListener('keydown', onKeydown, true);
        const index = activeTraps.indexOf(trap);
        if (index !== -1) activeTraps.splice(index, 1);

        // Return focus to the element that triggered the modal. If that element
        // is no longer rendered (e.g. a transient context-menu trigger hidden by
        // the very click that opened the modal), `.focus()` is a silent no-op and
        // focus would be stranded inside the now-hidden modal — so verify focus
        // actually landed, and otherwise fall back to the document body.
        // `tabindex="-1"` makes body programmatically focusable without adding it
        // to the tab order; a plain `body.focus()` is a no-op on a non-focusable
        // body.
        if (previousFocus && isRestorable(previousFocus)) {
            previousFocus.focus();
        }
        if (document.activeElement !== previousFocus) {
            document.body.setAttribute('tabindex', '-1');
            document.body.focus();
        }
    }

    function onKeydown(e) {
        if (activeTraps[activeTraps.length - 1] !== trap) return;
        if (e.key === 'Tab') {
            const focusable = focusableIn(modalElement);
            if (focusable.length === 0) {
                e.preventDefault();
                return;
            }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            const active = document.activeElement;

            if (e.shiftKey) {
                if (active === first || !modalElement.contains(active)) {
                    e.preventDefault();
                    last.focus();
                }
            } else if (active === last || !modalElement.contains(active)) {
                e.preventDefault();
                first.focus();
            }
        } else if (e.key === 'Escape' && typeof onClose === 'function') {
            e.preventDefault();
            e.stopPropagation();
            onClose();
        }
    }

    document.addEventListener('keydown', onKeydown, true);
    activeTraps.push(trap);

    // Move focus into the modal so the first Tab starts its cycle there.
    const focusable = focusableIn(modalElement);
    if (focusable.length > 0) {
        focusable[0].focus();
    } else if (previousFocus && previousFocus !== modalElement) {
        modalElement.setAttribute('tabindex', '-1');
        modalElement.focus();
    }

    return trap;
}
