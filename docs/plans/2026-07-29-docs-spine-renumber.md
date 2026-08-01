# Docs spine renumber — tracked step

**Date:** 2026-07-29
**Status:** Done for the spine. Housekeeping only — no reader-facing content
changes came from the renames themselves.

Two docs shared the `03-` ordinal on disk. The approved spine
(`supervoice/docs/plans/2026-07-28-docs-revamp-design.md` §4) files connectivity
as `04-connectivity-sdk` and adapters as `05-adapters`. Both halves have now
landed, each with its doc's rewrite and its link inventory. The filenames on
disk are authoritative and every published link resolves.

## Renames

| From | To | Status |
|---|---|---|
| `docs/03-connectivity-sdk.md` | `docs/04-connectivity-sdk.md` | Done — landed with the rewrite, all 9 inbound references repaired |
| `docs/04-adapters.md` | `docs/05-adapters.md` | Done — landed with the rewrite, all 7 inbound references repaired |

`05-architecture.md` and `06-browser-quickstart.md` were resolved separately —
neither was in the spine.

**`05-architecture.md`: archived 2026-07-30, not renumbered.** It was the
original `01-architecture.md` from the v0.1.0a1 release, renumbered into the
`05` collision by this plan's predecessor commit. Every section of it now has
a more accurate home: package structure and design principles in
`00-overview.md` § Package scope, data flow in § How a call reaches your code
plus the live-verified `01-quickstart.md`, concurrency and multi-replica in
`04-connectivity-sdk.md` and `02-run-your-agent.md`, and the supervoice
relationship table in § What Unpod owns vs what you own. Its one genuinely
unique half — the frame-level dispatch and bridge tables — is supervoice's to
own (`docs/api/bridge-protocol-v2.md` is a fuller spec of the same wire
format, and per the revamp design §5 `docs/04-connectivity.md` is the
canonical cross-repo story the SDK links to rather than retells). Renumbering
it to `08-` would have committed the SDK spine to maintaining a second copy of
an internal protocol that had already drifted five ways. It lives at
`docs/archive/05-architecture.md` under a banner naming those five.

The spine is now contiguous with no collisions: `00-overview`,
`01-quickstart`, `02-run-your-agent`, `03-management-sdk`,
`04-connectivity-sdk`, `05-adapters`, `06-deployment`,
`07-browser-quickstart`.

## Link inventory (regenerated 2026-07-29)

References renumbered in the same commit as each rename. Both columns are now
settled.

| File | `03-connectivity-sdk` (done) | `04-adapters` (done) |
|---|---|---|
| `README.md` | 1 | 1 |
| `docs/00-overview.md` | 3 | 2 |
| `docs/01-quickstart.md` | 1 | 1 |
| `docs/02-run-your-agent.md` | 1 | 1 |
| `docs/03-management-sdk.md` | 3 | 0 |
| `docs/04-connectivity-sdk.md` | — | 2 |
| `docs/archive/05-quickstart.md` | 1 | 0 |

Regenerate to confirm (expect no hits outside this file):

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
