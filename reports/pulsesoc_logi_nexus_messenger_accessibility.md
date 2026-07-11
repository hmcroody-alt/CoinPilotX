# PulseSoc LogiNexus Messenger Accessibility

## Improvements

- Added semantic button roles for conversation rows, attachment controls, voice recording, and send.
- Added selected-state semantics for Pulse Command segment tabs.
- Added conversation row labels that include unread counts.
- Added message bubble accessibility labels with sender direction and delivery state.
- Preserved 44pt-plus command actions and composer send control.
- Send button now has a disabled state when the draft is empty.

## Remaining Accessibility QA

- VoiceOver ordering on physical device.
- Dynamic Type stress for long conversation titles and long message bodies.
- Reduced-motion behavior for future message arrival/reaction animation.
- Focus restoration after future context menu or bottom-sheet actions.
# Pulse Command Accessibility Update

Added this milestone:

- Conversation rows now announce pinned, muted, unread, and destination state.
- Calls, groups, and rooms rows use accessible button labels.
- Message composer reply cancellation is a semantic button.
- Long-press message controls are exposed through a modal action sheet.
- Send, retry, report, delete, and Safety Hub actions use semantic button roles.

Remaining:

- Full VoiceOver pass on simulator.
- Dynamic Type layout proof for long names and action sheet controls.
- More detailed attachment descriptions for non-image files.
