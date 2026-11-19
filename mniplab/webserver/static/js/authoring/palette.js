// The palette rail: one Tab stop for every Node kind (APG toolbar), with each
// kind's name shown on hover and on focus in place of permanent labels.
import { NARROW_LAYOUT } from './shell.js';

// A pointer crossing from an item onto its name keeps the name (WCAG 1.4.13).
const HIDE_DELAY_MS = 150;

/**
 * Render the palette and bind its keyboard, tooltip and drag behaviour.
 *
 * @param {object} options
 * @param {Array<Array<{type: string, label: string}>>} options.groups Node kinds, grouped.
 * @param {(type: string) => string} options.iconPath The icon's SVG path.
 * @param {(type: string) => string} options.color The icon's colour.
 * @param {(type: string) => void} options.onPlace Place a Node of this kind.
 * @param {string} options.dragType The drag data type the canvas accepts.
 */
export function initPalette({ groups, iconPath, color, onPlace, dragType }) {
    const toolbar = document.getElementById('node-palette');
    const tip = document.getElementById('palette-tip');
    const narrow = window.matchMedia(NARROW_LAYOUT);
    let hideTimer = null;

    groups.forEach((group, index) => {
        if (index) toolbar.append(document.createElement('hr'));
        for (const item of group) {
            const button = document.createElement('button');
            button.type = 'button';
            button.id = `palette-${item.type}`;
            button.className = 'authoring-palette-item';
            button.draggable = true;
            button.tabIndex = toolbar.querySelector('button') ? -1 : 0;
            button.dataset.label = item.label;
            button.setAttribute('aria-label', `Add ${item.label}`);
            button.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path d="${iconPath(item.type)}" fill="${color(item.type)}"></path></svg>`;
            button.addEventListener('click', () => onPlace(item.type));
            button.addEventListener('dragstart', event => {
                hideTip();
                event.dataTransfer.setData(dragType, item.type);
                event.dataTransfer.effectAllowed = 'copy';
            });
            toolbar.append(button);
        }
    });
    const items = [...toolbar.querySelectorAll('button')];

    function showTip(button) {
        clearTimeout(hideTimer);
        tip.textContent = button.dataset.label;
        tip.hidden = false;
        const box = button.getBoundingClientRect();
        if (narrow.matches) {
            tip.style.left = `${box.left}px`;
            tip.style.top = `${box.bottom + 4}px`;
        } else {
            tip.style.left = `${box.right + 4}px`;
            tip.style.top = `${box.top + (box.height - tip.offsetHeight) / 2}px`;
        }
    }

    function hideTip() {
        clearTimeout(hideTimer);
        tip.hidden = true;
    }

    const hideSoon = () => {
        clearTimeout(hideTimer);
        hideTimer = setTimeout(() => {
            if (!items.includes(document.activeElement)) tip.hidden = true;
        }, HIDE_DELAY_MS);
    };

    for (const button of items) {
        button.addEventListener('mouseenter', () => showTip(button));
        button.addEventListener('mouseleave', hideSoon);
        button.addEventListener('focus', () => {
            for (const other of items) other.tabIndex = other === button ? 0 : -1;
            showTip(button);
        });
        button.addEventListener('blur', hideTip);
    }
    tip.addEventListener('mouseenter', () => clearTimeout(hideTimer));
    tip.addEventListener('mouseleave', hideTip);
    // Escape dismisses a showing name and nothing else, such as undoing a placement.
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || tip.hidden) return;
        hideTip();
        event.stopPropagation();
    });

    const syncOrientation = () => toolbar.setAttribute(
        'aria-orientation', narrow.matches ? 'horizontal' : 'vertical');
    syncOrientation();
    narrow.addEventListener('change', syncOrientation);

    toolbar.addEventListener('keydown', event => {
        const index = items.indexOf(document.activeElement);
        if (index < 0) return;
        const next = {
            ArrowDown: index + 1, ArrowRight: index + 1, ArrowUp: index - 1, ArrowLeft: index - 1,
            Home: 0, End: items.length - 1,
        }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        items[(next + items.length) % items.length].focus();
    });
}
