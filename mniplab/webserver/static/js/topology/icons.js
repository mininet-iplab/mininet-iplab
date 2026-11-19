// Node icon SVG path definitions used by the D3 renderer.
// Simple icons have 24x24 viewbox and use theme color.

// The Speaker's solid cloud.
const cloud = 'M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z';

// The Internet Gateway: the same cloud with a globe's meridian ring and equator
// knocked out, so its shape alone differs from the Speaker's.
const globeCloud = 'M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM8.4 12A3.6 5.8 0 1 1 15.6 12A3.6 5.8 0 1 1 8.4 12ZM9.4 12A2.6 4.8 0 1 0 14.6 12A2.6 4.8 0 1 0 9.4 12ZM1.5 11.5L22.5 11.5L22.5 12.5L1.5 12.5Z';

export const nodeIcons = {
    router: 'M 12,0.0 A 12.0,12.0 0 1,1 12,24.0 A 12.0,12.0 0 1,1 12,0.0 Z M 12,2.0 A 10.0,10.0 0 1,0 12,22.0 A 10.0,10.0 0 1,0 12,2.0 Z M 12.13,9.87 L 10.07,7.8 L 11.41,6.46 L 5.4,5.4 L 6.46,11.41 L 7.8,10.07 L 9.87,12.13 Z M 11.87,14.13 L 13.93,16.2 L 12.59,17.54 L 18.6,18.6 L 17.54,12.59 L 16.2,13.93 L 14.13,11.87 Z M 17.47,4.27 L 15.4,6.33 L 14.06,4.99 L 13,11 L 19.01,9.94 L 17.67,8.6 L 19.73,6.53 Z M 6.53,19.73 L 8.6,17.67 L 9.94,19.01 L 11,13 L 4.99,14.06 L 6.33,15.4 L 4.27,17.47 Z',
    switch: 'M 12,0.0 A 12.0,12.0 0 1,1 12,24.0 A 12.0,12.0 0 1,1 12,0.0 Z M 12,2.0 A 10.0,10.0 0 1,0 12,22.0 A 10.0,10.0 0 1,0 12,2.0 Z M 10.5,6.4 L 8.5,6.4 L 8.5,4.5 L 3.5,8 L 8.5,11.5 L 8.5,9.6 L 10.5,9.6 Z M 13.5,9.6 L 15.5,9.6 L 15.5,11.5 L 20.5,8 L 15.5,4.5 L 15.5,6.4 L 13.5,6.4 Z M 10.5,14.4 L 8.5,14.4 L 8.5,12.5 L 3.5,16 L 8.5,19.5 L 8.5,17.6 L 10.5,17.6 Z M 13.5,17.6 L 15.5,17.6 L 15.5,19.5 L 20.5,16 L 15.5,12.5 L 15.5,14.4 L 13.5,14.4 Z',
    container: 'M 13.03,11.32 L 10.76,11.32 L 10.76,9.28 L 13.03,9.28 L 13.03,11.32 M 13.03,4.3 L 10.76,4.3 L 10.76,6.39 L 13.03,6.39 L 13.03,4.3 M 15.72,9.28 L 13.44,9.28 L 13.44,11.32 L 15.72,11.32 L 15.72,9.28 L 15.72,9.28 M 10.34,6.8 L 8.07,6.8 L 8.07,8.86 L 10.34,8.86 L 10.34,6.8 M 13.03,6.8 L 10.76,6.8 L 10.76,8.86 L 13.03,8.86 L 13.03,6.8 M 22.54,10.23 C 22.05,9.9 20.91,9.78 20.03,9.95 C 19.92,9.12 19.46,8.4 18.62,7.76 L 18.14,7.44 L 17.82,7.92 C 17.18,8.87 17.01,10.45 17.69,11.49 C 17.39,11.65 16.8,11.87 16.03,11.85 L 1.08,11.85 C 0.78,13.6 1.28,15.87 2.6,17.43 C 3.87,18.93 5.78,19.7 8.28,19.7 C 13.69,19.7 17.7,17.21 19.57,12.68 C 20.31,12.7 21.89,12.69 22.71,11.13 C 22.76,11.04 22.93,10.67 23.0,10.54 L 22.54,10.23 L 22.54,10.23 M 4.97,9.28 L 2.71,9.28 L 2.71,11.32 L 4.97,11.32 L 4.97,9.28 M 7.66,9.28 L 5.39,9.28 L 5.39,11.32 L 7.66,11.32 L 7.66,9.28 M 10.34,9.28 L 8.07,9.28 L 8.07,11.32 L 10.34,11.32 L 10.34,9.28 M 7.66,6.8 L 5.39,6.8 L 5.39,8.86 L 7.66,8.86 L 7.66,6.8',
    host: 'M20,18c1.1,0,2-0.9,2-2V6c0-1.1-0.9-2-2-2H4C2.9,4,2,4.9,2,6v10c0,1.1,0.9,2,2,2H0v2h24v-2H20z M4,6h16v10H4V6z',
    'bgp-speaker': cloud,
    // The Internet Gateway and its Transit form, both drawn as the Internet.
    internet: globeCloud,
    nat: globeCloud,
    transit: globeCloud,
};

// Theme colour for a Node type, shared by the Lab and authoring canvases.
export function getNodeColor(type) {
    const style = getComputedStyle(document.documentElement);
    const colorMap = {
        'router': style.getPropertyValue('--router-color').trim(),
        'host': style.getPropertyValue('--host-color').trim(),
        'container': style.getPropertyValue('--container-color').trim() || style.getPropertyValue('--host-color').trim(),
        'switch': style.getPropertyValue('--switch-color').trim(),
        'bgp-speaker': style.getPropertyValue('--bgp-speaker-color').trim(),
        'nat': style.getPropertyValue('--nat-color').trim()
    };
    colorMap.speaker = colorMap['bgp-speaker'];
    colorMap.transit = colorMap.nat;
    colorMap.internet = colorMap.nat;
    return colorMap[type] || '#999';
}
