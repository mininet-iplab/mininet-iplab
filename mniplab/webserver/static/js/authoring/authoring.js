import { addressFamilyConflicts, showAddressFamily } from './address-family.js';
import { blankGenerator } from './route-generators.js';
import { nodeIcons, getNodeColor } from '../topology/icons.js';
import { initHeader, applyHeaderIdentity } from '../ui/header.js';
import { initShell, openSidebar, sidebarIsOpen, selectTab } from './shell.js';
import { initScriptPane, scriptPaneOpen, setScriptPaneOpen } from './script-pane.js';
import { initGuideDialog } from './guide-dialog.js';
import { confirmAction } from './confirm-dialog.js';
import { initLabsExplorer } from './labs-explorer.js';
import { renameInPlace } from './rename-in-place.js';
import { initPublishDialog } from './publish-dialog.js';
import { initKeepReview, reviewPreviewChanges, refreshPreviewChanges, previewChangeCount, previewFrr, previewChanges } from './keep-review.js';
import { initPalette } from './palette.js';
import { fitCanvasView, initCanvasView } from './canvas-view.js';
import {
    canvasItem, drawCanvas, focusNearestNode, initCanvas, resetCanvasFocus, showSelection,
} from './canvas.js';
import { connectSource, endConnect, initConnectMode, pickConnectTarget, startConnect } from './connect-mode.js';
import { initSelection, renderSelection } from './selection.js';
import { relationshipsOf } from './relationships.js';
import { createAutosave } from './autosave.js';
import { announce as announceOutcome, announceFailure, showDraftState } from './status-line.js';
import {
    detectPreview, initPreview, previewFailure, previewKey, previewStarting, previewStopped, renderPreview,
    runPreview,
} from './preview.js';
import {
    clearFieldError, initNodeDialog, nameProblem, openNodeDialog, showFieldError, validateNode,
} from './node-dialog.js';
import { initLinkDialog, openLinkDialog, validateLink } from './link-dialog.js';
import { initAddressesDialog, openAddressesDialog } from './interfaces.js';
import { createPythonEditor, createScriptEditor } from './python-editor.js';
import { drawnItems } from './drawing.js';
import { createLessonFiles } from './lesson-files.js';
import { renameRouterNatInterfaces } from './router-nat.js';

const api = '/api/authoring/drafts';
const PALETTE_DRAG_TYPE = 'application/x-mniplab-node';
// Each entry is one palette item and the fields a new Node of that type starts with.
const PALETTE = [
    { type: 'host', label: 'Host', namePrefix: 'h',
        fields: () => ({ ipv4_gateway: '', ipv6_gateway: '', startup: [] }) },
    { type: 'container', label: 'Container Host', namePrefix: 'd',
        fields: () => ({ image: 'alpine:3.20', ipv4_gateway: '', ipv6_gateway: '', startup: [] }) },
    { type: 'nat', label: 'Internet Gateway', namePrefix: 'nat',
        fields: () => ({ ip: '', subnet: '' }) },
    { type: 'transit', label: 'Transit Internet Gateway', namePrefix: 'transit',
        fields: () => ({ ip: '', subnet: '', downstream_ids: [] }) },
    { type: 'speaker', label: 'BGP Speaker', namePrefix: 'sp',
        fields: () => ({ local_as: 65001, neighbors: [{peer: '',
            local_address: '', local_as: 65001, peer_as: 65000,
            hold_time: null, routes: [], route_generator: blankGenerator()}], routes: [], startup: [] }) },
    { type: 'router', label: 'Router', namePrefix: 'r',
        fields: () => ({
            protocols: [], frr_config: '', fast_timers: true,
            subinterfaces: [], startup: [],
        }) },
    // A plain bridge: no addresses of its own and no Startup commands.
    { type: 'switch', label: 'Switch', namePrefix: 's', fields: () => ({}) },
];
// The palette's groups: endpoints and Switches, then routing and translation.
const PALETTE_GROUPS = [['host', 'container', 'switch'], ['router', 'nat', 'transit', 'speaker']];
let routerProtocols = [];
// Who is signed in: a Preview belongs to its Instructor.
let username = '';
let sessionExpired = false;
const sessionDialog = document.getElementById('session-dialog');
let draft = null;
const lessonFiles = createLessonFiles({ onChange: () => updateDirtyState() });
const pythonEditor = createPythonEditor(document.getElementById('python-code-editor'), {
    files: document.getElementById('python-files'),
    onChange: () => {
        if (draft) {
            draft.python_error = null;
            showPythonError(null);
            updateDirtyState();
        }
    },
});
const scriptEditor = createScriptEditor(document.getElementById('script-editor'), {
    onChange: () => {
        // The canvas tells whether the Lab has a Code section, not what it says.
        if (hasCodeSection() === document.getElementById('code-section-marker').hidden) renderCanvas();
        // An edit to the band may fix what failed; the editor drops its own mark.
        if (codeSectionError) showCodeSectionError(null);
        updateDirtyState();
    },
    onRefused: (first, last) =>
        announce(`Generated from the canvas: type in the Code section, ${lineRange(first, last)}.`),
    onBand: (first, last) => {
        document.getElementById('code-section-band').textContent = `Code section · `
            + `${lineRange(first, last)} ${first === last ? 'is' : 'are'} yours; the rest is generated from the canvas.`;
    },
});
// The failure marking a Code section line: its source ('preview' or
// 'drawing') and that line; null when none does.
let codeSectionError = null;
// The Script pane opens on the Code section once the draft's script is shown.
let scriptShown = false;
let revealBand = false;
// What only the script knows, drawn read-only, and where unarranged items sit.
let drawn = { nodes: [], links: [] };
let drawnSpots = new Map();
// A draft just opened fits its view again once its first drawing arrives.
let fitDrawing = false;
let selectedNodeId = null;
let selectedLinkId = null;
let pendingNodeId = null;
let pendingNodeWasSaved = false;
let dialogItem = null;
let focusAfterDialog = null;

function randomUuid() {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-`
        + `${hex.slice(16, 20)}-${hex.slice(20)}`;
}

async function request(url, options = {}) {
    if (sessionExpired) {
        throw Object.assign(new Error('Sign in again to save your edits.'), { status: 401 });
    }
    const response = await fetch(url, {
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
        if (response.status === 401) recoverSession();
        const detail = body.detail;
        const error = new Error(
            typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status})`
        );
        error.status = response.status;
        error.code = body.code || detail?.code;
        error.key = detail?.key;
        error.target = body.target || detail?.target;
        throw error;
    }
    return body;
}

// Keep the editor and its unsaved draft in memory while authentication returns.
function recoverSession() {
    if (!username) {
        window.location.href = '/';
        return;
    }
    if (sessionExpired) return;
    sessionExpired = true;
    autosave.pause('session');
    document.getElementById('session-recovery').hidden = false;
    document.getElementById('session-username').value = username;
    sessionDialog.showModal();
    document.getElementById('session-password').focus();
}

// Outcomes are announced politely from the status line; failures as alerts.
function announce(message, isError = false) {
    (isError ? announceFailure : announceOutcome)(message);
}

function isPython() {
    return draft?.kind === 'python';
}

function updateActionState() {
    const previewButton = document.getElementById('preview-draft');
    const hasHosts = isPython() || Boolean(draft?.topology.nodes.length);
    previewButton.disabled = !draft || (!hasHosts && !previewKey());
    renderPreview();
    renderKeepStatus();
    document.getElementById('script-download').disabled = !draft || !hasHosts;
}

// Every edit to the open draft: it saves itself shortly afterwards.
function updateDirtyState() {
    pendingNodeId = null;
    autosave.edit();
    updateActionState();
    renderSelection();
}

// Save any pending edit before an action that needs the saved draft. When the
// draft cannot be saved, the status line already says why; say it again here.
async function draftSaved() {
    const state = await autosave.flush();
    if (state === 'saved') return true;
    if (state === 'refused') announceOutcome(`Can't save yet: ${autosave.refusal.reason}`, { shown: false });
    return false;
}

function validityMessage(control) {
    if (control.validity.valueMissing) return 'Enter a value.';
    if (control.validity.rangeUnderflow || control.validity.rangeOverflow) {
        return `Enter a number from ${control.min} to ${control.max}.`;
    }
    return control.validationMessage || 'Correct this value.';
}

