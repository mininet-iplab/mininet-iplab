// The (i) button beside a name, the same disclosure the Capture and NAT Table
// panes use: its description stays out of the way until asked for. A disclosure
// rather than a hover tip, so a keyboard, touch and a screen reader open it as
// readily as a mouse, and it stays open.
//
// Markup: <button class="btn-icon info-tip" data-info-for="note-id" data-topic="Relay">
// beside the name, and the description as an element with that id.

const ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle>
    <line x1="12" y1="11" x2="12" y2="16"></line><circle cx="12" cy="7.5" r="0.6" fill="currentColor"></circle></svg>`;
let bound = false;

/** Give every (i) button its icon, name and closed state, and open or close it on click. */
export function initInfoTips(root = document) {
    for (const tip of root.querySelectorAll('.info-tip[data-info-for]')) {
        const label = `About ${tip.dataset.topic}`;
        tip.type = 'button';
        tip.setAttribute('aria-controls', tip.dataset.infoFor);
        tip.setAttribute('aria-expanded', 'false');
        tip.setAttribute('aria-label', label);
        tip.title = label;
        tip.innerHTML = ICON;
        if (tip.dataset.visibleLabel) {
            const text = document.createElement('span');
            text.textContent = tip.dataset.visibleLabel;
            tip.append(text);
        }
        document.getElementById(tip.dataset.infoFor).hidden = true;
    }
    if (bound) return;
    bound = true;
    document.addEventListener('click', event => {
        const tip = event.target.closest?.('.info-tip');
        if (tip) setInfoTip(tip, tip.getAttribute('aria-expanded') !== 'true');
    });
}

function setInfoTip(tip, open) {
    tip.setAttribute('aria-expanded', String(open));
    document.getElementById(tip.getAttribute('aria-controls')).hidden = !open;
}

/** Close every open description, so a reused dialog never opens showing the last Node's. */
export function closeInfoTips() {
    for (const tip of document.querySelectorAll('.info-tip[aria-expanded="true"]')) setInfoTip(tip, false);
}
