// The status line along the bottom of Lab Authoring: whether the draft is
// saved, the page's one polite region for outcomes, and an alert region kept
// for blocking failures.
import { escapeHtml } from '../utils/dom.js';

let draftKey = null;

function formHidden() {
    return document.getElementById('draft-form').hidden;
}

/**
 * Announce an outcome politely, such as "Added Host h1".
 *
 * With no draft open the status line is hidden, so the page's own status
 * paragraph carries the message.
 *
 * @param {string} message What happened.
 * @param {object} [options]
 * @param {boolean} [options.shown=true] False when the status line already
 *     shows the same words, so they are heard without being read twice.
 */
export function announce(message, { shown = true } = {}) {
    const target = document.getElementById(formHidden() ? 'authoring-status' : 'status-message');
    document.getElementById('status-alert').textContent = '';
    target.classList.toggle('sr-only', !shown);
    target.textContent = message;
}

/**
 * Announce a failure that stopped what the Instructor asked for.
 *
 * @param {string} message What failed and why.
 */
export function announceFailure(message) {
    if (formHidden()) {
        document.getElementById('authoring-status').textContent = message;
        return;
    }
    document.getElementById('status-message').textContent = '';
    document.getElementById('status-alert').textContent = message;
}

/**
 * Show the draft's save state.
 *
 * @param {string} state The autosave state.
 * @param {object|null} refusal Why saving was refused: ``reason``, and ``fix``
 *     when the reason names a field the Instructor can be taken to.
 * @param {object} actions ``fix(refusal)`` and ``reopen()``.
 */
export function showDraftState(state, refusal, actions) {
    // A standing refusal stays on screen while the next attempt runs, so its
    // link is not pulled from under the Instructor's focus.
    const shown = state === 'conflict' ? state
        : refusal ? 'refused'
        : state === 'saving' || state === 'pending' ? 'saving' : state;
    const key = `${shown}\n${refusal?.reason ?? ''}`;
    // Re-rendering the same state would drop focus from its button.
    if (key === draftKey) return;
    draftKey = key;
    const target = document.getElementById('status-draft-state');
    if (shown === 'conflict') {
        target.innerHTML = `<span role="alert">Someone else changed this draft ·
            <button type="button" class="authoring-link-button" id="status-draft-reopen">Reopen</button></span>`;
        target.querySelector('button').addEventListener('click', actions.reopen);
    } else if (shown === 'refused') {
        const reason = escapeHtml(refusal.reason);
        target.innerHTML = refusal.fix
            ? `Can't save yet: <button type="button" class="authoring-link-button" id="status-draft-fix">${reason}</button>`
            : `Can't save yet: ${reason}`;
        target.querySelector('button')?.addEventListener('click', () => actions.fix(refusal));
    } else {
        target.textContent = shown === 'saving' ? 'Saving…' : 'Saved';
    }
}
