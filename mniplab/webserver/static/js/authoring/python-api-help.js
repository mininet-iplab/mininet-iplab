// Common mnIPLab methods, based on the public signatures in mniplab/net.py.
// This is intentionally a small authoring aid, not a Python language server.
import { syntaxTree } from '../lib/codemirror.bundle.js';

const LAB_METHODS = [
    {
        label: 'addRouter',
        signature: 'addRouter(name, private_dirs=None, frr_dir=None, proto=None, **kwargs) → FRRouter',
        help: 'Add a router to the network with FRR routing support.',
    },
    {
        label: 'addHost',
        signature: 'addHost(name, **params) → Node',
        help: 'Add a host with shared /etc/hosts and IPv6 support.',
    },
    {
        label: 'addSwitch',
        signature: 'addSwitch(name, **params) → Node',
        help: 'Add an Open vSwitch switch to the Lab.',
    },
    {
        label: 'addLink',
        signature: 'addLink(node1, node2, port1=None, port2=None, cls=None, **params) → Link',
        help: 'Add a link between two nodes with IPv6 support.',
    },
    {
        label: 'addExaBGP',
        signature: 'addExaBGP(name, private_dirs=None, exabgp_dir=None, cls=None, **kwargs) → ExaBGPNode',
        help: 'Add an ExaBGP speaker to the network.',
    },
    {
        label: 'addNAT',
        signature: 'addNAT(name, subnet=None, **params) → Node',
        help: 'Add an Internet Gateway: a masquerade NAT to the outside world. Add its link explicitly; it is not auto-connected to a switch.',
    },
    {
        label: 'start',
        signature: 'start() → None',
        help: 'Start the Lab and configure IPv6 addresses.',
    },
    {
        label: 'stop',
        signature: 'stop() → None',
        help: 'Stop the Lab and its managed services.',
    },
];

function isLabReceiver(state, receiver, pos) {
    const before = state.sliceDoc(0, pos);
    return new RegExp(`(?:^|\\n)\\s*${receiver}\\s*=\\s*mnIPLab\\s*\\(`).test(before);
}

function isCode(state, pos) {
    for (let node = syntaxTree(state).resolveInner(pos, -1); node; node = node.parent) {
        if (node.name === 'String' || node.name === 'FormatString' || node.name === 'Comment') {
            return false;
        }
    }
    return true;
}

export function renderHelp(method) {
    const help = document.createElement('div');
    help.className = 'authoring-api-help';
    const signature = document.createElement('code');
    signature.textContent = method.signature;
    const description = document.createElement('p');
    description.textContent = method.help;
    help.append(signature, description);
    return help;
}

/** Find a documented Lab call at the caret, including within its name. */
export function labMethodAt(state, pos) {
    for (let node = syntaxTree(state).resolveInner(pos, -1); node; node = node.parent) {
        if (node.name !== 'CallExpression') continue;
        const member = node.getChild('MemberExpression');
        const receiverNode = member?.getChild('VariableName');
        const methodNode = member?.getChild('PropertyName');
        if (!receiverNode || !methodNode) continue;
        const receiver = state.sliceDoc(receiverNode.from, receiverNode.to);
        if (!isLabReceiver(state, receiver, member.from)) continue;
        const methodName = state.sliceDoc(methodNode.from, methodNode.to);
        const method = LAB_METHODS.find(entry => entry.label === methodName);
        if (method) return method;
    }
    if (!isCode(state, pos)) return null;
    const line = state.doc.lineAt(pos);
    const offset = pos - line.from;
    const calls = /([A-Za-z_]\w*)\.([A-Za-z_]\w*)/g;
    for (const match of line.text.matchAll(calls)) {
        const methodStart = match.index + match[1].length + 1;
        if (offset < methodStart || offset > match.index + match[0].length) continue;
        if (!isLabReceiver(state, match[1], line.from + match.index)) continue;
        return LAB_METHODS.find(entry => entry.label === match[2]) ?? null;
    }
    return null;
}

/** Complete common mnIPLab methods on a variable created by mnIPLab(...). */
export function labApiCompletion(context) {
    if (!isCode(context.state, context.pos)) return null;
    const match = context.matchBefore(/[A-Za-z_]\w*\.\w*$/);
    if (!match) return null;
    const [receiver, method] = match.text.split('.');
    if (!isLabReceiver(context.state, receiver, match.from)) return null;
    return {
        from: context.pos - method.length,
        options: LAB_METHODS.map(entry => ({
            label: entry.label,
            type: 'method',
            detail: 'Mininet-IPLab',
            info: () => renderHelp(entry),
        })),
        validFor: /^\w*$/,
    };
}
