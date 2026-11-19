// The server supplies the single ordered list; grouping preserves Catalog order.
export function groupByArea(examples, areas) {
    return [...areas, 'Other'].map(area => ({
        area,
        examples: examples.filter(example => (example.area || 'Other') === area),
    })).filter(group => group.examples.length);
}
