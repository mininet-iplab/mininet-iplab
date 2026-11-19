import { appState } from '../state/app-state.js';
import { loadGuide } from '../services/api.js';
import { layoutPanes } from '../ui/pane-arrangement.js';
import { normalizeGuideMarkdown } from '../utils/markdown.js';

export function renderGuideMarkdown(markdown) {
    if (!window.marked || !window.DOMPurify) {
        return null;
    }
    const parsed = window.marked.parse(normalizeGuideMarkdown(markdown));
    return window.DOMPurify.sanitize(parsed, { USE_PROFILES: { html: true } });
}

export function normalizeGuideUrls(container, guidePath) {
    const baseUrl = new URL(guidePath, window.location.origin);

    container.querySelectorAll('a[href]').forEach(link => {
        const rawHref = link.getAttribute('href') || '';
        if (!rawHref || rawHref.startsWith('#')) return;
        try {
            const resolved = new URL(rawHref, baseUrl);
            const protocol = resolved.protocol.toLowerCase();
            if (!['http:', 'https:', 'mailto:', 'tel:'].includes(protocol)) {
                link.removeAttribute('href');
                return;
            }
            link.setAttribute('href', resolved.toString());
            if (protocol === 'http:' || protocol === 'https:') {
                const isExternal = resolved.origin !== window.location.origin;
                link.setAttribute('target', isExternal ? '_blank' : '_self');
                link.setAttribute('rel', 'noopener noreferrer');
            }
        } catch (_) {
            link.removeAttribute('href');
        }
    });

    container.querySelectorAll('img[src]').forEach(image => {
        const rawSrc = image.getAttribute('src') || '';
        if (!rawSrc) return;
        try {
            const resolved = new URL(rawSrc, baseUrl);
            const protocol = resolved.protocol.toLowerCase();
            if (!['http:', 'https:', 'data:'].includes(protocol)) {
                image.removeAttribute('src');
                return;
            }
            image.setAttribute('src', resolved.toString());
        } catch (_) {
            image.removeAttribute('src');
        }
    });
}

function showNoGuide(labId) {
    document.getElementById('lab-guide-status').textContent = 'No Guide provided for this Lab.';
    document.getElementById('lab-guide-content').textContent = '';
    document.getElementById('lab-guide-open-btn').style.display = 'none';
    appState.currentGuidePath = null;
    appState.loadedGuideLabId = labId;
    return false;
}

export async function loadLabGuide(labId) {
    const statusEl = document.getElementById('lab-guide-status');
    const contentEl = document.getElementById('lab-guide-content');
    const openBtn = document.getElementById('lab-guide-open-btn');

    if (!labId || !contentEl || !statusEl) {
        if (contentEl) contentEl.textContent = '';
        if (statusEl) statusEl.textContent = 'No guide loaded.';
        if (openBtn) openBtn.style.display = 'none';
        appState.currentGuidePath = null;
        appState.loadedGuideLabId = null;
        return false;
    }

    if (appState.hasGuide === false) return showNoGuide(labId);

    const guidePath = `/api/load_guide?example=${encodeURIComponent(labId)}`
        + (appState.labKey ? `&key=${encodeURIComponent(appState.labKey)}` : '');
    if (appState.loadedGuideLabId === labId && appState.currentGuidePath === guidePath) {
        return true;
    }

    statusEl.textContent = 'Loading guide…';
    contentEl.textContent = '';
    if (openBtn) openBtn.style.display = 'none';

    try {
        const response = await loadGuide(labId, appState.labKey);
        if (!response.ok) {
            throw new Error('Guide not found');
        }
        const markdown = await response.text();
        if (!markdown.trim()) return showNoGuide(labId);
        const safeHtml = renderGuideMarkdown(markdown);
        if (safeHtml === null) {
            contentEl.textContent = markdown;
            statusEl.textContent = 'Guide loaded (plain text fallback).';
        } else {
            contentEl.innerHTML = safeHtml;
            normalizeGuideUrls(contentEl, guidePath);
            statusEl.textContent = 'Guide loaded.';
        }
        appState.currentGuidePath = guidePath;
        appState.loadedGuideLabId = labId;

        if (openBtn) {
            openBtn.style.display = '';
        }
        return true;
    } catch (_) {
        statusEl.textContent = 'Failed to load Guide. Please try again.';
        contentEl.textContent = '';
        appState.currentGuidePath = null;
        appState.loadedGuideLabId = labId;
        if (openBtn) openBtn.style.display = 'none';
        return false;
    }
}

export function clearLabGuide() {
    const statusEl = document.getElementById('lab-guide-status');
    const contentEl = document.getElementById('lab-guide-content');
    const openBtn = document.getElementById('lab-guide-open-btn');
    appState.currentGuidePath = null;
    appState.loadedGuideLabId = null;
    if (statusEl) statusEl.textContent = 'No guide loaded.';
    if (contentEl) contentEl.textContent = '';
    if (openBtn) openBtn.style.display = 'none';
}

/** Load the running Lab's Guide into the Guide pane and lay the Panes out. */
export async function syncRunningGuide(labId) {
    await loadLabGuide(labId);
    layoutPanes();
}
