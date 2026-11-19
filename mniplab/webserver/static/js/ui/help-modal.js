import { trapFocus } from '../utils/focus-trap.js';
import { showConfirmModal } from '../utils/modal.js';
import { STORAGE_PREFIX as TERMINALS_KEY_PREFIX } from '../state/remembered-terminals.js';

let helpTrap = null;

export function openHelpModal() {
    const modal = document.getElementById('help-modal');
    if (modal) {
        modal.classList.remove('hidden');
        // Confine focus to the modal while it is open; Escape closes it.
        helpTrap = trapFocus(modal, { onClose: closeHelpModal });
    }
}

export function closeHelpModal() {
    const modal = document.getElementById('help-modal');
    if (modal) {
        modal.classList.add('hidden');
    }
    if (helpTrap) {
        // Returns focus to the element that opened the modal (the help button).
        helpTrap.release();
        helpTrap = null;
    }
}

/** Ask to reset this app's saved browser preferences and reload the page. */
export function resetBrowserPreferences() {
    const feedback = document.getElementById('browser-preferences-error');
    feedback.hidden = true;
    feedback.textContent = '';
    showConfirmModal({
        title: 'Reset browser preferences',
        message: 'Reset Mininet-IPLab’s saved theme, panel sizes and other browser preferences? '
            + 'The page will reload. Saved Labs, drafts and configurations are kept.',
        confirmText: 'Reset and reload',
        onConfirm: () => {
            try {
                for (const storage of [window.localStorage, window.sessionStorage]) {
                    // Collect keys before removing any, so their indices stay valid.
                    const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index));
                    for (const key of keys) {
                        // The open Terminals are session state, not a preference:
                        // kept, so they reattach after the reload.
                        if (key && key.startsWith(TERMINALS_KEY_PREFIX)) continue;
                        if (key && (key.startsWith('mniplab-') || key.startsWith('mniplab.')
                            || ['theme', 'collapsedGroups', 'topologyLayout', 'nodePositions'].includes(key))) {
                            storage.removeItem(key);
                        }
                    }
                }
                window.location.reload();
            } catch {
                feedback.textContent = 'Could not reset browser preferences. '
                    + 'Check your browser’s storage settings and try again.';
                feedback.hidden = false;
            }
        },
    });
}
