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

from pydantic import BaseModel, ConfigDict, Field


class KVItem(BaseModel):
    """One dictionary row: a term and its variant/respelling/phrases."""

    model_config = ConfigDict(extra="allow")

    key: str
    value: str = ""


class DomainDictionary(BaseModel):
    """One domain's dictionary, seed and tenant rows merged."""

    model_config = ConfigDict(extra="allow")

    domain: str
    #: The bundled seed this domain resolved to, or ``None`` for a custom domain
    #: with no seed behind it.
    resolved_key: str | None = None
    seed_vocabulary: list[KVItem] = Field(default_factory=list)
    seed_pronunciation: list[KVItem] = Field(default_factory=list)
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


class DomainListItem(BaseModel):
    """One row of ``client.domain_dictionaries.list()``."""

    model_config = ConfigDict(extra="allow")

    domain: str
    #: True when a bundled seed ships for this domain — a dictionary that works
    #: with no tenant rows at all.
    seeded: bool = False
    agent_ids: list[str] = Field(default_factory=list)


__all__ = ["DomainDictionary", "DomainListItem", "KVItem"]
