// The Script pane's editors: a Python draft's uploaded script and Seed files,
// editable, and a Canvas draft's generated script with its Code section,
// whose edits use the same autosave entry point as the Lesson and Guide.
import {
    Compartment, Decoration, EditorState, EditorView, keymap, defaultKeymap,
    indentWithTab, indentUnit, lineNumbers, history, historyKeymap,
    bracketMatching, indentOnInput, syntaxHighlighting, HighlightStyle, tags,
    python, pythonLanguage, autocompletion, closeCompletion,
    search, searchKeymap,
    highlightSelectionMatches,
} from '../lib/codemirror.bundle.js';
import { labApiCompletion, labMethodAt, renderHelp } from './python-api-help.js';

const PYTHON_HIGHLIGHT = HighlightStyle.define([
    { tag: tags.keyword, color: 'var(--python-keyword)' },
    { tag: tags.string, color: 'var(--python-string)' },
    { tag: tags.comment, color: 'var(--python-comment)' },
    { tag: tags.number, color: 'var(--python-number)' },
]);

const ERROR_LINE = Decoration.line({ attributes: { class: 'cm-compile-error-line' } });

// Mark the line a failure names, with ⚠ in its number, or clear the mark (null).
function errorMark(view, numbers, highlight, line) {
    return [
        numbers.reconfigure(lineNumbers(line === null ? {} : {
            formatNumber: number => (number === line ? `⚠ ${number}` : String(number)),
        })),
        highlight.reconfigure(line === null ? [] : EditorView.decorations.of(
            Decoration.set([ERROR_LINE.range(view.state.doc.line(line).from)]))),
    ];
}

// The Script pane's Keys tip, worded for the file being edited.
const KEYS = {
    script: 'Ctrl+F searches · F1 or Show API help describes the method at the '
        + 'cursor (F1 may be taken by the browser) · Tab leaves the editor',
    seed: 'Ctrl+F searches · Tab leaves the editor',
    codeSection: 'Ctrl+] / Ctrl+[ indent / outdent · Ctrl+Space completes · F1 or Show API help '
        + 'describes the method at the cursor (F1 may be taken by the browser) · Tab leaves the editor',
};

function showKeys(keys) {
    document.getElementById('script-keys-text').textContent = keys;
}

// The Script pane's one API help region, for whichever script is shown.
function hideApiHelp() {
    const region = document.getElementById('python-api-help');
    region.hidden = true;
    region.replaceChildren();
    document.getElementById('python-show-api-help').setAttribute('aria-expanded', 'false');
}

function showApiHelp(view) {
    const region = document.getElementById('python-api-help');
    closeCompletion(view);
    const method = labMethodAt(view.state, view.state.selection.main.head);
    if (method) {
        region.replaceChildren(renderHelp(method));
    } else {
        region.textContent = 'Place the cursor on a supported mnIPLab method to see its signature.';
    }
    region.hidden = false;
    document.getElementById('python-show-api-help').setAttribute('aria-expanded', 'true');
    region.focus();
}

/**
 * Create a Python draft's source editor; opening a draft replaces its documents.
 *
 * @param {HTMLElement} host Where the editor is mounted, the file tabs' panel.
 * @param {object} hooks
 * @param {HTMLElement} hooks.files The tab strip naming ``lab.py`` and each Seed.
 * @param {() => void} hooks.onChange Save changed source through the page autosave.
 */
