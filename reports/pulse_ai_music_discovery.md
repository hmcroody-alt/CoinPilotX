# Pulse AI Music Discovery Foundation

## Summary

Pulse now has a rights-first music discovery foundation for Reels, Videos, Statuses, and Posts. The assistant can suggest tracks from the approved Pulse catalog, but it cannot expose unapproved, unclear, noncommercial, or no-derivatives music.

## What Was Added

- Shared search endpoint: `/api/pulse/music/search`
- AI suggestion endpoint: `/api/pulse/music/ai-suggest`
- License inventory endpoint: `/api/pulse/music/license-inventory`
- Metadata-only admin import endpoint: `/api/admin/pulse/music/import-metadata`
- Admin approval endpoint: `/api/admin/pulse/music/<track_id>/approval`
- Shared attachment table: `pulse_content_music`
- Composer picker for Posts/Videos using mood, genre, topic, and length
- Status music search now accepts mood, genre, topic, and length
- Reels sound picker now filters to approved, active, commercial/edit-safe tracks only

## Safety Model

The AI assistant ranks and recommends only tracks that pass all license checks:

- `approved_by_admin = 1`
- `active = 1`
- `commercial_use_allowed = 1`
- `remix_edit_allowed = 1`
- license proof exists through `proof_url` or `proof_file`
- license is not noncommercial or no-derivatives

User-uploaded sounds are stored as pending rights review and are not exposed to users until an admin approves them.

## Surfaces Covered

- Reels: approved tracks attach to `pulse_reel_audio` and snapshot into `pulse_content_music`
- Videos: approved track snapshots attach through `pulse_content_music`
- Statuses: approved catalog tracks attach to `pulse_status_music` and `pulse_content_music`
- Posts: approved track snapshots attach through `pulse_content_music`

## Remaining Production Work

- Add real vendor credentials only after a signed provider agreement.
- Build a richer admin UI for reviewing pending music metadata.
- Add proof-file upload storage for contracts/invoices if legal wants internal files instead of proof URLs.
- Add royalty/reporting exports if a provider contract requires usage reporting.
