# PulseSoc Incoming Call Screen Redesign

## What Changed

The incoming call screen was rebuilt so Accept and Decline are no longer using the active-call End button layout.

The redesign keeps:

- Top caller identity pill.
- Center Pulse animation.
- Incoming Pulse copy below the animation.
- Large safe-area action controls at the bottom.

## Root Cause

The Decline button shared the same CSS selector as the active-call End button:

```css
.pulsesoc-call-actions .is-decline,
.pulsesoc-call-end-primary
```

That made Decline absolute-positioned at the bottom center while Accept stayed inside the incoming action row. On mobile this caused overlap, poor spacing, and clipped/stacked controls.

## New Incoming Layout

Incoming mode now has its own layout:

- Center Pulse orb and wave animation.
- Large `Pulsing...` copy.
- Caller name.
- Voice or Video Connection label.
- Dedicated bottom action dock.
- Side-by-side Decline and Accept buttons.
- Safe-area bottom spacing for iPhone home indicator and Android gesture navigation.

## Button Design

Accept:

- Cyan/green Pulse surface.
- Large circular touch target.
- Soft animated glow.
- Phone icon and label.

Decline:

- Deep red surface.
- Same size and weight as Accept.
- Phone hang-up icon and label.
- No shared absolute positioning with End.

## Responsive Behavior

Added incoming-specific rules for:

- Standard mobile portrait.
- Narrow devices down to 360px and below.
- Landscape short-height screens.
- Safe-area inset bottom, left, and right.

## Files Changed

- `static/pulsesoc_calls.js`
- `static/css/pulse_messages_v2.css`
- `templates/pulse_messages_v2.html`
- `scripts/pulsesoc_incoming_call_screen_audit.py`
- `reports/pulsesoc_incoming_call_screen_redesign.md`

## QA Status

Verified with static checks:

- Accept and Decline no longer share the active End button absolute layout.
- Incoming actions have a dedicated safe-area dock.
- Buttons are large touch targets.
- Incoming Pulse copy is structured below the animation.
- Mobile and landscape rules exist.

Browser/device QA still requires a real incoming call from a second authenticated user to visually confirm the ringing state on physical iPhone/Android hardware.
