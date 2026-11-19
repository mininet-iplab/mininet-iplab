// Supporting bytes share the draft's autosave/revision boundary. The script,
// Seeds, Guide and Layout keep their existing controls as their sole owners.
import { confirmAction } from './confirm-dialog.js';

/** Mount supporting-file actions; edits use the workspace's shared autosave.
 * @param {object} hooks
 * @param {() => void} hooks.onChange Mark the open draft as edited.
 */
export function createLessonFiles({ onChange }) {
    const get = id => document.getElementById(`lesson-${id}`);
    const select = get('file');
    const content = get('content');
    const pathInput = get('path');
    const roleInput = get('role');
    const feedback = get('feedback');
    let files = Object.create(null);
    let selected = '';
    let draft = null;
    let selectionKey = '';

    function problem(message = '', input = pathInput) {
        feedback.textContent = message;
        for (const field of [pathInput, get('new-path'), roleInput]) {
            field.setAttribute('aria-invalid', String(Boolean(message) && field === input));
        }
        if (message) input.focus();
        return !message;
    }

    const presentationPath = path => path.startsWith('presentation/')
        && ['md', 'txt', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf']
            .includes(path.split('.').pop().toLowerCase());

    function valid(path, replacing = '') {
        const parts = path.split('/');
        if (path.length > 240 || parts.some(part => part.length > 100
            || !/^[A-Za-z0-9_][A-Za-z0-9_. ()+\-]*$/.test(part)
            || /[ .]$/.test(part) || part.toLowerCase() === '__pycache__')) {
            return 'Use a relative path with letters, digits, underscores, spaces, dots, parentheses, + or -. Each folder and name must start with a letter, digit or underscore and end without a space or dot.';
        }
        const lower = path.toLowerCase();
        const slug = draft.catalog.name.toLowerCase().replace(/[^a-z0-9]+/g, '-')
            .replace(/^-|-$/g, '').slice(0, 60).replace(/-$/, '') || 'lab-example';
        const reserved = ['lab.py', `${slug}.py`, 'definition.json', 'guide.md',
            'layout.json', 'readme.md', 'iplab-package.json', 'examples/guides', 'examples/layouts'];
        if (reserved.some(name => lower === name || lower.startsWith(`${name}/`))
            || (lower.startsWith('frr-config/')
                && /\/(frr.conf|daemons|vtysh.conf|support_bundle_commands.conf)$/.test(lower))) {
            return 'This path belongs to the script, Seeds, Guide, Layout or generated FRR controls. Edit it there.';
        }
        const others = [...Object.keys(files).filter(name => name !== replacing),
            ...Object.keys(draft.files || {})];
        if (others.some(name => {
            const other = name.toLowerCase();
            if (lower === other || lower.startsWith(`${other}/`) || other.startsWith(`${lower}/`)) return true;
            const components = name.split('/');
            return parts.some((part, index) => index < parts.length - 1
                && components[index]?.toLowerCase() === part.toLowerCase()
                && components[index] !== part
                && components.slice(0, index).join('/') === parts.slice(0, index).join('/'));
        })) return 'Choose a path that does not collide with another file or folder.';
        return '';
    }

    function remember() {
        try { sessionStorage.setItem(selectionKey, selected); }
        catch { /* Selection remains usable without browser storage. */ }
    }

    function render() {
        select.replaceChildren(...Object.keys(files).sort().map(path =>
            Object.assign(document.createElement('option'), { value: path, textContent: path })));
        select.value = selected;
        const file = files[selected];
        get('selected').hidden = !file;
        get('empty').hidden = !!file;
        pathInput.value = selected;
        roleInput.value = file?.role || 'build';
        content.hidden = get('content-label').hidden = !file || file.encoding !== 'utf-8';
        content.value = file?.encoding === 'utf-8' ? file.content : '';
        get('binary').hidden = !file || file.encoding !== 'base64';
        for (const action of ['rename', 'delete', 'download', 'replace']) {
            get(action).setAttribute('aria-label', `${get(action).textContent} ${selected}`);
        }
        remember();
    }

    select.addEventListener('change', () => {
        selected = select.value;
        problem();
        render();
    });
    content.addEventListener('input', () => {
        files[selected].content = content.value;
        onChange();
    });
    roleInput.addEventListener('change', () => {
        if (roleInput.value === 'presentation'
            && !presentationPath(selected)) {
            roleInput.value = files[selected].role || 'build';
            problem('Presentation requires a document or media file under presentation/. Code, configuration and files read while building must stay Build.', roleInput);
            return;
        }
        files[selected].role = roleInput.value;
        problem();
        onChange();
    });
    get('create').addEventListener('click', () => {
        const path = get('new-path').value;
        if (!problem(valid(path), get('new-path'))) return;
        if (Object.keys(files).length >= 200) {
            problem('A draft can hold at most 200 supporting files.', get('new-path'));
            return;
        }
        files[path] = { encoding: 'utf-8', content: '', role: 'build' };
        selected = path;
        get('new-path').value = '';
        render();
        onChange();
        content.focus();
    });
    get('rename').addEventListener('click', () => {
        const path = pathInput.value;
        if (!problem(valid(path, selected))) return;
        if (files[selected].role === 'presentation'
            && !presentationPath(path)) {
            problem('Keep presentation documents/media under presentation/, or change the role to Build first.');
            return;
        }
        if (path !== selected) {
            files[path] = files[selected];
            delete files[selected];
            selected = path;
            render();
            onChange();
        }
        pathInput.focus();
    });
    get('delete').addEventListener('click', async () => {
        const path = selected;
        if (await confirmAction({ title: 'Delete supporting file',
            message: `Delete ${path}? References in your script and Guide are not rewritten. Save/Preview reports missing build inputs.`,
            confirmLabel: 'Delete file', action: async () => {
                delete files[path];
                selected = Object.keys(files).sort()[0] || '';
                render();
                onChange();
            } })) {
            (selected ? select : get('new-path')).focus();
        }
    });
    get('download').addEventListener('click', () => {
        const file = files[selected];
        const bytes = file.encoding === 'utf-8' ? file.content
            : Uint8Array.from(atob(file.content), character => character.charCodeAt(0));
        const url = URL.createObjectURL(new Blob([bytes], { type: 'application/octet-stream' }));
        const link = Object.assign(document.createElement('a'), { href: url,
            download: selected.split('/').pop() });
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    async function upload(input, replacing) {
        const file = input.files[0];
        input.value = '';
        if (!file) return;
        const path = replacing ? selected : get('new-path').value;
        if (!problem(valid(path, replacing ? selected : ''), replacing ? pathInput : get('new-path'))) return;
        if (file.size > 2000000 || (!replacing && Object.keys(files).length >= 200)) {
            problem('Use files up to 2,000,000 bytes and at most 200 supporting files.', get('new-path'));
            return;
        }
        const currentDraft = draft;
        const role = replacing ? files[path].role || 'build' : 'build';
        let bytes;
        try { bytes = new Uint8Array(await file.arrayBuffer()); }
        catch {
            if (draft === currentDraft) problem('The file could not be read. Choose it again.',
                replacing ? pathInput : get('new-path'));
            return;
        }
        if (draft !== currentDraft) return;
        let text;
        try {
            text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
            const roundTrip = new TextEncoder().encode(text);
            if (/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(text)
                || roundTrip.length !== bytes.length || roundTrip.some((byte, index) => byte !== bytes[index])) {
                text = null;
            }
        } catch { text = null; }
        if (text !== null) {
            files[path] = { encoding: 'utf-8', content: text, role };
        } else {
            let encoded = '';
            for (const byte of bytes) encoded += String.fromCharCode(byte);
            files[path] = { encoding: 'base64', content: btoa(encoded), role };
        }
        selected = path;
        render();
        onChange();
        select.focus();
    }
    get('upload').addEventListener('click', () => get('upload-input').click());
    get('replace').addEventListener('click', () => get('replace-input').click());
    get('upload-input').addEventListener('change', () => upload(get('upload-input'), false));
    get('replace-input').addEventListener('change', () => upload(get('replace-input'), true));

    return {
        open(value) {
            draft = value;
            files = Object.assign(Object.create(null), structuredClone(value.lesson_files || {}));
            selectionKey = `mniplab-supporting-file:${value.id}`;
            try { selected = sessionStorage.getItem(selectionKey) || ''; }
            catch { selected = ''; }
            if (!files[selected]) selected = Object.keys(files).sort()[0] || '';
            problem();
            render();
            get('files').open = !!selected;
        },
        collect() {
            if (Object.keys(files).length || 'lesson_files' in draft) draft.lesson_files = structuredClone(files);
        },
    };
}
