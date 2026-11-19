// The one rule both views fit a Lab by, so what an Instructor arranges is
// what Learners see: Node centres plus padding, enlarged by at most 1.5 times.

// Room around the Nodes' centres for a Node's body, name and link handle.
export const FIT_PADDING = 80;
// Fitting never enlarges past this, so a lone Node is not blown up.
export const MAX_FIT_SCALE = 1.5;
export const ZOOM_EXTENT = [0.1, 4];

/**
 * The transform that shows these Nodes, or the default view when there are none.
 *
 * @param {number} width Drawing area width in pixels.
 * @param {number} height Drawing area height in pixels.
 * @param {Array<{x: number, y: number}>} points Node centres in layout units.
 * @returns {object} A d3 zoom transform.
 */
export function fitTransform(width, height, points) {
    if (!points.length) return d3.zoomIdentity;
    const xs = points.map(point => point.x);
    const ys = points.map(point => point.y);
    const [x0, y0] = [Math.min(...xs) - FIT_PADDING, Math.min(...ys) - FIT_PADDING];
    const [x1, y1] = [Math.max(...xs) + FIT_PADDING, Math.max(...ys) + FIT_PADDING];
    const scale = Math.min(width / (x1 - x0), height / (y1 - y0), MAX_FIT_SCALE);
    return d3.zoomIdentity.translate(width / 2 - scale * (x0 + x1) / 2,
        height / 2 - scale * (y0 + y1) / 2).scale(scale);
}
