import { appState } from '../state/app-state.js';

export function setViewOptionsMenuOpen(isOpen) {
    const viewBtn = document.getElementById('view-options-btn');
    const viewMenu = document.getElementById('view-options-menu');
    if (!viewBtn || !viewMenu) return;

    viewBtn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    viewMenu.classList.toggle('hidden', !isOpen);
}

export function initViewOptionsMenu() {
    if (appState.viewOptionsMenuInitialized) return;

    const viewBtn = document.getElementById('view-options-btn');
    const viewMenu = document.getElementById('view-options-menu');
    if (!viewBtn || !viewMenu) return;

    appState.viewOptionsMenuInitialized = true;

    viewBtn.addEventListener('click', (event) => {
        event.stopPropagation();
        const shouldOpen = viewMenu.classList.contains('hidden');
        setViewOptionsMenuOpen(shouldOpen);
    });

    viewBtn.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            setViewOptionsMenuOpen(true);
            const firstFocusable = viewMenu.querySelector('input, button');
            if (firstFocusable) firstFocusable.focus();
        }
    });

    viewMenu.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            setViewOptionsMenuOpen(false);
            viewBtn.focus();
        }
    });

    viewMenu.querySelectorAll('button').forEach((button) => {
        button.addEventListener('click', () => {
            setViewOptionsMenuOpen(false);
        });
    });

    document.addEventListener('click', (event) => {
        if (viewMenu.classList.contains('hidden')) return;
        if (viewMenu.contains(event.target) || event.target === viewBtn) return;
        setViewOptionsMenuOpen(false);
    });
}
