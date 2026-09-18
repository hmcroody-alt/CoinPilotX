"""The eight feature pages under /features, and the hub that lists them.

Why these are written out by hand instead of generated
------------------------------------------------------
This site already contains 108 pages produced by substituting a name into one
shared template. Measured against each other they are 99.2% and 98.9%
identical, against a 59.6% floor for two genuinely different templates, and
`services.search_visibility` now marks the whole family `noindex,follow`
because Google calls that scaled content abuse. Writing eight feature pages
from one skeleton with a noun swapped would recreate exactly the thing we just
finished removing, on the pages that matter most.

So the shape varies on purpose. Pages carry different numbers of sections,
different section kinds, and a "What it does not do" block only where there is
a real limit to state. `tests/test_feature_pages.py` measures the rendered
pairwise similarity and fails if these drift back toward a template.

Why the copy is this conservative
---------------------------------
Every claim below was read out of the running code rather than out of the
marketing, and the things we *cannot* say turned out to be the most useful
content on the page:

* There is no hashtag or @-mention system. Nothing in the post pipeline parses
  either one.
* Messages are not end-to-end encrypted. There is no cipher implementation in
  this repo; the transport is TLS and the rows are stored readable. A page that
  implied otherwise would be a security claim we cannot honour.
* Screen sharing is `not_implemented` in both the call and live paths.
* Group calls are gated on `PULSE_GROUP_CALLS_ENABLED`, which is unset in
  production and defaults to false, so calls are one-to-one today. (The audio
  and video subflags read the same way but every real call site passes
  `default=True`, so those are on.)
* Marketplace card checkout is hard-paused in
  `services/marketplace_payment_pause.py`. Cash, local pickup and in-person
  settlement are the live lanes, and they carry no platform fee.
* Profiles have no story-highlight surface.

If one of those becomes false, the fix is to change the sentence here, not to
quietly leave a stale claim in front of a search visitor.
"""

