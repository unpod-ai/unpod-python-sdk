# Docs spine renumber — tracked step

**Date:** 2026-07-29
**Status:** Planned, not done. Housekeeping only — no reader-facing content changes.

Two docs share the `03-` ordinal on disk. The approved spine
(`supervoice/docs/plans/2026-07-28-docs-revamp-design.md` §4) files connectivity
as `04-connectivity-sdk` and adapters as `05-adapters`, but the rename cascades
across every inbound link, so it lands as one sweep rather than per-doc. Until
then the filenames on disk are authoritative and every published link resolves.

## Renames

| From | To |
|---|---|
| `docs/03-connectivity-sdk.md` | `docs/04-connectivity-sdk.md` |
| `docs/04-adapters.md` | `docs/05-adapters.md` |

`05-architecture.md` and `06-browser-quickstart.md` are resolved separately —
neither is in the spine.

## Link inventory (regenerated 2026-07-29)

References to renumber in the same commit as the `git mv`:

| File | `03-connectivity-sdk` | `04-adapters` |
|---|---|---|
| `README.md` | 1 | 1 |
| `docs/00-overview.md` | 3 | 2 |
| `docs/01-quickstart.md` | 1 | 1 |
| `docs/02-run-your-agent.md` | 1 | 1 |
| `docs/03-management-sdk.md` | 3 | 0 |
| `docs/03-connectivity-sdk.md` | — | 1 |
| `docs/archive/05-quickstart.md` | 1 | 0 |

Regenerate before executing:

```bash
grep -rc "03-connectivity-sdk\|04-adapters" --include="*.md" README.md docs/
```

## Verification

After the sweep, no `.md` file outside `docs/plans/` may still name the old
filenames, and the README's GitHub blob URLs must point at files that exist on
`main` — those are public links, so a stale one is a 404 for external readers.
The `docs/02-management-sdk.md` → `docs/03-management-sdk.md` rename left five
such dangling pointers behind; they were repaired in the follow-up commit that
added this file.
