"""Metadata for a link that points off PulseSoc — fetched by us, never by the reader.

## Why the server fetches, and why that is the whole point

A preview of an external page needs somebody to load that page. If the app did
it, every reader of the message would hand their IP address, their approximate
location and their User-Agent to whatever host the *sender* chose. That turns a
pasted link into a tracking pixel: paste it into a DM and learn where the
recipient is, without them tapping anything. So the fetch happens here, once,
and the third party only ever sees PulseSoc's server.

This is the mirror image of the rule in ``mobile-native/src/links/entityPreview.ts``.
For a PulseSoc object the danger is the card disclosing *more of our data* than
the destination would, so that module refuses to have a preview endpoint at all
and reuses the destination's own loader. For an external page there is no
PulseSoc authorization to inherit — an OpenGraph tag is what the page publishes
to every crawler on the internet — and the danger runs the other way, toward
disclosing *the reader* to a stranger. Different threat, opposite remedy, which
is why this is a separate endpoint rather than an arm of that one.

## This module is the fetching layer that ``safe_media_url`` names

``services/business_os/suppliers/normalize.py:safe_media_url`` ends its docstring
by saying what it cannot do:

    A hostname that resolves to a private address at fetch time is *not* caught
    here and cannot be: this function does no DNS, deliberately, because a
    check-then-fetch would be a TOCTOU window. The fetching layer is where a
    resolved-address check belongs.

This is that layer, so the two are not duplicates of one another — one is a
*shape* check on a URL we hand to a client, this is a *fetch* check on a URL we
dial ourselves, and only the second one can resolve names. The sequence here is
resolve, inspect every answer, then dial the address that was inspected:

1. ``safe_preview_url`` refuses on shape: scheme, credentials, length, control
   characters, IP literals in any private range.
2. ``_public_addresses`` resolves the hostname and refuses if **any** answer is
   private. Not "pick a public one" — a host answering with both a public and a
   loopback address is not a host with a configuration problem, it is an attack
   in progress, and choosing the good answer would mean retrying until we got
   the bad one.
3. The fetch dials that **address**, with SNI and certificate verification
   pinned to the hostname. This is what closes the rebinding window: a second
   DNS answer cannot redirect a socket that was opened against an address we
   already inspected. It also means no ambient ``HTTP_PROXY`` can quietly
   receive the request, because a ``urllib3`` pool does not read the
   environment the way ``requests`` does.
4. Every redirect hop starts again at (1). This is the step most unfurlers
   miss: a public URL that 302s to ``http://169.254.169.254/`` has passed every
   check that was only applied to what the user typed.

## Refusals are indistinguishable from failures, on purpose

The route answers the same 404 with the same body whether the page was down,
refused us, served a PDF, or resolved to ``127.0.0.1``. A caller who can tell
those apart has a network scanner: feed it hostnames, read the difference, and
map our internal address space one refusal at a time. So the reason is recorded
here for our own logs and cache, and never travels to the client.

## A URL that looks like a credential is not fetched at all

A password-reset link, a signed download, an email confirmation — these are
one-time URLs, and *loading* one can consume it. A reader who was shown a nice
preview of a reset link may find the link already spent when they tap it. The
query-key deny list below is a blunt instrument and deliberately so: the cost of
refusing wrongly is a link that renders as plain text, which is what it did
before this feature existed.

## What this does not do yet: pictures

``image_url`` is parsed, stored, and deliberately **not** sent to the client.
Handing an external image URL to an ``<Image>`` would undo the entire privacy
argument above — the metadata would come from us and the picture would come
straight from the third party, carrying the reader's IP with it. Doing it
properly needs the image proxied through us behind a signature we mint, so the
proxy cannot be driven as an open fetcher. That is a separate change; until it
lands the card renders text, and ``preview_payload`` is the single place that
decides so.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import certifi
import urllib3

# A URL longer than this is not a link somebody typed into a sentence.
MAX_URL = 2048

# http as well as https, because a pasted `http://` link is one the host itself
# redirects to https, and refusing it means no card for a link that works
# perfectly in a browser. The redirect is followed and re-checked like any other.
ALLOWED_SCHEMES = frozenset({"http", "https"})

CONNECT_TIMEOUT = 4.0
READ_TIMEOUT = 6.0

# Metadata lives in <head>. Past this we are downloading a page body for nothing,
# and the cap is what stops a hostile host from streaming until we run out of
# memory. Measured against *decompressed* bytes, so a gzip bomb hits it too.
MAX_BYTES = 512 * 1024

MAX_REDIRECTS = 3

# How many of a hostname's addresses we will dial before giving up. More than one
# so a host with a dead A record but a live AAAA still previews; not all of them,
# because a hostname with fifty addresses is a way to spend fifty timeouts.
MAX_DIAL_ATTEMPTS = 2

TITLE_LIMIT = 120
DESCRIPTION_LIMIT = 200
SITE_NAME_LIMIT = 60

# A good answer is kept for a week: OpenGraph tags are marketing copy and change
# on the timescale of a redesign. A refusal is kept for six hours, which is long
# enough to stop a popular dead link from being retried on every render and short
# enough that a site coming back up is noticed the same day.
TTL_READY = timedelta(days=7)
TTL_UNAVAILABLE = timedelta(hours=6)

USER_AGENT = "PulseSocLinkPreview/1.0 (+https://pulsesoc.com)"

HTML_TYPES = ("text/html", "application/xhtml+xml", "application/xhtml")

# Query keys that make a URL a one-time credential rather than a page. See the
# docstring: a preview that consumes a reset token is worse than no preview.
CREDENTIAL_QUERY_KEYS = frozenset({
    "access_token", "apikey", "api_key", "auth", "authorization", "code",
    "confirmation", "credential", "id_token", "key", "nonce", "oauth_token",
    "otp", "password", "pw", "refresh_token", "reset", "reset_token",
    "secret", "session", "sig", "signature", "token", "unsubscribe", "verify",
)}

# NAT64: an IPv6 address in this prefix carries an IPv4 address in its last 32
# bits, so `64:ff9b::7f00:1` is loopback wearing a costume that `is_private`
# does not recognise. Only reachable on hosts with a NAT64 gateway, which is
# exactly the kind of environment nobody remembers they are running in.
NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


class PreviewRefused(Exception):
    """Refused, with a reason kept for our logs and never sent to a client.

    Carries a machine reason so the cache can distinguish a policy refusal from
    a transport failure when it decides how long to remember it. The *route*
    flattens every one of these into one 404; see the docstring.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class LinkPreview:
    """What a card can draw about an external page."""

    url: str
    host: str
    title: str
    description: str
    site_name: str
    image_url: str
    is_video: bool


