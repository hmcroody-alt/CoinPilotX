"""The render queue, and the one real encode that proves it produces a Reel.

Why this suite encodes for real
-------------------------------
Almost everything here is queue mechanics and could be tested against a stubbed
renderer. One test is not: the composition itself. ``_render_from_images``
builds a single ffmpeg filter graph roughly forty terms long — scale, pad,
overlay, zoompan, xfade, drawtext, format — and the failure mode of a wrong term
is not an exception. It is a video that encodes cleanly and looks wrong: a
stretched product, a title off the bottom of the frame, a 9:16 output that is
secretly 16:9 letterboxed. Nothing short of running ffmpeg and measuring the
result can see that, so this suite runs it once and measures the result.

The cost is bounded deliberately: one encode, of a two-second target, from three
generated stills. Everything else — enqueue, claim, retry, settle — runs against
the database with no ffmpeg at all.

Why the fixtures are generated rather than committed
----------------------------------------------------
The throwaway harness this was ported from read PNGs out of ``/tmp``. Committing
them instead would put ~800KB of binary into the repository to assert something
fully described by three pairs of numbers: a square, a wide and a tall image. So
the suite draws them with ffmpeg at session scope.

They are drawn as *flat* colour, which is what makes
``test_never_distorts_the_product`` possible. ``_fit_filter`` puts the product
over a blurred, dimmed copy of itself; blurring a flat fill leaves it flat, so
the finished frame is exactly two luminances — the bright fitted product and the
dim backdrop — and the product's bounding box can be measured off the pixels
with a threshold. That measurement is the one that matters in this whole
subsystem: the brief's hard rule is that a product is never stretched, the
filter graph's guarantee is one ``force_original_aspect_ratio=decrease`` buried
forty terms in, and a graph that loses it still encodes a clean, playable,
correctly-sized video of a squashed product.

Why the storage root is redirected and the uploader is not stubbed
------------------------------------------------------------------
``_publish`` has two branches: object storage when a provider is configured, and
a local copy under ``media_storage.PUBLIC_UPLOAD_ROOT`` otherwise. The local
branch is the one a developer and CI take, and it writes into the *repository's*
``static/uploads``. Pointing the root at a temp directory keeps the real function
under test — including the ``/static/uploads/...`` URL it returns, which is what
the app later serves — while keeping the working tree clean.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from services.pulsedrop import config, distribution, reel_composer

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg is a deploy dependency; absent on this machine"
)

LISTING_ID = 77
BROKEN_ID = 78

#: Three aspect ratios, because the composer's job is to make all of them 9:16
#: without distorting any of them. Taller than the frame, wider than the frame,
#: and square — the three cases the fit has to get right.
SHAPES = {"sq.png": (800, 800), "wide.png": (1600, 900), "tall.png": (900, 1600)}

#: Bright enough that the backdrop's ``brightness=-0.18`` puts the two well
#: apart, and saturated enough that its ``saturation=0.85`` does not close the
#: gap. See ``_foreground_box``.
FILL = "0x30b36a"

_BASE = "https://example.com/"


def _listing(listing_id=LISTING_ID, names=("sq.png", "wide.png", "tall.png")):
    """A listing shaped the way ``eligibility.image_urls`` actually reads one.

    Deliberately not a convenient ``image_urls`` list. ``image_urls`` is left
    unstubbed here, so the cover column and the ``marketplace_product_media``
    rows are named exactly as production names them — and the cover leads,
    which is the order the composition depends on. A fixture that handed the
    composer a pre-made list would pass even if the two stopped agreeing about
    where a product's photographs live.
    """
    cover, *rest = names
    return {
        "id": listing_id,
        "seller_user_id": 1,
        "title": "Test Product",
        "category": "gadgets",
        "price_label": "$19.99",
        "currency": "USD",
        "cover_image_url": _BASE + cover,
        "media_rows": [
            {"media_type": "image", "media_url": _BASE + name} for name in rest
        ],
    }


LISTING = _listing()


@pytest.fixture(scope="session")
def stills(tmp_path_factory):
    """One PNG per shape, drawn once for the whole session."""
    directory = tmp_path_factory.mktemp("pulsedrop-stills")
    for name, (width, height) in SHAPES.items():
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-y", "-f", "lavfi",
                "-i", f"color=c={FILL}:s={width}x{height}",
                "-frames:v", "1", str(directory / name),
            ],
            check=True, capture_output=True,
        )
    return directory


@pytest.fixture(autouse=True)
def _empty_renders():
    from services import db as platform_db

    conn = platform_db.connect()
    try:
        conn.execute("DELETE FROM pulsedrop_renders")
        conn.commit()
    finally:
        conn.close()
    config.invalidate_cache()
    yield
    config.invalidate_cache()


@pytest.fixture()
def composer(monkeypatch, stills, tmp_path):
    """Wire the composer to local fixtures and a temp storage root.

    ``_download`` is the only part of the pipeline replaced. It refuses non-https
    sources by design, and a test that reached the network would be a test of
    someone else's uptime; everything below it is the real code.
    """
    from services import media_storage

    def download(url, target):
        name = url.rsplit("/", 1)[-1]
        if name not in SHAPES:
            return False
        shutil.copyfile(stills / name, target)
        return True

    monkeypatch.setattr(reel_composer, "_download", download)
    monkeypatch.setattr(
        reel_composer,
        "_load_listing",
        lambda listing_id: dict(LISTING) if int(listing_id) == LISTING_ID else {},
    )
    monkeypatch.setenv("PULSEDROP_REELS_ENABLED", "1")
    # Two seconds, not the production length: the assertions are about geometry
    # and state, and every extra second is spent in the encoder.
    monkeypatch.setenv("PULSEDROP_REEL_TARGET_SECONDS", "2")
    config.invalidate_cache()

    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(media_storage, "PUBLIC_UPLOAD_ROOT", root)
    monkeypatch.setattr(media_storage, "provider", lambda: "local")
    return root


def _probe(path: Path) -> dict:
    """Width, height and duration of a file, straight from ffprobe."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "default=noprint_wrappers=1", str(path),
        ],
        check=True, capture_output=True, text=True,
    ).stdout
    return dict(
        line.split("=", 1) for line in out.strip().splitlines() if "=" in line
    )


