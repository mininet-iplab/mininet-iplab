# Contributing to Mininet-IPLab

Thank you for your interest in contributing to Mininet-IPLab! We welcome contributions of all kinds: bug fixes, new features, documentation improvements, and examples.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally: `git clone https://github.com/YOUR_USERNAME/mininet-iplab.git`
3. **Get an environment that can run the tests** — see below. Mininet, FRR, Kea,
   Knot and Open vSwitch are not pip-installable, so this is not optional.
4. **Create a branch** for your work: `git checkout -b fix/your-fix-name` or `git checkout -b feature/your-feature-name`
5. **Make your changes** following the guidelines below
6. **Push to your fork** and **open a pull request** against the main repository

### Setting up your environment

**The dev container is the shortest path**, and the only one where every test
tier runs. Open the repository in VS Code with the Dev Containers extension and
choose *Reopen in Container*: it builds Mininet, FRR, Kea, Knot, Unbound,
ExaBGP and Open vSwitch, and bakes the dev tooling (`requirements-dev.lock`)
and Playwright's Chromium into the image. Nothing else to do.

**With Docker Compose** (the image the labs ship in) the dev tooling is baked
in too, so tests run straight away. It has no Chromium, so the browser tier
does not run there:

```bash
docker compose up -d
docker compose exec mniplab pytest -m unit
```

**On your own machine, without a container**, the unit suite will run — it is
deliberately decoupled from the system *daemons* — but the browser and
integration tiers will not. `mniplab` imports `mininet` at module level and
does not declare it as a dependency (the only Mininet on PyPI is a 2020
pre-release this project does not run against), so install the pinned ref
alongside it or even the unit suite fails at import:

```bash
MININET_REF=$(grep -oP 'ARG MININET_VERSION=\K\S+' Dockerfile)
pip install -e ".[web,dev]" "git+https://github.com/mininet/mininet@${MININET_REF}"
pytest -m unit
```