export function createPythonEditor(host, { files: tabs, onChange }) {
    const helpButton = document.getElementById('python-show-api-help');
    const errorNumbers = new Compartment();
    const errorHighlight = new Compartment();
    let activePath = null;
    let selectionKey = null;
    let states = new Map();
    // CodeMirror stores line breaks internally as LF. Keep the supplied text
    // alongside each document so saving an untouched source never rewrites
    // CRLF-authored scripts or Seeds merely because another draft field changed.
    let openedSource = new Map();
    let compileError = null;
    const extensions = [
        history(),
        indentOnInput(),
        EditorView.updateListener.of(update => {
            if (update.docChanged) queueMicrotask(onChange);
        }),
        errorNumbers.of(lineNumbers()),
        errorHighlight.of([]),
        python(),
        syntaxHighlighting(PYTHON_HIGHLIGHT),
        bracketMatching(),
        search(),
        highlightSelectionMatches(),
        keymap.of([
            { key: 'F1', run: () => { if (activePath === null) showApiHelp(view); return true; } },
            ...searchKeymap, ...defaultKeymap, ...historyKeymap,
        ]),
        EditorView.contentAttributes.of({
            id: 'python-source',
            'aria-label': 'Lab script',
            'aria-describedby': 'script-keys-text python-source-error',
            spellcheck: 'false',
        }),
    ];
    const view = new EditorView({ parent: host, extensions });
    states.set(null, view.state);

    const tabId = path => `python-file-${[...states.keys()].indexOf(path)}`;

    // One tab per file; the selected one is the strip's Tab stop (APG tabs).
    function renderFiles() {
        view.contentDOM.setAttribute('aria-describedby', activePath === null
            ? 'script-keys-text python-source-error' : 'script-keys-text');
        showKeys(activePath === null ? KEYS.script : KEYS.seed);
        helpButton.hidden = activePath !== null;
        tabs.replaceChildren(...[...states.keys()].map(path => {
            const tab = document.createElement('button');
            tab.type = 'button';
            tab.id = tabId(path);
            tab.setAttribute('role', 'tab');
            tab.setAttribute('aria-controls', host.id);
            tab.setAttribute('aria-selected', String(path === activePath));
            tab.tabIndex = path === activePath ? 0 : -1;
            tab.textContent = path === null ? 'lab.py' : path.replace(/^frr-config\//, '');
            tab.title = path ?? 'lab.py';
            tab.dataset.path = path ?? '';
            return tab;
        }));
        host.setAttribute('aria-labelledby', tabId(activePath));
    }

    function selectFile(path, { focus = true } = {}) {
        if (!states.has(path)) return;
        states.set(activePath, view.state);
        activePath = path;
        try {
            if (selectionKey) sessionStorage.setItem(selectionKey, path ?? '');
        } catch { /* File selection still works when browser storage is unavailable. */ }
        view.setState(states.get(path));
        view.contentDOM.setAttribute('aria-label', path === null ? 'Lab script' : `Seed file ${path}`);
        if (path === null) setError(compileError);
        else view.contentDOM.setAttribute('aria-invalid', 'false');
        document.getElementById('python-source-error').hidden = path !== null || !compileError;
        hideApiHelp();
        renderFiles();
        if (focus) view.focus();
    }

    // The strip is redrawn on selection, so focus the new tab by its id.
    function activate(tab) {
        selectFile(tab.dataset.path === '' ? null : tab.dataset.path, { focus: false });
        document.getElementById(tab.id).focus();
    }
    tabs.addEventListener('click', event => {
        const tab = event.target.closest('[role="tab"]');
        if (tab) activate(tab);
    });
    tabs.addEventListener('keydown', event => {
        const all = [...tabs.querySelectorAll('[role="tab"]')];
        const index = all.indexOf(document.activeElement);
        const next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: all.length - 1 }[event.key];
        if (index < 0 || next === undefined) return;
        event.preventDefault();
        activate(all[(next + all.length) % all.length]);
    });

    function clearError() {
        if (!view.dom.isConnected) return;
        view.dispatch({ effects: errorMark(view, errorNumbers, errorHighlight, null) });
        view.contentDOM.setAttribute('aria-invalid', 'false');
    }

    function setError(error) {
        compileError = error;
        if (activePath !== null) return;
        const line = Number(error?.line);
        if (!error || !Number.isInteger(line) || line < 1 || line > view.state.doc.lines) {
            clearError();
            if (error) view.contentDOM.setAttribute('aria-invalid', 'true');
            return;
        }
        view.dispatch({ effects: errorMark(view, errorNumbers, errorHighlight, line) });
        view.contentDOM.setAttribute('aria-invalid', 'true');
    }

    return {
        open: (source, files, draftId) => {
            selectionKey = `mniplab-authoring-file:${draftId}`;
            states = new Map([[null, EditorState.create({ doc: source, extensions })]]);
            openedSource = new Map([[null, source]]);
            for (const [path, text] of Object.entries(files)) {
                states.set(path, EditorState.create({ doc: text, extensions }));
                openedSource.set(path, text);
            }
            activePath = null;
            compileError = null;
            hideApiHelp();
            view.setState(states.get(null));
            view.contentDOM.setAttribute('aria-label', 'Lab script');
            let selected = null;
            try { selected = sessionStorage.getItem(selectionKey) || null; }
            catch { /* Open the script when browser storage is unavailable. */ }
            selectFile(states.has(selected) ? selected : null, { focus: false });
        },
        source: () => {
            states.set(activePath, view.state);
            const text = (path, state) => {
                const current = state.doc.toString();
                const opened = openedSource.get(path);
                return opened !== undefined && opened.replace(/\r\n?|\n/g, '\n') === current
                    ? opened : current;
            };
            return {
                python: text(null, states.get(null)),
                files: Object.fromEntries([...states].filter(([path]) => path !== null)
                    .map(([path, state]) => [path, text(path, state)])),
            };
        },
        activePath: () => activePath,
        showApiHelp: () => { if (activePath === null) showApiHelp(view); },
        /** Put the cursor on a line of the script, opening ``lab.py`` if a Seed is shown. */
        showLine: (line = 1, { focus = false } = {}) => {
            if (activePath !== null) selectFile(null, { focus: false });
            const position = view.state.doc.line(Math.max(1, Math.min(line, view.state.doc.lines))).from;
            view.dispatch({ selection: { anchor: position },
                effects: EditorView.scrollIntoView(position, { y: 'center' }) });
            if (focus) view.focus();
        },
        setError,
    };
}

