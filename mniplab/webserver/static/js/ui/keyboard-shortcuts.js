import { appState } from '../state/app-state.js';
import { toggleSidebar } from './sidebar.js';
import { toggleTerminalPane } from './pane-arrangement.js';
import { openHelpModal, closeHelpModal, resetBrowserPreferences } from './help-modal.js';
import { setViewOptionsMenuOpen } from './view-options.js';
import { hideLinkContextMenu } from '../topology/interactions.js';
import { showConfirmModal } from '../utils/modal.js';
import { clearPinnedRelayPath } from '../topology/relay-paths.js';

function focusIsInPane() {
    const active = document.activeElement;
    return !!(active && active.closest && active.closest('[data-pane="terminal"], [data-pane="packet"]'));
}

function activeTerminalTabNode() {
    const active = document.activeElement;
    if (active && active.classList && active.classList.contains('terminal-tab')) {
        return active.getAttribute('data-node');
    }
    return null;
}

// Closing a terminal destroys an in-progress shell session, so both the Escape
// and Ctrl+W paths ask for confirmation through this single helper rather than
// closing outright.
function confirmCloseTerminal(node) {
    const tabManager = appState.tabManager;
    if (!tabManager || !node) return;
    showConfirmModal({
        title: 'Close Terminal',
        message: `Close the terminal for ${node}?`,
        confirmText: 'Close',
        onConfirm: () => tabManager.closeTab(node),
    });
}

export function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Don't trigger shortcuts when typing in input fields
        const isInputFocused = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName);
        // Don't trigger them while focus is anywhere in the Terminal pane or the
        // Packet Analysis pane (tab buttons, close-all button) either. The live
        // xterm session is already protected by xterm.js, which stops propagation
        // on the keys it forwards to the shell; this guard covers the chrome
        // that xterm.js does not own.
        const inPane = focusIsInPane();
        // One guard for every non-destructive shortcut, so they stay consistent:
        // a shortcut that is wrong to fire mid-typing is equally wrong to fire
        // from those panes' chrome.
        const shortcutsSuppressed = isInputFocused || inPane;

        // Ctrl+B (or Cmd+B on macOS) to toggle sidebar
        if ((e.ctrlKey || e.metaKey) && e.key === 'b' && !shortcutsSuppressed) {
            e.preventDefault();
            toggleSidebar();
        }

        // Ctrl+T (or Cmd+T on macOS) to show or hide the Terminal pane
        if ((e.ctrlKey || e.metaKey) && e.key === 't' && !shortcutsSuppressed) {
            e.preventDefault();
            toggleTerminalPane();
        }

        // Ctrl+W to close the active terminal. Destructive, so it asks for
        // confirmation instead of silently destroying an in-progress session.
        // It deliberately keeps no focus guard of its own: suppressing it would
        // also skip the preventDefault below, handing the keystroke to the
        // browser's own close-tab.
        if ((e.ctrlKey || e.metaKey) && e.key === 'w') {
            const node = inPane
                ? (activeTerminalTabNode() || appState.tabManager?.activeTab)
                : appState.tabManager?.activeTab;
            if (node) {
                e.preventDefault();
                confirmCloseTerminal(node);
            }
        }

        // ? to open help modal (Shift+/ is ?)
        if (e.key === '?' && !shortcutsSuppressed) {
            e.preventDefault();
            openHelpModal();
        }

        // Escape to close help modal, link context menu, view options, or (from a
        // terminal tab button) ask to close that terminal. The live xterm session
        // never reaches here — xterm.js stops propagation on the keys it forwards
        // to the shell — so this only fires from the panel's own chrome.
        if (e.key === 'Escape') {
            clearPinnedRelayPath();
            const helpModal = document.getElementById('help-modal');
            if (helpModal && !helpModal.classList.contains('hidden')) {
                e.preventDefault();
                closeHelpModal();
            } else {
                hideLinkContextMenu();
                setViewOptionsMenuOpen(false);
            }

            const node = inPane ? activeTerminalTabNode() : null;
            if (node && appState.tabManager && appState.tabManager.getTerminalCount() > 0) {
                e.preventDefault();
                confirmCloseTerminal(node);
            }
        }
    });

    // Setup help button click handler
    const helpBtn = document.getElementById('help-btn');
    if (helpBtn) {
        helpBtn.addEventListener('click', openHelpModal);
    }

    // Setup help modal close button
    const helpCloseBtn = document.getElementById('help-modal-close');
    if (helpCloseBtn) {
        helpCloseBtn.addEventListener('click', closeHelpModal);
    }

    const resetPreferencesBtn = document.getElementById('reset-browser-preferences');
    if (resetPreferencesBtn) {
        resetPreferencesBtn.addEventListener('click', resetBrowserPreferences);
    }

    // Close modal when clicking outside
    const helpModal = document.getElementById('help-modal');
    if (helpModal) {
        helpModal.addEventListener('click', (e) => {
            if (e.target === helpModal) {
                closeHelpModal();
            }
        });
    }
}