> Open vSwitch kernel modules load on the **host**, never inside a container
> (`sudo modprobe openvswitch`). See [docs/install.md](docs/install.md#host-requirements)
> for host requirements and troubleshooting.

## Code Style & Standards

### Python Code

- **Type hints**: Use type hints for all function parameters and return types (Python 3.11+, the version the lab image runs)
  ```python
  def addRouter(self, name: str, proto: Optional[str] = None, frr_dir: Optional[str] = None) -> FRRouter:
      """Router documentation."""
  ```

- **Docstrings**: All public methods and classes must have docstrings following Google style:
  ```python
  def addRouter(self, name: str, proto: Optional[str] = None) -> FRRouter:
      """Add a new FRR router to the network.
      
      Args:
          name: Router hostname (must be unique)
          proto: Routing protocol(s) as comma-separated list (default: None,
                 static routing only). Supported: OSPF, BGP, RIP, ISIS
      
      Returns:
          FRRouter instance added to network
      
      Raises:
          ValueError: If router name already exists
          RuntimeError: If FRR is not installed
      """
  ```

- **Formatting**: match the surrounding hand-wrapped style; lint with `pylint --disable=all --enable=E,F mniplab/` and type-check with `mypy`.

- **Linting**: Check code with `pylint`:
  ```bash
  pylint --disable=all --enable=E,F mniplab/
  ```

- **Type checking**: Run `mypy` for static type analysis:
  ```bash
  mypy mniplab/ --ignore-missing-imports
  ```

### Naming Conventions

- **Classes**: `PascalCase` (e.g., `FRRouter`, `mnIPLabHost`)
- **Functions/Methods**: `snake_case` (e.g., `add_router`, `add_link`)
- **Constants**: `UPPER_CASE` (e.g., `PROTO_MAP`, `DEFAULT_PORT`)
- **Private/Internal**: Prefix with `_` (e.g., `_apply_vrf_attachments`)

## Testing

The automated test suite lives under `mniplab/tests/` and is split with pytest
markers. By default, tests are fully collected; privileged integration tests run
when capabilities are present and otherwise skip automatically.

### Automated Tests

```bash
# Unit suite: fast, unprivileged, runs anywhere without Docker or root (~1 minute)
pytest -m unit

# Browser suite: frontend UI and API tests driven via Playwright (~10 minutes)
pytest -m browser
# ...or one area of it at a time
pytest -m browser_authoring   # Lab Authoring (~5.5 minutes)
pytest -m browser_services    # Service and Speaker panels, captures, NAT Tables (~2.5 minutes)
pytest -m browser_classroom   # Multi-Lab Mode, Groups, the Learner start screen (~1 minute)
pytest -m browser_workspace   # Topology, Terminals and the page's common controls (~1 minute)

# Privileged integration suite: verifies real daemons (~3-4 minutes)
python -m mniplab cleanup
pytest -m integration
python -m mniplab cleanup
```

> Every browser test carries exactly one `browser_*` area, usually as one
> `pytestmark` line per file; collection stops with an error naming any test
> that has none or two.
>
> If the privileged tests report *skipped* rather than running, ask why with
> `pytest -m integration -rs`.
> The reason names the missing capability — usually an unmet host requirement
> from [docs/install.md](docs/install.md#host-requirements).

Markers are configured in `pyproject.toml`:

- `unit`: fast tests without privileged networking dependencies
- `browser`: frontend UI and API tests driven by Playwright against an ephemeral web server
- `integration`: real Mininet/FRR/Docker lab behavior
- `requires_root`: root, Mininet, FRR, and CAP_NET_ADMIN/netlink required
- `requires_docker`: reachable Docker CLI/daemon required
- `requires_ovs`: a usable Open vSwitch, for tests that build a switch
- `requires_browser`: Playwright's Chromium must be installed (`playwright install chromium`)
- `slow`: longer-running protocol convergence or Docker scenarios

### Manual Verification

Run these **inside the dev container**, which already provides Mininet, FRR and root.
Running them on your host only works if those are installed there — see
[docs/install.md](docs/install.md#running-on-a-host-without-the-container).

```bash
# Run a specific example lab
python3 examples/static-lab.py

# Run with web UI
python3 examples/bgp-lab.py --enable-web

# Clean up between test runs
python -m mniplab cleanup
```

### What to Verify

After making changes, manually verify:

- Network connectivity (ping between nodes)
- Routing table correctness (`show ip route` in vtysh)
- FRR daemon health (daemons running and responsive)
- Web UI endpoints (if applicable)

## Documentation

### Adding to Docs

- **API changes**: Update `docs/api-reference.md` with new public methods
- **New features**: Create docs/feature-name.md with examples and use cases
- **Examples**: Add docstring at top of new example files explaining the topology and learning objectives
- **README**: Update if changes affect getting started or high-level features

### Documentation Format

- Use Markdown with clear headings
- Include code blocks with language identifier (python, bash, etc.)
- Use inline code for class names, method names, paths: `mnIPLab`, `addRouter()`, `/etc/frr`
- Include expected outputs for commands
- Add troubleshooting sections for complex features

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): subject

body

footer
```

**Types**:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `refactor`: Code refactoring without behavior change
- `test`: Adding or updating tests
- `chore`: Build, dependencies, CI/CD
- `perf`: Performance improvements

**Examples**:
```
feat(exabgp): add per-neighbor route configuration support

Allows each BGP neighbor to announce distinct route sets,
enabling realistic ISP peering scenarios with divergent policies.

Closes #42
```

Pull requests are squash-merged, so the **PR title** becomes the commit on
`main`: give it the same `type(scope): subject` form. A `Co-authored-by:` line
that credits a person who worked on the change is welcome; leave out trailers
added by tools.

```
fix(web-ui): correct session timeout handling

Terminals now properly cleanup on idle timeout.
Prevents orphaned xterm processes.
```

## Pull Request Process

1. **Update documentation** if adding features or changing APIs
2. **Add tests** for new functionality
3. **Run linting & type checks** before submitting:
   ```bash
   pylint mniplab/ --disable=all --enable=E,F
   mypy mniplab/ --ignore-missing-imports
   pytest -m unit
   ```
4. **Verify manually or with integration tests** using at least one example lab if the environment supports it:
   ```bash
   python -m mniplab cleanup
   python3 examples/static-lab.py
   python -m mniplab cleanup
   ```
5. **Fill out PR template** with:
   - Description of changes
   - Why this change is needed
   - Links to related issues
   - Test plan and verification steps
6. **Be responsive** to code review feedback

## Areas for Contribution

### High Priority

- **Web UI enhancements**: Additional visualization, better error reporting, real-time metrics
- **Documentation**: API reference, troubleshooting guide, advanced examples (VRF, multi-area OSPF)
- **Testing**: Unit tests, integration tests with more protocol combinations
- **Examples**: New lab topologies (datacenter, multi-AS, FRR-only labs without ExaBGP)

### Medium Priority

- **Performance**: Optimize config rendering, reduce startup time
- **Features**: IPv6 improvements
- **Error handling**: More specific exception types, better diagnostics
- **Shell command sanitization**: Input validation for safety

### Community

- **Issue triage**: Help categorize and prioritize bug reports
- **Answering questions**: Help users in issues
- **Feedback on examples**: Suggest improvements or new use cases
- **Blog posts/videos**: Create learning content and tutorials

## Community Standards

### Code of Conduct

We are committed to providing a welcoming and inclusive community. All community members, including maintainers, are expected to:

- **Be respectful**: Treat others with kindness and professionalism
- **Be inclusive**: Welcome contributions from people of all backgrounds and experience levels
- **Be collaborative**: Work together to solve problems and improve the project
- **Assume good intent**: Give others the benefit of the doubt in communication
- **Report issues**: Reach out to maintainers if you witness or experience violations

### Reporting Issues

- **Security issues**: Report privately through the repository's **Security** tab → *Report a vulnerability* (do NOT open a public issue). See [SECURITY.md](SECURITY.md).
- **Bug reports**: Include reproduction steps, expected vs. actual behavior, environment details
- **Feature requests**: Explain the use case and benefit
- **Questions**: Open an issue; label it as a question

## Asking for Help

- **New to Mininet?** Start with [Mininet documentation](http://mininet.org/)
- **Questions about FRR?** See [FRR docs](https://docs.frrouting.org/)
- **ExaBGP questions?** Check [ExaBGP docs](https://github.com/Exa-Networks/exabgp)
- **Confused about this project?** Open an issue or reach out on GitHub

## Maintainers

Current maintainers:
- Achmad Basuki ([@abazh](https://github.com/abazh)) — initial creator and lead
- Akbar Fikri Abdillah ([@AkbarFikri](https://github.com/AkbarFikri))

## License

All contributions are licensed under the project's existing license (see LICENSE file).

---

Thank you for contributing to Mininet-IPLab! 🚀