const CODE_SECTION_HEADING = '    # Code section\n';
const INDENT = '    ';
const BAND_LINE = Decoration.line({ class: 'cm-code-band' });
// The bundle does not export Transaction; its addToHistory annotation is on
// every transaction's class.
const Transaction = EditorState.create().update({}).constructor;

// The Code section as the script runs it: each line indented into build_network().
function indented(code) {
    return code.replace(/\n$/, '').split('\n').map(line => (line ? INDENT + line : line)).join('\n')
        || INDENT;
}

// The Code section as stored: without the indent the script adds. Every
// line loses the same indent, at most four spaces, so blocks keep their shape.
function dedented(text) {
    const lines = text.split('\n');
    const leading = line => /^ */.exec(line)[0].length;
    const indent = Math.min(INDENT.length, ...lines.filter(line => line.trim()).map(leading));
    const code = lines.map(line => line.slice(Math.min(indent, leading(line)))).join('\n');
    return code.trim() ? code : '';
}

/**
 * Create a Canvas draft's script editor: one document with real line
 * numbers, generated from the canvas, whose only editable band is the Code
 * section at the end of the build step.
 *
 * @param {HTMLElement} host Where the editor is mounted.
 * @param {object} hooks
 * @param {() => void} hooks.onChange Save a Code section edit.
 * @param {(first: number, last: number) => void} hooks.onRefused Say that an
 *     edit outside the band was refused, naming the band's first and last lines.
 * @param {(first: number, last: number) => void} hooks.onBand Show which lines
 *     the band holds.
 */
