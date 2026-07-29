# Docs spine renumber — tracked step

**Date:** 2026-07-29
**Status:** Half done. Housekeeping only — no reader-facing content changes.

Two docs shared the `03-` ordinal on disk. The approved spine
(`supervoice/docs/plans/2026-07-28-docs-revamp-design.md` §4) files connectivity
as `04-connectivity-sdk` and adapters as `05-adapters`. The connectivity half
landed with that doc's rewrite, link inventory included; the adapters half is
still pending. Until it lands, the filenames on disk are authoritative and
every published link resolves.

## Renames

| From | To | Status |
|---|---|---|
| `docs/03-connectivity-sdk.md` | `docs/04-connectivity-sdk.md` | Done — landed with the rewrite, all 9 inbound references repaired |
| `docs/04-adapters.md` | `docs/05-adapters.md` | Pending |

`05-architecture.md` and `06-browser-quickstart.md` are resolved separately —
neither is in the spine.

## Link inventory (regenerated 2026-07-29)

References to renumber in the same commit as the rename. The
`03-connectivity-sdk` column is settled; the `04-adapters` column is what the
remaining half must repair.

| File | `03-connectivity-sdk` (done) | `04-adapters` (pending) |
|---|---|---|
| `README.md` | 1 | 1 |
| `docs/00-overview.md` | 3 | 2 |
| `docs/01-quickstart.md` | 1 | 1 |
| `docs/02-run-your-agent.md` | 1 | 1 |
| `docs/03-management-sdk.md` | 3 | 0 |
| `docs/04-connectivity-sdk.md` | — | 2 |
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
