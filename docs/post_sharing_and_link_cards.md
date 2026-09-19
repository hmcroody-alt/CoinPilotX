# Sharing a post, and what a post link looks like when it arrives

Two halves of one boundary. A share is a post leaving PulseSoc; a card is a post
link arriving somewhere PulseSoc still controls. They are written down together
because the same privacy rule has to hold on both sides of it, and because they
deliberately *do not* share a payload.

## What was there before

Four screens each built their own share text, and all four built the same one:

| Screen | Line |
| --- | --- |
| `components/PostCard.tsx` | 50 |
| `screens/PostDetailScreen.tsx` | 343 |
| `screens/ProfilePostViewerScreen.tsx` | 195 |
| `screens/HomeScreen.tsx` | 657 |

Each passed `post.body` into the OS share sheet with no check of `visibility`.
A followers-only caption went to whatever target the person picked — SMS,
clipboard, another app's notification shade — and stayed there. That is not a
bug that can be taken back after the fact, which is why the fix is a single
builder rather than four corrected copies: a corrected copy is one refactor away
from being wrong again.

`sharing/postShare.ts` is now the only thing that composes a post share, and the
four call sites ask it.

## The body is opt-in

`visibility` is read as an allowlist of exactly one value. A post previews its
body when the server says `"public"` and in no other case — not for an
unrecognised visibility, not for a missing field, not for a value added to the
product after the file was written.

The reasoning is the same as `links/messageLinks.ts`'s scheme allowlist: a
denylist has to be right about every value that will ever exist, and an
allowlist only has to be right about one. The failure mode of the other
direction is a private caption sitting in someone's SMS thread forever; the
failure mode of this direction is a share that is blander than it needed to be.

It costs nothing in practice. `pulse_feed_engine._public_post` emits
`item.get("visibility") or "public"`, so a genuinely public post always carries
the literal. If that ever stops being true, shares get quieter rather than
louder.

### Only the body is ever read

The preview is built from `post.body` alone. No media URL, no avatar URL, no
author id, no media row id reaches the text — not because they are filtered out
but because they are never looked up. The canonical post URL is the only
identifier that leaves, and it is the same one the web serves publicly.

The one thing that *is* filtered is a credentialed URL inside the body. A
pre-signed storage link carries `X-Amz-Signature` in its query string, and a
body containing one would otherwise hand a working credential to whoever
received the share. Presence of any credential parameter redacts the whole URL
rather than trimming it: a signed URL with its signature removed is still an
internal storage path, and publishing the bucket layout is its own small leak.

### Restricted posts still send the link

A restricted share says `"Someone shared a post with you on PulseSoc."` and
carries the canonical URL. The recipient is meant to learn that a post exists,
just not what it says; authorization happens when they open it. Title, author
and preview image are dropped alongside the body, because the share sheet hands
every field to the target the person picks and a target that renders a title and
an author picture has published exactly what the visibility setting existed to
prevent.

That is why the tests assert "the caption does not appear anywhere in the
payload" rather than "the preview field is empty". A caption leaking through
`description` while `preview` stayed blank would satisfy the second and fail the
first.

## The card

A post link inside PulseSoc Messenger renders as the post: author avatar,
display name, `@handle`, media thumbnail, short caption, and a `View Post →`
call to action. The whole card is one tap to the native post screen.

### It is derived, never stored

Nothing about the card is written into the message. There is no new
`message_type`, no preview columns, no second send path — the card is computed
from the message body on every render.

That is not a shortcut, it is the requirement. Every post link already sitting
in every conversation became a card the day this shipped, with nothing resent
and no backfill that would have had to guess at bodies it had never parsed. A
message is text; a card is a reading of that text; a reading can be improved
later where a stored snapshot is frozen at the version that wrote it.

It also means the card cannot be made to say something the link does not. The
URL is taken out of the body by the same parser that makes links tappable, and
the preview comes back from the server for that id. There is no sender-supplied
field anywhere in the card, so "the card is the destination" is true by
construction rather than by review.

### Authorization is not re-implemented — there is nothing to re-implement

A preview of a post is a partial disclosure of that post. Anything that shows an
author, a caption and a thumbnail to someone who cannot open the post has
published the thing visibility existed to protect, and has done it in a
conversation, where the person did not even ask to see it.

So there is no preview endpoint. `links/entityPreview.ts` calls `getPostDetail`,
which is the same `GET /api/pulse/posts/:id` the app calls when someone taps
through. Identical request, identical authorization. There is no second code
path that could be made more permissive than the first, because there is no
second code path.

This is the assertion worth keeping honest:

```ts
expect(feed.getPostDetail).toHaveBeenCalledWith(2432);
```

