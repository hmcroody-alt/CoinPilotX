# Intelligence Mobile QA

Date: 2026-06-27

## Mobile Layout Requirements Covered

The Intelligence Center shell uses mobile-first CSS with:

- Safe bottom padding above mobile navigation
- Responsive card grids
- No fixed-width content blocks
- Contextual action buttons
- Clear state pills
- Wrapped text for long labels
- Reduced visual clutter in subsystem pages

## Desktop Layout Requirements Covered

Desktop renders:

- Intelligence Hub summary grid
- Subsystem cards
- Event mesh
- Privacy boundary
- Admin command center table and detail cards

## Functional QA Coverage

Automated route and render checks verify:

- `/dashboard/intelligence`
- `/dashboard/intelligence/<subsystem_key>` for every subsystem
- `/api/dashboard/intelligence/state`
- `/admin/intelligence-command-center`
- `/admin/intelligence-command-center/<section_key>` for every backend surface
- Non-admin users blocked from admin Intelligence surfaces
- No user-facing internal technology name leak
- No raw secrets/tokens/private keys in rendered responses
- No generic Intelligence `Open` buttons
- No misleading Intelligence `ACTIVE` states

## Remaining Risk

This report records automated QA coverage. Manual screenshot capture should be repeated after deployment if visual regressions are suspected on a real device viewport.
