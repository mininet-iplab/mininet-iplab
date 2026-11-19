/**
 * Preparation shared by every place authored Guide Markdown is rendered.
 *
 * Both the in-app Guide panel and the standalone ``/guide`` viewer promote a
 * Guide's own title into their page ``<h1>``, so both must apply the same
 * preparation before parsing. This module is the one home for that rule; it
 * deliberately imports nothing, so the classic-script ``/guide`` page can load
 * it as a module alongside the app's own bundle.
 */

/**
 * Prepare authored Guide Markdown for parsing.
 *
 * Strips a leading zero-width/BOM character (which would otherwise stop the
 * first line being recognised as a heading) and demotes a leading top-level
 * heading to an ``<h2>``: the page chrome already owns the single ``<h1>``, so
 * rendering the Guide's own ``# Title`` would add a near-duplicate. Leading
 * blank lines are skipped (and preserved), so a Guide that opens with one is
 * still covered, and the title text survives as a sub-heading rather than being
 * dropped. Only a ``#`` at the very start of its line counts — an indented one
 * is Markdown's indented code block, not a heading.
 *
 * @param {string} markdown Authored Markdown, as fetched.
 * @returns {string} The same Markdown, ready to parse.
 */
export function normalizeGuideMarkdown(markdown) {
    return markdown
        .replace(/^[\u200B\u200C\u200D\u200E\u200F\uFEFF]/, '')
        .replace(/^((?:[ \t]*\r?\n)*)#[ \t]+/, '$1## ');
}
