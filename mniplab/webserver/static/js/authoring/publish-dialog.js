// Publish…: what stops the draft publishing, each with a one-step fix, or
// the one decision left — a new Lab Example, or an update of the one the
// draft came from, asked only when both are possible.
import { renderKeepChoices, keepReviewedRows } from './keep-review.js';
import { keepRowCount } from './keep-rows.js';
import { escapeHtml } from '../utils/dom.js';

/**
 * Bind the Publish… control to its dialog.
 *
 * @param {object} actions
 * @param {() => Promise<object>} actions.readiness Save pending edits, then say
 *     what publishing needs: ``missing`` (``need``, and ``fixLabel`` with
 *     ``fix`` when one step fixes it), ``published`` when this revision is
 *     already published, ``canUpdate`` and the Catalog ``name``.
 * @param {(mode: string) => Promise<string|null>} actions.publish Publish in
 *     ``save_as_new`` or ``update_existing`` mode; the failure, or null.
 */
export function initPublishDialog(actions) {
    const dialog = document.getElementById('publish-dialog');
    const trigger = document.getElementById('publish-draft');
    const list = document.getElementById('publish-missing-list');
    const summary = document.getElementById('publish-summary');
    const feedback = document.getElementById('publish-feedback');
    const publishNew = document.getElementById('publish-new');
    const publishUpdate = document.getElementById('publish-update');
    const close = document.getElementById('publish-close');
    let fixes = [];
    let rows = null;
    let checking = false;
    let busy = false;
    const progress = document.getElementById('publish-progress');

    function hide() {
        dialog.close();
        trigger.focus();
    }

    function render(state) {
        const blocked = state.missing.length > 0;
        checking = state.checking;
        document.getElementById('publish-check-note').hidden = blocked || !checking || state.published;
        const changes = document.getElementById('publish-changes');
        changes.hidden = blocked || state.published || !keepRowCount(state.changes);
        rows = changes.hidden ? null : renderKeepChoices(document.getElementById('publish-keep-choices'), state.changes);
        progress.textContent = '';
        fixes = state.missing.map(item => item.fix);
        document.getElementById('publish-missing').hidden = !blocked;
        list.innerHTML = state.missing.map((item, index) => `<li>
            <span>${escapeHtml(item.need)}</span>
            ${item.fix ? `<button type="button" class="btn-secondary" data-fix="${index}">${escapeHtml(item.fixLabel)}</button>` : ''}
        </li>`).join('');
        const ready = !blocked && !state.published;
        const name = escapeHtml(state.name);
        summary.hidden = blocked;
        summary.innerHTML = state.published
            ? 'This revision is already published. Edit the draft to publish it again.'
            : state.canUpdate
                ? `Publish <strong>${name}</strong> as a new lab, or update the one this draft came from?`
                : `Publish <strong>${name}</strong> to the Lab Catalog as a new lab?`;
        publishNew.hidden = !ready;
        publishNew.textContent = state.canUpdate ? 'Publish as new' : 'Publish';
        publishUpdate.hidden = !ready || !state.canUpdate;
        close.textContent = ready ? 'Cancel' : 'Close';
        feedback.textContent = '';
        dialog.setAttribute('aria-describedby', blocked ? 'publish-missing-title' : 'publish-summary');
    }

    trigger.addEventListener('click', async () => {
        const state = await actions.readiness();
        render(state);
        dialog.showModal();
        (list.querySelector('button') || (publishNew.hidden ? close : publishNew)).focus();
    });

    // A fix closes the dialog and takes the Instructor where the work is.
    list.addEventListener('click', event => {
        const button = event.target.closest('button[data-fix]');
        if (!button) return;
        hide();
        fixes[Number(button.dataset.fix)]();
    });

    async function publish(mode) {
        if (busy) return;
        busy = true;
        dialog.setAttribute('aria-busy', 'true');
        for (const button of [publishNew, publishUpdate, close]) button.disabled = true;
        const selected = rows?.selected().selectedCount;
        progress.textContent = checking || selected ? 'Starting check Lab and publishing…' : 'Publishing…';
        let failure;
        try {
            if (selected) await keepReviewedRows(rows);
            failure = await actions.publish(mode);
        } catch (error) {
            failure = error.message;
        }
        busy = false;
        dialog.removeAttribute('aria-busy');
        progress.textContent = '';
        for (const button of [publishNew, publishUpdate, close]) button.disabled = false;
        if (failure) {
            feedback.textContent = failure;
            return;
        }
        hide();
    }

    publishNew.addEventListener('click', () => publish('save_as_new'));
    publishUpdate.addEventListener('click', () => publish('update_existing'));
    close.addEventListener('click', hide);
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        if (!busy) hide();
    });
}
