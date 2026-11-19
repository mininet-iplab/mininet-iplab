// The Labs explorer: every way to open, copy, delete, upload or start a Lab
// Example Draft, beside the canvas. It folds to a rail while a draft is open.
import { escapeHtml } from '../utils/dom.js';
import { confirmAction } from './confirm-dialog.js';
import { initInfoTips } from './info-tip.js';
import { renameInPlace } from './rename-in-place.js';

function draftName(item) {
    return item.catalog.name || 'Untitled lab';
}

// A section's status line: said politely, or at once when it is a failure.
function statusSetter(target) {
    return (message, isError = false) => {
        target.textContent = message;
        target.setAttribute('role', isError ? 'alert' : 'status');
    };
}

// What a Lab Example's row says under its name: protocols, size and origin.
function exampleMeta(example) {
    const nodes = example.node_count ? `${example.node_count} Node${example.node_count === 1 ? '' : 's'}` : '';
    const origin = example.authored ? 'Authored' : 'Shipped';
    const source = example.kind === 'python' ? 'Python' : '';
    return [...(example.protocols || []), nodes, origin, source].filter(Boolean).join(' · ');
}

/**
 * Bind the Labs explorer to the page's draft actions.
 *
 * @param {object} actions What the page does with a draft.
 * @param {() => Promise<object[]>} actions.listDrafts The shared drafts.
 * @param {() => (string|undefined)} actions.currentId The open draft's id.
 * @param {() => Promise<boolean>} actions.settle Save the open draft's pending edits;
 *     whether it is now saved.
 * @param {(id: string) => Promise<void>} actions.open Open a draft.
 * @param {(message: string) => void} actions.announce Say an outcome in the page's status line.
 * @param {() => Promise<void>} actions.create Create an empty draft.
 * @param {(item: object) => Promise<void>} actions.copy Copy a draft as a new one.
 * @param {(item: object, name: string) => Promise<void>} actions.rename Give a draft a new
 *     Catalog name.
 * @param {(item: object) => Promise<void>} actions.remove Delete a draft.
 * @param {() => Promise<object[]>} actions.listExamples The published Lab Examples.
 * @param {(example: object, revise: boolean, source: string|null) => Promise<void>} actions.startFromExample
 *     Copy a Lab Example as a new one, or Revise one published from Lab Authoring.
 * @param {(example: object) => Promise<string[]>} actions.configCopies The Groups whose
 *     Config Copies removing an authored Lab Example would discard.
 * @param {(example: object) => Promise<string[]>} actions.removeExample Remove an authored
 *     Lab Example from the Catalog; the Groups whose Config Copies were discarded.
 * @param {(file: File) => Promise<string|null>} actions.upload Make and open a new Python
 *     draft from an uploaded script; the reason it was refused, or null.
 * @param {(file: File, source?: string) => Promise<object>} actions.importPackage
 *     Validate and import a complete lesson, with an explicit Python choice.
 * @param {(draft: object, file: File) => Promise<void>} actions.openImportedPackage
 *     Open a successfully imported package after any choice dialog closes.
 * @returns {{refresh: () => Promise<void>, setFolded: (folded: boolean) => void,
 *     focusDraft: (id: string) => void, showName: (id: string, name: string) => void}}
 *     Reload both lists; fold the explorer to its rail or open it; focus a draft's
 *     row, or New draft when it is gone; show a draft's new name without reloading.
 */
