"""Domain dictionary models (matches sv_domain_dictionaries response shape).

A domain dictionary is per-domain, reusable and tenant-scoped, with three
sections:

* **vocabulary** — STT. ``key`` is the term, ``value`` an optional misheard
  variant. Both halves become keyterms, so the recognizer stops guessing.
* **pronunciation** — TTS. ``key`` is the term, ``value`` its respelling.
* **fillers** — ``key`` is a language code, ``value`` its phrases (one per
  line), spoken while the agent is still thinking.

Reads are MERGED: a bundled seed (``banking``, ``real_estate``, ``hospital``)
unioned with this tenant's rows, tenant winning per key. ``seed_vocabulary`` /
``seed_pronunciation`` expose the bundled half read-only, so a caller can tell
what it inherited from what it typed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KVItem(BaseModel):
    """One dictionary row: a term and its variant/respelling/phrases."""

    model_config = ConfigDict(extra="allow")

    key: str
    value: str = ""


class _MergedSections(BaseModel):
    """The merged (seed ∪ tenant) sections both the listing and the document carry.

    Shared so ``keyterms`` has one implementation: reading a listing row and
    reading a fetched document must project the same STT terms, or the two
    answers to "what does this domain boost?" drift apart. Every section defaults
    to empty — a platform that predates the widened listing sends only the
    identifying keys, and the SDK ships ahead of deployments.
    """

    model_config = ConfigDict(extra="allow")

    #: The bundled seed this domain resolved to, or ``None`` for a custom domain
    #: with no seed behind it.
    resolved_key: str | None = None
    #: What the runtime actually uses (seed ∪ tenant).
    vocabulary: list[KVItem] = Field(default_factory=list)
    pronunciation: list[KVItem] = Field(default_factory=list)
    fillers: list[KVItem] = Field(default_factory=list)
    #: Filler knobs (enabled/selection/deferred_ms), defaults already layered in.
    settings: dict = Field(default_factory=dict)
    #: Agents attached to this dictionary — the reverse index, not the source of
    #: truth. What an agent USES is its own ``domain`` tag.
    agent_ids: list[str] = Field(default_factory=list)
    updated_by_user_id: str | None = None

    @property
    def keyterms(self) -> list[str]:
        """Every STT keyterm this dictionary contributes, deduped, in order.

        Mirrors the server's own ``to_keyterms``: a row's key AND its variant
        both boost recognition, and a variant repeated across rows is only
        boosted once.
        """
        out: list[str] = []
        seen: set[str] = set()
        for item in self.vocabulary:
            for term in (item.key, item.value):
                term = (term or "").strip()
                if term and term.lower() not in seen:
                    seen.add(term.lower())
                    out.append(term)
        return out


class DomainDictionary(_MergedSections):
    """One domain's dictionary, seed and tenant rows merged."""

    domain: str
    #: The bundled half, read-only — what this domain inherited rather than what
    #: the tenant typed. Detail reads only; a listing omits it to stay small.
    seed_vocabulary: list[KVItem] = Field(default_factory=list)
    seed_pronunciation: list[KVItem] = Field(default_factory=list)


class DomainListItem(_MergedSections):
    """One row of ``client.domain_dictionaries.list()``.

    Carries the merged content, so rendering a table of domains — with the words
    each one boosts — takes one request rather than one per domain. The read-only
    ``seed_*`` split stays on :class:`DomainDictionary`, where editing happens.
    """

    domain: str
    #: True when a bundled seed file ships under exactly this name. It is a
    #: filename check, so an ALIAS domain ("Medical", which resolves to the
    #: hospital seed) reports ``False`` while still carrying seed words —
    #: ``resolved_key`` is what names the seed behind them.
    seeded: bool = False
    updated_at: datetime | None = None


__all__ = ["DomainDictionary", "DomainListItem", "KVItem"]
