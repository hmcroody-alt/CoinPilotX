# PulseSoc Native Pulse Command Exact Parity Inventory

Status: first exact-production-parity slice completed for the top-level Messenger shell, conversation rows, and base conversation geometry.

Design authority: current production PulseSoc Messenger (`templates/pulse_messages_v2.html`, `static/js/pulse_messages_v2.js`, `static/css/pulse_messages_v2.css`).

## Inventory

| Surface | Production source | Native source | Backend/API source | Current parity | Reuse decision | Remaining mismatch |
| --- | --- | --- | --- | --- | --- | --- |
| Messenger header | `pulse_messages_v2.html`, `pulse_messages_v2.css` | `MessengerScreen.tsx`, `PulseCommand.tsx` | session/unread APIs | 84% | Reuse native header primitive; tighten copy/sizing | Need fresh side-by-side capture against production header |
| Chats tab | `pulse_messages_v2.js`, `.comm-tabs` CSS | `MessengerScreen.tsx`, `PulseCommandSegmentRail` | `/api/pulse/messages/conversations` | 88% | Reuse tabs and API, refine rail density | Badge and selected-state screenshot proof pending |
| Calls tab | production calls controls | `MessengerScreen.tsx`, `CallScreen.tsx` | `/api/calls/*` | 78% | Reuse call API/provider contracts | Call-history visual parity and provider boundary capture pending |
| Groups tab | production group routes | `MessengerScreen.tsx`, `GroupsScreen.tsx` | `/api/pulse/groups/*` | 82% | Reuse group APIs/domain rules | Full group-detail visual parity pending |
| Rooms tab | production rooms routes | `MessengerScreen.tsx`, `GroupsScreen.tsx` | `/api/pulse/communications/rooms` | 76% | Reuse room APIs/domain rules | Participant/provider state parity pending |
| Search | `pulse_messages_v2.js` | `PulseCommandSearch`, `searchMessenger` | `/api/pulse/messages/search` | 86% | Reuse endpoint and debounce intent | Message-text result depth pending |
| Active users | production active/presence rail | `MessengerScreen.tsx` | conversation presence payload | 82% | Reuse authoritative presence only | Needs visual comparison and empty state capture |
| Conversation rows | `.conversation` CSS | `ConversationRow` | conversation summaries | 90% | Reuse native row; match production density | Avatar image handoff and exact profile action placement pending |
| Pinned UNDX conversation | production Pulse AI/UNDX entry | `PulseAiScreen.tsx`, conversation row | UNDX endpoints | 76% | Reuse production backend contract | Pinned placement screenshot pending |
| New-chat action | production new chat action | `MessengerScreen.tsx` | Search/profile routes | 82% | Reuse native route intent | Production-equivalent modal/sheet not complete |
| Create-group action | production create group action | `GroupsScreen.tsx` | group APIs | 72% | Reuse group contracts | Create-group native parity pending |
| Conversation header | production chat header | `ChatScreen.tsx`, `PulseCommandHeader` | conversation detail | 78% | Reuse native header | Avatar/presence/action exact sizing pending |
| Message list | `.messages` CSS | `ChatScreen.tsx` | `/messages`, `/sync` | 82% | Reuse FlatList and sync pipeline | Date separators/unread divider proof pending |
| Message bubbles | `.message` CSS | `MessageBubble` | message payload | 86% | Reuse bubble system; tighten geometry | Sender grouping and all attachment variants pending |
| Typing indicator | production typing state | `typingSummary`, `ChatScreen.tsx` | `/typing` | 82% | Reuse endpoint and shared domain text | Timing proof pending |
| Read/delivery state | production message meta | `messageDeliveryLabel` | seen/read routes | 84% | Reuse domain helper and API | Stale receipt QA pending |
| Reactions | production reaction row | `ReactionRow`, `messageActionRules` | `/react` | 84% | Reuse server mutation | Exact picker visual parity pending |
| Reply | production reply preview | `replyTo`, `replyBlock` | send payload reply fields | 84% | Reuse existing payload contract | Jump-to-reply pending |
| Context menu | production action sheet | `MessageActionSheet` | delete/report/react routes | 78% | Reuse shared domain availability | Copy/forward/edit parity pending |
| Composer | `.pulse-message-composer-shell`, `.composer` CSS | `ChatScreen.tsx` | send/upload/typing APIs | 86% | Reuse native composer and send pipeline | Full tool rail and attachment sheet visual parity pending |
| Attachments | production attachment sheet/media viewer | `uploadMessengerMedia`, `NativeMediaViewer` | media upload routes | 78% | Reuse upload/media contracts | Gallery/document/voice parity pending |
| Voice messages | production recorder/waveform | `ChatScreen.tsx` | media upload route | 58% | Reuse media contract only | Native playback/recording depth pending; device checks later |
| Call history | production calls tab | `CallScreen.tsx` | calls APIs | 74% | Reuse provider contracts | Recents/missed filters pending |
| Group detail | production groups | `GroupsScreen.tsx` | group APIs | 82% | Reuse shared domain and APIs | Exact visual side-by-side pending |
| Room detail | production rooms | `GroupsScreen.tsx` | room APIs/provider | 76% | Reuse provider boundary rules | Detail state screenshots pending |
| Loading states | production skeletons | `LogiNexusStatePanel` | API state | 84% | Reuse shared state panels | Exact skeleton dimensions pending |
| Empty states | production empty cards | `LogiNexusStatePanel` | API state | 84% | Reuse shared state panels with production wording | More state screenshots pending |
| Error states | production retry/error | `LogiNexusStatePanel`, inline error | API state | 82% | Reuse existing retry/refresh paths | Disruption QA pending |
| Offline/reconnect | `pulse_chat_recovery.js` | caches + sync APIs | cache/sync services | 78% | Reuse native cache and sync | API-disruption proof pending |
| Deep links | production routes | native route registry | notification metadata | 80% | Reuse route intent | Push/tap remains device QA |
| Safety actions | production report/block/mute | `MessageActionSheet`, Safety Hub | moderation/safety routes | 82% | Reuse server authority | Direct mute/block exact path pending |

## Current Decision

Pulse Command should remain the active subsystem. This milestone improves exact UI parity for the top-level inbox and base conversation surface, but the nested Messenger experience is not frozen yet.
