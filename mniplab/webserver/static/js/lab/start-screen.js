// The Learner's start screen (Multi-Lab Mode): with no Lab running, the
// Group's Assignment as catalog cards, one-click Start, and the
// persistent-config note. Shown in place of the empty state, the sidebar
// closed, so the Assignment — not navigation chrome — is the first thing a
// Learner sees.

import { groupByArea } from './areas.js';
import { appState } from '../state/app-state.js';
import { getExamples } from '../services/api.js';
import { escapeHtml } from '../utils/dom.js';
import { showConfirmModal } from '../utils/modal.js';
import { startLab } from './lifecycle.js';

// The Assignment fetched for the cards, kept so confirmStartClean can name the
// Lab in its dialog without a second fetch.
let assignmentExamples = [];

// The start screen and the empty state are mutually exclusive: showing one
// hides the other. A running Lab always replaces both (see
// showTopologyEmptyState), so this only ever runs in the no-Lab state.
export function showLearnerStartScreen(show) {
    const emptyState = document.getElementById('topology-empty-state');
    const startScreen = document.getElementById('start-screen');
    if (startScreen) startScreen.style.display = show ? '' : 'none';
    if (emptyState) emptyState.style.display = show ? 'none' : '';
}

// Fetch the Group's Assignment and render it as cards. Only the Learner's own
// Group reaches this surface; the Instructor's listing lives in the Catalog.
export async function renderStartScreen() {
    if (appState.currentUserRole !== 'learner' || appState.labMode !== 'multi') return;
    const cards = document.getElementById('start-screen-cards');
    if (!cards) return;
    cards.innerHTML = '<p class="instructor-panel-empty">Loading…</p>';
    try {
        const res = await getExamples();
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        const { examples, areas } = await res.json();
        appState.catalogAreas = areas || [];
        assignmentExamples = examples || [];
        renderStartScreenCards(examples || []);
    } catch (err) {
        console.error('Failed to load Assignment:', err);
        cards.innerHTML =
            '<p class="instructor-panel-empty">Failed to load your Assignment. Please refresh.</p>';
    }
}

function renderStartScreenCards(examples) {
    const cards = document.getElementById('start-screen-cards');
    if (!cards) return;
    if (!examples.length) {
        cards.innerHTML =
            '<p class="instructor-panel-empty">No Lab Example is assigned to your Group yet.</p>';
        return;
    }
    cards.innerHTML = groupByArea(examples, appState.catalogAreas || []).map(
        ({ area, examples: grouped }, index) => `<section role="group" aria-labelledby="start-area-${index}">
            <h3 id="start-area-${index}" class="catalog-area-heading">${escapeHtml(area)}</h3>
            <div class="start-screen-area-cards">${grouped.map(renderAssignmentCard).join('')}</div>
        </section>`
    ).join('');
    // The list is rendered once per no-Lab state, not on a poll, so a direct
    // listener on each button is safe (no re-render to lose it to).
    cards.querySelectorAll('.start-screen-start').forEach(btn => {
        btn.addEventListener('click', () => startLab(btn.dataset.example));
    });
    cards.querySelectorAll('.start-screen-start-clean').forEach(btn => {
        btn.addEventListener('click', () => confirmStartClean(btn.dataset.example));
    });
}

function renderAssignmentCard(example) {
    const id = escapeHtml(example.id);
    const name = escapeHtml(example.name || example.id);
    const protocols = (example.protocols || []).map(escapeHtml).join(', ');
    const nodes = example.node_count;
    const desc = example.description
        ? `<p class="catalog-card-desc">${escapeHtml(example.description)}</p>`
        : '';
    return `
        <article class="catalog-card start-screen-card" aria-label="Assigned Lab Example ${name}">
            <div class="catalog-card-header">
                <h4 class="catalog-card-name">${name}</h4>
                <span class="catalog-card-meta">${protocols ? protocols + ' · ' : ''}${nodes} node${nodes === 1 ? '' : 's'}</span>
            </div>
            ${desc}
            <div class="catalog-card-actions">
                ${example.incompatible ? '' : `<button type="button" class="start-screen-start" data-example="${id}"
                    aria-label="Start ${name}">Start</button>`}
                ${renderPersistentConfigNote(example, name)}
            </div>
        </article>`;
}

// The persistent-config note: what the next start gives. A copy created by
// opaque code still needs its recovery action, regardless of the Catalog flag.
function renderPersistentConfigNote(example, name) {
    if (!example.persistent_config && !example.resumes && !example.incompatible) return '';
    if (!example.resumes) {
        return '<p class="sidebar-lab-config">Next start begins from the seed.</p>';
    }
    if (example.incompatible) {
        return `
        <p class="sidebar-lab-config">Your Group's saved Router Configs are structurally incompatible with this revision. Start clean to use the revised seed, or ask an Instructor for Config Reset. Your saved work remains until you choose one.</p>
        <button class="sidebar-lab-start-clean start-screen-start-clean" type="button"
            data-example="${escapeHtml(example.id)}"
            aria-label="Start ${name} clean, from the seed">Start clean</button>`;
    }
    return `
        <p class="sidebar-lab-config">Next start resumes your Group's saved Router Configs.</p>
        <button class="sidebar-lab-start-clean start-screen-start-clean" type="button"
            data-example="${escapeHtml(example.id)}"
            aria-label="Start ${name} clean, from the seed">Start clean</button>`;
}

function confirmStartClean(exampleId) {
    const example = assignmentExamples.find(e => e.id === exampleId);
    const name = example ? example.name : exampleId;
    showConfirmModal({
        title: 'Start clean',
        message: `Discard your Group's saved Router Configs for ${name} and start from the seed?\n\nWhat write memory saved cannot be recovered.`,
        confirmText: 'Start clean',
        cancelText: 'Cancel',
        onConfirm: () => startLab(exampleId, true)
    });
}
