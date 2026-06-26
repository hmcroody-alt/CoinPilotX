# PulseSoc Ad Creative Media Upload Workflow QA

## Summary

Replaced advertiser-supplied creative media URL fields with PulseSoc-owned media upload workflow. Creative Studio now requires internal uploaded media assets for image, video, and audio ads. Custom video thumbnails are optional uploaded assets. Creatives reference `media_asset_id` and `thumbnail_asset_id`; delivery resolves safe internal media URLs from approved assets.

## Security Controls

- Media URL and Thumbnail URL inputs are removed from the advertiser UI.
- `create_creative` rejects client-provided `media_url` and `thumbnail_url`.
- Image, video, and audio creatives require an owned uploaded media asset.
- Media assets are scoped by `owner_user_id` and `ad_account_id`.
- Cross-advertiser asset reuse is blocked server-side.
- Upload endpoint requires authentication and CSRF.
- Unsafe SVG, HTML, script, executable, and installer payloads are blocked.
- Delivery payloads expose safe media fields only; storage keys/checksums are not returned.
- Review board resolves uploaded media preview server-side from approved internal asset records.

## Database Changes

Additive support was added for:

- `pulse_ad_media_assets`
- `pulse_ad_creatives.media_asset_id`
- `pulse_ad_creatives.thumbnail_asset_id`
- `pulse_ad_creatives.media_ready`
- `pulse_ad_creatives.media_metadata_json`

No existing ad, wallet, billing, or campaign tables were removed.

## Validation

Passed:

- `venv/bin/python -m py_compile bot.py services/pulse_ads_service.py services/pulse_advertiser_portal.py scripts/ad_creative_media_upload_audit.py scripts/activate_pulse_radio_ad.py scripts/review_board_audit.py scripts/pulse_sci_fi_ads_layer_audit.py`
- `venv/bin/python scripts/ad_creative_media_upload_audit.py`
- `venv/bin/python scripts/review_board_audit.py`
- `venv/bin/python scripts/pulse_sci_fi_ads_layer_audit.py`
- `venv/bin/python scripts/pulse_ads_foundation_audit.py`
- `venv/bin/python scripts/pulse_ads_delivery_engine_audit.py`
- `venv/bin/python scripts/advertiser_portal_audit.py`
- `venv/bin/python scripts/pulse_radio_ad_campaign_audit.py`

Browser QA:

- Opened `http://127.0.0.1:5096/pulse/advertise`.
- Confirmed `Media URL` and `Thumbnail URL` are not visible.
- Confirmed upload zone and custom video thumbnail controls render.
- Confirmed no login redirect in the local QA session.

## Remaining Risks

- Real thumbnail generation depends on the existing upload/media processing infrastructure available in the running environment.
- Browser QA did not upload a real large video file; automated route coverage validates ownership, CSRF, unsafe-file rejection, and internal asset delivery.
