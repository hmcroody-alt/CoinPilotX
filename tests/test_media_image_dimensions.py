"""`_image_dimensions` must actually measure images in production.

It did not, for four months. `from PIL import Image` sat inside a bare
`except Exception` and Pillow was never declared in requirements.txt, so the
import raised on every deploy and every caller read the result as "unreadable
image". 318 of 318 uploaded images stored width/height NULL; the only 67 rows
that carry dimensions came from the automated pipeline, which passes its own.

Nothing in the suite noticed, because a test that builds its own image and
calls the function passes in any developer venv that happens to have Pillow.
The check that matters is therefore the dependency itself, not the arithmetic.
"""
import importlib.util
import io
from pathlib import Path

import pytest

from services import media_service

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"


def _declared_packages():
    names = set()
    for raw in REQUIREMENTS.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        for sep in ("==", ">=", "<=", "~=", ">", "<", "["):
            if sep in line:
                line = line.split(sep, 1)[0]
                break
        names.add(line.strip().lower())
    return names


def test_pillow_is_declared_as_a_dependency():
    # The whole defect in one assertion. media_service imports PIL at runtime,
    # so a deploy that installs only requirements.txt must install it too --
    # an import guarded by try/except gives back no error, just silence and a
    # NULL column, and the consequence only surfaces months later in the app.
    assert "pillow" in _declared_packages(), (
        "services/media_service._image_dimensions imports PIL, so Pillow must be "
        "declared in requirements.txt; without it every uploaded image silently "
        "records width/height NULL in production."
    )


def test_pillow_is_actually_importable_here():
    # Guards the inverse mistake: declaring a package that will not import on
    # the deploy's interpreter. If this fails in CI, the production container
    # is in exactly the state that produced the ghost post.
    assert importlib.util.find_spec("PIL") is not None, "Pillow is declared but not installed"


@pytest.mark.parametrize(
    "fmt, suffix",
    [("PNG", ".png"), ("JPEG", ".jpg"), ("GIF", ".gif"), ("WEBP", ".webp")],
)
def test_measures_every_image_format_the_uploader_accepts(tmp_path, fmt, suffix):
    # camera_filter_engine allows jpg/jpeg/png/webp/gif. A format the validator
    # lets through but the measurer cannot read would reintroduce the NULL.
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / f"sample{suffix}"
    Image.new("RGB", (640, 480), "white").save(path, format=fmt)

    assert media_service._image_dimensions(path) == (640, 480)


def test_non_square_dimensions_are_not_transposed(tmp_path):
    # width/height feed the client's aspect ratio; swapping them renders every
    # portrait photo as landscape, which is a quieter bug than a missing one.
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "portrait.png"
    Image.new("RGB", (300, 900), "white").save(path)

    assert media_service._image_dimensions(path) == (300, 900)


def test_unreadable_bytes_return_none_rather_than_raising(tmp_path):
    # Must stay non-fatal: a corrupt upload should lose its size, not its post.
    path = tmp_path / "broken.png"
    path.write_bytes(b"this is not a png")

    assert media_service._image_dimensions(path) == (None, None)


def test_missing_file_returns_none_rather_than_raising(tmp_path):
    assert media_service._image_dimensions(tmp_path / "absent.png") == (None, None)


def test_a_missing_pillow_is_logged_as_an_error(monkeypatch, caplog, tmp_path):
    """The silence is the bug, so the absence of Pillow must be loud.

    Simulated by making the import fail the way it failed in production.
    """
    import builtins

    real_import = builtins.__import__

    def refuse_pil(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("No module named 'PIL'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse_pil)
    path = tmp_path / "any.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

    with caplog.at_level("WARNING"):
        assert media_service._image_dimensions(path) == (None, None)

    records = [r for r in caplog.records if "MEDIA_IMAGE_DIMENSIONS_UNAVAILABLE" in r.getMessage()]
    assert records, "a missing Pillow must be logged, not swallowed"
    # ERROR, not WARNING: no image can ever be measured until it is reinstalled,
    # which is a different severity from one file that would not parse.
    assert records[0].levelname == "ERROR"


def test_unreadable_bytes_log_a_distinguishable_warning(caplog, tmp_path):
    # The two failure modes were indistinguishable before; keep them apart so a
    # dependency outage is never filed as a run of bad uploads.
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not a jpeg at all")

    with caplog.at_level("WARNING"):
        media_service._image_dimensions(path)

    messages = [r.getMessage() for r in caplog.records]
    assert any("MEDIA_IMAGE_DIMENSIONS_UNREADABLE" in m for m in messages)
    assert not any("MEDIA_IMAGE_DIMENSIONS_UNAVAILABLE" in m for m in messages)