FEATURES = (
    {
        "slug": "feed",
        "card_title": "Post and follow",
        "card_summary": "Share text, photos and video, follow the people you care about, and reply in the open.",
        "breadcrumb": "Feed",
        "h1": "The feed",
        "title": "The PulseSoc feed — post, follow and reply on iPhone",
        "description": (
            "Post text, photos, video, GIFs, polls and scam reports to the PulseSoc feed, "
            "follow the accounts you want to hear from, reply in threads, and pick who can "
            "see each post. Free on iPhone."
        ),
        "lede": (
            "Six kinds of post, eleven ways to read the result, and an audience you choose "
            "one post at a time."
        ),
        "sections": [
            {
                "heading": "Six kinds of post",
                "body": [
                    "A post can be plain text, a photo, a video, a GIF, or a poll that other "
                    "people vote in.",
                    "The sixth is a scam report, which is a post type rather than a form: you "
                    "describe what you were approached with, and it lands in the feed where "
                    "other people can recognise it. It is the one piece of the crypto product "
                    "this app grew out of that made more sense as a social object than as a "
                    "tool.",
                ],
            },
            {
                "heading": "Eleven ways to read it",
                "body": [
                    "The feed is not one list. For You, Following, Friends and Communities "
                    "change who you are reading; Trending, Crypto, Scam Alerts, Arena "
                    "Highlights, Roast Clips and Questions change what about; My Posts is your "
                    "own.",
                    "Following and Friends are the honest ones — they show the accounts you "
                    "picked, and nothing else.",
                ],
            },
            {
                "heading": "Replies are threads",
                "body": [
                    "A reply can be replied to. Conversations nest rather than flattening into "
                    "one long list under the original post, so an exchange between two people "
                    "stays legible to everyone reading it later.",
                    "Posts can also be liked, reposted to your own followers, and saved for "
                    "yourself.",
                ],
            },
            {
                "heading": "Who sees a post",
                "body": [
                    "You choose per post, not once in settings: public, your followers only, "
                    "or just you. Changing your mind about one post does not change the others.",
                ],
            },
        ],
        "limits": {
            "heading": "What the feed does not do",
            "body": [
                "There are no hashtags and no @-mentions. Nothing in PulseSoc parses either "
                "one, so typing #crypto or @someone produces text and not a link. You find "
                "people and posts through search and through the topic tabs above.",
            ],
        },
        "faqs": [
            {
                "question": "Can I choose who sees each post?",
                "answer": "Yes, and separately for each one. A post can be public, visible to your followers only, or visible to just you.",
            },
            {
                "question": "Does PulseSoc use hashtags?",
                "answer": "No. There is no hashtag or @-mention system today, so a # or @ in a post stays plain text. Search and the topic tabs are how you find things.",
            },
            {
                "question": "Can I reply to a reply?",
                "answer": "Yes. Replies nest, so a conversation under a post reads as a thread rather than as one flat list.",
            },
        ],
    },
    {
        "slug": "reels",
        "card_title": "Reels",
        "card_summary": "Short vertical video with sound, in a full-screen feed.",
        "breadcrumb": "Reels",
        "h1": "Reels",
        "title": "PulseSoc Reels — record or upload short vertical video",
        "description": (
            "Record a reel in the PulseSoc camera or upload one you already made, put an "
            "audio track behind it, and publish to a full-screen vertical feed. Comments "
            "can be switched off for a single reel."
        ),
        "lede": "Vertical video, either made here or brought with you.",
        "sections": [
            {
                "heading": "Two ways in",
                "body": [
                    "You can shoot a reel inside the app, in a camera built for it rather than "
                    "the system camera, or upload something you already have in your library. "
                    "Neither route is the second-class one — an upload is a reel in exactly the "
                    "same way a recording is.",
                ],
            },
            {
                "heading": "Sound",
                "body": [
                    "A reel carries its own audio track. That can be the sound you recorded or "
                    "a track you attach to it.",
                ],
            },
            {
                "heading": "Comments, per reel",
                "body": [
                    "Comments are a setting on the individual reel, not on your account. You "
                    "can post something you want a conversation under and something you do not, "
                    "on the same day, without changing anything about your profile.",
                ],
            },
        ],
        "faqs": [
            {
                "question": "Do I have to film in the app?",
                "answer": "No. You can upload a video you already made, or record one in the app's own camera.",
            },
            {
                "question": "Can I turn off comments on a reel?",
                "answer": "Yes, on that reel alone. It is a per-reel setting, so it does not affect anything else you have posted.",
            },
        ],
    },
    {
        "slug": "live-video",
        "card_title": "Live video",
        "card_summary": "Go live from your phone, or watch and comment in someone else's stream.",
        "breadcrumb": "Live video",
        "h1": "Live video",
        "title": "Go live on PulseSoc — broadcast, guests, chat and replays",
        "description": (
            "Start a live broadcast from your iPhone, bring a guest into the stream, take "
            "questions in live chat, and leave a replay behind when you stop. Live audio "
            "rooms are a separate thing."
        ),
        "lede": (
            "Going live is a broadcast you can hand part of to someone else, and it does not "
            "disappear when you stop."
        ),
        "sections": [
            {
                "heading": "Starting one",
                "body": [
                    "You go live from the app, from the phone you are holding. There is no "
                    "encoder to configure and no stream key to paste.",
                ],
            },
            {
                "heading": "Bringing someone in",
                "body": [
                    "A broadcast can have more than one person in it. A guest joins your stream "
                    "and appears in it, rather than watching from the other side of the glass.",
                ],
            },
            {
                "heading": "Live chat",
                "body": [
                    "Viewers comment while it is happening, and you see it while it is "
                    "happening. That is usually the whole reason to go live rather than post "
                    "a video.",
                ],
            },
            {
                "heading": "What is left afterwards",
                "body": [
                    "When you stop, a replay stays behind. People who missed it can watch it, "
                    "which means a stream is worth starting even at an hour when most of the "
                    "people you want are asleep.",
                ],
            },
            {
                "heading": "Audio rooms are not this",
                "body": [
                    "If you want the conversation without the camera, that is a live audio "
                    "room, and it lives with "
                    "<a href=\"/features/groups-and-rooms\">groups and rooms</a>.",
                ],
            },
        ],
        "limits": {
            "heading": "What live does not do",
            "body": [
                "You cannot share your screen. Live video is the camera and the microphone on "
                "your phone, and there is no path today for broadcasting what is on your "
                "display instead.",
            ],
        },
        "faqs": [
            {
                "question": "Can someone else join my broadcast?",
                "answer": "Yes. A broadcast can carry more than one person, so a guest appears inside your stream rather than watching it.",
            },
            {
                "question": "Does the stream stay up after I finish?",
                "answer": "Yes. A replay is left behind when you stop, so people who missed it live can still watch.",
            },
            {
                "question": "Can I share my screen on a live stream?",
                "answer": "No. Screen sharing is not available in PulseSoc live video today.",
            },
        ],
    },
    {
        "slug": "messages",
        "card_title": "Direct messages",
        "card_summary": "One-to-one and group conversations, with photos, voice notes and files.",
        "breadcrumb": "Direct messages",
        "h1": "Direct messages",
        "title": "PulseSoc direct messages — photos, voice notes and files",
        "description": (
            "One-to-one and group conversations on PulseSoc, with photos, video, voice notes "
            "and files, read receipts, typing indicators, and the ability to edit or delete "
            "what you sent. Messages are encrypted in transit, not end-to-end."
        ),
        "lede": (
            "Conversations that carry more than text, and a plain answer about how they are "
            "protected."
        ),
        "sections": [
            {
                "heading": "One person or several",
                "body": [
                    "A conversation can be between two people or a group. It is the same "
                    "surface either way, so nothing has to be re-learned when a thread grows.",
                ],
            },
            {
                "heading": "What you can send",
                "body": [
                    "Photos, video, voice notes and files, alongside text. A voice note is "
                    "recorded in the conversation rather than attached to it.",
                ],
            },
            {
                "heading": "Knowing it arrived",
                "body": [
                    "Read receipts tell you when a message was read, and typing indicators show "
                    "when the other person is composing one.",
                ],
            },
            {
                "heading": "Changing your mind",
                "body": [
                    "You can edit a message after sending it, and you can delete one.",
                ],
            },
        ],
        "limits": {
            "heading": "How messages are protected, precisely",
            "body": [
                "PulseSoc messages are <strong>not end-to-end encrypted</strong>. They are "
                "encrypted in transit, which means the connection between your phone and our "
                "servers is protected by TLS in the same way a banking website is. It does not "
                "mean we are unable to read them.",
                "Messages are stored on our servers in a form we can access — which is what "
                "makes it possible to act on a report, restore an account, or answer a lawful "
                "request. If you need a conversation that the operator of the service "
                "mathematically cannot read, PulseSoc is not the right tool for it, and we "
                "would rather say so on this page than in a support ticket.",
            ],
        },
        "faqs": [
            {
                "question": "Are PulseSoc messages end-to-end encrypted?",
                "answer": "No. Messages are encrypted in transit with TLS, but they are stored on our servers in a form we can access. PulseSoc is not an end-to-end encrypted messenger and does not claim to be.",
            },
            {
                "question": "Can I unsend a message?",
                "answer": "You can delete a message after sending it, and you can edit one instead if you would rather correct it than remove it.",
            },
            {
                "question": "Can I send voice notes?",
                "answer": "Yes. Voice notes are recorded inside the conversation, alongside photos, video and files.",
            },
        ],
    },
    {
        "slug": "calls",
        "card_title": "Voice and video calls",
        "card_summary": "Call anyone you can message, with incoming calls that ring like a phone call.",
        "breadcrumb": "Calls",
        "h1": "Voice and video calls",
        "title": "PulseSoc calls — voice and video that ring like a phone call",
        "description": (
            "Voice and video calls between PulseSoc accounts on iPhone. An incoming call "
            "rings on the lock screen and appears in Recents through iOS CallKit, whether or "
            "not the app is open."
        ),
        "lede": (
            "The part worth explaining is not that the app has calls. It is that an incoming "
            "one behaves like a phone call rather than like a notification."
        ),
        "sections": [
            {
                "heading": "It rings, it does not buzz",
                "body": [
                    "PulseSoc calls use CallKit, the same system iOS uses for the phone itself. "
                    "An incoming call takes over the lock screen with a full-screen ringing "
                    "interface, rings through, can be answered without unlocking, and shows up "
                    "afterwards in your Recents list.",
                    "It works when the app is closed. The call arrives over a dedicated VoIP "
                    "push that wakes the app specifically to ring, which is the difference "
                    "between a call you answer and a notification you find twenty minutes "
                    "later.",
                ],
            },
            {
                "heading": "Voice or video",
                "body": [
                    "Both, and the person calling chooses which. A voice call is not a video "
                    "call with the camera switched off — it is its own thing, and it costs less "
                    "of a bad connection.",
                ],
            },
            {
                "heading": "Who you can call",
                "body": [
                    "Anyone you can <a href=\"/features/messages\">message</a>. Calls follow the "
                    "same relationship and blocking rules as conversations do, so an account you "
                    "have blocked cannot ring your phone.",
                ],
            },
        ],
        "limits": {
            "heading": "What calls do not do yet",
            "body": [
                "Calls are between two people. Group calling exists in the code but is switched "
                "off, so it is not something you can use today.",
                "There is no screen sharing on a call.",
            ],
        },
        "faqs": [
            {
                "question": "Do PulseSoc calls ring when the app is closed?",
                "answer": "Yes. Incoming calls arrive over a VoIP push and ring full-screen on the lock screen through iOS CallKit, then appear in your Recents list like any other call.",
            },
            {
                "question": "Can I make a group call?",
                "answer": "Not today. Calls are one-to-one. Group calling is built but is not switched on.",
            },
            {
                "question": "Can someone I blocked call me?",
                "answer": "No. Calls follow the same blocking rules as messages, so a blocked account cannot ring you.",
            },
        ],
    },
    {
        "slug": "groups-and-rooms",
        "card_title": "Groups and rooms",
        "card_summary": "Topic groups, plus live audio rooms for conversations that are not written down.",
        "breadcrumb": "Groups and rooms",
        "h1": "Groups and rooms",
        "title": "PulseSoc groups and live audio rooms",
        "description": (
            "Create a public or private PulseSoc group with roles and moderation controls, or "
            "open a live audio room for a conversation that is spoken rather than typed."
        ),
        "lede": "Two different shapes of group conversation, on one page because people confuse them.",
        "sections": [
            {
                "heading": "Groups are written and they persist",
                "body": [
                    "Anyone can create one. A group can be public, so people can find and join "
                    "it, or private, so they cannot.",
                    "Whoever runs it has real controls rather than the illusion of them: members "
                    "hold roles, a member can be removed, an account can be banned so it cannot "
                    "come back, and the whole group can be archived when the thing it was for "
                    "is over — or deleted, if archiving is not enough.",
                ],
            },
            {
                "heading": "Rooms are spoken and they end",
                "body": [
                    "A live audio room is a conversation happening now, with voices instead of "
                    "typing. There is no video and no thread to scroll back through afterwards. "
                    "It is the closest thing in the app to standing in a group and talking, and "
                    "the fact that nothing is left behind is the point rather than a limitation.",
                    "If you want the camera as well, that is a "
                    "<a href=\"/features/live-video\">live broadcast</a>, which works differently.",
                ],
            },
        ],
        "faqs": [
            {
                "question": "Can I make a private group?",
                "answer": "Yes. A group can be public, so that people can find and join it, or private.",
            },
            {
                "question": "Can I remove someone from a group I run?",
                "answer": "Yes. You can remove a member, and you can ban an account so it cannot rejoin.",
            },
            {
                "question": "What is the difference between a group and a live audio room?",
                "answer": "A group is written and stays. A live audio room is spoken, happens once, and leaves no thread behind.",
            },
        ],
    },
    {
        "slug": "creator-profiles",
        "card_title": "Creator profiles",
        "card_summary": "A profile page that collects your posts, reels and links in one place.",
        "breadcrumb": "Creator profiles",
        "h1": "Creator profiles",
        "title": "PulseSoc creator profiles — one page for your posts and media",
        "description": (
            "A PulseSoc profile carries your avatar, cover image, bio and follower counts, and "
            "sorts everything you have posted into Posts, Media and About. Profiles can be "
            "public or private."
        ),
        "lede": "The page someone lands on when they decide whether to follow you.",
        "sections": [
            {
                "heading": "What it shows",
                "body": [
                    "An avatar, a cover image behind it, a bio, and counts for followers, "
                    "following, posts and media. The counts are the real ones.",
                ],
            },
            {
                "heading": "Three tabs, not one wall",
                "body": [
                    "Posts is everything you have written. Media is only the photos and video, "
                    "which is what most people are actually scrolling for. About is the longer "
                    "version of who you are.",
                    "Splitting them matters more than it sounds: a visitor deciding whether to "
                    "follow you is usually looking for one of those three specifically, and "
                    "making them scroll past the other two is how you lose them.",
                ],
            },
            {
                "heading": "Verified accounts",
                "body": [
                    "Some accounts carry a verified badge. It is displayed on the profile and "
                    "alongside the account elsewhere in the app.",
                ],
            },
            {
                "heading": "Public or private",
                "body": [
                    "A profile can be public or private, and that is separate from the audience "
                    "you set on any individual <a href=\"/features/feed\">post</a>.",
                ],
            },
        ],
        "limits": {
            "heading": "What a profile does not have",
            "body": [
                "There are no story highlights. PulseSoc has no stories surface, so there is "
                "nothing to pin a row of them to at the top of a profile.",
            ],
        },
        "faqs": [
            {
                "question": "Can I make my profile private?",
                "answer": "Yes. A profile can be public or private, separately from the audience you choose for any individual post.",
            },
            {
                "question": "Does a profile have story highlights?",
                "answer": "No. PulseSoc does not have stories, so profiles have no highlights row.",
            },
        ],
    },
    {
        "slug": "marketplace",
        "card_title": "Marketplace",
        "card_summary": "List something for sale and settle it in cash or on pickup. Card payments are paused.",
        "breadcrumb": "Marketplace",
        "h1": "Marketplace",
        "title": "PulseSoc Marketplace — list and sell from the app",
        "description": (
            "List an item in the PulseSoc Marketplace and arrange payment face to face. Card "
            "checkout is temporarily unavailable; cash, local pickup and in-person settlement "
            "are the live options and carry no platform fee."
        ),
        "lede": (
            "You can list and sell today. You cannot take a card for it today, and this page "
            "would rather tell you that than let you find out at checkout."
        ),
        "sections": [
            {
                "heading": "Listing something",
                "body": [
                    "A listing is created from inside the app. Selling means registering as a "
                    "seller first, which is a real step rather than a checkbox — it is what "
                    "keeps a marketplace from being a place where anyone can post anything and "
                    "vanish.",
                ],
            },
            {
                "heading": "How a sale settles right now",
                "body": [
                    "Card payments in the Marketplace are temporarily switched off. A buyer "
                    "cannot start a card checkout on a listing, and no card flow will begin and "
                    "then fail — the option is simply not offered.",
                    "What works is cash, local pickup, and payment in person. Those settlements "
                    "carry no platform fee, because the platform is not moving the money.",
                    "This is a pause on Marketplace card checkout specifically. It is unrelated "
                    "to PulseSoc Premium, which is billed through your Apple ID and is "
                    "unaffected.",
                ],
            },
        ],
        "faqs": [
            {
                "question": "Can I pay for a Marketplace item by card?",
                "answer": "Not at the moment. Card checkout in the Marketplace is temporarily unavailable. Cash, local pickup and in-person payment are the available options.",
            },
            {
                "question": "Is there a fee on a Marketplace sale?",
                "answer": "Cash, pickup and in-person settlements carry no platform fee.",
            },
            {
                "question": "Do I need to do anything before I can sell?",
                "answer": "Yes. You register as a seller before you can list something for sale.",
            },
        ],
    },
)