function showItemError(target) {
    if (target.kind === 'host' || target.kind === 'node') {
        const controlId = target.controlId || target.control?.id;
        openNodeDialog(target.id, true);
        const control = target.control || (controlId ? document.getElementById(controlId) : null);
        if (control) showFieldError(control, target.message, false);
        const summary = document.getElementById('node-dialog-error-summary');
        if (summary && !summary.hidden) summary.focus();
    } else if (target.kind === 'link') {
        openLinkDialog(target.id);
        showFieldError(target.control || document.getElementById(target.controlId), target.message);
    } else {
        const control = target.control || (target.controlId ? document.getElementById(target.controlId) : null);
        if (control) {
            if (control.closest('#authoring-sidebar')) {
                openSidebar();
                selectTab('tab-lesson');
            }
            showFieldError(control, target.message);
        }
    }
}

function draftValidationError() {
    if (isPython()) return null;
    const seenNames = new Map();
    for (const node of draft.topology.nodes) {
        const err = validateNode(node, draft, seenNames);
        if (err) return err;
    }
    const occupiedPorts = new Map();
    for (const link of draft.topology.links) {
        const err = validateLink(link, draft, occupiedPorts);
        if (err) return err;
    }
    return null;
}

// What stops the draft saving, found without moving focus: autosave runs while
// the Instructor works elsewhere.
function draftRefusal() {
    const form = document.getElementById('draft-form');
    for (const control of form.querySelectorAll('input:not([type="hidden"]), textarea, select')) {
        if (!control.checkValidity()) return { control, message: validityMessage(control) };
    }
    const protocols = document.getElementById('catalog-protocols');
    if (protocols.value.split(',').some(value => value.trim().length > 80)) {
        return { control: protocols, message: 'Keep each protocol or service name to 80 characters or fewer.' };
    }
    return draftValidationError();
}

// The status line's reason names what is wrong and where.
function refusalOf(target) {
    const isNode = target.kind === 'host' || target.kind === 'node';
    const item = isNode ? nodeById(target.id) : target.kind === 'link' ? linkById(target.id) : null;
    const control = target.control || (target.controlId && document.getElementById(target.controlId));
    const where = isNode && item ? `${typeLabel(item)} ${nodeLabel(item)}`
        : target.kind === 'link' && item ? linkTitle(item)
        : control?.labels?.[0]?.textContent.replace('(required)', '').trim();
    return { reason: where ? `${where}: ${target.message}` : target.message, fix: target };
}

async function loadDraftList() {
    const { drafts } = await request(api);
    return drafts;
}

function closeDraft() {
    draft = null;
    autosave.reset();
    pendingNodeId = null;
    document.getElementById('draft-form').hidden = true;
    document.getElementById('authoring-empty').hidden = false;
    document.getElementById('draft-title').textContent = '';
    explorer.setFolded(false);
    updateActionState();
}

// Close draft is remembered in this browser, so a reload keeps no draft open.
const CLOSED_KEY = 'mniplab.authoring.draftClosed';

function rememberClosed(closed) {
    try {
        if (closed) localStorage.setItem(CLOSED_KEY, '1');
        else localStorage.removeItem(CLOSED_KEY);
    } catch (error) {
        // Without storage, a reload opens the most recent draft as before.
    }
}

function wasClosed() {
    try {
        return localStorage.getItem(CLOSED_KEY) === '1';
    } catch (error) {
        return false;
    }
}

// What a draft is called until it has a Catalog name.
const UNTITLED = 'Untitled lab';
const NO_DRAFT_OPEN = 'Open a Lab draft from Drafts, or create or upload a new one.';
const BEGIN = 'Create a Lab draft, upload a script, or copy a lab from the Lab Catalog to begin.';

// Close draft: pending edits are saved first, as when another draft opens;
// then no draft is open until one is chosen in the Labs explorer.
async function closeOpenDraft() {
    if (!draft) return;
    if (await autosave.flush() !== 'saved' && !await confirmAction({
        title: 'Close draft',
        message: `Can't save yet: ${autosave.refusal?.reason ?? 'the draft changed elsewhere'}. `
            + 'Discard those edits and close this draft?',
        confirmLabel: 'Discard and close',
        action: async () => {},
    })) return;
    const { id } = draft;
    const name = draft.catalog.name || UNTITLED;
    closeDraft();
    rememberClosed(true);
    announce(`Closed ${name}. ${NO_DRAFT_OPEN}`);
    await explorer.refresh();
    explorer.focusDraft(id);
}

function openDraft(documentValue) {
    rememberClosed(false);
    if (draft?.id !== documentValue.id) {
        detectPreview(documentValue.id, () => draft?.id === documentValue.id);
    }
    draft = documentValue;
    drawn = { nodes: [], links: [] };
    drawnSpots = new Map();
    fitDrawing = true;
    showDrawingNote(null);
    clearCodeSectionError();
    scriptShown = false;
    revealBand = true;
    autosave.reset();
    selectedNodeId = null;
    selectedLinkId = null;
    pendingNodeId = null;
    endConnect();
    resetCanvasFocus();
    renderSelection();
    const form = document.getElementById('draft-form');
    form.hidden = false;
    document.getElementById('authoring-empty').hidden = true;
    document.getElementById('authoring-status').textContent = '';
    document.getElementById('draft-title').textContent = documentValue.catalog.name || UNTITLED;
    document.getElementById('draft-id').value = documentValue.id;
    document.getElementById('draft-revision').value = documentValue.revision;
    document.getElementById('catalog-name').value = documentValue.catalog.name;
    document.getElementById('catalog-description').value = documentValue.catalog.description;
    document.getElementById('catalog-protocols').value = documentValue.catalog.protocols.join(', ');
    document.getElementById('catalog-area').value = documentValue.catalog.area || '';
    document.getElementById('draft-guide').value = documentValue.guide;
    lessonFiles.open(draft);
    // A Python draft is its script and Seed files; it has no canvas.
    const python = documentValue.kind === 'python';
    document.getElementById('lab-settings').hidden = python;
    if (!python) {
        document.getElementById('lab-address-family').value = draft.address_family;
        document.getElementById('lab-persistent-config').checked = draft.persistent_config;
    }
    document.getElementById('node-palette').hidden = python;
    document.getElementById('canvas-help').textContent = python ? PYTHON_CANVAS_HELP : CANVAS_HELP;
    document.getElementById('python-editor').hidden = !python;
    document.getElementById('script-continue-python').hidden = python;
    document.getElementById('replace-script').hidden = !python;
    document.getElementById('replace-script-help').hidden = !python;
    document.getElementById('continue-python-help').hidden = python;
    for (const id of ['script-editor', 'code-section-band', 'code-section-help']) {
        document.getElementById(id).hidden = python;
    }
    for (const id of ['python-editor-help', 'python-files-help']) document.getElementById(id).hidden = !python;
    document.getElementById('python-show-api-help').hidden = false;
    document.getElementById('canvas-heading').textContent = python ? 'Python Lab Example' : 'Topology canvas';
    document.querySelector('.skip-link').textContent = python ? 'Skip to editor' : 'Skip to canvas';
    if (python) {
        renderPythonEditor();
        setScriptPaneOpen(true);
    } else {
        scriptEditor.open(draft.code_section || '');
    }
    renderCanvas();
    fitCanvasView();
    updateActionState();
    announce(`Opened ${documentValue.catalog.name || UNTITLED}.`);
    refreshPython();
    refreshDrawing();
    explorer.refresh();
}

const CANVAS_HELP = document.getElementById('canvas-help').textContent;
const PYTHON_CANVAS_HELP = 'Drawn from the script after each save · read-only · Shift+Arrows move a Node';

// A Canvas draft's own Nodes and Links, then what only its script knows.
function allNodes() {
    return [...(isPython() ? [] : draft.topology.nodes), ...drawn.nodes];
}

function allLinks() {
    return [...(isPython() ? [] : draft.topology.links), ...drawn.links];
}

function showDrawingNote(note) {
    const element = document.getElementById('drawing-note');
    if (element.textContent !== (note || '')) element.textContent = note || '';
    element.hidden = !note;
}

function lineRange(first, last) {
    return first === last ? `line ${first}` : `lines ${first}–${last}`;
}

// The open draft's script editor.
function activeEditor() {
    return isPython() ? pythonEditor : scriptEditor;
}

// Put the script's cursor on a line; a script still loading keeps it there.
function showScriptLine(line, options) {
    revealBand = false;
    activeEditor().showLine(line, options);
}

