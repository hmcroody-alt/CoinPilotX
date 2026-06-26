# PulseSoc Sci-Fi Ads Privacy Review

## Privacy Controls

- Client receives only sanitized creative payloads from the delivery engine.
- No advertiser owner data, private targeting data, secrets, filesystem paths, or internal tokens are rendered.
- The ad hook sends tracking only through signed delivery payloads.
- User hide/report actions use authenticated endpoints.
- Personalized ad opt-out remains enforced by the delivery service.

## Placement Safety

- Sponsored experiences are clearly labeled.
- Ads are delivered only from approved placement keys.
- Mobile receives inline placements only.
- Desktop side UFOs are constrained to rail ad zones.

## Data Boundaries

- No private messages, private statuses, exact location, or admin/security data are used for ad display.
- Click destinations are opened only after the server records/validates the click.