#: Sampling grid for ``_foreground_box``. Small on purpose: the question is the
#: shape of a region hundreds of pixels across, and a 1080x1920 scan would be
#: two megabytes of Python for no extra resolution.
_GRID = (216, 384)


def _foreground_box(video: Path) -> tuple[int, int]:
    """Width and height, in grid cells, of the fitted product in frame one.

    Frame one is the unzoomed one — ``zoompan`` starts at z=1.0 — so what is
    measured is the fit, with the zoom's own scaling kept out of the answer.

    The frame is pulled as raw 8-bit grayscale so nothing but the standard
    library has to parse it. Two luminances are present: the product at full
    brightness and the backdrop after ``brightness=-0.18``, roughly 130 against
    85, so the midpoint separates them with a wide margin either side.
    """
    raw = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-i", str(video), "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{_GRID[0]}x{_GRID[1]}", "-",
        ],
        check=True, capture_output=True,
    ).stdout
    columns, rows = _GRID
    assert len(raw) == columns * rows, f"expected one {columns}x{rows} frame, got {len(raw)} bytes"

    threshold = 108
    bright = [
        (index % columns, index // columns)
        for index, value in enumerate(raw)
        if value >= threshold
    ]
    assert bright, "no foreground found — the fixture fill and the threshold disagree"
    xs = [x for x, _ in bright]
    ys = [y for _, y in bright]
    return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def _row(listing_id=LISTING_ID) -> dict:
    from services import db as platform_db

    conn = platform_db.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM pulsedrop_renders WHERE listing_id=? LIMIT 1", (listing_id,))
        return dict(cur.fetchone() or {})
    finally:
        conn.close()


class TestTheFingerprint:
    def test_is_stable_for_the_same_sources(self):
        first = reel_composer.source_fingerprint(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        second = reel_composer.source_fingerprint(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        # Instability here would re-render every product on every tick, which is
        # an unbounded encoding bill rather than a wrong picture.
        assert first == second

    def test_changes_when_the_sources_change(self):
        fewer = _listing(names=("sq.png", "wide.png"))
        assert reel_composer.source_fingerprint(
            LISTING, distribution.SOURCE_COMPOSED_IMAGES
        ) != reel_composer.source_fingerprint(fewer, distribution.SOURCE_COMPOSED_IMAGES)

    def test_changes_when_the_target_length_changes(self, monkeypatch):
        # The duration is baked into the pixels, so a deployment that shortens
        # Reels has to re-render. A fingerprint over the URLs alone would serve
        # the old length forever and nothing would ever report a problem.
        before = reel_composer.source_fingerprint(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        monkeypatch.setenv("PULSEDROP_REEL_TARGET_SECONDS", "9")
        config.invalidate_cache()
        assert reel_composer.source_fingerprint(
            LISTING, distribution.SOURCE_COMPOSED_IMAGES
        ) != before

    def test_separates_a_seller_video_from_composed_images(self):
        # Same product, two source kinds, two different pieces of video. Sharing
        # a key would let one overwrite the other in storage.
        video = dict(LISTING, video_url=_BASE + "clip.mp4")
        assert reel_composer.source_fingerprint(
            video, distribution.SOURCE_SELLER_VIDEO
        ) != reel_composer.source_fingerprint(video, distribution.SOURCE_COMPOSED_IMAGES)


class TestTheQueue:
    def test_enqueueing_twice_returns_the_one_row(self, composer):
        first = reel_composer.find_or_enqueue(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        second = reel_composer.find_or_enqueue(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        assert first["id"] == second["id"]
        assert first["state"] == reel_composer.PENDING

    def test_a_second_tick_does_not_queue_a_second_encode(self, composer):
        from services import db as platform_db

        for _ in range(3):
            reel_composer.find_or_enqueue(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        conn = platform_db.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM pulsedrop_renders WHERE listing_id=?", (LISTING_ID,))
            assert int(list(dict(cur.fetchone()).values())[0]) == 1
        finally:
            conn.close()

    def test_only_one_worker_can_claim_a_row(self, composer):
        reel_composer.find_or_enqueue(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        won = reel_composer._claim_render("worker-a")
        lost = reel_composer._claim_render("worker-b")
        # Two containers run the media worker. Both claiming would encode the
        # same product twice and race on the settle.
        assert won and won["claimed_by"] == "worker-a"
        assert lost == {}
        assert won["attempts"] == 1

    def test_an_empty_queue_claims_nothing(self, composer):
        assert reel_composer._claim_render("worker-a") == {}


class TestTheEncode:
    def test_produces_a_playable_nine_by_sixteen_reel(self, composer):
        reel_composer.find_or_enqueue(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        claimed = reel_composer._claim_render("worker-a")
        ok, reason = reel_composer._execute_render(claimed)
        assert (ok, reason) == (True, "")

        row = _row()
        assert row["state"] == reel_composer.READY
        assert (row["frame_width"], row["frame_height"]) == (
            reel_composer.FRAME_WIDTH,
            reel_composer.FRAME_HEIGHT,
        )

        # The settled row is a promise about a file. Measure the file: a filter
        # graph that silently fell back to a different geometry would still
        # write 1080x1920 into the row, because the row is written from the
        # module constants and not from the output.
        served = composer / row["video_url"].split("/static/uploads/", 1)[1]
        assert served.exists()
        probed = _probe(served)
        assert (int(probed["width"]), int(probed["height"])) == (1080, 1920)
        assert 1.0 <= float(probed["duration"]) <= 6.0

    def test_writes_a_poster_beside_the_video(self, composer):
        reel_composer.find_or_enqueue(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        reel_composer._execute_render(reel_composer._claim_render("worker-a"))
        row = _row()
        # The poster is what the feed shows before the video decodes. Without it
        # a Reel opens on a black frame.
        assert row["poster_url"].endswith(".jpg")
        assert (composer / row["poster_url"].split("/static/uploads/", 1)[1]).exists()

    def test_the_stored_key_is_namespaced_by_product_and_fingerprint(self, composer):
        reel_composer.find_or_enqueue(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        reel_composer._execute_render(reel_composer._claim_render("worker-a"))
        row = _row()
        # Re-rendering the same product under a new fingerprint must not
        # overwrite the file a published Reel is already pointing at.
        assert f"pulsedrop/reels/{LISTING_ID}/{row['source_fingerprint']}.mp4" in row["video_url"]

    @pytest.mark.parametrize("name", sorted(SHAPES))
    def test_never_distorts_the_product(self, composer, monkeypatch, name):
        """The product keeps its own aspect ratio, whatever shape it arrived in.

        One image per render, so the measurement is of the fit alone and not of
        a crossfade between two differently-shaped stills.
        """
        source_width, source_height = SHAPES[name]
        single = _listing(names=(name,))
        monkeypatch.setattr(reel_composer, "_load_listing", lambda listing_id: dict(single))
        reel_composer.find_or_enqueue(single, distribution.SOURCE_COMPOSED_IMAGES)
        assert reel_composer._execute_render(reel_composer._claim_render("worker-a"))[0]

        served = composer / _row()["video_url"].split("/static/uploads/", 1)[1]
        width, height = _foreground_box(served)
        # Two percent of the frame's long edge: the fit lands on whole pixels
        # and the grayscale downscale softens each edge by a cell or so, but a
        # stretch is not a rounding error. Squashing a 16:9 product into 9:16
        # moves this ratio by more than 3x.
        assert width / height == pytest.approx(source_width / source_height, rel=0.02)

    def test_a_ready_row_is_never_claimed_again(self, composer):
        reel_composer.find_or_enqueue(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        reel_composer._execute_render(reel_composer._claim_render("worker-a"))
        # A re-claim would re-encode a finished Reel on every sweep forever.
        assert reel_composer._claim_render("worker-c") == {}


class TestWhenTheEncodeFails:
    @pytest.fixture()
    def broken(self, composer, monkeypatch):
        """A product whose only image cannot be fetched."""
        listing = _listing(BROKEN_ID, names=("missing.png",))
        monkeypatch.setattr(
            reel_composer,
            "_load_listing",
            lambda listing_id: dict(listing) if int(listing_id) == BROKEN_ID else {},
        )
        reel_composer.find_or_enqueue(listing, distribution.SOURCE_COMPOSED_IMAGES)
        return listing

    def test_an_early_failure_goes_back_to_pending_for_a_retry(self, broken):
        reel_composer._execute_render(reel_composer._claim_render("worker-a"))
        row = _row(BROKEN_ID)
        # A CDN blip must not permanently retire a product from Reels.
        assert row["state"] == reel_composer.PENDING
        assert row["attempts"] == 1
        assert row["failure_reason"]

    def test_the_ladder_ends_in_failed_and_stops(self, broken):
        ladder = []
        for _ in range(6):
            claimed = reel_composer._claim_render("worker-a")
            if not claimed:
                break
            reel_composer._execute_render(claimed)
            row = _row(BROKEN_ID)
            ladder.append((row["state"], row["attempts"]))

        attempts = config.reel_render_max_attempts()
        assert len(ladder) == attempts, ladder
        assert [state for state, _ in ladder[:-1]] == [reel_composer.PENDING] * (attempts - 1)
        # Terminal, and then the queue stops offering it — which is the half
        # that matters. A FAILED row that kept being claimed would burn a core
        # on a product that can never succeed.
        assert ladder[-1] == (reel_composer.FAILED, attempts)
        assert reel_composer._claim_render("worker-a") == {}

    def test_a_failed_render_publishes_no_url(self, broken):
        reel_composer._execute_render(reel_composer._claim_render("worker-a"))
        row = _row(BROKEN_ID)
        # The publisher gates on ``state == READY and video_url``; a failure that
        # left a stale URL behind would be published as a working Reel.
        assert row["video_url"] == ""
        assert (row["frame_width"], row["frame_height"]) == (0, 0)

    def test_a_product_that_vanished_is_not_encoded_at_all(self, composer, monkeypatch):
        reel_composer.find_or_enqueue(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        monkeypatch.setattr(reel_composer, "_load_listing", lambda listing_id: {})

        def unreachable(*args, **kwargs):
            raise AssertionError("re-read the listing before spending a core on it")

        monkeypatch.setattr(reel_composer, "_render_from_images", unreachable)
        ok, reason = reel_composer._execute_render(reel_composer._claim_render("worker-a"))
        assert (ok, reason) == (False, "listing_unavailable")


class TestTheWorkerEntryPoint:
    def test_drains_one_render_and_reports_counters(self, composer):
        reel_composer.find_or_enqueue(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        assert reel_composer.run_pending(1) == {"started": 1, "succeeded": 1, "failed": 0}
        assert _row()["state"] == reel_composer.READY

    def test_an_empty_queue_is_not_an_error(self, composer):
        assert reel_composer.run_pending(1) == {"started": 0, "succeeded": 0, "failed": 0}

    def test_the_reels_kill_switch_stops_the_worker_before_it_claims(self, composer, monkeypatch):
        reel_composer.find_or_enqueue(LISTING, distribution.SOURCE_COMPOSED_IMAGES)
        monkeypatch.setenv("PULSEDROP_REELS_ENABLED", "0")
        config.invalidate_cache()
        assert reel_composer.run_pending(1)["started"] == 0
        # Still pending, not consumed: flipping the switch back must resume the
        # queue rather than find it drained.
        assert _row()["state"] == reel_composer.PENDING
