const themeChangeListeners = [];

export function onThemeChange(listener) {
    if (typeof listener === 'function') {
        themeChangeListeners.push(listener);
    }
}

export function initTheme() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
    updateThemeIcon(savedTheme);

    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
        updateThemeToggleState(themeToggle, savedTheme);
        themeToggle.addEventListener('click', toggleTheme);
    }
}

export function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    updateThemeIcon(newTheme);
    updateThemeToggleState(document.getElementById('theme-toggle'), newTheme);

    for (const listener of themeChangeListeners) {
        try {
            listener(newTheme);
        } catch (err) {
            console.error('Error in theme change listener:', err);
        }
    }
}

// Report the toggle's state to assistive tech: true when dark mode is
// active. In the account menu the toggle is a "Dark mode" menuitemcheckbox
// (aria-checked); elsewhere an icon button (aria-pressed), whose only other
// signal is which of two icons is visually hidden.
function updateThemeToggleState(themeToggle, theme) {
    if (!themeToggle) return;
    const state = themeToggle.getAttribute('role') === 'menuitemcheckbox' ? 'aria-checked' : 'aria-pressed';
    themeToggle.setAttribute(state, theme === 'dark' ? 'true' : 'false');
}

export function updateThemeIcon(theme) {
    const darkIcon = document.querySelector('.theme-icon-dark');
    const lightIcon = document.querySelector('.theme-icon-light');

    if (darkIcon && lightIcon) {
        if (theme === 'dark') {
            darkIcon.classList.remove('hidden');
            lightIcon.classList.add('hidden');
        } else {
            darkIcon.classList.add('hidden');
            lightIcon.classList.remove('hidden');
        }
    }
}
