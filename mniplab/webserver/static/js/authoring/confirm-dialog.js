// One confirmation in the page's own dialog; a refusal is shown beside
// what was asked. Keep warnings can render shared choices and a second action.

/**
 * Ask before an action, run it on confirmation, and show why it was refused.
 *
 * Cancel holds focus first. A refusal hides the confirming button and
 * leaves Close; closing hands focus back to the control that asked.
 *
 * @param {object} request
 * @param {string} request.title The dialog's name, such as "Delete draft".
 * @param {string} request.message What is asked, as one or two sentences.
 * @param {string} request.confirmLabel The confirming button's text.
 * @param {() => Promise<void>} request.action Runs on confirmation; an Error
 *     it throws is the refusal shown.
 * @param {string} [request.itemsLabel] Introduces ``items``.
 * @param {string[]} [request.items] Named under the message, such as Groups.
 * @param {string} [request.refusal] Why it cannot be asked at all: shown
 *     with Close alone.
 * @param {(container: HTMLElement) => void} [request.renderChoices] Render review controls.
 * @param {string} [request.secondaryLabel] An alternative action's label.
 * @param {() => Promise<void>} [request.secondaryAction] The alternative action.
 * @param {boolean} [request.retryOnError] Keep choices available after a refusal.
 * @returns {Promise<boolean>} Whether an action ran.
 */
export function confirmAction({
    title, message, confirmLabel, action, itemsLabel = '', items = [], refusal = '',
    renderChoices = null, secondaryLabel = '', secondaryAction = null, retryOnError = false,
}) {
    const dialog = document.getElementById('confirm-dialog');
    const confirm = document.getElementById('confirm-dialog-confirm');
    const cancel = document.getElementById('confirm-dialog-cancel');
    const secondary = document.getElementById('confirm-dialog-secondary');
    const choices = document.getElementById('confirm-dialog-choices');
    choices.replaceChildren();
    choices.hidden = !renderChoices;
    renderChoices?.(choices);
    secondary.textContent = secondaryLabel;
    secondary.hidden = !secondaryAction;
    secondary.disabled = false;
    const feedback = document.getElementById('confirm-dialog-feedback');
    const label = document.getElementById('confirm-dialog-items-label');
    const list = document.getElementById('confirm-dialog-items');
    document.getElementById('confirm-dialog-title').textContent = title;
    document.getElementById('confirm-dialog-message').textContent = message;
    label.textContent = itemsLabel;
    list.replaceChildren(...items.map(item => Object.assign(document.createElement('li'),
        { textContent: item })));
    label.hidden = list.hidden = !items.length;
    dialog.setAttribute('aria-describedby', items.length
        ? 'confirm-dialog-message confirm-dialog-items-label confirm-dialog-items'
        : 'confirm-dialog-message');
    feedback.textContent = '';
    confirm.textContent = confirmLabel;
    confirm.hidden = false;
    confirm.disabled = cancel.disabled = false;
    cancel.textContent = 'Cancel';

    return new Promise(resolve => {
        let ran = false;
        const finish = () => {
            confirm.removeEventListener('click', onConfirm);
            cancel.removeEventListener('click', onCancel);
            secondary.removeEventListener('click', onSecondary);
            dialog.removeEventListener('cancel', onEscape);
            dialog.close();
            resolve(ran);
        };
        async function run(chosen) {
            confirm.disabled = cancel.disabled = secondary.disabled = true;
            try {
                await chosen();
                ran = true;
                finish();
            } catch (error) {
                if (retryOnError) {
                    feedback.textContent = error.message;
                    confirm.disabled = cancel.disabled = secondary.disabled = false;
                } else showRefusal(error.message);
                cancel.focus();
            }
        }
        const onConfirm = () => run(action);
        const onSecondary = () => run(secondaryAction);
        function showRefusal(reason) {
            confirm.hidden = secondary.hidden = true;
            cancel.disabled = false;
            cancel.textContent = 'Close';
            feedback.textContent = reason;
        }
        const onCancel = () => finish();
        const onEscape = event => {
            event.preventDefault();
            if (!cancel.disabled) finish();
        };
        confirm.addEventListener('click', onConfirm);
        secondary.addEventListener('click', onSecondary);
        cancel.addEventListener('click', onCancel);
        dialog.addEventListener('cancel', onEscape);
        dialog.showModal();
        if (refusal) showRefusal(refusal);
        cancel.focus();
    });
}
