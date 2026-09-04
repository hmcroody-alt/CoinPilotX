# Multi-guest Live — archive, replay and recording cost

Stages 32 and 33 of the multi-guest livestream mission. This is an audit. Nothing
in the recording path was enabled, disabled or reconfigured as a result of it.
One thing was changed, in the *publish* path, for reasons the cost section
explains.

---

## The headline finding

The mission plan assumed composite recording was a capability to investigate,
cost, and seek approval for. It is not. **Composite recording has been running in
production this whole time, and it already handles multiple publishers
correctly.** There was nothing to enable and no approval to ask for.

`services/agora_cloud_recording_service.py` starts every Live in Agora's `mix`
mode (`MODE = "mix"`, line 16). It sends no `subscribeVideoUids` and no
`subscribeAudioUids`, so the recorder subscribes to every publisher in the
channel. The transcoder is configured with `mixedVideoLayout: 1` — Agora's
best-fit layout — onto a 720×1280 canvas.

The practical consequence: when a host does a Live with three guests, the replay
shows four people tiled, not the host alone. The equal-tile behaviour is
deliberate and was documented in the source before this mission touched it.

`mixedVideoLayout: 2`, the vertical layout, is deliberately *not* used. It
requires `maxResolutionUid`, and without that the large pane renders black and
the host is relegated to a side tile. That trap is now locked by a test.

## The archive path, end to end

1. **Live starts.** `bot.py:48506` fires `pulse_live_bootstrap_recording_async`
   immediately after the session row is committed — before the client has
   published anything. This is a deliberate fix to an earlier design in which
   recording began only when the client reached `/native-publish`: an app crash
   or a dropped network call before that point produced a Live with no recording
   source and a structurally impossible replay. The provider's `maxIdleTime` of
   120 seconds bounds the cost when a host never actually joins.
2. **Fallback.** `/native-publish` still starts recording synchronously if the
   async bootstrap failed. It first probes for an existing sid and reuses it if
   the provider still knows it.
3. **Idempotency.** The sid is claimed with a guarded `UPDATE` on an empty
   `agora_recording_sid`. A duplicate start from a concurrent caller is stopped
   immediately, so one Live can never carry two provider recordings.
4. **Live ends.** The end endpoint enqueues a durable `finalize_live_replay` job.
   `media_worker.py:668` stops the Agora recording and retrieves the HLS output
   from R2 at `pulsesoc/live-recordings/{live_id}/`.
5. **Mux.** The Agora manifest's segment references are rewritten to short-lived
   SigV4 URLs and Mux fetches the packets provider-to-provider, so the recording
   is never downloaded and re-uploaded through PulseSoc.
6. **Reel.** `pulse_live_publish_replay_reel` publishes one reel to the feed and
   claims it on `pulse_live_sessions.replay_reel_id` with a guarded `UPDATE`, so
   a retried finalize job cannot put the same Live in the feed twice.

There is no separate replay table. A replay *is* a reel.

**Recording is not feature-flagged.** It runs on every Live unconditionally. If
that is not the intent for multi-guest rollout, it is a decision to make
explicitly — but it is the pre-existing behaviour, not something this mission
introduced, so it was left alone.

## Cost

Agora's published Cloud Recording pricing, per 1,000 minutes:

| Tier | Aggregate resolution | Price |
|---|---|---|
| Audio | — | $1.49 |
| HD | up to 921,600 | $5.99 |
| Full HD | 921,601 – 2,073,600 | $13.49 |
| 2K | 2,073,601 – 3,686,400 | $23.99 |
| 2K+ | 3,686,401 – 8,847,360 | $53.99 |

Every account gets 10,000 free minutes per month, shared across RTC products.

Two properties of this pricing matter here, and both are counter-intuitive.

**The recording mode does not affect the bill.** Individual and composite cost
the same. So the "composite is expensive" premise the mission was working from
was wrong in both directions: composite is not more expensive, and it is already
on.

**The number of publishers does affect the bill, through resolution.** Agora
bills on *aggregate* resolution — the sum of the resolutions of every stream the
recorder subscribes to at that moment. Our `recordingConfig` sets
`videoStreamType: 0`, so the recorder takes the **high** stream of every
publisher. Duration is not additive across streams (six publishers for ten
minutes is billed as ten minutes, not sixty), but the tier those ten minutes fall
into climbs with each person who comes on stage.

That makes the publish-side encoder ladder a billing decision, which is not where
anyone would look for one.

| Publishers | Fixed 720×1280 | Tier | On the ladder | Tier |
|---|---|---|---|---|
| 1 | 921,600 | HD | 720×1280 → 921,600 | HD |
| 2 | 1,843,200 | Full HD | 540×960 → 1,036,800 | Full HD |
| 3 | 2,764,800 | **2K** | 480×854 → 1,229,760 | Full HD |
| 4 | 3,686,400 | **2K** | 480×854 → 1,639,680 | Full HD |
| 6 | 5,529,600 | **2K+** | 360×640 → 1,382,400 | Full HD |
| 13 (max) | 11,980,800 | over ceiling | 360×640 → 2,995,200 | 2K |

A six-person Live on a fixed 720p encoder bills at $53.99 per 1,000 minutes. The
same Live on the ladder bills at $13.49 — four times cheaper, for a picture
nobody can tell apart, because at six tiles on a phone each tile is smaller than
a business card.

### The change this audit caused

`liveStreamQuality.ts` — the encoder ladder — was written and unit-tested earlier
in this mission, and **was never wired into the hook.** Both encoder call sites
in `useAgoraLiveBroadcastRoom.ts` still set a fixed 720×1280. A pure module with
passing tests and no call site is the most convincing kind of dead code: it
reviews well and does nothing.

It is now applied:

- at join, at the solo rung, so a single-host Live is encoded exactly as before;
- on promotion, starting from the stage size already in force, so a new guest's
  tile does not open at solo resolution and visibly step down a moment later;
- from `setStagePublisherCount`, **before** the audio-scenario early return. The
  audio scenario moves once, when the stage stops being solo. The ladder moves
  again at three publishers and again at five. Ordering the encoder call after
  that return would have silently pinned every stage larger than two to the
  two-publisher profile — a bug that only appears on a busy Live.

An audience member configures no encoder at all. That is a privacy property
before it is a performance one, and it is asserted at the wiring level, not only
in the pure decision.

### Recommendation

No change to the recording configuration. It is correct for multi-guest as it
stands.

Two things are worth watching once multi-guest is on real traffic:

- **Tier drift.** A Live that sustains a full stage for a long time sits in Full
  HD rather than HD. If Live minutes grow substantially, the transcoding canvas
  (currently 720×1280 at 2500 kbps) is the lever, not the recording mode.
- **The 120-second idle window.** Recording starts before the host publishes, so
  every abandoned Live costs up to two minutes billed at the audio rate. That is
  the deliberate price of never losing a replay to a client crash, and it is
  cheap, but it scales with the number of Lives started rather than completed.

## Sources

- [Cloud Recording Pricing — Agora Docs](https://docs.agora.io/en/cloud-recording/overview/pricing)
- [Cloud Recording composite mode — Agora Docs](https://docs.agora.io/en/cloud-recording/develop/composite-mode)
- `services/agora_cloud_recording_service.py`
- `bot.py` — `pulse_live_bootstrap_recording`, `pulse_live_publish_replay_reel`
- `media_worker.py` — `_process_live_replay_job`
- `tests/protection/test_live_recording_archive.py` — the guards for all of the above
