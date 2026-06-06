# PulseSoc Email Live Test

Generated: 2026-06-06

## Mailbox status

Namecheap Private Email previously confirmed these PulseSoc mailboxes exist and are active:

- `support@pulsesoc.com`
- `security@pulsesoc.com`
- `noreply@pulsesoc.com`

Brevo now has verified senders for the same three addresses.

## DNS status

| Check | Status |
| --- | --- |
| MX | Pass: Namecheap Private Email |
| SPF | Pass: one SPF record, Private Email include |
| Private Email DKIM | Pass |
| Brevo DKIM | Pass |
| DMARC | Saved in Namecheap and accepted by Brevo, but public DNS lookup still pending |

## Live tests

| Test | Result |
| --- | --- |
| Gmail to `support@pulsesoc.com` | Not run: Gmail login required |
| Gmail to `security@pulsesoc.com` | Not run: Gmail login required |
| Gmail to `noreply@pulsesoc.com` | Not run: Gmail login required |
| `support@pulsesoc.com` to Gmail | Not run: PulseSoc mailbox login required |
| Header SPF/DKIM/DMARC inspection | Not run: requires delivered message |

## Browser access note

The in-app browser is currently logged into Private Email, but the visible mailbox is not one of the PulseSoc mailboxes. I did not ask for or handle mailbox passwords.

## Next manual steps

1. Log into Gmail manually.
2. Send test messages to the three PulseSoc mailboxes.
3. Log into each PulseSoc mailbox manually or open the relevant mailbox session.
4. Confirm inbound delivery.
5. Send an outbound message from `support@pulsesoc.com` to Gmail.
6. In Gmail, inspect message headers for SPF, DKIM, and DMARC alignment or monitor-only result.
