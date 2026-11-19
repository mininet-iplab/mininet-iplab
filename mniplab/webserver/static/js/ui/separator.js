// A separator that resizes one part of the Lab workspace by a percentage:
// a Slot's edge or the line between the two Panes of a Slot. The
// arrangement module owns the sizes and their storage; this module owns the
// pointer, the keyboard and the ARIA report, and asks it, through callbacks,
// what the size is, what it may be and to set a new one.
//
// What the separator reports, and what an arrow key steps from, is the size
// the layout actually rendered (the callbacks read it back): a minimum in
// force, or a window too small for the saved size, can override the saved
// one, and the first key press must still show.

/**
 * A length CSS defines as a custom property, in pixels: the one place a
 * minimum size is written, read here so the layout's bounds match the CSS.
 *
 * @param {Element} element - Where the property applies (it inherits).
 * @param {string} name - The custom property, e.g. '--pane-min-centre-w'.
 * @returns {number} The length in pixels, or 0 when unset.
 */
export function cssPixels(element, name) {
    return parseFloat(getComputedStyle(element).getPropertyValue(name)) || 0;
}

export class Separator {
    /**
     * Wire a separator: pointer drags, the arrow keys and Home/End.
     *
     * @param {HTMLElement} handle - The role="separator" element.
     * @param {object} options
     * @param {'width'|'height'} options.axis - What it resizes; a 'width'
     *     separator is vertical and takes the left and right arrows.
     * @param {1|-1} options.direction - The way that grows the area: 1 when
     *     moving right or down does (the line between two Panes, the Left
     *     Slot's edge), -1 when moving left or up does (the Right and Bottom
     *     Slots' edges).
     * @param {number} options.step - One arrow key's step, in percent.
     * @param {() => ({value: number, min: number, max: number, text: string, total: number}|null)} options.read
     *     The size now as rendered, its bounds (all in percent), its words,
     *     and the pixels that 100% is; null while nothing can be measured.
     * @param {(percent: number) => void} options.write - Set the size and
     *     lay out again, without storing it.
     * @param {() => void} options.commit - Store the size, after a key or
     *     the end of a drag.
     * @param {(active: boolean) => void} [options.onDrag] - Called as a
     *     drag starts and ends.
     */
    constructor(handle, options) {
        this.handle = handle;
        this.options = { onDrag: () => {}, ...options };
        this.handle.setAttribute('aria-orientation', options.axis === 'width' ? 'vertical' : 'horizontal');
        this.setupPointer();
        this.setupKeys();
    }

    /** Report the rendered size, the bounds and the size in words. */
    describe() {
        const state = this.options.read();
        if (!state) return;
        this.handle.setAttribute('aria-valuemin', String(Math.round(state.min)));
        this.handle.setAttribute('aria-valuemax', String(Math.round(Math.max(state.min, state.max))));
        this.handle.setAttribute('aria-valuenow', String(Math.round(state.value)));
        this.handle.setAttribute('aria-valuetext', state.text);
    }

    clamp(percent, state) {
        return Math.max(state.min, Math.min(Math.max(state.min, state.max), percent));
    }

    setupPointer() {
        const handle = this.handle;
        const { axis, direction } = this.options;
        let start = null;
        handle.addEventListener('pointerdown', (e) => {
            if (e.button !== 0) return;
            const state = this.options.read();
            if (!state || !state.total) return;
            start = { x: e.clientX, y: e.clientY, value: state.value, total: state.total };
            handle.setPointerCapture(e.pointerId);
            handle.setAttribute('data-resizing', 'true');
            this.options.onDrag(true);
            e.preventDefault();
        });
        handle.addEventListener('pointermove', (e) => {
            if (!start || !handle.hasPointerCapture(e.pointerId)) return;
            const moved = axis === 'width' ? e.clientX - start.x : e.clientY - start.y;
            const state = this.options.read();
            if (!state) return;
            this.options.write(this.clamp(start.value + (direction * moved / start.total) * 100, state));
        });
        handle.addEventListener('lostpointercapture', () => {
            if (!start) return;
            start = null;
            handle.removeAttribute('data-resizing');
            this.options.commit();
            this.options.onDrag(false);
        });
    }

    setupKeys() {
        const { axis, direction, step } = this.options;
        const grow = axis === 'width'
            ? (direction > 0 ? 'ArrowRight' : 'ArrowLeft')
            : (direction > 0 ? 'ArrowDown' : 'ArrowUp');
        const shrink = {
            ArrowRight: 'ArrowLeft', ArrowLeft: 'ArrowRight', ArrowDown: 'ArrowUp', ArrowUp: 'ArrowDown',
        }[grow];
        this.handle.addEventListener('keydown', (e) => {
            const state = this.options.read();
            if (!state) return;
            const next = {
                [grow]: state.value + step,
                [shrink]: state.value - step,
                Home: state.min,
                End: state.max,
            }[e.key];
            if (next === undefined) return;
            e.preventDefault();
            this.options.onDrag(true);
            this.options.write(this.clamp(next, state));
            this.options.commit();
            this.options.onDrag(false);
        });
    }
}
