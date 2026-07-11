# PulseSoc Pulse Command Motion

Status: partial; motion language exists but is not complete for the full communications stack.

## Current Motion Coverage

- Shared LogiNexus motion utilities are available and audited.
- Pulse Command inbox, chat, calls, groups, and rooms use shared screen/layout primitives.
- This slice did not add new animation; it reduced duplicated domain logic so future motion can bind to stable shared state.

## Still Needed

- Message arrival animation.
- Reaction float/anchor animation.
- Context menu bloom and focus return.
- Attachment upload/download progress motion.
- Room live-presence motion.
- Call-state transition motion.
- Reduced-motion verification for all of the above.

## Current Decision

Do not add decorative motion before Groups / Rooms detail and offline/reconnect states are structurally complete.
