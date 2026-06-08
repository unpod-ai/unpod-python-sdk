# Contributing to the Unpod Python SDK

Thanks for your interest in improving the Unpod Python SDK. This guide covers
the basics for getting set up and submitting changes.

## Development setup

The project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
git clone https://github.com/unpod-ai/unpod-python-sdk
cd unpod-python-sdk
uv sync --extra dev
```

## Before you open a pull request

Run the same checks CI runs:

```bash
uv run ruff check .          # lint
uv run ruff format --check . # formatting
uv run pytest -q             # tests
```

- Add tests for new behavior and bug fixes.
- Keep changes focused; one logical change per pull request.
- Follow the existing code style (PEP 8, type hints, 88-char lines).
- Public APIs need docstrings.

## Reporting bugs and requesting features

Open an issue with a clear description and, for bugs, a minimal reproduction.
For anything security-related, see [SECURITY.md](SECURITY.md) instead of
opening a public issue.

## License

By contributing, you agree that your contributions are licensed under the
project's [Apache-2.0](LICENSE) license.
