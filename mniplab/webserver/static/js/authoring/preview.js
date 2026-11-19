// The open draft's Preview Lab: whether it is starting, running, out of date
// or failed, shown in the status line and on the Preview toggle. A running
// Preview is read back from the server's Lab list, so it survives a reload.
import { escapeHtml } from '../utils/dom.js';

let options = null;
// 'none' | 'starting' | 'running' | 'failed'; a failed rebuild keeps the
// key of the Preview the server left running.
let status = 'none';
let labKey = null;
let failure = '';
let shownKey = null;

/** Show the Preview state again, as after the draft's evidence changed. */
export function renderPreview() {
    const toggle = document.getElementById('preview-draft');
    const running = Boolean(previewKey());
    toggle.textContent = running ? 'Stop Preview' : 'Preview';
    // Kept focusable while starting, so a keyboard user's place is not lost.
    toggle.setAttribute('aria-disabled', String(status === 'starting'));
    const outOfDate = running && !options.current();
    const key = `${status}\n${outOfDate}\n${labKey}\n${failure}`;
    if (key === shownKey) return;
    shownKey = key;
    const state = document.getElementById('status-preview-state');
    if (status === 'failed') {
        // The failure is the news; a Preview still running keeps its links.
        state.innerHTML = `<span role="alert">failed: ${escapeHtml(failure)}</span>`;
    } else {
        state.textContent = { none: 'not running', starting: 'starting…' }[status]
            || (outOfDate ? 'out of date' : 'running');
    }
    const link = document.getElementById('preview-lab-link');
    link.hidden = !running;
    if (running) link.href = `/labs/${encodeURIComponent(labKey)}`;
    else link.removeAttribute('href');
    document.getElementById('keep-draft').hidden = !running;
}

function set(nextStatus, nextKey = null, nextFailure = '') {
    status = nextStatus;
    labKey = nextKey;
    failure = nextFailure;
    renderPreview();
}

/**
 * Bind the Preview toggle and the status line's Preview segment.
 *
 * @param {object} opts
 * @param {(url: string, init?: object) => Promise<object>} opts.request The page's JSON request.
 * @param {() => boolean} opts.current Whether the saved draft's network matches the last Preview.
 * @param {() => string} opts.username Who is signed in; a Preview belongs to them.
 */
export function initPreview(opts) {
    options = opts;
}

/** The running Preview Lab's key, or null. */
export function previewKey() {
    return status === 'running' || status === 'failed' ? labKey : null;
}

/** Why the last Preview of the open draft failed to start, or null. */
export function previewFailure() {
    return status === 'failed' ? failure : null;
}

/** Whether a Preview of the open draft is starting. */
export function previewStarting() {
    return status === 'starting';
}

/**
 * Find this Instructor's running Preview of a draft on the server.
 *
 * @param {string} draftId The draft just opened.
 * @param {() => boolean} stillOpen Whether that draft is still the open one.
 */
export async function detectPreview(draftId, stillOpen) {
    set('none');
    let labs;
    try {
        ({ labs } = await options.request('/api/labs'));
    } catch {
        return;
    }
    const lab = labs.find(item => item.example === `preview-${draftId}`
        && item.group === options.username());
    if (lab && stillOpen() && status === 'none') {
        set('running', lab.key);
        options.changed?.();
    }
}

/** Record that the running Preview has gone, as a stop or a refused review found. */
export function previewStopped() {
    set('none');
}

/**
 * Start a Preview of the saved draft.
 *
 * @param {() => Promise<object|null>} start Ask the server for the Preview; resolves to
 *     its result, or null when the Instructor declined to replace another Lab.
 * @returns {Promise<object|null>} The result, or null when it did not start.
 */
export async function runPreview(start) {
    // A refused rebuild leaves the running Preview alone; the server stops it
    // only once the new one is allowed to start.
    const before = previewKey();
    set('starting', before);
    try {
        const result = await start();
        if (result) set('running', result.key);
        else set(before ? 'running' : 'none', before);
        return result;
    } catch (error) {
        set('failed', before, error.message);
        return null;
    }
}
