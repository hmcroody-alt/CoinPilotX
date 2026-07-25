"""Localization vertical (Business OS, Stage 6).

Informational-only, deterministic **string-resolution projection** over an org's
declared locales and translated strings. An org declares *locales* (a locale code such
as ``en``, ``en-US``, ``fr``; one may be the org default; each may name an explicit
fallback); an append-only log records *strings* (a ``string_key`` has a ``value`` in some
locale). The engine computes a rebuildable per-org projection: for each active target
locale and each known key, a deterministic **resolution** of the value via a transparent
fallback chain (exact locale -> explicit fallback -> language base -> org default ->
missing), plus a per-locale coverage rollup.

Hard boundary — this vertical only *reports* which value localization *would* serve for a
key in a locale. It renders nothing, ships nothing to a client, mutates no product copy,
and takes no side effect. Wiring a resolved value into an actual rendered surface is a
separate, separately-reviewed integration.

Gated behind ``BUSINESS_OS_LOCALIZATION``. Follows the strangler pattern of the
attribution / recommendations / merchant-automation / creator-commerce / governed-UNDX
verticals: canonical ``business_os_l10n_*`` tables, append-only truth + rebuildable
projection, idempotent ingest, dark-404 gating, curated error codes. Nothing legacy is
read or written.
"""
