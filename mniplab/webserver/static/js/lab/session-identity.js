import { getMe, renameSessionNickname } from '../services/api.js';
import { trapFocus } from '../utils/focus-trap.js';

let identity = null;
let focusTrap = null;
let bound = false;

export function applySessionIdentity(data) {
    identity = data;
    const button = document.getElementById('your-session-btn');
    if (!button) return;
    // The account menu item keeps its name and shows the code beside it.
    const code = document.getElementById('your-session-code');
    if (code) code.textContent = data.connection_code || '';
    if (bound) return;
    bound = true;
    button.addEventListener('click', openSessionIdentity);
    document.getElementById('session-identity-close').addEventListener('click', closeSessionIdentity);
    document.getElementById('session-nickname-form').addEventListener('submit', saveNickname);
    document.getElementById('session-copy').addEventListener('click', copyDetails);
}

function renderIdentity() {
    document.getElementById('session-code').textContent = identity.connection_code;
    document.getElementById('session-nickname').value = identity.nickname;
    const group = identity.mode === 'multi' ? `${identity.group} · ` : '';
    document.getElementById('session-details').value = `${group}${identity.nickname} · ${identity.connection_code}`;
}

async function openSessionIdentity() {
    if (!identity) return;
    const initialNickname = identity.nickname;
    renderIdentity();
    document.getElementById('session-identity-status').textContent = '';
    const modal = document.getElementById('session-identity-modal');
    modal.style.display = 'flex';
    focusTrap = trapFocus(modal, { onClose: closeSessionIdentity });
    try {
        const response = await getMe();
        if (!response.ok) return;
        if (document.getElementById('session-nickname').value !== initialNickname || modal.style.display !== 'flex') return;
        applySessionIdentity(await response.json());
        renderIdentity();
    } catch (_) {
        document.getElementById('session-identity-status').textContent = 'Showing your last-known Session details. Check your connection.';
    }
}

export function closeSessionIdentity() {
    const modal = document.getElementById('session-identity-modal');
    if (modal) modal.style.display = 'none';
    if (focusTrap) focusTrap.release();
    focusTrap = null;
}

async function saveNickname(event) {
    event.preventDefault();
    const input = document.getElementById('session-nickname');
    const status = document.getElementById('session-identity-status');
    try {
        const response = await renameSessionNickname(input.value);
        if (!response.ok) {
            status.textContent = 'Use a nickname of 1–40 characters without control characters.';
            input.setAttribute('aria-invalid', 'true');
            input.focus();
            return;
        }
        identity = { ...identity, ...await response.json() };
        input.removeAttribute('aria-invalid');
        renderIdentity();
        status.textContent = 'Nickname saved.';
    } catch (_) {
        status.textContent = 'Could not save your nickname. Please try again.';
    }
}

async function copyDetails() {
    const input = document.getElementById('session-details');
    const status = document.getElementById('session-identity-status');
    try {
        await navigator.clipboard.writeText(input.value);
        status.textContent = 'Session details copied.';
    } catch (_) {
        // Classroom servers are often accessed over LAN HTTP, where the
        // Clipboard API is unavailable. Keep a selectable manual path too.
        input.focus();
        input.select();
        status.textContent = document.execCommand('copy')
            ? 'Session details copied.' : 'Details selected. Press Ctrl+C or Command+C to copy.';
    }
}
