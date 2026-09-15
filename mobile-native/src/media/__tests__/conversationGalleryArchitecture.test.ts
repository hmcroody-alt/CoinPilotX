/**
 * The two §37 mutations that no rendering test can reach.
 *
 * "The gallery is built only from mounted message cells" and "closing the
 * viewer resets the thread scroll position" are both *shapes* rather than
 * behaviours. A screen that mounts one viewer per bubble still renders a photo;
 * a screen that swaps the message list out for the viewer still shows the photo
 * you tapped. Both bugs are invisible until a real thread is long enough — 17
 * photos, a few hundred messages of scrollback — which is exactly the condition
 * a unit test cannot produce.
 *
 * So they are asserted against the source. This is the same guard style as
 * `PostCardArchitecture.test.ts`: cheap, and it fails on the commit that
 * reintroduces the shape rather than on the bug report months later.
 */

import { readFileSync } from "fs";
import { join } from "path";

const chatScreen = readFileSync(join(__dirname, "..", "..", "screens", "ChatScreen.tsx"), "utf8");
const galleryHost = readFileSync(join(__dirname, "..", "ConversationMediaGalleryHost.tsx"), "utf8");
const galleryHook = readFileSync(join(__dirname, "..", "useConversationMediaGallery.ts"), "utf8");

describe("one viewer per conversation, not one per bubble", () => {
  /**
   * MUTATION §37: "the gallery is built only from currently mounted message
   * cells". A per-bubble `<NativeMediaViewer items={[one]}>` *is* that bug:
   * a bubble only knows about itself, so the collection can never be larger
   * than one, and only the cells React happens to have mounted can open it.
   */
  it("mounts no media viewer inside a message bubble", () => {
    expect(chatScreen).not.toMatch(/<NativeMediaViewer/);
    expect(chatScreen).not.toMatch(/\bNativeMediaViewerItem\b/);
  });

  it("mounts exactly one screen-level gallery viewer", () => {
    const mounts = chatScreen.match(/<ConversationMediaGalleryViewer/g) || [];
    expect(mounts).toHaveLength(1);
  });

  it("hands the viewer a collection that came from the server, not from the list", () => {
    // The host renders `gallery.items`, and `gallery.items` is only ever fed by
    // `fetchConversationMedia` plus the tapped seed.
    expect(galleryHost).toMatch(/gallery\.items/);
    expect(galleryHook).toMatch(/fetchConversationMedia/);
    // Nothing in the gallery may read the rendered message array.
    expect(galleryHook).not.toMatch(/\bmessages\b/);
  });
});

describe("the thread survives the viewer", () => {
  /**
   * MUTATION §37: "closing the viewer resets the conversation scroll position".
   *
   * The message list must be a *sibling* of the viewer, never an alternative to
   * it. If the screen renders `gallery.visible ? <Viewer/> : <List/>`, closing
   * remounts the list and the user is returned to the bottom of a thread they
   * were reading three months back. Keeping both mounted — the viewer is a
   * full-screen Modal layered above — means there is nothing to restore,
   * because nothing was ever lost.
   */
  it("never makes the message list conditional on the gallery being closed", () => {
    expect(chatScreen).not.toMatch(/gallery\.visible\s*\?/);
    expect(chatScreen).not.toMatch(/mediaGallery\.visible\s*\?/);
    expect(chatScreen).not.toMatch(/!\s*mediaGallery\.visible\s*&&/);
    expect(chatScreen).not.toMatch(/!\s*mediaGallery\.visible\s*\?/);
  });

  it("renders the viewer after the screen shell rather than in place of it", () => {
    const shellClose = chatScreen.indexOf("</LogiNexusScreenShell>");
    const viewer = chatScreen.indexOf("<ConversationMediaGalleryViewer");
    expect(shellClose).toBeGreaterThan(-1);
    expect(viewer).toBeGreaterThan(shellClose);
  });

  it("keeps the viewer inside a Modal, which layers instead of replacing", () => {
    const viewerSource = readFileSync(join(__dirname, "..", "..", "components", "NativeMediaViewer.tsx"), "utf8");
    expect(viewerSource).toMatch(/<Modal\s+visible=\{visible\}/);
  });
});

describe("the bubble's only job", () => {
  /**
   * A bubble hands over a seed and nothing else. If it ever assembled a list,
   * the collection would be back to depending on what is mounted.
   */
  it("opens the gallery with a single seed built from its own message", () => {
    expect(chatScreen).toMatch(/gallery\?\.open\(\s*gallerySeedFromMessage\(/);
    expect(chatScreen).not.toMatch(/\.open\(\s*\[/);
  });
});

describe("no audio-session reach from the gallery", () => {
  /**
   * The realtime-audio policy, restated where it can be broken: pausing a
   * player is fine, configuring the shared session from a media surface is not.
   * A gallery that calls `setAudioModeAsync` steals the session from a live
   * call and the build stays green.
   */
  it("neither the host nor the hook touches the audio session", () => {
    for (const source of [galleryHost, galleryHook]) {
      expect(source).not.toMatch(/setAudioModeAsync/);
      expect(source).not.toMatch(/AVAudioSession/);
    }
  });
});