def url_fingerprint(url: str) -> str:
    """The cache key. A hash because the column is an index, not a display field."""
    return hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()


def _clean_text(value, limit: int) -> str:
    """Flattened, de-tricked, and cut to a length a card can hold.

    Control and format characters go first, and that is a display-integrity
    check rather than tidiness: U+202E reverses the rendering of everything
    after it, so a title ending in one can make ``moc.elppa`` appear as
    ``apple.com`` inside our own chrome. Unicode category ``Cf`` is where that
    character and its relatives live.
    """
    if not isinstance(value, str):
        return ""
    stripped = "".join(
        " " if ch.isspace() else ch
        for ch in value
        if unicodedata.category(ch) not in {"Cc", "Cf"}
    )
    flat = " ".join(stripped.split())
    if len(flat) <= limit:
        return flat
    clipped = flat[:limit]
    spaced = clipped.rsplit(" ", 1)[0]
    base = spaced if len(spaced) > limit * 0.6 else clipped
    return base.rstrip(" .,;:!?-") + "…"


def _denied_address(address) -> bool:
    """Whether an address is one we refuse to dial.

    The IPv4-mapped unwrap and the NAT64 unwrap are both here because both are
    ways of writing a private IPv4 address that no IPv6 predicate answers
    ``True`` for: ``::ffff:127.0.0.1`` and ``64:ff9b::7f00:1`` are loopback, and
    ``address.is_loopback`` says no to each of them.
    """
    candidates = [address]
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        candidates.append(mapped)
    if getattr(address, "version", 4) == 6 and address in NAT64_PREFIX:
        candidates.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    for candidate in candidates:
        if (candidate.is_private or candidate.is_loopback or candidate.is_link_local
                or candidate.is_reserved or candidate.is_multicast
                or candidate.is_unspecified):
            return True
    return False


