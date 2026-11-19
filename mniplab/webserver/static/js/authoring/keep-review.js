// Keep review: the running Preview's supported changes, each ticked by
// hand to write it into the draft. The same rows serve Keep, Stop and Publish.
import { renderKeepRows, keepRowCount } from './keep-rows.js';
import { confirmAction } from './confirm-dialog.js';
import { closeInfoTips } from './info-tip.js';

let options = null;
let review = null;
let current = null;
let readSerial = 0;

function count(total, noun) {
    return `${total} ${noun}${total === 1 ? '' : 's'}`;
}

/** The most recently read supported changes, optionally for one Node. */
export function previewChanges(nodeId = null) {
    const changes = current && current.draftId === options.draft()?.id && current.key === options.previewKey()
        ? current : { routers: [], features: [] };
    if (nodeId === null) return changes;
    return { routers: changes.routers.filter(router => router.node_id === nodeId),
        features: changes.features.filter(feature => feature.node_id === nodeId) };
}

/** Refresh after Preview startup, Selection, or returning from the running Lab. */
export async function refreshPreviewChanges() {
    const draft = options.draft();
    const key = options.previewKey();
    const serial = ++readSerial;
    if (!draft || !key) {
        current = null;
        options.updated?.();
        return;
    }
    try {
        const result = await options.request(`${options.api}/${encodeURIComponent(draft.id)}/keep`);
        if (serial !== readSerial || options.draft()?.id !== draft.id || options.previewKey() !== key) return;
        current = { ...result, draftId: draft.id, allRouters: result.routers,
            routers: result.routers.filter(router => router.sections.length) };
    } catch {
        if (serial !== readSerial) return;
        current = null;
    }
    options.updated?.();
}

/** How many changed Router or Service rows were last read. */
export function previewChangeCount(nodeId = null) {
    return keepRowCount(previewChanges(nodeId));
}

/** The Router's current FRR text, including unchanged configuration. */
export function previewFrr(nodeId) {
    const changes = previewChanges();
    return changes.allRouters?.find(router => router.node_id === nodeId)?.config;
}

function render(routers, features, hasKeptChanges) {
    const container = document.getElementById('keep-routers');
    review.rows = renderKeepRows(container, { routers, features });
    if (!routers.length && !features.length) {
        container.innerHTML = hasKeptChanges === true
            ? '<p>Your changes are kept in the draft.</p><p>No new supported changes to Keep.</p>'
            : `<p>No supported changes to Keep${hasKeptChanges === false ? ' yet' : ''}.</p>`;
    }
    document.getElementById('keep-dialog-intro').hidden = !review.rows.count;
    document.getElementById('keep-cancel').textContent = review.rows.count ? 'Cancel' : 'Close';
    document.getElementById('keep-dialog').setAttribute('aria-describedby',
        review.rows.count ? 'keep-dialog-intro' : 'keep-routers');
    const all = document.getElementById('keep-select-all');
    all.checked = false;
    all.indeterminate = false;
    document.getElementById('keep-select-all-label').hidden = !review.rows.count;
    document.getElementById('keep-apply').hidden = !review.rows.count;
    document.getElementById('keep-dialog-feedback').textContent = '';
}

function close(focusTrigger = true) {
    document.getElementById('keep-dialog').close();
    const trigger = review?.trigger;
    review = null;
    if (focusTrigger && trigger?.isConnected) trigger.focus();
}

/** Keep the selections from a shared review into the open draft. */
export async function keepReviewedRows(rows) {
    const { selections, features, selectedCount } = rows.selected();
    if (!selectedCount) throw new Error('Select at least one change to Keep.');
    const draft = options.draft();
    const saved = await options.request(`${options.api}/${encodeURIComponent(draft.id)}/keep`, {
        method: 'POST', body: JSON.stringify({ revision: draft.revision, selections, features }),
    });
    options.kept(saved, `Kept ${count(selectedCount, 'change')} into the draft.`);
    await refreshPreviewChanges();
}

/** Render the shared rows with their Select all control. */
export function renderKeepChoices(container, changes) {
    const allId = `${container.id}-all`;
    container.innerHTML = `<label class="authoring-choice" for="${allId}"><input type="checkbox" id="${allId}"> Select all</label>`
        + `<div id="${container.id}-rows"></div>`;
    const rows = renderKeepRows(container.lastElementChild, changes);
    const all = container.querySelector('input');
    all.closest('label').hidden = !rows.count;
    all.addEventListener('change', () => rows.selectAll(all.checked));
    container.onchange = () => {
        const { selectedCount, completeCount } = rows.selected();
        all.checked = completeCount === rows.count && rows.count > 0;
        all.indeterminate = selectedCount > 0 && !all.checked;
    };
    return rows;
}