// Show a Code section failure's message under the script, or none (null).
function showCodeSectionError(error) {
    codeSectionError = error;
    const target = document.getElementById('code-section-error');
    target.textContent = error?.message ?? '';
    target.hidden = !error;
}

function clearCodeSectionError() {
    showCodeSectionError(null);
    scriptEditor.setError(null);
}

// A failure naming a Code section line marks it, and puts the cursor there
// when the pane is open and not being typed in; returns that line, or null.
// A source's failure without one clears its own mark.
function markCodeSectionError(message, source) {
    const line = isPython() ? null : Number(/\bCode section line (\d+)/.exec(message || '')?.[1]) || null;
    if (!line) {
        if (codeSectionError?.source === source) clearCodeSectionError();
        return null;
    }
    showCodeSectionError({ source, line, message });
    scriptEditor.setError(line);
    if (scriptPaneOpen() && !scriptEditor.hasFocus()) showScriptLine(line);
    return line;
}

// Draw what the saved script builds: a Python draft's Topology, or what a
// Canvas draft's Code section adds. Only a saved script can be recorded.
async function refreshDrawing() {
    const drawing = draft;
    const revision = drawing.revision;
    let result = { nodes: [], links: [], note: null };
    if (isPython() || hasCodeSection()) {
        try {
            result = await request(`${api}/${encodeURIComponent(drawing.id)}/drawing`);
        } catch (error) {
            result.note = error.message;
        }
    }
    if (draft !== drawing || draft.revision !== revision) return;
    drawn = drawnItems(result, isPython() ? null : draft.topology.nodes);
    // An item the Layout does not place yet gets a free spot, kept until it moves.
    const taken = allNodes().filter(node => draft.layout.nodes[node.id]).map(node => positionOf(node.id));
    for (const node of drawn.nodes) {
        if (draft.layout.nodes[node.id] || drawnSpots.has(node.id)) continue;
        drawnSpots.set(node.id, freeSpot([...taken, ...drawnSpots.values()]));
        // A Node the script just added is brought into view.
        fitDrawing = true;
    }
    showDrawingNote(result.note);
    markCodeSectionError(result.note, 'drawing');
    renderCanvas();
    if (fitDrawing && drawn.nodes.length) fitCanvasView({ smooth: true });
    fitDrawing = false;
}

function nodeById(id) {
    return allNodes().find(node => node.id === id);
}

function linkById(id) {
    return allLinks().find(link => link.id === id);
}

function positionOf(nodeId) {
    const position = draft.layout.nodes[nodeId] || drawnSpots.get(nodeId) || {};
    return { x: Number(position.x) || 0, y: Number(position.y) || 0 };
}

function nodeLabel(node) {
    return node?.name || `unnamed ${typeLabel(node || {})}`;
}

function typeLabel(node) {
    return PALETTE.find(item => item.type === node.type)?.label || 'Node';
}

function interfaceName(end) {
    return `${nodeLabel(nodeById(end.node_id))}-eth${end.port}`;
}

function linkTitle(link) {
    const [first, second] = link.ends.map(end => nodeLabel(nodeById(end.node_id)));
    return `Link between ${first} and ${second}`;
}

function selectNode(nodeId) {
    if (nodeId !== pendingNodeId) pendingNodeId = null;
    selectedNodeId = nodeId;
    selectedLinkId = null;
    showSelection();
    renderSelection();
    refreshPreviewChanges();
    followSelection('host', nodeId);
}

function selectLink(linkId) {
    pendingNodeId = null;
    selectedLinkId = linkId;
    selectedNodeId = null;
    showSelection();
    renderSelection();
    followSelection('link', linkId);
}

function renderCanvas() {
    drawCanvas();
    const description = document.getElementById('draft-canvas-description');
    const summary = (nodes, links) => {
        const counts = [...PALETTE, { label: 'Node' }]
            .map(item => [item, nodes.filter(node => typeLabel(node) === item.label).length])
            .filter(([, total]) => total)
            .map(([item, total]) => count(total, item.label));
        return counts.length ? `${counts.join(', ')} and ${count(links.length, 'Link')}` : '';
    };
    const own = isPython() ? '' : summary(draft.topology.nodes, draft.topology.links);
    const added = summary(drawn.nodes, drawn.links);
    description.textContent = isPython()
        ? (added ? `Drawn from the script, read-only: ${added}.` : 'Nothing drawn from the script yet.')
        : (own ? `${own}.` : 'An empty Topology.') + (!hasCodeSection() ? ''
            : added ? ` The Lab also has a Code section, which adds ${added}, read-only.`
                : ' The Lab also has a Code section.');
    renderCodeSectionMarker();
    updateActionState();
}

function hasCodeSection() {
    return !isPython() && Boolean(scriptEditor.code().trim());
}

// The canvas shows that the Lab is more than its drawing.
function renderCodeSectionMarker() {
    document.getElementById('code-section-marker').hidden = !hasCodeSection();
}

function count(total, noun) {
    return `${total} ${noun}${total === 1 ? '' : 's'}`;
}

// A Node of a kind the canvas does not know is a plain grey circle.
function nodeColor(type) {
    return type === 'other' ? 'var(--text-muted)' : getNodeColor(type);
}

function iconPath(type) {
    if (type === 'other') return '';
    return nodeIcons[type] || (type === 'speaker' ? nodeIcons['bgp-speaker'] : nodeIcons.router);
}

// Auto-Arrange settles the running-Lab view's physics around the current Nodes.
function arrangedPositions() {
    const nodes = allNodes().map(node => ({ id: node.id, ...positionOf(node.id) }));
    const ids = new Set(nodes.map(node => node.id));
    const links = allLinks().filter(link => link.ends.every(end => ids.has(end.node_id)))
        .map(link => ({ source: link.ends[0].node_id, target: link.ends[1].node_id }));
    d3.forceSimulation(nodes)
        .force('link', d3.forceLink(links).id(node => node.id).distance(200))
        .force('charge', d3.forceManyBody().strength(-400))
        .force('center', d3.forceCenter(d3.mean(nodes, node => node.x), d3.mean(nodes, node => node.y)))
        .force('collision', d3.forceCollide().radius(60))
        .stop()
        .tick(300);
    return nodes.map(node => [node.id, {
        x: Math.round(node.x),
        y: Math.round(node.y),
    }]);
}

async function autoArrangeNodes() {
    if (!draft || !allNodes().length) return;
    await confirmAction({
        title: 'Auto-Arrange Nodes',
        message: "Re-arrange every Node automatically? The draft's current Layout is replaced and cannot be recovered.",
        confirmLabel: 'Re-arrange',
        action: async () => {
            const positions = arrangedPositions();
            for (const [nodeId, position] of positions) draft.layout.nodes[nodeId] = position;
            updateDirtyState();
            renderCanvas();
            fitCanvasView({ smooth: true });
            announce(`Re-arranged ${positions.length} Nodes.`);
        },
    });
}

// Search successive rows for a free spot, without a fixed canvas boundary.
function freeSpot(taken = draft.topology.nodes.map(node => positionOf(node.id))) {
    const clearance = point => Math.min(Infinity, ...taken.map(other =>
        Math.hypot(other.x - point.x, other.y - point.y)));
    for (let index = 0; ; index += 1) {
        const point = { x: 150 + (index % 6) * 350, y: 150 + Math.floor(index / 6) * 350 };
        if (clearance(point) >= 150) return point;
    }
}

function nextNodeName(prefix) {
    const existing = new Set(draft.topology.nodes.map(node => node.name));
    let index = 1;
    while (existing.has(`${prefix}${index}`)) index += 1;
    return `${prefix}${index}`;
}

// Open vSwitch refuses port 0, so a Switch's Interfaces start at 1.
function firstPort(nodeId) {
    return nodeById(nodeId)?.type === 'switch' ? 1 : 0;
}

function nextPort(nodeId) {
    const ports = draft.topology.links.flatMap(link => link.ends)
        .filter(end => end.node_id === nodeId).map(end => Number(end.port) || 0);
    return ports.length ? Math.max(...ports) + 1 : firstPort(nodeId);
}