export function createScriptEditor(host, { onChange, onRefused, onBand }) {
    // Characters before the band (the script and its Code section heading),
    // and after it (the blank line and the script from ``return net``).
    let head = 0;
    let tail = 0;
    let regenerating = false;
    // A stored Code section that ends with a newline keeps it when saved again.
    let keepsNewline = false;
    // The Code section line a failure names, marked until the band changes.
    let errorAt = null;
    const errorNumbers = new Compartment();
    const errorHighlight = new Compartment();
    const band = state => ({ from: head, to: state.doc.length - tail });
    const bandLines = state => {
        const { from, to } = band(state);
        return [state.doc.lineAt(from).number, state.doc.lineAt(to).number];
    };
    const inBand = (state, position) => {
        const { from, to } = band(state);
        return position >= from && position <= to;
    };
    const extensions = [
        errorNumbers.of(lineNumbers()),
        errorHighlight.of([]),
        indentUnit.of(INDENT),
        python(),
        pythonLanguage.data.of({ autocomplete: context =>
            (inBand(context.state, context.pos) ? labApiCompletion(context) : null) }),
        autocompletion(),
        syntaxHighlighting(PYTHON_HIGHLIGHT),
        indentOnInput(),
        bracketMatching(),
        history(),
        search(),
        highlightSelectionMatches(),
        // Tab leaves the script: it is one stop among the pane's controls, and
        // Enter keeps the indentation.
        keymap.of([
            { key: 'F1', run: view => { showApiHelp(view); return true; } },
            { key: 'Ctrl-]', run: indentWithTab.run },
            { key: 'Ctrl-[', run: indentWithTab.shift },
            ...searchKeymap, ...defaultKeymap, ...historyKeymap,
        ]),
        // Pasted lines keep their shape: each after the first is indented
        // like the line it lands on, as if written there.
        EditorView.clipboardInputFilter.of((text, state) => {
            const { head: position } = state.selection.main;
            if (!inBand(state, position)) return text;
            const line = state.doc.lineAt(position);
            const indent = /^ */.exec(line.text)[0].slice(0, position - line.from);
            return text.replace(/\n(?=[^\n])/g, `\n${indent}`);
        }),
        // Only the Code section changes here; the rest is the canvas's.
        EditorState.changeFilter.of(transaction => {
            if (regenerating || !transaction.docChanged) return true;
            const { from, to } = band(transaction.startState);
            let inside = true;
            transaction.changes.iterChangedRanges((changeFrom, changeTo) => {
                if (changeFrom < from || changeTo > to) inside = false;
            });
            if (!inside) onRefused(...bandLines(transaction.startState));
            return inside;
        }),
        EditorView.decorations.of(view => {
            const { doc } = view.state;
            const { from, to } = band(view.state);
            const lines = [];
            for (let number = doc.lineAt(from).number; number <= doc.lineAt(to).number; number += 1) {
                lines.push(BAND_LINE.range(doc.line(number).from));
            }
            return Decoration.set(lines);
        }),
        EditorView.contentAttributes.of({
            id: 'script-source',
            'aria-label': 'Lab script',
            'aria-describedby': 'code-section-band code-section-error code-section-help script-keys-text',
            spellcheck: 'false',
        }),
        EditorView.updateListener.of(update => {
            if (!update.docChanged) return;
            onBand(...bandLines(update.state));
            if (regenerating) return;
            if (errorAt !== null) {
                errorAt = null;
                // Dispatch after CodeMirror completes this update.
                queueMicrotask(markError);
            }
            onChange();
        }),
    ];
    const view = new EditorView({ parent: host, extensions });

    // The Code section's own line, as the script numbers it.
    function scriptLine(line) {
        const { doc } = view.state;
        const { from, to } = band(view.state);
        return Math.min(doc.lineAt(to).number, doc.lineAt(from).number + Math.max(1, line) - 1);
    }

    function markError() {
        const line = errorAt === null ? null : scriptLine(errorAt);
        view.dispatch({ effects: errorMark(view, errorNumbers, errorHighlight, line) });
        view.contentDOM.setAttribute('aria-invalid', String(line !== null));
    }

    return {
        /** Open a draft's Code section; the script around it follows with ``show``. */
        open: code => {
            const text = indented(code);
            keepsNewline = code.endsWith('\n');
            head = CODE_SECTION_HEADING.length;
            tail = 2;
            errorAt = null;
            view.setState(EditorState.create({
                doc: `${CODE_SECTION_HEADING}${text}\n\n`,
                selection: { anchor: head + text.length },
                extensions,
            }));
            view.contentDOM.setAttribute('aria-invalid', 'false');
            hideApiHelp();
            showKeys(KEYS.codeSection);
            onBand(...bandLines(view.state));
        },
        /**
         * Show the saved script around the Code section, keeping what is
         * typed in it. With a Code section, the document is the saved script.
         */
        show: ({ before = '', after = '' }) => {
            const { doc } = view.state;
            const top = before + CODE_SECTION_HEADING;
            const bottom = `\n\n${after}`;
            if (doc.sliceString(0, head) === top && doc.sliceString(doc.length - tail) === bottom) return;
            const changes = [
                { from: 0, to: head, insert: top },
                { from: doc.length - tail, to: doc.length, insert: bottom },
            ];
            head = top.length;
            tail = bottom.length;
            regenerating = true;
            try {
                view.dispatch({ changes, annotations: Transaction.addToHistory.of(false) });
            } finally {
                regenerating = false;
            }
            // The band moved with the script before it; so does its mark.
            if (errorAt !== null) markError();
        },
        code: () => {
            const { from, to } = band(view.state);
            const code = dedented(view.state.doc.sliceString(from, to));
            return code && keepsNewline && !code.endsWith('\n') ? `${code}\n` : code;
        },
        /**
         * Put the cursor on one of the Code section's own lines (numbered
         * from 1, as a Preview's error names them), or at its end.
         */
        showLine: (line = null, { focus = false } = {}) => {
            let position = band(view.state).to;
            if (line !== null) {
                const target = view.state.doc.line(scriptLine(line));
                position = target.from + Math.min(INDENT.length, /^ */.exec(target.text)[0].length);
            }
            view.dispatch({ selection: { anchor: position },
                effects: EditorView.scrollIntoView(position, { y: 'center' }) });
            if (focus) view.focus();
        },
        showApiHelp: () => showApiHelp(view),
        hasFocus: () => view.hasFocus,
        /** Mark the Code section line a failure names, or clear the mark (null). */
        setError: line => {
            errorAt = line;
            markError();
        },
    };
}
