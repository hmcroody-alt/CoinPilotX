# PulseSoc LogiNexus Home Accessibility

Status: foundation accessibility preserved; full review pending.

## Preserved

- Composer input, mode buttons, publish button, retry button, draft recovery, and media actions retain QA/test IDs or accessibility labels.
- Feed card author, reaction, comment, save, repost, share, report, hide, block, mute, and media actions retain semantic button paths.
- Hero tiles use button semantics and explicit labels.

## Improved

- Status empty state uses clearer language.
- Feed tab selected state uses both color and stronger border/background state.
- Composer labels remain familiar instead of over-theming all controls.

## Pending

- VoiceOver order through the full Home stack.
- Dynamic type stress pass.
- Reduced-motion review once Home ambient motion is implemented.
- Physical iPhone tap target and screen recording review.

## Reconstruction Update

- Hero metric cells that route are exposed as buttons; passive metric cells are disabled pressables without button role.
- Hero quick tiles expose route-specific accessibility labels.
- Composer publish flow keeps the existing QA-addressable publish selector.
- Feed card actions remain semantic native pressables with existing QA/test selectors.
- Bottom navigation remains shared and label-driven.
- Keyboard/screen-reader pass on web QA remains useful, but does not replace native VoiceOver release QA.