function addNode(item, position, { autoFit = true } = {}) {
    if (!draft) return;
    const wasEmpty = !allNodes().length;
    const wasSaved = autosave.state === 'saved';
    const node = { id: randomUuid(), name: nextNodeName(item.namePrefix), type: item.type, ...item.fields() };
    draft.topology.nodes.push(node);
    draft.layout.nodes[node.id] = { x: Math.round(position.x), y: Math.round(position.y) };
    selectedNodeId = node.id;
    updateDirtyState();
    pendingNodeId = node.id;
    pendingNodeWasSaved = wasSaved;
    renderCanvas();
    const element = canvasItem('host', node.id);
    // Palette buttons reveal their additions; pointer drops preserve the chosen view.
    if (autoFit) {
        const placedBox = element.getBoundingClientRect();
        const view = document.getElementById('draft-canvas').getBoundingClientRect();
        if (wasEmpty || placedBox.left < view.left || placedBox.right > view.right
            || placedBox.top < view.top || placedBox.bottom > view.bottom) {
            fitCanvasView();
        }
    }
    element.focus();
    const placed = draft.layout.nodes[node.id];
    announce(`Added ${item.label} ${node.name} at x ${placed.x}, y ${placed.y}. Press Escape to undo.`);
}

function addLink(firstId, secondId) {
    const ends = [firstId, secondId].map(nodeId => ({
        node_id: nodeId,
        interface_id: `if-${randomUuid().replaceAll('-', '').slice(0, 12)}`,
        port: nextPort(nodeId),
        ipv4: '',
        ipv6: '',
    }));
    const link = { id: randomUuid(), ends };
    draft.topology.links.push(link);
    selectLink(link.id);
    updateDirtyState();
    renderCanvas();
    announce(`Added ${linkTitle(link)}.`);
    return link;
}

// Rename a Node where it sits, under the dialog's own naming rules.
function startRename(nodeId) {
    const node = nodeById(nodeId);
    const item = canvasItem('host', nodeId);
    if (!node || !item) return;
    endConnect();
    document.querySelector('.node-rename')?.remove();
    const container = document.getElementById('draft-canvas-drop');
    const frame = container.getBoundingClientRect();
    const box = item.getBoundingClientRect();
    const wrap = document.createElement('div');
    wrap.className = 'node-rename';
    wrap.style.left = `${box.left + box.width / 2 - frame.left}px`;
    wrap.style.top = `${box.top + box.height / 2 - frame.top}px`;
    const field = document.createElement('input');
    field.value = node.name;
    field.maxLength = 8;
    field.autocomplete = 'off';
    field.setAttribute('aria-label', `Rename ${node.name}`);
    field.setAttribute('aria-describedby', 'node-rename-error');
    const error = document.createElement('p');
    error.id = 'node-rename-error';
    error.className = 'field-error';
    error.setAttribute('role', 'alert');
    error.hidden = true;
    wrap.append(field, error);
    container.append(wrap);
    field.select();
    field.focus();
    let finished = false;
    const problemWith = value => {
        const others = draft.topology.nodes.filter(other => other.id !== nodeId);
        return value === node.name ? null
            : nameProblem(typeLabel(node), value, others.some(other =>
                other.name.toLowerCase() === value.toLowerCase()));
    };
    const finish = commit => {
        if (finished) return;
        finished = true;
        wrap.remove();
        if (commit) {
            const previousName = node.name;
            node.name = field.value;
            renameRouterNatInterfaces(node, previousName);
            updateDirtyState();
            renderCanvas();
            announce(`Renamed to ${node.name}.`);
        }
        canvasItem('host', nodeId)?.focus();
    };
    field.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            finish(false);
        } else if (event.key === 'Enter') {
            event.preventDefault();
            const problem = problemWith(field.value);
            if (problem) {
                field.setAttribute('aria-invalid', 'true');
                error.textContent = problem;
                error.hidden = false;
            } else finish(field.value !== node.name);
        } else if (field.hasAttribute('aria-invalid')) {
            field.removeAttribute('aria-invalid');
            error.hidden = true;
        }
    });
    // Clicking away keeps a valid new name and drops one the dialog would refuse.
    field.addEventListener('blur', () => finish(!problemWith(field.value) && field.value !== node.name));
}

function closeDialogs() {
    document.querySelectorAll('.authoring-dialog[open]').forEach(dialog => dialog.close());
}

// Focus returns synchronously when a dialog closes: the queued close event
// can arrive after the user has already moved on and would steal focus back.
function closeDialogToCanvas(dialog) {
    dialog.close();
    const target = focusAfterDialog || (dialogItem && canvasItem(dialogItem.kind, dialogItem.id));
    focusAfterDialog = null;
    dialogItem = null;
    (target || document.getElementById('palette-host'))?.focus();
}

function bindDialogClosing(dialog) {
    dialog.querySelectorAll('[data-close]').forEach(button => {
        button.addEventListener('click', () => closeDialogToCanvas(dialog));
    });
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        closeDialogToCanvas(dialog);
    });
    dialog.querySelector('form').addEventListener('submit', event => {
        event.preventDefault();
        closeDialogToCanvas(dialog);
    });
    // Browsers may close on Escape without a cancel event; still return focus.
    dialog.addEventListener('close', () => {
        if (dialogItem?.kind === dialog.id.replace('-dialog', '')
            && !document.querySelector('.authoring-dialog[open]')) {
            closeDialogToCanvas(dialog);
        }
    });
}

function removeNode(nodeId, cancelled = false) {
    const node = nodeById(nodeId);
    if (!node) return;
    if (connectSource() === nodeId) endConnect();
    const wasSaved = pendingNodeWasSaved;
    const index = draft.topology.nodes.indexOf(node);
    draft.topology.nodes.splice(index, 1);
    if (node.type === 'router') draft.topology.nodes.filter(other => other.type === 'transit')
        .forEach(other => { other.downstream_ids = other.downstream_ids.filter(id => id !== node.id); });
    draft.topology.links = draft.topology.links.filter(link =>
        !link.ends.some(end => end.node_id === node.id));
    delete draft.layout.nodes[node.id];
    selectedNodeId = null;
    updateDirtyState();
    renderCanvas();
    // A placement undone before it saved leaves nothing to save.
    if (cancelled && wasSaved) autosave.revert();
    const next = draft.topology.nodes[Math.min(index, draft.topology.nodes.length - 1)];
    // Undoing a placement returns to the palette item that made it.
    const target = cancelled ? document.getElementById(`palette-${node.type}`)
        : next ? canvasItem('host', next.id) : document.getElementById('palette-host');
    announce(cancelled
        ? `Cancelled ${typeLabel(node)} ${nodeLabel(node)} placement.`
        : `Removed ${typeLabel(node)} ${nodeLabel(node)} and its Links.`);
    if (dialogItem?.kind === 'host' && dialogItem.id === nodeId) {
        focusAfterDialog = target;
        closeDialogToCanvas(document.getElementById('host-dialog'));
    } else {
        target?.focus();
    }
}

function removeLink(linkId) {
    const link = linkById(linkId);
    if (!link) return;
    const title = linkTitle(link);
    const sourceNodeId = link.ends[0].node_id;
    draft.topology.links = draft.topology.links.filter(l => l.id !== linkId);
    if (selectedLinkId === linkId) selectedLinkId = null;
    updateDirtyState();
    renderCanvas();
    focusAfterDialog = canvasItem('host', sourceNodeId);
    announce(`Removed ${title}.`);
    canvasItem('host', sourceNodeId)?.focus();
}

