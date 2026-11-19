// Seeded Speaker intent. Family and next hop always come from the Neighbor.

/** New generators repeat their output until the Instructor edits a seed. */
export function blankGenerator() {
    const seeds = crypto.getRandomValues(new Uint32Array(2));
    return { count: 8, sub_prefixes: 2, prefix_seed: seeds[0], as_path_seed: seeds[1] };
}

const settings = [
    ['count', 'Route count', 1, 2000],
    ['sub_prefixes', 'Sub-prefixes per prefix', 0, 16],
    ['prefix_seed', 'Prefix seed', 0, 4294967295],
    ['as_path_seed', 'AS-path seed', 0, 4294967295],
];

/** A generator's errors, shared by Neighbor rows and the Global controls. */
export function generatorErrors(generator) {
    if (!generator) return {};
    const errors = {};
    settings.forEach(([key, _label, min, max]) => {
        if (!Number.isInteger(generator[key]) || generator[key] < min || generator[key] > max) {
            errors[key] = `Enter a whole number from ${min} to ${max}.`;
        }
    });
    if (!errors.count && !errors.sub_prefixes && generator.count * (1 + generator.sub_prefixes) > 2000) {
        errors.count = 'Request at most 2000 Routes including sub-prefixes.';
    }
    return errors;
}

/** Fields inside a Neighbor card; edits keep the generator object in its owner. */
export const generatorFields = [
    { key: 'route_generator', label: 'Generate Routes', kind: 'checkbox',
        read: row => Boolean(row.route_generator),
        write: (row, checked) => { if (checked) row.route_generator = blankGenerator(); else delete row.route_generator; } },
    ...settings.map(([key, label]) => ({
        key, label, kind: 'number', required: true,
        visibleWhen: row => Boolean(row?.route_generator),
        read: row => row.route_generator?.[key],
        write: (row, value) => { row.route_generator[key] = value; },
        check: (_value, row) => generatorErrors(row.route_generator)[key] || null,
    })),
];

/** Edit the optional Global generator with native, labelled controls. */
export function createGlobalGenerator(host, hooks) {
    let owner;
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = 'speaker-generator-enabled';
    const label = document.createElement('label');
    label.className = 'authoring-choice';
    label.append(checkbox, ' Generate Global Routes');
    const fields = document.createElement('div');
    fields.className = 'row-card-grid';
    const controls = new Map();
    for (const [key, text] of settings) {
        const group = document.createElement('div');
        group.className = 'row-field';
        const input = document.createElement('input');
        input.id = `speaker-generator-${key}`;
        input.inputMode = 'numeric';
        input.autocomplete = 'off';
        input.setAttribute('aria-label', `Global ${text}`);
        input.setAttribute('aria-required', 'true');
        input.setAttribute('aria-describedby', `${input.id}-error`);
        const fieldLabel = document.createElement('label');
        fieldLabel.htmlFor = input.id;
        fieldLabel.textContent = text;
        const marker = document.createElement('span');
        marker.className = 'required-marker';
        marker.setAttribute('aria-hidden', 'true');
        marker.textContent = ' *';
        fieldLabel.append(marker);
        const error = document.createElement('p');
        error.id = `${input.id}-error`;
        error.className = 'field-error';
        error.hidden = true;
        group.append(fieldLabel, input, error);
        fields.append(group);
        controls.set(key, input);
        input.addEventListener('input', () => {
            owner.route_generator[key] = /^\d+$/.test(input.value) ? Number(input.value) : input.value;
            const errors = generatorErrors(owner.route_generator);
            controls.forEach((control, field) => {
                if (!errors[field]) hooks.clearError(control);
            });
            hooks.onChange();
        });
        input.addEventListener('blur', () => {
            const message = generatorErrors(owner.route_generator)[key];
            if (message) hooks.showError(input, message);
        });
    }
    host.replaceChildren(label, fields);
    function render() {
        checkbox.checked = Boolean(owner.route_generator);
        fields.hidden = !owner.route_generator;
        controls.forEach((control, key) => { control.value = owner.route_generator?.[key] ?? ''; });
    }
    checkbox.addEventListener('change', () => {
        if (checkbox.checked) owner.route_generator = blankGenerator();
        else delete owner.route_generator;
        render();
        hooks.onChange();
    });
    return { setOwner(node) { owner = node; render(); } };
}