BY_SLUG = {feature["slug"]: feature for feature in FEATURES}

HUB_PATH = "/features"


def canonical_path(feature):
    return f"{HUB_PATH}/{feature['slug']}"


def all_paths():
    """Every path this module is responsible for, hub first."""

    return [HUB_PATH] + [canonical_path(feature) for feature in FEATURES]


def hub_page(canonical_url):
    return {
        "canonical": canonical_url(HUB_PATH),
        "breadcrumb": "Features",
        "h1": "What PulseSoc does",
        "title": "PulseSoc features — feed, reels, live video, messages, calls and marketplace",
        "description": (
            "Every part of the PulseSoc iPhone app, described one page at a time: the feed, "
            "reels, live video, direct messages, voice and video calls, groups and audio "
            "rooms, creator profiles and the marketplace."
        ),
        "lede": (
            "Eight things the app does, each with its own page — including the parts that are "
            "switched off or that work differently from what the name suggests."
        ),
        "features": [feature_card(feature, canonical_url) for feature in FEATURES],
    }


def feature_card(feature, canonical_url):
    return {
        "slug": feature["slug"],
        "card_title": feature["card_title"],
        "card_summary": feature["card_summary"],
        "canonical_path": canonical_path(feature),
        "canonical": canonical_url(canonical_path(feature)),
    }


def cards(canonical_url):
    """The eight cards, for the hub and for /app."""

    return [feature_card(feature, canonical_url) for feature in FEATURES]


def detail_page(slug, canonical_url):
    """The render context for one feature page, or None if the slug is not ours."""

    feature = BY_SLUG.get(slug)
    if not feature:
        return None
    page = {
        "canonical": canonical_url(canonical_path(feature)),
        "breadcrumb": feature["breadcrumb"],
        "title": feature["title"],
        "description": feature["description"],
        "h1": feature["h1"],
        "lede": feature["lede"],
        "sections": feature["sections"],
        "limits": feature.get("limits"),
        "faqs": feature["faqs"],
    }
    return page


def siblings(slug, canonical_url):
    """The other seven, so every feature page links to every other one.

    A hub-and-spoke with no spoke-to-spoke edges makes the hub the only way
    between two sibling pages, which is both worse for a reader and a weaker
    internal link graph than it needs to be.
    """

    return [
        feature_card(feature, canonical_url)
        for feature in FEATURES
        if feature["slug"] != slug
    ]
