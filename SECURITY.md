# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in the Unpod Python SDK, please report
it privately rather than opening a public issue.

Email **security@unpod.ai** (or **parvinder@unpod.ai**) with:

- A description of the issue and its potential impact.
- Steps to reproduce, or a proof of concept.
- Any suggested remediation.

We aim to acknowledge reports within a few business days and will keep you
informed as we work on a fix. Please give us a reasonable window to address the
issue before any public disclosure.

## Supported versions

The SDK is in early development (pre-1.0). Security fixes are applied to the
latest released version on [PyPI](https://pypi.org/project/unpod/).

## Handling credentials

- Never commit API keys or `.env` files. The repository's `.gitignore` excludes
  `.env` by default.
- `UNPOD_API_KEY` and provider keys should be supplied via environment
  variables, not hard-coded in source.
