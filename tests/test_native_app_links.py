from services.native_app_links import android_asset_links, apple_app_site_association


def test_apple_association_requires_valid_team_id(monkeypatch):
    monkeypatch.delenv("PULSESOC_APPLE_TEAM_ID", raising=False)
    payload, error = apple_app_site_association()
    assert payload is None
    assert "Apple Team ID" in error


def test_apple_association_scopes_links_to_pulsesoc_paths(monkeypatch):
    monkeypatch.setenv("PULSESOC_APPLE_TEAM_ID", "A1B2C3D4E5")
    monkeypatch.setenv("PULSESOC_APPLE_ASSOCIATED_BUNDLE_IDS", "com.pulsesoc.app")
    payload, error = apple_app_site_association()
    assert error == ""
    detail = payload["applinks"]["details"][0]
    assert detail["appID"] == "A1B2C3D4E5.com.pulsesoc.app"
    assert {item["/"] for item in detail["components"]} == {"/pulse/*", "/search*"}


def test_android_association_requires_a_sha256_fingerprint(monkeypatch):
    monkeypatch.setenv("PULSESOC_ANDROID_SHA256_CERT_FINGERPRINTS", "not-a-fingerprint")
    payload, error = android_asset_links()
    assert payload is None
    assert "SHA-256" in error


def test_android_association_supports_production_and_parallel_packages(monkeypatch):
    fingerprint = ":".join(["AB"] * 32)
    monkeypatch.setenv("PULSESOC_ANDROID_SHA256_CERT_FINGERPRINTS", fingerprint)
    monkeypatch.setenv("PULSESOC_ANDROID_ASSOCIATED_PACKAGES", "com.pulsesoc.app,com.pulsesoc.nativeapp")
    payload, error = android_asset_links()
    assert error == ""
    assert [item["target"]["package_name"] for item in payload] == [
        "com.pulsesoc.app",
        "com.pulsesoc.nativeapp",
    ]
    assert payload[0]["target"]["sha256_cert_fingerprints"] == [fingerprint]

