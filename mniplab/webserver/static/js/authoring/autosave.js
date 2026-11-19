// Autosave for the open Lab Example Draft, as a small state machine:
//   saved    — the server holds every edit
//   pending  — an edit waits for the pause after typing (or for a flush)
//   saving   — one save is in flight
//   refused  — validation refused the last attempt; the edit stays in memory
//   conflict — another Instructor saved a newer revision; nothing saves again
// Saving pauses while a Preview is starting, because the server stops a
// Preview whose draft revision moves during startup.

/** How long after the last edit a save starts. */
export const SAVE_DELAY_MS = 1000;

/**
 * Create the autosave for one page.
 *
 * @param {object} options
 * @param {() => Promise<{saved?: true, refused?: object, conflict?: true}>} options.save
 *     Validate and save the draft once. A refusal carries the reason to show.
 * @param {(state: string, refusal: (object|null)) => void} options.onChange
 *     Called whenever the state or the refusal changes.
 * @returns {object} The autosave's controls.
 */
export function createAutosave({ save, onChange }) {
    let state = 'saved';
    let refusal = null;
    let timer = null;
    const pauses = new Set();
    let editedWhileSaving = false;
    let inFlight = null;
    // A new draft starts a new generation; a save of the old one is ignored.
    let generation = 0;

    function set(next, nextRefusal = refusal) {
        state = next;
        refusal = nextRefusal;
        onChange(state, refusal);
    }

    function schedule() {
        clearTimeout(timer);
        timer = setTimeout(() => {
            timer = null;
            run();
        }, SAVE_DELAY_MS);
    }

    function run() {
        if (state === 'saving') return inFlight;
        if (pauses.size || state !== 'pending') return Promise.resolve(state);
        clearTimeout(timer);
        timer = null;
        editedWhileSaving = false;
        const started = generation;
        set('saving');
        inFlight = save().then(result => {
            if (started !== generation) return state;
            if (result.conflict) set('conflict', null);
            else if (editedWhileSaving) {
                set('pending', result.refused || null);
                schedule();
            } else if (result.refused) set('refused', result.refused);
            else set('saved', null);
            return state;
        });
        return inFlight;
    }

    return {
        /** The current state. */
        get state() {
            return state;
        },
        /** Why the last attempt was refused, or null. */
        get refusal() {
            return refusal;
        },
        /** Record an edit: save about a second after the last one. */
        edit() {
            if (state === 'conflict') return;
            if (state === 'saving') {
                editedWhileSaving = true;
                return;
            }
            // A refused edit keeps its reason on screen until the next attempt.
            if (state !== 'pending') set('pending');
            schedule();
        },
        /**
         * Save any pending edit now, and wait for saving to settle.
         *
         * @returns {Promise<string>} The state once settled.
         */
        async flush() {
            while (!pauses.size && (state === 'pending' || state === 'saving')) {
                await run();
            }
            return state;
        },
        /** Hold saves for an action or another named reason, such as sign-in. */
        pause(reason = 'action') {
            pauses.add(reason);
        },
        /** Release only this reason's hold; save once every hold has ended. */
        resume(reason = 'action') {
            pauses.delete(reason);
            if (!pauses.size && state === 'pending') run();
        },
        /** Forget an edit undone before it was saved, such as a cancelled placement. */
        revert() {
            if (state !== 'pending' || refusal) return;
            clearTimeout(timer);
            timer = null;
            set('saved', null);
        },
        /** Start over for a freshly opened draft. */
        reset() {
            generation += 1;
            clearTimeout(timer);
            timer = null;
            pauses.clear();
            editedWhileSaving = false;
            set('saved', null);
        },
    };
}
