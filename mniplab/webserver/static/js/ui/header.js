/**
 * Shared Header Management.
 *
 * Coordinates the account menu, theme toggling, user identity display
 * (username and role) and logout across the main Web UI and Lab Authoring.
 */

import { initTheme } from './theme.js';
import { bindMenuButton } from './menu-button.js';
import { logout } from '../services/api.js';
import { forgetRememberedTerminals } from '../state/remembered-terminals.js';

export function initHeader({ username, role, onLogout } = {}) {
    initTheme();

    const accountButton = document.getElementById('account-menu-btn');
    const accountMenu = document.getElementById('account-menu');
    if (accountButton && accountMenu) bindMenuButton(accountButton, accountMenu);

    if (username !== undefined || role !== undefined) {
        applyHeaderIdentity(username, role);
    }

    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            if (onLogout) {
                await onLogout();
            } else {
                try {
                    await logout();
                } catch (err) {
                    console.error('Logout error:', err);
                }
                forgetRememberedTerminals();
                window.location.href = '/';
            }
        });
    }
}

export function applyHeaderIdentity(username, role) {
    const usernameEl = document.getElementById('header-username');
    const badge = document.getElementById('role-badge');
    if (usernameEl && username) {
        usernameEl.textContent = username;
    }
    if (badge && role) {
        badge.textContent = role === 'instructor' ? 'Instructor' : 'Learner';
        badge.dataset.role = role;
    }
}
