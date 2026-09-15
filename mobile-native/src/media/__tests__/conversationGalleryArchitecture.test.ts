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

describe("a multi-media message's tiles", () => {
  /**
   * MUTATION §37: "tapping the third photo of a multi-photo message opens a
   * different photo". `messageMediaTiles.test.ts` proves the tile and the
   * collection entry share a key; this proves the screen actually seeds from
   * the tile. A grid that seeded from `message` would hand every tile the first
   * attachment's identity — three tiles, all opening photo one.
   */
  it("seeds each tile's open from that tile, not from the message", () => {
    expect(chatScreen).toMatch(/onOpen\(\{ \.\.\.tile,/);
  });

  /**
   * And it seeds with the tile's *granted* URL.
   *
   * The host renders `grant?.url || item.url`, so the seeded URL is what the
   * viewer shows until its own resolve lands. Seeding `tile.url` — the protected
   * API path straight off the attachment payload — still opens the viewer and
   * still ends up showing the photo a moment later, so it looks fine; it just
   * hands the platform image loader a protected path in the meantime, which is
   * what made image loads run session refresh on the server and sign people out.
   */
  it("seeds the tile's granted url, never the raw protected path", () => {
    expect(chatScreen).toMatch(/onOpen\(\{ \.\.\.tile, url, thumbnailUrl: thumbnail \}\)/);
    // The grid has no grant of its own, so it must not be the one building the
    // seed — that is the shape that would reintroduce the raw path.
    expect(chatScreen).not.toMatch(/gallerySeedFromMessage\(tile\)/);
  });

  /**
   * The grid decision must be made before the single-attachment grant is
   * consulted. Every branch below reads `mediaUrl`, which is the grant for
   * attachment *one*; `if (!mediaUrl) return null` sits among them. A grid
   * placed after that line renders nothing for a three-photo message whose
   * first photo happens to be the one that failed — and renders correctly for
   * every message where it did not, which is how it would survive review.
   */
  it("decides on the grid before the first attachment's grant can veto it", () => {
    const gridBranch = chatScreen.indexOf("if (isMultiMediaMessage(mediaTiles))");
    const singleGrantVeto = chatScreen.indexOf("if (!mediaUrl) return null;");
    expect(gridBranch).toBeGreaterThan(-1);
    expect(singleGrantVeto).toBeGreaterThan(-1);
    expect(gridBranch).toBeLessThan(singleGrantVeto);
  });

  /**
   * Each tile needs its own short-lived grant, which needs its own hook, which
   * is only possible in a per-tile component. A grid that reused the bubble's
   * single `mediaAccess` would point every tile at the same URL.
   */
  it("gives every tile its own media grant", () => {
    expect(chatScreen).toMatch(/function MediaGridTile\(/);
    const tileBody = chatScreen.slice(chatScreen.indexOf("function MediaGridTile("));
    expect(tileBody.slice(0, 1200)).toMatch(/useMessengerMediaAccessUrl\(\s*\{\s*mediaUploadId: tile\.mediaUploadId/);
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
