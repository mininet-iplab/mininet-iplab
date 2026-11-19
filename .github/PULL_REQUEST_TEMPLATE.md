<!--
Thanks for contributing to Mininet-IPLab. CONTRIBUTING.md has the coding
standards, the test tiers, and how to get an environment that can run them.
-->

## What this changes

<!-- One or two sentences. What is different after this PR? -->

## Why

<!-- The problem or constraint behind it, not a restatement of the diff. -->

## Related issues

<!-- e.g. Closes #42. Delete this section if there are none. -->

## How it was tested

<!--
Tick what you actually ran. Not every tier is runnable everywhere: the
integration tier needs a privileged environment, and the browser tier needs
Playwright's Chromium. Say so rather than leaving a box ticked you did not run.
-->

- [ ] `pytest -m unit`
- [ ] `pytest -m browser`
- [ ] `pytest -m integration`
- [ ] Ran at least one Lab Example end to end (say which):
- [ ] Not runnable in my environment (say which tier and why):

## Checklist

- [ ] Type hints and Google-style docstrings on new public methods and classes
- [ ] `pylint mniplab/ --disable=all --enable=E,F` and `mypy` are clean
- [ ] Documentation updated if this changes an API, a setting, or getting started
- [ ] Web UI changes meet WCAG 2.2 AA (semantic HTML, `aria` attributes, keyboard operable)
- [ ] Commit messages follow Conventional Commits