def _has_credential_query(query: str) -> bool:
    """Whether the query string looks like it carries a one-time secret."""
    if not query:
        return False
    for pair in query.split("&"):
        if not pair:
            continue
        key = pair.split("=", 1)[0].strip().lower().replace("-", "_")
        if key in CREDENTIAL_QUERY_KEYS:
            return True
    return False


def safe_preview_url(value) -> str:
    """The URL we are willing to dial, or raise :class:`PreviewRefused`.

    Shape only. Name resolution is :func:`_public_addresses`, and the split
    matters: this function is also what every redirect hop is re-checked
    against, and it has to be cheap enough to run on each one.
    """
    if not isinstance(value, str):
        raise PreviewRefused("not-a-string")
    url = value.strip()
    if not url or len(url) > MAX_URL:
        raise PreviewRefused("length")
    if any(unicodedata.category(ch) in {"Cc", "Cf"} or ch.isspace() for ch in url):
        raise PreviewRefused("control-characters")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise PreviewRefused("unparseable") from None
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise PreviewRefused("scheme")
    # The part before `@` is ignored by the server and read by humans, which is
    # what makes it a phishing primitive; it is also a way to smuggle a second
    # host past a naive parser.
    if parts.username or parts.password or "@" in parts.netloc:
        raise PreviewRefused("credentials-in-authority")
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise PreviewRefused("no-host")
    if _has_credential_query(parts.query):
        raise PreviewRefused("credential-query")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if _denied_address(address):
            raise PreviewRefused("private-address-literal")
    elif "." not in host or host.endswith((".local", ".internal", ".localhost", ".test")):
        # `https://metadata/` resolves inside many container networks and
        # nowhere on the public internet.
        raise PreviewRefused("non-public-host")
    return url