async function apply() {
    const { selectedCount } = review.rows.selected();
    const feedback = document.getElementById('keep-dialog-feedback');
    if (!selectedCount) {
        feedback.textContent = 'Select at least one change to Keep.';
        document.querySelector('#keep-routers input')?.focus();
        return;
    }
    try {
        await keepReviewedRows(review.rows);
        close();
    } catch (error) {
        feedback.textContent = error.message;
        feedback.setAttribute('role', 'alert');
    }
}

/**
 * Bind the Keep review dialog.
 *
 * @param {object} opts
 * @param {(url: string, init?: object) => Promise<object>} opts.request The page's JSON request.
 * @param {string} opts.api The drafts endpoint.
 * @param {() => object} opts.draft The open draft.
 * @param {() => Promise<boolean>} opts.saved Save pending edits; whether the draft is saved.
 * @param {() => (string|null)} opts.previewKey The running Preview's key.
 * @param {() => void} opts.previewGone Record that no Preview of the draft runs.
 * @param {(saved: object, message: string) => void} opts.kept Show the draft a keep saved.
 * @param {(message: string) => void} opts.fail Report a failure that stopped the review.
 */
export function initKeepReview(opts) {
    options = opts;
    const dialog = document.getElementById('keep-dialog');
    document.getElementById('keep-apply').addEventListener('click', apply);
    document.getElementById('keep-select-all').addEventListener('change', event => {
        review?.rows.selectAll(event.target.checked);
    });
    document.getElementById('keep-routers').addEventListener('change', () => {
        const { selectedCount, completeCount } = review.rows.selected();
        const all = document.getElementById('keep-select-all');
        all.checked = completeCount === review.rows.count;
        all.indeterminate = selectedCount > 0 && !all.checked;
    });
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        close();
    });
    dialog.querySelector('form').addEventListener('submit', event => {
        event.preventDefault();
        close();
    });
}

/**
 * Review the running Preview's not yet kept changes.
 *
 * Before a stop or rebuild (a ``continuation``), nothing to review continues
 * at once; otherwise the review warns that what is not kept will be lost.
 *
 * @param {(() => Promise<void>)|null} continuation What to do once reviewed.
 * @param {HTMLElement|null} trigger Where focus returns when the review is cancelled.
 */
export async function reviewPreviewChanges(continuation = null, trigger = null, nodeId = null) {
    const draft = options.draft();
    if (!draft || !options.previewKey()) return continuation?.();
    if (!await options.saved()) return null;
    let result;
    try {
        result = await options.request(`${options.api}/${encodeURIComponent(draft.id)}/keep`);
    } catch (error) {
        if (error.status === 409) {
            options.previewGone();
            return continuation?.();
        }
        options.fail(error.message);
        return null;
    }
    const routers = result.routers.filter(router => router.sections.length
        && (nodeId === null || router.node_id === nodeId));
    const features = (result.features || []).filter(feature => nodeId === null || feature.node_id === nodeId);
    if (continuation && !routers.length && !features.length) return continuation();
    const changes = keepRowCount({ routers, features });
    if (continuation) {
        let rows;
        return confirmAction({
            title: 'Stop Preview',
            message: `${count(changes, 'change')} not yet kept will be lost when this Preview stops. `
                + 'Keep selected changes, or discard them. Unselected changes will be lost.',
            confirmLabel: 'Keep selected',
            renderChoices: container => { rows = renderKeepChoices(container, { routers, features }); },
            action: async () => { await keepReviewedRows(rows); await continuation(); },
            secondaryLabel: 'Discard and continue',
            secondaryAction: continuation,
            retryOnError: true,
        });
    }
    review = { routers, features, trigger: trigger || document.activeElement };
    render(routers, features, result.has_kept_changes);
    if (nodeId !== null) {
        review.rows.selectAll(true);
        const all = document.getElementById('keep-select-all');
        all.checked = Boolean(review.rows.count);
    }
    const dialog = document.getElementById('keep-dialog');
    closeInfoTips();
    dialog.showModal();
    (dialog.querySelector('#keep-routers input:not(:disabled), #keep-routers button') || document.getElementById('keep-cancel')).focus();
    return null;
}