function bindCanvasDrop() {
    const area = document.getElementById('draft-canvas-drop');
    area.addEventListener('dragover', event => {
        if (!event.dataTransfer.types.includes(PALETTE_DRAG_TYPE)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
    });
    area.addEventListener('drop', event => {
        const item = PALETTE.find(entry => entry.type === event.dataTransfer.getData(PALETTE_DRAG_TYPE));
        if (!item || !draft) return;
        event.preventDefault();
        const g = document.getElementById('draft-canvas-g') || document.getElementById('draft-canvas');
        const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(g.getScreenCTM().inverse());
        addNode(item, point, { autoFit: false });
    });
    window.addEventListener('keydown', event => {
        if (event.key === 'Escape' && pendingNodeId === selectedNodeId
            && document.activeElement === canvasItem('host', pendingNodeId)) {
            event.preventDefault();
            removeNode(pendingNodeId, true);
        }
    });
}

function collectForm() {
    lessonFiles.collect();
    if (isPython()) {
        Object.assign(draft, pythonEditor.source());
    } else {
        // A draft that never had a Code section saves exactly as it was.
        const code = scriptEditor.code();
        if (code || 'code_section' in draft) draft.code_section = code;
    }
    draft.catalog.name = document.getElementById('catalog-name').value;
    draft.catalog.area = document.getElementById('catalog-area').value;
    draft.catalog.description = document.getElementById('catalog-description').value;
    draft.catalog.protocols = document.getElementById('catalog-protocols').value
        .split(',').map(value => value.trim()).filter(Boolean);
    draft.guide = document.getElementById('draft-guide').value;
}

// One autosave attempt. The page keeps its own copy of the draft, so edits
// made while the request is in flight stay in place and save next.
async function persistDraft() {
    const refused = draftRefusal();
    if (refused) return { refused: refusalOf(refused) };
    collectForm();
    const saving = draft;
    try {
        const saved = await request(`${api}/${encodeURIComponent(saving.id)}`, {
            method: 'PUT',
            body: JSON.stringify({ revision: saving.revision, document: saving }),
        });
        if (draft !== saving) return { saved: true };
        for (const field of ['revision', 'preview_valid', 'published_revision']) {
            draft[field] = saved[field];
        }
        if (isPython()) {
            // Diagnostics describe the submitted source, never newer local text.
            draft.python_error = pythonEditor.source().python === saved.python
                ? saved.python_error : null;
            showPythonError(draft.python_error);
        }
        document.getElementById('draft-revision').value = saved.revision;
        // The server now holds any Node just placed: undoing it is an edit to save.
        pendingNodeWasSaved = false;
        updateActionState();
        refreshPython();
        refreshDrawing();
        return { saved: true };
    } catch (error) {
        if (error.status === 409) return { conflict: true };
        const target = error.status === 422 && error.target;
        return { refused: target ? refusalOf(target) : { reason: error.message } };
    }
}

// The palette is one Tab stop; its current item is the one to focus.
function focusPalette() {
    document.querySelector('#node-palette [tabindex="0"]')?.focus();
}

async function createDraft(sourceId = null, exampleId = null, revise = false, source = null) {
    const created = await request(api, {
        method: 'POST',
        body: JSON.stringify(exampleId ? { example_id: exampleId, revise, ...(source ? { source } : {}) }
            : sourceId ? { source_id: sourceId } : {}),
    });
    openDraft(created);
    focusPalette();
}

async function stopPreview() {
    try {
        await request(`/api/labs/${encodeURIComponent(previewKey())}/stop`, { method: 'POST' });
    } catch (error) {
        if (error.status !== 404) {
            announce(error.message, true);
            return;
        }
    }
    previewStopped();
    refreshPreviewChanges();
    updateActionState();
    document.getElementById('preview-draft').focus();
    announce('Preview Lab stopped.');
}

// Start a Preview of the saved draft; rebuilding a running one offers Keep first.
async function startPreview() {
    if (!draft || previewStarting()) return;
    if (previewKey()) {
        await reviewPreviewChanges(startPreviewLab, document.getElementById('preview-draft'));
        return;
    }
    await startPreviewLab();
}

async function startPreviewLab() {
    const replacing = previewKey();
    const previewed = draft;
    if (codeSectionError?.source === 'preview') clearCodeSectionError();
    try {
        const result = await runPreview(async () => {
            // Mark Preview busy before waiting for an in-flight save, so a
            // second activation cannot queue another start for that revision.
            // Settle edits, then pause without a wait before Lab startup.
            if (!await draftSaved()) return null;
            const payload = { revision: draft.revision };
            const url = `${api}/${encodeURIComponent(draft.id)}/preview`;
            // The server stops a Preview whose revision moves during startup.
            autosave.pause();
            try {
                return await request(url, { method: 'POST', body: JSON.stringify(payload) });
            } catch (error) {
                // Rebuilding this draft's own Preview was already reviewed.
                const ownPreview = error.key && error.key === replacing;
                if (error.status !== 409 || error.code !== 'preview_replacement_required') throw error;
                if (!ownPreview && !window.confirm(error.message)) return null;
                return request(url, { method: 'POST', body: JSON.stringify({ ...payload, replace_key: error.key }) });
            }
        });
        if (!result) {
            // A Preview that failed in the Code section opens the pane at its line.
            const line = draft === previewed && markCodeSectionError(previewFailure(), 'preview');
            if (line) {
                setScriptPaneOpen(true);
                showScriptLine(line);
            }
            return;
        }
        if (draft === previewed) draft.preview_valid = true;
        await refreshPreviewChanges();
        const warnings = result.warnings?.length
            ? ` ${count(result.warnings.length, 'Startup command')} failed but may fail:\n${result.warnings.join('\n')}`
            : '';
        announce('Preview Lab started. Startup succeeded; routing and connectivity have not been checked.'
            + warnings);
    } finally {
        autosave.resume();
        updateActionState();
    }
}

async function openDraftById(draftId) {
    try {
        openDraft(await request(`${api}/${encodeURIComponent(draftId)}`));
    } catch (error) {
        announce(error.message, true);
    }
}

// Rename a draft: the open one through its Catalog name, which saves itself;
// another by saving its stored document under the new name.
async function renameDraft(item, name) {
    const old = item.catalog.name || UNTITLED;
    if (item.id === draft?.id) {
        const field = document.getElementById('catalog-name');
        field.value = name;
        field.dispatchEvent(new Event('input'));
        // The status line says why a name that cannot be saved yet waits.
        if (!await draftSaved()) return;
    } else {
        const url = `${api}/${encodeURIComponent(item.id)}`;
        const stored = await request(url);
        stored.catalog.name = name;
        await request(url, { method: 'PUT', body: JSON.stringify({ revision: stored.revision, document: stored }) });
    }
    announce(`Renamed ${old} to ${name}.`);
}

// A copy of the open draft includes its latest edits, as Copy as new always did.
async function copyDraft(item) {
    try {
        if (item.id === draft?.id && !await draftSaved()) return;
        await createDraft(item.id);
        announce('Created a separate draft copy.');
    } catch (error) {
        announce(error.message, true);
    }
}

// Deleting the open draft opens the first one left, or leaves none open.
async function removeDraft(item) {
    const open = item.id === draft?.id;
    // A save still in flight would move the revision the delete names.
    if (open) await autosave.flush();
    const revision = open ? draft.revision : item.revision;
    await request(`${api}/${encodeURIComponent(item.id)}?revision=${revision}`, { method: 'DELETE' });
    const drafts = await loadDraftList();
    if (open) {
        closeDraft();
        if (drafts.length) openDraft(drafts[0]);
    }
    announce(`Deleted ${item.catalog.name || 'this untitled draft'}.${drafts.length ? '' : ` ${BEGIN}`}`);
}

async function startFromExample(example, revise, source = null) {
    try {
        await createDraft(null, example.id, revise, source);
        if (example.kind === 'python' || source === 'recipe') pythonEditor.showLine(1, { focus: true });
        if (source === 'canvas') {
            announce(`Started from the Canvas version of ${example.name}. The Canvas definition can differ from the shipped recipe.`);
            return;
        }
        announce(revise
            ? `Created a revision draft for ${example.name}. Choose Publish as new or Update existing after Preview.`
            : `Created a copy of ${example.name}. It publishes as a new Lab Example.`);
    } catch (error) {
        announce(error.message, true);
    }
}

// What stops the saved draft publishing, each with the one step that fixes it.
async function publishReadiness() {
    const state = await autosave.flush();
    const missing = [];
    if (state === 'conflict') {
        missing.push({ need: 'Someone else changed this draft', fixLabel: 'Reopen', fix: reopenDraft });
    } else if (previewStarting()) {
        missing.push({ need: 'Waiting for the Preview to start' });
    } else if (state !== 'saved') {
        const { reason, fix } = autosave.refusal;
        missing.push({ need: `Can't save yet: ${reason}`, fixLabel: 'Show the field',
            fix: fix && (() => showItemError(fix)) });
    }
    if (!isPython() && !draft.topology.nodes.length) {
        missing.push({ need: 'Needs at least one Node', fixLabel: 'Add a Node',
            fix: focusPalette });
    }
    if (isPython() && draft.python_error) {
        missing.push({ need: 'Needs a script that compiles', fixLabel: 'Show the error',
            fix: () => {
                setScriptPaneOpen(true);
                pythonEditor.showLine(1, { focus: true });
            } });
    }
    if (!document.getElementById('catalog-name').value.trim()) {
        missing.push({ need: 'Needs a Catalog name', fixLabel: 'Open Lesson tab', fix: () => {
            openSidebar();
            selectTab('tab-lesson');
            document.getElementById('catalog-name').focus();
        } });
    }
    if (!document.getElementById('catalog-area').value) {
        missing.push({ need: 'Needs a Catalog Area', fixLabel: 'Open Lesson tab', fix: () => {
            openSidebar();
            selectTab('tab-lesson');
            document.getElementById('catalog-area').focus();
        } });
    }
    // Its Lab Example may have left the Catalog since the draft opened.
    try {
        draft.can_update = (await request(`${api}/${encodeURIComponent(draft.id)}`)).can_update;
    } catch (error) {
        draft.can_update = false;
    }
    await refreshPreviewChanges();
    return {
        missing,
        checking: !draft.preview_valid,
        changes: previewChanges(),
        published: draft.published_revision === draft.revision - 1,
        canUpdate: draft.can_update,
        name: draft.catalog.name,
    };
}

async function publishDraft(mode) {
    try {
        if (!await draftSaved()) return "Save the draft before publishing.";
        const publishing = draft;
        const url = `${api}/${encodeURIComponent(draft.id)}/publish`;
        const payload = { revision: draft.revision, mode };
        if (!draft.preview_valid && previewKey()) payload.replace_key = previewKey();
        autosave.pause();
        let result;
        try {
            result = await request(url, { method: 'POST', body: JSON.stringify(payload) });
        } catch (error) {
            if (error.code !== 'preview_replacement_required') throw error;
            const accepted = await confirmAction({
                title: 'Replace Lab for Publish', message: error.message,
                confirmLabel: 'Replace and publish',
                action: async () => {
                    result = await request(url, { method: 'POST',
                        body: JSON.stringify({ ...payload, replace_key: error.key }) });
                },
            });
            if (!accepted) return 'Publication cancelled; your current Lab is still running.';
        } finally {
            autosave.resume();
        }
        if (draft === publishing) {
            openDraft(result.draft);
            await detectPreview(draft.id, () => draft?.id === publishing.id);
        }
        const affected = result.incompatible_groups || [];
        const impact = affected.length
            ? ` Incompatible Config Copies: ${affected.join(', ')}. Their work remains saved. `
                + 'After their Labs stop, use Config Reset in the Instructor dashboard, or ask each Group to Start clean.'
            : '';
        announce(`${mode === 'update_existing' ? 'Updated' : 'Published'} ${result.example.name} in the Lab Catalog.${impact}`);
        return null;
    } catch (error) {
        return error.message;
    }
}

document.getElementById('draft-form').addEventListener('submit', event => event.preventDefault());
// One toggle: Preview starts a Preview, and Stop Preview offers Keep before stopping.
document.getElementById('preview-draft').addEventListener('click', event => {
    if (previewStarting()) return;
    if (previewKey()) reviewPreviewChanges(stopPreview, event.currentTarget);
    else startPreview();
});
document.getElementById('keep-draft').addEventListener('click', event =>
    reviewPreviewChanges(null, event.currentTarget));
document.getElementById('draft-form').querySelectorAll('input, textarea').forEach(input => {
    // Choosing or cancelling a replacement is an action, not a draft edit.
    if (input.id === 'replace-script-file') return;
    input.addEventListener('input', () => {
        clearFieldError(input);
        updateDirtyState();
        if (input.id === 'catalog-name') {
            document.getElementById('draft-title').textContent = input.value || UNTITLED;
            explorer.showName(draft.id, input.value);
        }
    });
});
document.getElementById('catalog-area').addEventListener('change', event => {
    clearFieldError(event.currentTarget);
    updateDirtyState();
});
// Generated Python: always the saved draft's script, as a Preview runs it.
function pythonUrl(suffix = 'python') {
    return `${api}/${encodeURIComponent(draft.id)}/${suffix}`;
}

async function loadPython() {
    const loading = draft;
    const revision = loading.revision;
    const shown = await request(pythonUrl());
    if (draft === loading && draft.revision === revision) showScript(shown);
    return shown.source;
}

// Show the saved script around the Code section; the first time for this
// draft, or since the pane opened, bring the Code section into view.
function showScript(parts) {
    scriptEditor.show(parts);
    scriptShown = true;
    if (revealBand && scriptPaneOpen()) {
        revealBand = false;
        scriptEditor.showLine();
    }
}

async function refreshPython() {
    if (!draft || isPython()) return;
    if (!draft.topology.nodes.length) {
        showScript({ before: '# Add a Node to generate Python.\n' });
        return;
    }
    try {
        await loadPython();
    } catch (error) {
        announce(error.message, true);
    }
}

async function downloadPackage() {
    if (!await draftSaved()) return;
    try {
        const response = await fetch(pythonUrl('python.zip'), { credentials: 'same-origin' });
        if (!response.ok) {
            if (response.status === 401) recoverSession();
            const body = await response.json().catch(() => ({}));
            throw new Error(typeof body.detail === 'string' ? body.detail : `Download failed (${response.status})`);
        }
        const name = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '')?.[1]
            || 'lab-example.zip';
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement('a');
        link.href = url;
        link.download = name;
        document.body.append(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        announce(`Downloaded ${name}: the script with its Seeds, Guide, Layout and a README.`);
    } catch (error) {
        announce(error.message, true);
    }
}

// Freeze edits and draft navigation until the saved Canvas recipe has been
// copied. A failed save/conversion keeps the existing editor and local text.
async function continueInPython() {
    const workspace = document.querySelector('.authoring-workspace');
    if (!draft || isPython() || workspace.inert) return;
    const source = draft;
    const action = document.getElementById('script-continue-python');
    const navigation = document.getElementById('labs-explorer');
    // Keep the status/alert regions exposed while the controls are held.
    const held = [workspace, navigation, ...document.querySelectorAll(
        '#status-line > :not(#status-message):not(#status-alert)')];
    held.forEach(element => { element.inert = true; });
    let created = null;
    announce('Saving this Canvas draft before creating a separate Python draft…');
    try {
        if (!await draftSaved()) {
            announce("Could not continue in Python. Your Canvas draft and local edits stay open. "
                + (autosave.state === 'conflict'
                    ? 'Someone else changed this draft; resolve the conflict before continuing.'
                    : `Correct the save problem first: ${autosave.refusal?.reason || 'sign in again'}.`), true);
            return;
        }
        created = await request(`${api}/${encodeURIComponent(source.id)}/continue-in-python`, {
            method: 'POST', body: JSON.stringify({ revision: source.revision }),
        });
    } catch (error) {
        announce(`Could not continue in Python: ${error.message}. `
            + 'Your Canvas draft stays open. Reopen it if it changed elsewhere, then try again.', true);
    } finally {
        held.forEach(element => { element.inert = false; });
        if (!created) action.focus();
    }
    if (!created) return;
    openDraft(created);
    pythonEditor.showLine(1, { focus: true });
    announce('Opened a separate editable Python draft with this lesson’s files. '
        + 'The Canvas original remains in Drafts; the drafts do not synchronize. '
        + 'Nothing was published or changed in a running Lab.');
}

document.getElementById('script-continue-python').addEventListener('click', continueInPython);
document.getElementById('script-download').addEventListener('click', () => downloadPackage());
document.getElementById('python-show-api-help').addEventListener('click', () =>
    activeEditor().showApiHelp());

// Python drafts: the uploaded script, its Seed files and where it fails to compile.
function showPythonError(error) {
    const target = document.getElementById('python-source-error');
    target.hidden = !error || pythonEditor.activePath() !== null;
    target.textContent = !error ? '' : `${error.line ? `Line ${error.line}${error.column
        ? `, column ${error.column}` : ''}: ` : ''}${error.message}. Fix the script here, then Preview it.`;
    pythonEditor.setError(error);
}

function renderPythonEditor() {
    pythonEditor.open(draft.python, draft.files, draft.id);
    showPythonError(draft.python_error);
}

// Upload a script…: a new Python draft from one .py file, opened with its
// script focused; the failure, or null.
async function uploadScript(file) {
    const body = new FormData();
    body.append('file', file);
    let created;
    try {
        created = await request(`${api}/upload`, { method: 'POST', headers: {}, body });
    } catch (error) {
        return error.message;
    }
    openDraft(created);
    pythonEditor.showLine(1, { focus: true });
    announce(`Uploaded ${file.name} as a new Python draft.`);
    return null;
}

// Replacement shares the current draft's save boundary. Pause autosave while
// its revision changes, then retain anything typed during the upload.
const replaceScriptButton = document.getElementById('replace-script');
const replaceScriptFile = document.getElementById('replace-script-file');
replaceScriptButton.addEventListener('click', () => replaceScriptFile.click());
replaceScriptFile.addEventListener('cancel', () => replaceScriptButton.focus());
replaceScriptFile.addEventListener('change', async () => {
    const [file] = replaceScriptFile.files;
    replaceScriptFile.value = '';
    if (!file || !isPython()) return;
    const replacing = draft;
    replaceScriptButton.disabled = true;
    let replaced = false;
    try {
        if (await autosave.flush() !== 'saved' || draftRefusal()) {
            throw new Error('Save your current edits before replacing the script.');
        }
        if (draft !== replacing) return;
        collectForm();
        const previousSource = draft.python;
        autosave.pause('replace-script');
        const body = new FormData();
        body.append('revision', String(draft.revision));
        body.append('file', file);
        const saved = await request(`${api}/${encodeURIComponent(draft.id)}/script`, {
            method: 'PUT', headers: {}, body,
        });
        if (draft !== replacing) return;
        collectForm();
        const newerSource = draft.python !== previousSource;
        if (!newerSource) draft.python = saved.python;
        for (const field of ['revision', 'preview_valid', 'published_revision']) {
            draft[field] = saved[field];
        }
        draft.python_error = newerSource ? null : saved.python_error;
        document.getElementById('draft-revision').value = saved.revision;
        renderPythonEditor();
        // Opening an editor restores its file tab; replacement always shows lab.py.
        pythonEditor.showLine(saved.python_error?.line || 1, { focus: true });
        updateActionState();
        refreshDrawing();
        explorer.refresh();
        replaced = true;
        announce(`Replaced script.${saved.python !== previousSource || newerSource
            ? ' Script changed; rebuild Preview before publishing.'
            : saved.preview_valid ? ' Script unchanged; Preview retained.' : ' Script unchanged.'}${newerSource
            ? ' Newer local script edits are retained and will save next.' : ''}`);
    } catch (error) {
        if (draft === replacing) {
            // Reuse autosave's conflict state without replacing any local text.
            if (error.status === 409) autosave.edit();
            announce(error.status === 409
                ? 'Someone else changed this draft. Your local edits are retained. Reopen before replacing the script.'
                : error.message, true);
        }
    } finally {
        autosave.resume('replace-script');
        replaceScriptButton.disabled = false;
        if (!replaced && draft === replacing) replaceScriptButton.focus();
    }
});

// Import never leaves unsaved work behind, including edits made during upload.
async function importPackage(file, source = null) {
    const body = new FormData();
    body.append('file', file);
    return request(`${api}/import${source ? '?source=python' : ''}`, {
        method: 'POST', headers: {}, body,
    });
}

async function openImportedPackage(created, file) {
    if (draft && await autosave.flush() !== 'saved') {
        await explorer.refresh();
        throw new Error('Package imported as a separate draft. Save your current edits before opening it from Drafts.');
    }
    openDraft(created);
    if (isPython()) pythonEditor.showLine(1, { focus: true });
    else document.getElementById('canvas-region').focus();
    announce(`Imported ${file.name} as a new ${isPython() ? 'Python' : 'Canvas'} draft.`);
}

let announcedRefusal = null;
const autosave = createAutosave({
    save: persistDraft,
    onChange: (state, refusal) => {
        showDraftState(state, refusal, {
            fix: target => showItemError(target.fix),
            reopen: reopenDraft,
        });
        // A new reason is said once; the line keeps showing it.
        if (state === 'refused' && refusal.reason !== announcedRefusal) {
            announceOutcome(`Can't save yet: ${refusal.reason}`, { shown: false });
        }
        announcedRefusal = refusal?.reason ?? null;
    },
});

document.getElementById('session-sign-in').addEventListener('click', () => {
    sessionDialog.showModal();
    document.getElementById('session-password').focus();
});
document.getElementById('session-close').addEventListener('click', () => sessionDialog.close());
sessionDialog.addEventListener('close', () => {
    document.getElementById('session-password').value = '';
    document.getElementById('session-password').removeAttribute('aria-invalid');
    document.getElementById('session-error').textContent = '';
});
document.getElementById('session-password').addEventListener('input', event => {
    event.target.removeAttribute('aria-invalid');
    document.getElementById('session-error').textContent = '';
});
document.getElementById('session-form').addEventListener('submit', async event => {
    event.preventDefault();
    const submit = document.getElementById('session-submit');
    const password = document.getElementById('session-password');
    const feedback = document.getElementById('session-error');
    submit.disabled = true;
    feedback.textContent = '';
    password.removeAttribute('aria-invalid');
    try {
        // Login's own 401 is a credential refusal, not another expired Session.
        const response = await fetch('/api/login', {
            method: 'POST', credentials: 'same-origin',
            headers: { Authorization: `Basic ${btoa(`${username}:${password.value}`)}` },
        });
        const identity = await response.json();
        if (!response.ok) {
            if (response.status === 401) password.setAttribute('aria-invalid', 'true');
            throw new Error(identity.detail || 'Sign in failed. Try again.');
        }
        if (identity.username !== username || identity.role !== 'instructor') {
            throw new Error('This account no longer has the Instructor role. Your edits remain in this page.');
        }
        sessionExpired = false;
        document.getElementById('session-recovery').hidden = true;
        sessionDialog.close();
        if (draft && autosave.state !== 'saved') autosave.edit();
        autosave.resume('session');
        if (draft && !previewStarting()) {
            const id = draft.id;
            await detectPreview(id, () => draft?.id === id);
        }
        announce('Signed in again. Actions that were interrupted can be retried.');
    } catch (error) {
        feedback.textContent = error.message;
        password.value = '';
        password.focus();
    } finally {
        submit.disabled = false;
    }
});

// Nothing is merged: reopening shows the latest revision in place of these edits.
async function reopenDraft() {
    await openDraftById(draft.id);
    document.getElementById('canvas-region').focus();
}

// Closing any dialog saves what was edited in it.
document.querySelectorAll('.authoring-dialog').forEach(dialog =>
    dialog.addEventListener('close', () => autosave.flush()));
// Leaving with edits the server does not hold yet asks first.
window.addEventListener('beforeunload', event => {
    if (draft && autosave.state !== 'saved') event.preventDefault();
});

initPreview({
    request,
    current: () => Boolean(draft?.preview_valid),
    username: () => username,
    changed: refreshPreviewChanges,
});
initPalette({
    groups: PALETTE_GROUPS.map(group => group.map(type => PALETTE.find(item => item.type === type))),
    iconPath,
    color: getNodeColor,
    onPlace: type => addNode(PALETTE.find(item => item.type === type), freeSpot()),
    dragType: PALETTE_DRAG_TYPE,
});
const currentSelection = () => ({ nodeId: selectedNodeId, linkId: selectedLinkId });
const describeNode = node => `${typeLabel(node)} ${nodeLabel(node)}`;
const focusLink = linkId => canvasItem('link', linkId)?.focus();

// A drawn Node or Link, which only its script changes.
function readOnlyItem(kind, id) {
    const item = kind === 'link' ? linkById(id) : nodeById(id);
    return item?.readOnly ? item : null;
}

// Refuse an edit to a drawn item and say where to make it; true when refused.
function refuseReadOnly(kind, id) {
    const item = readOnlyItem(kind, id);
    if (!item) return false;
    const what = kind === 'link' ? linkTitle(item) : describeNode(item);
    announce(`${what} comes from ${item.fromCode ? 'the Code section' : 'the script'}; change it there.`);
    return true;
}

// Put the cursor where the script makes a drawn item.
function showWhereMade(item) {
    setScriptPaneOpen(true);
    showScriptLine(item.line ?? 1, { focus: true });
}

// While the Script pane is open, selecting a drawn item puts the script's
// cursor where it is made; focus stays on the canvas.
function followSelection(kind, id) {
    const item = readOnlyItem(kind, id);
    if (!item?.line || !scriptPaneOpen()) return;
    showScriptLine(item.line);
}

const openItem = (kind, id) => {
    const item = readOnlyItem(kind, id);
    if (item) showWhereMade(item);
    else if (kind === 'link') openLinkDialog(id);
    else openNodeDialog(id);
};
initCanvas({
    nodes: allNodes,
    links: allLinks,
    position: positionOf,
    selection: currentSelection,
    describeNode,
    linkLabel: link => `${linkTitle(link)}, Interfaces ${interfaceName(link.ends[0])} and ${interfaceName(link.ends[1])}`,
    iconPath,
    color: nodeColor,
    select: (kind, id) => (kind === 'link' ? selectLink(id) : selectNode(id)),
    open: openItem,
    remove: (kind, id) => refuseReadOnly(kind, id)
        || (kind === 'link' ? removeLink(id) : removeNode(id)),
    move: (nodeId, position) => {
        draft.layout.nodes[nodeId] = position;
        updateDirtyState();
        renderCanvas();
    },
    link: (firstId, secondId) => (refuseReadOnly('host', secondId) ? null : addLink(firstId, secondId)),
    announce,
    related: nodeId => (readOnlyItem('host', nodeId) ? [] : [...new Set(relationshipsOf(draft.topology, nodeId)
        .map(item => item.nodeId).filter(Boolean))]),
    connecting: connectSource,
    pick: nodeId => refuseReadOnly('host', nodeId) || pickConnectTarget(nodeId),
    rename: nodeId => refuseReadOnly('host', nodeId) || startRename(nodeId),
});
initConnectMode({
    nodeName: nodeId => nodeLabel(nodeById(nodeId)),
    nodeElement: nodeId => canvasItem('host', nodeId),
    focusNearest: focusNearestNode,
    link: addLink,
    focusLink,
    redraw: renderCanvas,
    announce,
});
initSelection({
    selection: currentSelection,
    links: allLinks,
    nodeById,
    linkById,
    nodeLabel,
    describeNode,
    linkTitle,
    interfaceName,
    relationships: nodeId => (readOnlyItem('host', nodeId) ? [] : relationshipsOf(draft.topology, nodeId)),
    // The Selection's actions leave connect mode first.
    edit: (kind, id, trigger) => {
        endConnect();
        openItem(kind, id);
        // Only a dialog hands focus back; a drawn item's script keeps it.
        if (!readOnlyItem(kind, id)) focusAfterDialog = trigger;
    },
    addresses: openAddressesDialog,
    connect: startConnect,
    remove: removeNode,
    focusLink,
    changes: previewChangeCount,
    frr: previewFrr,
    keep: (nodeId, trigger) => reviewPreviewChanges(null, trigger, nodeId),
});
document.getElementById('lab-address-family').addEventListener('change', event => {
    const conflicts = addressFamilyConflicts(draft.topology, event.target.value);
    if (conflicts.length) {
        event.target.value = draft.address_family;
        showFieldError(event.target, `Clear these fields before switching family: ${conflicts.join('; ')}.`);
        return;
    }
    draft.address_family = event.target.value;
    showAddressFamily(document, draft.address_family);
    clearFieldError(event.target);
    updateDirtyState();
});
document.getElementById('lab-persistent-config').addEventListener('change', event => {
    draft.persistent_config = event.target.checked;
    updateDirtyState();
});
initNodeDialog({
    getDraft: () => draft,
    nodeById,
    typeLabel,
    nodeLabel,
    getDialogItem: () => dialogItem,
    setDialogItem: item => { dialogItem = item; },
    clearPendingNode: () => { pendingNodeId = null; },
    selectNode,
    closeDialogs,
    openLinkDialog,
    getRouterProtocols: () => routerProtocols,
    getRefusalFix: () => autosave.refusal?.fix,
    updateDirtyState,
    renderCanvas,
    removeNode,
    bindDialogClosing,
    randomUuid,
    announce,
});
initLinkDialog({
    getDraft: () => draft,
    linkById,
    linkTitle,
    typeLabel,
    nodeLabel,
    getDialogItem: () => dialogItem,
    setDialogItem: item => { dialogItem = item; },
    closeDialogs,
    closeDialogToCanvas,
    bindDialogClosing,
    clearFieldError,
    updateDirtyState,
    renderCanvas,
    removeLink,
});
initAddressesDialog({
    getDraft: () => draft,
    nodeById,
    endConnect,
    closeDialogs,
    previewFrr,
    clearFieldError,
    updateDirtyState,
    canvasItem,
});
bindCanvasDrop();
initCanvasView(() => (draft ? allNodes().map(node => positionOf(node.id)) : []));
document.getElementById('canvas-auto-arrange').addEventListener('click', autoArrangeNodes);
initKeepReview({
    request,
    api,
    draft: () => draft,
    saved: draftSaved,
    previewKey,
    previewGone: previewStopped,
    kept: (saved, message) => {
        openDraft(saved);
        announce(message);
    },
    fail: message => announce(message, true),
    updated: () => { renderSelection(); renderKeepStatus(); },
});
initShell();
function renderKeepStatus() {
    const changes = previewChangeCount();
    const folded = !sidebarIsOpen();
    const count = document.getElementById('status-keep-count');
    count.hidden = !folded || !changes;
    count.textContent = `${changes} change${changes === 1 ? '' : 's'} not yet kept · `;
    document.getElementById('keep-draft').textContent = folded && changes ? 'Keep…' : 'Keep changes…';
}
document.getElementById('sidebar-toggle').addEventListener('click', renderKeepStatus);
window.addEventListener('focus', refreshPreviewChanges);
document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshPreviewChanges();
});
initScriptPane({
    onShow: () => {
        revealBand = !scriptShown;
        // The pane opens on a failing line, or else at the end of the Code section.
        if (scriptShown && !isPython()) scriptEditor.showLine(codeSectionError?.line ?? null);
    },
});
initGuideDialog();
const explorer = initLabsExplorer({
    listDrafts: loadDraftList,
    currentId: () => draft?.id,
    settle: async () => !draft || await autosave.flush() === 'saved',
    open: openDraftById,
    announce,
    create: async () => {
        try {
            await createDraft();
            announce('Created a new shared draft.');
        } catch (error) {
            announce(error.message, true);
        }
    },
    copy: copyDraft,
    rename: renameDraft,
    remove: removeDraft,
    listExamples: async () => (await request('/api/examples')).examples,
    startFromExample,
    configCopies: async example => (await request(
        `/api/authoring/examples/${encodeURIComponent(example.id)}/removal`)).config_copies,
    removeExample: async example => (await request(
        `/api/authoring/examples/${encodeURIComponent(example.id)}`, { method: 'DELETE' })).discarded,
    upload: uploadScript,
    importPackage,
    openImportedPackage,
});
initPublishDialog({ readiness: publishReadiness, publish: publishDraft });
document.getElementById('close-draft').addEventListener('click', closeOpenDraft);
document.getElementById('draft-rename').addEventListener('click', event => {
    renameInPlace(event.currentTarget, {
        name: document.getElementById('catalog-name').value || UNTITLED,
        rename: name => renameDraft({ id: draft.id, catalog: { name: draft.catalog.name } }, name),
    });
});
initHeader();

try {
    const identity = await request('/api/me');
    if (identity.role !== 'instructor') throw new Error('Instructor role required');
    applyHeaderIdentity(identity.username, identity.role);
    username = identity.username;
    ({ protocols: routerProtocols } = await request('/api/authoring/protocols'));
    const { areas } = await request('/api/examples');
    const areaSelect = document.getElementById('catalog-area');
    areas.forEach(area => areaSelect.add(new Option(area, area)));
    const drafts = await loadDraftList();
    document.getElementById('authoring-status').textContent = 'Shared drafts are ready.';
    const adaptId = new URLSearchParams(window.location.search).get('adapt');
    if (adaptId) {
        await createDraft(null, adaptId);
        window.history.replaceState(null, '', '/authoring');
        announce('Created an editable draft from the Lab Example. Publish as new to keep the original.');
    } else if (wasClosed()) {
        document.getElementById('authoring-status').textContent = NO_DRAFT_OPEN;
    } else if (drafts.length) {
        openDraft(drafts[0]);
    } else {
        announce(BEGIN);
    }
} catch (error) {
    announce(error.message, true);
}
// A draft opened on arrival folds the explorer to its rail; with none, it lists them.
if (draft) explorer.setFolded(true);
else explorer.refresh();