A test that only checked "a 403 produces an unavailable card" would pass just as
happily against a dedicated preview endpoint carrying its own, laxer rules.

### Unavailable is a state, not an absence

| Server says | Card says |
| --- | --- |
| 401 / 403 | This content isn't available to you. |
| 404 / 410 | This post is no longer available. |
| transport failed | Content unavailable |

An unavailable card is not tappable: sending someone to a screen that will show
them the same refusal one navigation later is a worse answer than the card
already gave. And it is still a card — dropping back to a raw URL would make the
sender's message look broken and would leave the URL as the one thing on screen,
which is the part carrying no information the viewer can use.

401 is grouped with 403 on purpose. "You are not signed in for this" and "you
are not allowed this" produce the same line, because telling them apart in a
chat bubble is a disclosure rather than a kindness.

### Failures cache differently because they mean different things

A 403 or a 404 is an *answer*. It will not change because the conversation
scrolled, so it is cached — twenty bubbles quoting one restricted post must not
be twenty 403s.

A dropped connection is not an answer. Caching it would leave a card reading
"unavailable" for the rest of the session on a post that is perfectly fine, with
no recovery short of restarting the app. Those are not cached, and they fall
back to the device's own copy of the post if `getPostDetail` had already written
one.

The offline fallback is reached *only* on a transport failure, never on a
refusal. A post cached from back when the viewer could see it must not resurface
after the author restricted it.

### Two layers of dedupe

`resolved` stops the second render from refetching. `inFlight` stops the second
*concurrent* render from starting a parallel fetch before the first has anything
to cache — which is the common case, because a conversation page mounts twenty
bubbles at once and several may quote the same post. A cache without an
in-flight table dedupes everything except the burst that actually needed it.

(The same lesson as `MediaUploadManager` — see
`docs/media/QUICK_SWALLOW_MEDIA_INGESTION.md`. A dedupe at one layer is not a
dedupe at the other.)

### One card, or none

A body naming two different posts gets no card. The card is a claim that this
message is that object, and with two candidates the claim is a guess: whichever
came first would be promoted over the other for no reason the sender chose.
Those bodies keep their inline tappable links, which is the honest rendering of
"here are two things". Two links to the *same* post is one subject, not an
ambiguity.

A body that is nothing but the link is replaced by its card, so no raw URL is
drawn. A body with prose around the link keeps the prose — that part is the
sender's.

## Why Messenger gets the URL and SMS gets the paragraph

`sharing/postShare.ts:messengerShareBody` sends the canonical URL alone into
Messenger and the composed paragraph everywhere else.

An SMS has no idea what a PulseSoc post is and needs the caption spelled out.
Messenger does know. Sending the composed text there would put a second, frozen
copy of the caption directly above a card showing the live one — and that copy
would stay frozen after an edit, a takedown or a visibility change, which is
precisely the problem with storing a preview instead of deriving one.

Copy Link is URL-only and always was.

## The routing table is asked, never copied

`links/pulseEntity.ts` calls `classifyLink`, which calls
`navigation/linking.ts` — the same table React Navigation uses for a Universal
Link from Safari and for a push notification's deep link. Only an `internal`
verdict is considered, so a path the app stops claiming stops producing cards on
the same day it stops opening natively. The card and its destination cannot
drift apart, because the card only exists when the destination does.

The patterns in `pulseEntity.ts` are therefore a *narrowing* of that table, not
a second one. Anything not already claimed over there is rejected before they
are consulted.

`https://pulsesoc.com/r/<code>` is the case that proves it matters. That is an
invite: the server user-agent-detects iOS behind it and writes the deferred
attribution row that `POST /api/mobile/referral/claim` later redeems. It is not
in the linking config, so it classifies external, opens in the browser, and gets
no card. A resolver that had matched "any pulsesoc.com URL" would have silently
broken referral attribution.

## Rules for changing this

- Visibility is an allowlist of one. Adding a value to it is a product decision
  about what may leave the app, not a bug fix.
- Assert that a secret is absent from the *whole payload*, not that one field is
  empty. The share sheet forwards every field.
- A preview must call the endpoint that opens the thing. The moment there are
  two endpoints, there are two authorization rules, and one of them is wrong.
- Never card what the routing table does not claim. Ask it; do not restate it.
- Terminal answers cache, transport failures do not. Getting this backwards
  gives you either a request storm or a card that is permanently wrong.
- The card is derived from the body. Anything that stores preview fields on a
  message loses every conversation that already exists and freezes the ones that
  do not.
- One subject or no card. A guess about which of two posts a message is about is
  worse than plain text.
