# Pulse Music License Policy

## Core Policy

Pulse must never scrape, download, or expose random internet music. Every available music track must have verified rights before users can attach it to Reels, Videos, Statuses, or Posts.

## Required Track Fields

- title
- artist
- source
- license type
- commercial use allowed
- remix/edit allowed
- attribution required
- proof URL/file
- approved by admin
- active/inactive

## Block Rules

Tracks are blocked when:

- admin approval is missing
- license proof is missing
- commercial use is not allowed
- remix/edit is not allowed
- license is noncommercial
- license is no-derivatives
- source/provider is not on the approved provider list
- upload is user-provided and pending rights review

## Admin Approval Workflow

1. AI or admin imports metadata only.
2. Track remains inactive and unavailable.
3. Admin adds/verifies proof URL or proof file.
4. Admin confirms commercial and edit rights.
5. Admin activates the track.
6. Users can then find it through the picker.

## Inventory

The active catalog is available through `/api/pulse/music/license-inventory`. It returns only creator-safe tracks and includes license/proof fields for audit review.

## Current Implementation

Pulse originals are seeded as approved internal works. External provider tracks must be imported as metadata and explicitly approved before appearing in any user picker.
