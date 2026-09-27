"""PulseDrop — PulseSoc's autonomous commerce curator.

@pulsedrop is an ordinary ``users`` row that nobody can log into, publishing
ordinary Signals and Reels about ordinary marketplace listings through the
ordinary pipelines. Nothing in this package forks a PulseSoc system; each module
composes one that already exists and adds only the part that is genuinely new —
the decision about *what* to publish and *whether* to publish anything at all.

Read the modules in this order; each one's docstring argues for its own
existence, and several argue against doing the obvious thing:

    config        every tunable, resolved at call time so a kill switch works
    schema        PulseDrop's five tables, created once per process
    account       the @pulsedrop row, its badge, its brand assets
    eligibility   may this product be promoted (stricter than "may it be sold")
    editorial     what PulseDrop is allowed to say, and why DEAL does not exist
    ranking       a transparent linear scorer, versioned for its replacement
    diversity     cooldowns, pacing and the seller-share rule that self-disables
    lease         one tick at a time across every instance, via one UPDATE
    distribution  Signal, Reel, both or neither — and why "both" is rare
    publisher     claim first, revalidate, then post; the idempotency mechanism
    reel_composer 9:16 video, and the argument for putting no text in it
    curator       the tick that binds all of the above, and the run log
    hydration     price, stock and CTA, read live — the other half of the above

``curator.worker_cycle`` is the entry point a background process should call;
everything else is either called by it or exists to be read.

Nothing here imports the rest at module scope except through ``services.*``, so
importing this package does not connect to a database.
"""

from services.pulsedrop import (  # noqa: F401
    account,
    config,
    curator,
    distribution,
    diversity,
    editorial,
    eligibility,
    hydration,
    lease,
    publisher,
    ranking,
    reel_composer,
    schema,
)
