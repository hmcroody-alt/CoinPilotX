# Status Viewer Immersive Redesign

## Problem

The Status viewer reserved a large dark metadata/actions panel beneath the media. This made video statuses feel smaller and less immersive, especially on mobile.

## Changes

- Removed the visual bottom-panel treatment from the Status viewer.
- Expanded the story frame to use nearly the full viewport.
- Kept media anchored full-screen inside the viewer.
- Moved author, caption, and view information into compact translucent overlays.
- Moved Like, Comment, Share, Save, More, and Sound into floating story-style controls.
- Kept progress, previous/next navigation, and close controls above the media.
- Added ambient Pulse background treatment behind the media so unused space never reads as a large black block.
- Made the reply field appear only when focused from the Comment action.

## QA Targets

- Portrait video status
- Landscape video status
- Image status
- Text status
- Multiple-status sequence
- Sound toggle
- Reactions and replies
- Close button

## Result

Pulse Status now behaves like an immersive stories viewer: content-first, full-screen, and mobile friendly, with no large black metadata section below the media.