def _public_addresses(host: str, port: int) -> list[tuple[int, str]]:
    """Every address the name resolves to, refused entirely if any is private.

    Returns ``(family, address)`` pairs in the order the resolver gave them,
    which on a correctly configured host is RFC 6724 order — so the first entry
    is the one an ordinary client would have used.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise PreviewRefused("dns") from None
    except UnicodeError:
        raise PreviewRefused("dns-name") from None
    if not infos:
        raise PreviewRefused("dns-empty")
    results: list[tuple[int, str]] = []
    seen: set[str] = set()
    for family, _type, _proto, _canon, sockaddr in infos:
        literal = sockaddr[0]
        try:
            address = ipaddress.ip_address(literal)
        except ValueError:
            raise PreviewRefused("unparseable-address") from None
        if _denied_address(address):
            # Not "skip this one". See the module docstring: a mixed answer is
            # the signature of a rebinding attempt, not a misconfiguration.
            raise PreviewRefused("private-address")
        if literal not in seen:
            seen.add(literal)
            results.append((family, literal))
    return results


def _pool_for(scheme: str, address: str, port: int, host: str):
    """A connection pool dialing ``address`` while speaking for ``host``.

    ``server_hostname`` and ``assert_hostname`` are what keep this from being a
    downgrade: the socket goes to an address we have inspected, and the
    certificate is still checked against the name the URL claimed, so pinning
    the address costs nothing in transport security. Without both of these,
    dialing by IP would either fail verification or — worse — skip it.
    """
    timeout = urllib3.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT)
    if scheme == "http":
        return urllib3.HTTPConnectionPool(
            host=address, port=port, timeout=timeout, retries=False, maxsize=1,
        )
    return urllib3.HTTPSConnectionPool(
        host=address, port=port, timeout=timeout, retries=False, maxsize=1,
        server_hostname=host, assert_hostname=host,
        cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(),
    )


def _read_capped(response) -> bytes:
    """At most :data:`MAX_BYTES` of decompressed body."""
    chunks: list[bytes] = []
    total = 0
    while total < MAX_BYTES:
        chunk = response.read(amt=min(16 * 1024, MAX_BYTES - total), decode_content=True)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _charset(content_type: str, body: bytes) -> str:
    for piece in content_type.split(";")[1:]:
        key, _, value = piece.partition("=")
        if key.strip().lower() == "charset":
            candidate = value.strip().strip('"\'')
            if candidate:
                return candidate
    head = body[:2048].lower()
    marker = b"charset="
    index = head.find(marker)
    if index != -1:
        tail = head[index + len(marker):]
        candidate = bytes(bytearray(
            ch for ch in tail.split(b">")[0].strip().strip(b'"\'')
            if ch not in b' ;/"\''
        )).decode("ascii", "ignore")
        if candidate:
            return candidate
    return "utf-8"


def _fetch_document(url: str) -> tuple[str, str]:
    """``(final_url, html)`` for a page we were allowed to read.

    The redirect loop is hand-rolled rather than delegated because delegating it
    is the bug: every library that follows redirects for you follows them from
    inside a connection that was opened against a *validated* address to a
    location that nobody has validated.
    """
    current = safe_preview_url(url)
    for _hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        port = parts.port or (80 if scheme == "http" else 443)
        target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        last_error: Exception | None = None
        response = None
        for _family, address in _public_addresses(host, port)[:MAX_DIAL_ATTEMPTS]:
            pool = _pool_for(scheme, address, port, host)
            try:
                response = pool.request(
                    "GET", target, headers={
                        "Host": host if port in (80, 443) else f"{host}:{port}",
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "en",
                    },
                    redirect=False, preload_content=False,
                )
                break
            except Exception as error:  # noqa: BLE001 - every transport failure is one refusal
                last_error = error
                continue
        if response is None:
            raise PreviewRefused("unreachable") from last_error
        try:
            status = response.status
            if status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location") or ""
                if not location:
                    raise PreviewRefused("redirect-without-location")
                # Resolved against the hop we are on, so a relative Location
                # works; then re-checked from scratch by the next iteration.
                current = safe_preview_url(urljoin(current, location))
                continue
            if status != 200:
                raise PreviewRefused(f"status-{status}")
            content_type = (response.headers.get("Content-Type") or "").lower()
            if not any(content_type.startswith(kind) for kind in HTML_TYPES):
                # A PDF or an image has no OpenGraph tags, and downloading one
                # to discover that is bandwidth spent to learn nothing.
                raise PreviewRefused("not-html")
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_BYTES * 4:
                raise PreviewRefused("too-large")
            body = _read_capped(response)
        finally:
            response.release_conn()
            response.close()
        if not body:
            raise PreviewRefused("empty")
        return current, body.decode(_charset(content_type, body), "replace")
    raise PreviewRefused("too-many-redirects")


class _MetaParser(HTMLParser):
    """First-wins collection of the handful of tags a card can use.

    Lenient about placement: tags outside ``<head>`` are accepted because real
    pages put them there and the document is attacker-influenced wherever it
    sits, so being strict would cost previews without buying safety.

    ``<title>`` is tracked with an SVG guard — SVG has a ``<title>`` element of
    its own, and an inline icon at the top of a page would otherwise name the
    whole document after the icon.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self._svg_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "svg":
            self._svg_depth += 1
            return
        if tag == "title":
            if self._svg_depth == 0 and not self.title:
                self._in_title = True
            return
        if tag != "meta":
            return
        values = {key.lower(): (value or "") for key, value in attrs}
        name = (values.get("property") or values.get("name") or "").strip().lower()
        content = values.get("content") or ""
        if name and content and name not in self.meta:
            self.meta[name] = content

    def handle_endtag(self, tag):
        if tag == "svg" and self._svg_depth:
            self._svg_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def parse_preview(final_url: str, html: str) -> LinkPreview:
    """A page's own description of itself, in the fields a card draws.

    ``host`` comes from the URL rather than from ``og:site_name`` and that is a
    security property, not a preference. Everything else on this card is text a
    page author chose, so a sender who controls a page controls the title and
    the description: they can publish ``PulseSoc Security — confirm your
    password`` and have our own chrome render it. The host is the one line they
    cannot forge, because we read it off the address we actually dialed, so the
    card always carries a true statement about where a tap goes.
    """
    parser = _MetaParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup is ordinary, not exceptional
        pass
    meta = parser.meta
    host = (urlsplit(final_url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    title = _clean_text(
        meta.get("og:title") or meta.get("twitter:title") or parser.title, TITLE_LIMIT,
    )
    description = _clean_text(
        meta.get("og:description") or meta.get("twitter:description")
        or meta.get("description") or "",
        DESCRIPTION_LIMIT,
    )
    site_name = _clean_text(meta.get("og:site_name") or "", SITE_NAME_LIMIT)
    image = (meta.get("og:image") or meta.get("og:image:url")
             or meta.get("twitter:image") or "").strip()
    image_url = ""
    if image:
        try:
            absolute = urljoin(final_url, image)
            # Reusing the page check for the image URL: it is stored, and a
            # stored `file:///` or `http://10.0.0.1/` would be waiting for the
            # day somebody adds the proxy the module docstring describes.
            image_url = safe_preview_url(absolute)
        except PreviewRefused:
            image_url = ""
    og_type = (meta.get("og:type") or "").strip().lower()
    return LinkPreview(
        url=final_url,
        host=host,
        title=title,
        description=description,
        site_name=site_name,
        image_url=image_url,
        is_video=og_type.startswith("video"),
    )


def fetch_link_preview(url: str) -> LinkPreview:
    """Read a URL and describe it. Raises :class:`PreviewRefused` for every no.

    Deliberately holds no database connection. A preview is up to ten seconds of
    somebody else's network, and this process has eight pooled connections with
    a three second checkout timeout — so eight concurrent previews of one slow
    host, with a connection held across each, is the whole site stopping. The
    caller reads the cache, closes, fetches, reopens, writes.
    """
    final_url, html = _fetch_document(url)
    preview = parse_preview(final_url, html)
    if not preview.title and not preview.description:
        # A page with neither a title nor a description has told us nothing a
        # card could show, and an empty card is worse than the plain link the
        # client already renders.
        raise PreviewRefused("no-metadata")
    return preview


def preview_payload(preview: LinkPreview) -> dict:
    """The wire shape. The one place that decides what leaves the server.

    ``image_url`` is stored and not sent — see the module docstring. It is
    filtered here rather than never parsed so that the day the signed image
    proxy lands, the cache already holds the URLs and no backfill is needed.
    """
    return {
        "kind": "external",
        "url": preview.url,
        "host": preview.host,
        "title": preview.title,
        "description": preview.description,
        "site_name": preview.site_name,
        "video": bool(preview.is_video),
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


def read_cached_preview(cur, url: str):
    """``(payload_or_None, is_fresh)`` for a URL, from the cache table.

    A stale row is reported rather than hidden so the caller can serve it if the
    refetch fails: a week-old title is a better answer than no card for a page
    that was fine yesterday and is briefly down today.
    """
    cur.execute(
        "SELECT status, title, description, site_name, image_url, is_video, host, url, expires_at "
        "FROM link_previews WHERE url_hash = ?",
        (url_fingerprint(url),),
    )
    row = cur.fetchone()
    if not row:
        return None, False
    # Positional indexing throughout: a row is a sequence on SQLite and a
    # Mapping on Postgres, so iterating it would yield values in one and column
    # names in the other. See services/db.row_values.
    expires_at = row[8] or ""
    fresh = bool(expires_at) and expires_at > _iso(_now())
    if (row[0] or "") != "ready":
        return None, fresh
    preview = LinkPreview(
        url=row[7] or url,
        host=row[6] or "",
        title=row[1] or "",
        description=row[2] or "",
        site_name=row[3] or "",
        image_url=row[4] or "",
        is_video=bool(row[5]),
    )
    return preview_payload(preview), fresh


def write_cached_preview(cur, url: str, preview: LinkPreview | None, reason: str = ""):
    """Record an answer — a good one or a refusal — with its own lifetime."""
    now = _now()
    ready = preview is not None
    expires = now + (TTL_READY if ready else TTL_UNAVAILABLE)
    cur.execute("DELETE FROM link_previews WHERE url_hash = ?", (url_fingerprint(url),))
    cur.execute(
        "INSERT INTO link_previews (url_hash, url, host, status, reason, title, description, "
        "site_name, image_url, is_video, fetched_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            url_fingerprint(url),
            (preview.url if ready else url)[:MAX_URL],
            preview.host if ready else (urlsplit(url).hostname or "").lower()[:255],
            "ready" if ready else "unavailable",
            "" if ready else reason[:64],
            preview.title if ready else "",
            preview.description if ready else "",
            preview.site_name if ready else "",
            preview.image_url if ready else "",
            1 if (ready and preview.is_video) else 0,
            _iso(now),
            _iso(expires),
        ),
    )
