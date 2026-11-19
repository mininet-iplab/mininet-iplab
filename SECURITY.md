# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | ✅ Yes    |

Earlier versions are not supported. Mininet-IPLab is alpha software for
classroom and lab use; fixes land on `main` rather than as backports.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Report privately through this repository's **Security** tab →
**Report a vulnerability**. That opens a private advisory visible only to the
maintainers. Include:

- A description of the vulnerability and its potential impact
- Steps to reproduce the issue
- Affected versions
- Any suggested fix (optional)

We aim to acknowledge receipt within **72 hours** and provide a status update within **7 days**.

If a fix is needed, we will coordinate a disclosure timeline with you before publishing details publicly.

## Important Notes

**Educational & lab use:** Mininet-IPLab is designed for teaching and testing IP routing in isolated lab environments. Root privileges are required to create Linux network namespaces and manage kernel routing tables — this is a Mininet architectural requirement, not a misconfiguration.

**Web UI:** With `--enable-web` or `python -m mniplab serve`, set your own logins (`MNIPLAB_PASSWORD`, or `MNIPLAB_USERS` for Instructor and Learner logins) before sharing network access; without them the server generates a new password at each start. See [README.md](README.md#web-ui-security) and [docs/web-ui.md](docs/web-ui.md) for configuration details.

**Learners are trusted:** A Web UI Terminal is a root shell on the lab machine. Node isolation (network, PID and UTS namespaces) is there so exercises run as separate copies, not to contain a Learner: anyone with a Terminal can read `.env` and `accounts.json`, every password included. Run a class only with Learners you would trust with the machine.

**Deployment:** For production-like environments, isolate the lab (dedicated VM, container, or secured host) and restrict network access to the web UI port. The Docker image and devcontainers are configured for this purpose.
