import { groupByArea } from './areas.js';
import { appState } from '../state/app-state.js';
import { getExamples } from '../services/api.js';
import { escapeHtml } from '../utils/dom.js';
import { startLab } from './lifecycle.js';

export function handleLabSearch() {
    clearTimeout(appState.labSearchDebounce);
    appState.labSearchDebounce = setTimeout(filterAndRenderLabs, 200);
}

export async function renderSidebarLabList() {
    const list = document.getElementById('sidebar-lab-list');
    const countEl = document.getElementById('sidebar-lab-count');
    if (!list) return;
    list.innerHTML = '<li class="sidebar-lab-empty" role="status">Loading labs…</li>';
    if (countEl) countEl.textContent = '';
    try {
        const res = await getExamples();
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        const { examples, areas } = await res.json();
        appState.catalogAreas = areas || [];
        appState.allExamples = examples || [];
        filterAndRenderLabs();
        // Wire search with debounce (remove+re-add to prevent stacking)
        const searchInput = document.getElementById('sidebar-lab-search');
        if (searchInput) {
            searchInput.removeEventListener('input', handleLabSearch);
            searchInput.addEventListener('input', handleLabSearch);
            searchInput.value = '';
        }
    } catch (err) {
        console.error('Failed to load lab list:', err);
        list.innerHTML = '<li class="sidebar-lab-empty">Failed to load labs. Please refresh.</li>';
    }
}

export function filterAndRenderLabs() {
    const list = document.getElementById('sidebar-lab-list');
    const countEl = document.getElementById('sidebar-lab-count');
    const searchInput = document.getElementById('sidebar-lab-search');
    if (!list) return;
    const searchTerm = searchInput ? searchInput.value.toLowerCase().trim() : '';
    let filtered = appState.allExamples;
    if (searchTerm) {
        filtered = filtered.filter(lab =>
            lab.name.toLowerCase().includes(searchTerm) ||
            (lab.description || '').toLowerCase().includes(searchTerm) ||
            (lab.protocols || []).some(p => p.toLowerCase().includes(searchTerm))
        );
    }
    if (countEl) {
        countEl.textContent = filtered.length === appState.allExamples.length
            ? `${appState.allExamples.length} lab${appState.allExamples.length !== 1 ? 's' : ''}`
            : `${filtered.length} of ${appState.allExamples.length}`;
    }
    list.innerHTML = '';
    if (filtered.length === 0) {
        list.innerHTML = '<li class="sidebar-lab-empty">No matching labs.</li>';
        return;
    }
    groupByArea(filtered, appState.catalogAreas || []).forEach(({ area, examples }, index) => {
        const groupItem = document.createElement('li');
        groupItem.className = 'sidebar-lab-area';
        const headingId = `sidebar-area-${index}`;
        groupItem.innerHTML = `<div role="group" aria-labelledby="${headingId}">
            <h3 id="${headingId}" class="catalog-area-heading">${escapeHtml(area)}</h3>
            <ul class="catalog-area-list" role="list"></ul></div>`;
        list.appendChild(groupItem);
        const groupList = groupItem.querySelector('ul');
        examples.forEach(lab => {
            const li = document.createElement('li');
            li.className = 'sidebar-lab-item';
            const tagsHtml = (lab.protocols && lab.protocols.length > 0)
                ? `<div class="sidebar-lab-tags">${lab.protocols.map(p => `<span class="sidebar-lab-tag">${escapeHtml(p)}</span>`).join('')}</div>`
                : '';
            const descHtml = lab.description
                ? `<p class="sidebar-lab-desc">${escapeHtml(lab.description)}</p>`
                : '';
            li.innerHTML = `
                <div class="sidebar-lab-header">
                    <span class="sidebar-lab-name">${escapeHtml(lab.name)}</span>
                    <button class="btn-primary sidebar-lab-start" aria-label="Start ${escapeHtml(lab.name)}" type="button">
                        <svg aria-hidden="true" focusable="false" viewBox="0 0 16 16" width="12" height="12" fill="currentColor"><path d="M3 2.5l10 5.5-10 5.5V2.5z"/></svg>
                    </button>
                </div>
                ${descHtml}
                ${tagsHtml}`;
            li.querySelector('.sidebar-lab-start').addEventListener('click', () => startLab(lab.id));
            groupList.appendChild(li);
        });
    });
}
