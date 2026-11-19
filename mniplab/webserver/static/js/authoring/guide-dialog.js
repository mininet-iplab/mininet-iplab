// The Guide's Expand dialog: room to write, and a Preview drawn by the
// same renderer Learners read the Guide through.
import { renderGuideMarkdown } from '../lab/guide.js';
import { bindTabs, selectTab } from './shell.js';

function renderPreview() {
    const preview = document.getElementById('guide-dialog-preview');
    const html = renderGuideMarkdown(document.getElementById('guide-dialog-source').value);
    if (html === null) preview.textContent = 'The Guide preview is unavailable.';
    else preview.innerHTML = html;
}

// Edits in the dialog go straight to the sidebar's Guide field, as typing there does.
export function initGuideDialog() {
    const dialog = document.getElementById('guide-dialog');
    const guide = document.getElementById('draft-guide');
    const source = document.getElementById('guide-dialog-source');
    const expand = document.getElementById('guide-expand');
    bindTabs(dialog.querySelector('[role="tablist"]'), tabId => {
        if (tabId === 'guide-tab-preview') renderPreview();
    });
    expand.addEventListener('click', () => {
        source.value = guide.value;
        selectTab('guide-tab-edit');
        dialog.showModal();
        source.focus();
    });
    source.addEventListener('input', () => {
        guide.value = source.value;
        guide.dispatchEvent(new Event('input'));
    });
    const close = () => {
        dialog.close();
        expand.focus();
    };
    document.getElementById('guide-dialog-close').addEventListener('click', close);
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        close();
    });
}
