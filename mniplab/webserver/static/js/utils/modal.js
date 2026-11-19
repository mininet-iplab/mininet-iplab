import { trapFocus } from './focus-trap.js';

/**
 * Generic Confirm/Alert Modal Helper
 * @param {Object} options
 * @param {string} options.title - Modal title
 * @param {string} options.message - Modal body text
 * @param {string} [options.confirmText='OK'] - Text for confirm button
 * @param {string} [options.cancelText='Cancel'] - Text for cancel button. If null, cancel button is hidden (Alert mode)
 * @param {Function} [options.onConfirm] - Callback when confirmed
 */
export function showConfirmModal({ title, message, confirmText = 'OK', cancelText = 'Cancel', onConfirm }) {
    const modal = document.getElementById('confirm-modal');
    const titleEl = document.getElementById('confirm-modal-title');
    const msgEl = document.getElementById('confirm-modal-message');
    const confirmBtn = document.getElementById('confirm-ok');
    const cancelBtn = document.getElementById('confirm-cancel');

    if (!modal || !titleEl || !msgEl || !confirmBtn || !cancelBtn) return;

    titleEl.textContent = title;
    msgEl.textContent = message; // textContent is safe; white-space: pre-line in CSS preserves line breaks
    confirmBtn.textContent = confirmText;

    if (cancelText) {
        cancelBtn.textContent = cancelText;
        cancelBtn.style.display = 'inline-flex';
    } else {
        cancelBtn.style.display = 'none';
    }

    modal.style.display = 'flex';

    let trap = null;

    const cleanup = () => {
        modal.style.display = 'none';
        confirmBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        if (trap) {
            trap.release();
            trap = null;
        }
    };

    const onOk = () => {
        if (onConfirm) onConfirm();
        cleanup();
    };

    const onCancel = () => {
        cleanup();
    };

    // The trap focuses the modal's first focusable element, which is Cancel —
    // the safe default for a destructive prompt, so Enter dismisses rather than
    // confirms. Each button confirms or cancels through its own native
    // activation; a container-level Enter handler would wrongly confirm while
    // Cancel holds focus. Escape is owned by the focus trap below.
    confirmBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);

    // Confine focus to the modal while open; Escape (via the trap) dismisses it.
    trap = trapFocus(modal, { onClose: onCancel });
}