export function initLabsExplorer(actions) {
    const explorer = document.getElementById('labs-explorer');
    const toggle = document.getElementById('labs-explorer-toggle');
    const list = document.getElementById('drafts-list');
    const catalog = document.getElementById('example-source-list');
    const search = document.getElementById('example-source-search');
    const newDraft = document.getElementById('new-draft');
    let drafts = [];
    let examples = [];
    let expandedId = null;
    let openMenu = null;
    let loads = 0;
    let shown = { drafts: null, examples: null };
    // The open draft's name as typed on the page, which a save has yet to reach.
    let liveName = null;

    const setStatus = statusSetter(document.getElementById('drafts-status'));
    const setExampleStatus = statusSetter(document.getElementById('example-source-status'));

    // Edits save themselves; only edits that cannot be saved yet ask before they are left.
    const mayDiscard = async message => await actions.settle() || window.confirm(message);

    function setFolded(folded) {
        closeMenu();
        explorer.classList.toggle('folded', folded);
        toggle.setAttribute('aria-expanded', String(!folded));
        document.getElementById('labs-explorer-body').hidden = folded;
    }

    // Run what opens a draft; a draft it opened folds the explorer to its rail.
    async function opening(run) {
        const before = actions.currentId();
        await run();
        const now = actions.currentId();
        if (now && now !== before) setFolded(true);
    }

    // ⋯ menus: one open at a time, floating over the list under its button.
    function showMenu(more) {
        closeMenu();
        document.getElementById(more.getAttribute('aria-controls')).hidden = false;
        more.setAttribute('aria-expanded', 'true');
        openMenu = more;
        placeMenu();
    }

    function placeMenu() {
        if (!openMenu) return;
        const menu = document.getElementById(openMenu.getAttribute('aria-controls'));
        const box = openMenu.getBoundingClientRect();
        menu.style.top = `${box.bottom + 4}px`;
        menu.style.left = `${Math.max(8, box.right - menu.offsetWidth)}px`;
    }

    function closeMenu(focusButton = false) {
        if (!openMenu) return;
        const more = openMenu;
        openMenu = null;
        more.setAttribute('aria-expanded', 'false');
        document.getElementById(more.getAttribute('aria-controls')).hidden = true;
        if (focusButton) more.focus();
    }

    // Re-rendering a list keeps focus on the same control of the same row.
    function rerender(target, html) {
        const focused = target.contains(document.activeElement) ? document.activeElement : null;
        const row = focused?.closest('li')?.dataset;
        const control = focused?.dataset.action ?? (focused?.hasAttribute('aria-expanded') ? 'toggle' : null);
        if (target === list) closeMenu();
        target.innerHTML = html;
        if (!row) return;
        const key = row.draftId ? `[data-draft-id="${CSS.escape(row.draftId)}"]`
            : `[data-example-id="${CSS.escape(row.exampleId)}"]`;
        const again = target.querySelector(`li${key}`);
        const match = control === 'toggle' ? again?.querySelector('[aria-expanded]')
            : again?.querySelector(`[data-action="${control}"]`) ?? again?.querySelector('button');
        match?.focus();
    }

    function renderDrafts() {
        const currentId = actions.currentId();
        rerender(list, drafts.map((item, index) => {
            const id = `drafts-item-${index}`;
            const name = escapeHtml(draftName(item));
            const python = item.kind === 'python';
            const current = item.id === currentId;
            return `<li data-draft-id="${escapeHtml(item.id)}"${current ? ' class="current"' : ''}>
                <button type="button" class="authoring-explorer-open" data-action="open"
                    aria-label="Open ${name}${python ? ', Python' : ''}"${current ? ' aria-current="true"' : ''}>
                    <span id="${id}-name" class="authoring-explorer-name">${name}</span>
                    ${python ? '<span class="authoring-explorer-meta">Python</span>' : ''}
                </button>
                <button type="button" class="btn-icon authoring-explorer-more" aria-expanded="false"
                    aria-controls="${id}-menu" aria-label="More actions for ${name}">⋯</button>
                <div id="${id}-menu" class="authoring-explorer-menu" hidden>
                    <button type="button" id="${id}-rename" data-action="rename" class="btn-secondary"
                        aria-labelledby="${id}-rename ${id}-name">Rename</button>
                    <button type="button" id="${id}-copy" data-action="copy" class="btn-secondary"
                        aria-labelledby="${id}-copy ${id}-name">Copy as new</button>
                    <button type="button" id="${id}-delete" data-action="delete" class="btn-danger"
                        aria-labelledby="${id}-delete ${id}-name">Delete</button>
                </div>
            </li>`;
        }).join(''));
        setStatus(drafts.length ? '' : 'No Lab drafts yet.');
    }

    function renderExamples() {
        const query = search.value.trim().toLowerCase();
        const matches = examples.filter(example =>
            `${example.name} ${example.id} ${(example.protocols || []).join(' ')}`
                .toLowerCase().includes(query));
        rerender(catalog, matches.map((example, index) => {
            const name = escapeHtml(example.name || example.id);
            const id = escapeHtml(example.id);
            const row = `example-source-${index}`;
            const expanded = example.id === expandedId;
            return `<li data-example-id="${id}">
                <button type="button" class="authoring-explorer-example" aria-expanded="${expanded}"
                    aria-controls="${row}-details" aria-labelledby="${row}-name" aria-describedby="${row}-meta">
                    <span id="${row}-name" class="authoring-explorer-name">${name}</span>
                    <span id="${row}-meta" class="authoring-explorer-meta">${escapeHtml(exampleMeta(example))}</span>
                </button>
                <div id="${row}-details" class="authoring-explorer-details"${expanded ? '' : ' hidden'}>
                    <p>${escapeHtml(example.description || 'No description.')}</p>
                    ${!example.authored ? `<p id="${row}-canvas-note">The prepared Canvas definition can differ from the shipped recipe.</p>` : ''}
                    <div class="authoring-row">
                        <button type="button" data-example-id="${id}" data-source="${example.authored ? '' : 'recipe'}" aria-label="Copy ${name}">Copy</button>
                        ${!example.authored ? `<button type="button" class="btn-secondary" data-example-id="${id}"
                            data-source="canvas" aria-label="Start from Canvas version of ${name}"
                            aria-describedby="${row}-canvas-note">Start from Canvas version</button>` : ''}
                        ${example.authored ? `<button type="button" class="btn-secondary" data-example-id="${id}"
                            data-revise="true" aria-label="Revise ${name}">Revise</button>
                        <button type="button" class="btn-danger" data-example-id="${id}" data-remove="true"
                            aria-label="Remove ${name} from the Catalog">Remove from Catalog</button>` : ''}
                    </div>
                </div>
            </li>`;
        }).join(''));
        setExampleStatus(matches.length
            ? `${matches.length} lab${matches.length === 1 ? '' : 's'} shown.`
            : 'No matching labs.');
    }

    // Reload both lists; a slower, older load never overwrites a newer one, and
    // a list that has not changed is left as it is, under the Instructor's focus.
    async function refresh() {
        const load = ++loads;
        const [draftList, exampleList] = await Promise.allSettled(
            [actions.listDrafts(), actions.listExamples()]);
        if (load !== loads) return;
        if (draftList.status === 'fulfilled') {
            const open = draftList.value.find(item => item.id === actions.currentId());
            if (open && liveName?.id === open.id) open.catalog.name = liveName.name;
            const seen = JSON.stringify([draftList.value, actions.currentId()]);
            if (seen !== shown.drafts) {
                drafts = draftList.value;
                renderDrafts();
                shown.drafts = seen;
            }
        } else {
            setStatus(draftList.reason.message, true);
            shown.drafts = null;
        }
        if (exampleList.status === 'fulfilled') {
            const seen = JSON.stringify(exampleList.value);
            if (seen !== shown.examples) {
                examples = exampleList.value;
                renderExamples();
                shown.examples = seen;
            }
        } else {
            setExampleStatus(exampleList.reason.message, true);
            shown.examples = null;
        }
    }

    function showName(id, name) {
        liveName = { id, name };
        const item = drafts.find(entry => entry.id === id);
        if (!item || item.catalog.name === name) return;
        item.catalog.name = name;
        renderDrafts();
    }

    function focusDraft(id) {
        const row = [...list.children].find(item => item.dataset.draftId === id);
        (row?.querySelector('[data-action="open"]') || newDraft).focus();
    }

    // Remove from Catalog: one confirmation naming the Config Copies it discards.
    async function removeExample(example) {
        const name = example.name || example.id;
        let groups = [];
        let refusal = '';
        try {
            groups = await actions.configCopies(example);
        } catch (error) {
            refusal = error.message;
        }
        let discarded = [];
        const removed = await confirmAction({
            title: 'Remove from Catalog',
            message: `Remove ${name} from the Lab Catalog for every Group? ${groups.length
                ? '' : 'No Group holds a Config Copy of it. '}Drafts revising it publish as a new `
                + 'lab. This cannot be undone.',
            itemsLabel: "These Groups' Config Copies are discarded:",
            items: groups,
            confirmLabel: 'Remove from Catalog',
            refusal,
            action: async () => { discarded = await actions.removeExample(example); },
        });
        if (!removed) return;
        examples = examples.filter(entry => entry.id !== example.id);
        renderExamples();
        search.focus();
        actions.announce(`Removed ${name} from the Lab Catalog.${discarded.length
            ? ` Discarded the Config Copies of ${discarded.join(', ')}.` : ''}`);
    }

    async function deleteDraft(item, index) {
        const name = item.catalog.name || 'this untitled draft';
        const deleted = await confirmAction({
            title: 'Delete draft',
            message: `Delete ${draftName(item)} for all Instructors? Anything already published `
                + 'stays in the Lab Catalog. This cannot be undone.',
            confirmLabel: 'Delete',
            action: async () => {
                try {
                    await actions.remove(item);
                } catch (error) {
                    // A stale revision means someone else changed the draft: show the list as it is now.
                    if (error.status !== 409) throw error;
                    await refresh();
                    throw new Error(`${error.message} The list now shows the latest drafts.`);
                }
            },
        });
        if (!deleted) return;
        await refresh();
        setStatus(`Deleted ${name}.`);
        const next = list.querySelectorAll('button[data-action="open"]')[Math.min(index, drafts.length - 1)];
        (next || newDraft).focus();
    }

    toggle.addEventListener('click', () => {
        const folded = !explorer.classList.contains('folded');
        setFolded(folded);
        // Other Instructors' drafts and Lab Examples may have changed meanwhile.
        if (!folded) refresh();
    });

    list.addEventListener('click', async event => {
        const button = event.target.closest('button');
        if (!button) return;
        if (button.matches('.authoring-explorer-more')) {
            if (openMenu === button) closeMenu();
            else showMenu(button);
            return;
        }
        const row = button.closest('li');
        const index = [...list.children].indexOf(row);
        const item = drafts[index];
        if (!item || !button.dataset.action) return;
        // A menu choice closes its menu; focus waits on its ⋯ while the choice runs.
        if (openMenu?.closest('li') === row) closeMenu(true);
        if (button.dataset.action === 'delete') {
            await deleteDraft(item, index);
        } else if (button.dataset.action === 'rename') {
            renameInPlace(row.querySelector('[data-action="open"]'), {
                name: draftName(item),
                rename: async name => {
                    try {
                        await actions.rename(item, name);
                    } catch (error) {
                        setStatus(error.message, true);
                    }
                    await refresh();
                },
            });
        } else if (button.dataset.action === 'open') {
            if (item.id !== actions.currentId()) {
                if (!await mayDiscard('Discard edits that cannot be saved and open another draft?')) return;
                await actions.open(item.id);
            }
            if (actions.currentId() !== item.id) return;
            setFolded(true);
            document.getElementById('canvas-region').focus();
        } else if (item.id === actions.currentId()
            || await mayDiscard('Discard edits that cannot be saved and open a copy of another draft?')) {
            await opening(() => actions.copy(item));
        }
    });

    newDraft.addEventListener('click', async () => {
        if (!await mayDiscard('Discard edits that cannot be saved and create another draft?')) return;
        await opening(actions.create);
    });

    // Upload a script…: the button opens the file chooser; a refusal keeps focus on it.
    const upload = document.getElementById('upload-script');
    const uploadFile = document.getElementById('upload-script-file');
    upload.addEventListener('click', () => uploadFile.click());
    uploadFile.addEventListener('change', async () => {
        const [file] = uploadFile.files;
        uploadFile.value = '';
        if (!file || !await mayDiscard('Discard edits that cannot be saved and upload a script?')) return;
        setStatus(`Uploading ${file.name}…`);
        let failure = null;
        await opening(async () => { failure = await actions.upload(file); });
        if (failure) {
            setStatus(failure, true);
            upload.focus();
        }
    });

    // Hold the selected File through the choice so explicit Python import sends
    // the same archive bytes. Cancel and any refusal keep the current draft open.
    const importButton = document.getElementById('import-package');
    const importFile = document.getElementById('import-package-file');
    importButton.addEventListener('click', () => importFile.click());
    importFile.addEventListener('change', async () => {
        const [file] = importFile.files;
        importFile.value = '';
        if (!file) return;
        importButton.disabled = true;
        let opened = false;
        let created = null;
        try {
            if (!await actions.settle()) {
                throw new Error('Save your current edits before importing a package.');
            }
            setStatus(`Importing ${file.name}…`);
            try {
                created = await actions.importPackage(file);
            } catch (error) {
                if (error.status !== 409 || error.code !== 'python_import_choice_required') throw error;
                const accepted = await confirmAction({
                    title: 'Import changed generated files',
                    message: 'Generated files in this Canvas package were changed outside the canvas. '
                        + 'Import as Python to keep the packaged script and files. The Canvas definition '
                        + 'is preserved in the package; this new draft uses Python. '
                        + 'Cancel keeps your current draft open.',
                    confirmLabel: 'Import as Python',
                    action: async () => { created = await actions.importPackage(file, 'python'); },
                });
                if (!accepted) {
                    setStatus('Package import cancelled. Your current draft stays open.');
                    actions.announce('Package import cancelled. Your current draft stays open.');
                    return;
                }
            }
            await opening(() => actions.openImportedPackage(created, file));
            opened = true;
            await refresh();
        } catch (error) {
            setStatus(error.message, true);
            actions.announce(error.message, true);
        } finally {
            importButton.disabled = false;
            // A successful open already focused its editor; otherwise return to
            // the action that opened the chooser or decision dialog.
            if (!opened) {
                importButton.focus();
            }
        }
    });

    search.addEventListener('input', renderExamples);
    catalog.addEventListener('click', async event => {
        const summary = event.target.closest('button[aria-expanded]');
        if (summary) {
            // One Lab Example shows its description and actions at a time.
            const id = summary.closest('li').dataset.exampleId;
            expandedId = expandedId === id ? null : id;
            renderExamples();
            // The list keeps its scroll position, so a row opened at its bottom
            // would leave its details below the visible edge.
            if (expandedId) {
                catalog.querySelector(`li[data-example-id="${CSS.escape(expandedId)}"] .authoring-explorer-details`)
                    ?.scrollIntoView({ block: 'nearest' });
            }
            return;
        }
        const button = event.target.closest('button[data-example-id]');
        if (!button) return;
        const example = examples.find(entry => entry.id === button.dataset.exampleId);
        if (!example) return;
        if (button.dataset.remove === 'true') {
            await removeExample(example);
            return;
        }
        if (!await mayDiscard('Discard edits that cannot be saved and start from this Lab Example?')) return;
        button.disabled = true;
        await opening(() => actions.startFromExample(example, button.dataset.revise === 'true', button.dataset.source || null));
        button.disabled = false;
    });

    // A ⋯ menu closes on a click elsewhere, on Escape, when focus leaves its row,
    // and when anything scrolls under it, so it never covers what has focus.
    document.addEventListener('click', event => {
        if (openMenu && !openMenu.closest('li').contains(event.target)) closeMenu();
    });
    list.addEventListener('focusout', event => {
        if (openMenu && !openMenu.closest('li').contains(event.relatedTarget)) closeMenu();
    });
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !openMenu) return;
        // Escape is the menu's alone; the canvas never sees it.
        event.preventDefault();
        event.stopPropagation();
        closeMenu(true);
    }, true);
    document.addEventListener('scroll', () => closeMenu(), true);
    // A resized window moves its button; the menu follows it.
    window.addEventListener('resize', placeMenu);

    initInfoTips(explorer);
    return { refresh, setFolded, focusDraft, showName };
}
