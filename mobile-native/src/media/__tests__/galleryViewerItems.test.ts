/**
 * What the fullscreen viewer is actually handed for each item.
 *
 * This function had no tests, and that is the whole story of the black-screen
 * incident. Every part around it was correct — the collection was right, the key
 * was right, both media ids were right, the grant resolved successfully — and
 * this one line chose between two URLs by truthiness:
 *
 *     url: grant?.url || item.url
 *
 * The seed carries the URL the chat bubble ALREADY rendered from: absolute,
 * warm, proven on screen. The grant arrived site-relative, because `/access`
 * mints site-relative paths and only the chat bubble happened to absolutize
 * them. Truthiness ranked the broken URL first, so tapping a photo the user was
 * looking at replaced it with a URL AVPlayer rejects outright (-1002) and
 * `<Image>` drops before it reaches the network. Both paint black, neither
 * raises an error, and the viewer's controls all keep working — the failure was
 * indistinguishable from "still loading", forever.
 *
 * Requirement §3/§4: tapping content must never make it disappear, and black is
 * never the loading state. These assert that as a property of the chooser.
 */

// The host module also exports the viewer component, whose import chain reaches
// expo-av's native module. `galleryViewerItems` is pure data mapping and needs
// none of it; stubbing the component keeps this a real behavioural test of the
// chooser rather than a source-text assertion.
jest.mock("../../components/NativeMediaViewer", () => ({ NativeMediaViewer: () => null }));

import { galleryViewerItems } from "../ConversationMediaGalleryHost";
import { ConversationMediaItem, conversationMediaKey } from "../conversationMediaCollection";

const labels = {
  photo: (_item: ConversationMediaItem, position: number) => `Photo ${position + 1}`,
  video: (_item: ConversationMediaItem, position: number) => `Video ${position + 1}`,
  unavailable: "Not available"
};

function item(overrides: Partial<ConversationMediaItem> = {}): ConversationMediaItem {
  return {
    key: conversationMediaKey(1728, 601),
    attachmentId: 601,
    mediaUploadId: 87,
    messageId: 1728,
    kind: "video",
    url: "https://pulsesoc.com/api/messages/media/87/download?mt=seed",
    thumbnailUrl: "https://pulsesoc.com/api/messages/media/87/thumbnail?mt=seed",
    senderName: "Roody Cherie",
    createdAt: "",
    ...overrides
  } as ConversationMediaItem;
}

describe("an unloadable URL never displaces a loadable one", () => {
  it("keeps the seed's absolute URL when the grant came back site-relative", () => {
    // The exact production shape: attachment 601 / media_upload 87, grant
    // resolved fine, but as a path rather than a URL.
    const seed = item();
    const [viewerItem] = galleryViewerItems(
      [seed],
      { [seed.key]: { url: "/api/messages/media/87/download?mt=grant", thumbnailUrl: "", unavailable: false } },
      labels
    );
    expect(viewerItem.url).toBe("https://pulsesoc.com/api/messages/media/87/download?mt=seed");
  });

  it("still prefers the grant when the grant is loadable", () => {
    // Loadability is the floor under the preference, not a replacement for it:
    // the grant is the freshest credential and the only one that can be
    // re-minted on expiry, so it must win whenever it is usable.
    const seed = item();
    const [viewerItem] = galleryViewerItems(
      [seed],
      {
        [seed.key]: {
          url: "https://pulsesoc.com/api/messages/media/87/download?mt=grant",
          thumbnailUrl: "",
          unavailable: false
        }
      },
      labels
    );
    expect(viewerItem.url).toContain("mt=grant");
  });

  it("carries the already-rendered poster forward rather than re-fetching an equivalent one", () => {
    // Both URLs address the same bitmap and differ only in their access token,
    // which makes them different cache keys. Preferring the grant's would throw
    // away the decoded image the user is looking at and re-download it, leaving
    // a black gap exactly where §3 says the poster must already be.
    const seed = item();
    const [viewerItem] = galleryViewerItems(
      [seed],
      {
        [seed.key]: {
          url: "https://pulsesoc.com/api/messages/media/87/download?mt=grant",
          thumbnailUrl: "https://pulsesoc.com/api/messages/media/87/thumbnail?mt=grant",
          unavailable: false
        }
      },
      labels
    );
    expect(viewerItem.thumbnailUrl).toContain("mt=seed");
  });

  it("falls back to the grant's poster when the seed has none", () => {
    // Neighbours are not seeded from a bubble — the server's collection ships
    // `thumbnail_url: ""` — so for every item except the tapped one the grant's
    // poster is the only one there is.
    const seed = item({ thumbnailUrl: "" });
    const [viewerItem] = galleryViewerItems(
      [seed],
      {
        [seed.key]: {
          url: "https://pulsesoc.com/api/messages/media/87/download?mt=grant",
          thumbnailUrl: "https://pulsesoc.com/api/messages/media/87/thumbnail?mt=grant",
          unavailable: false
        }
      },
      labels
    );
    expect(viewerItem.thumbnailUrl).toContain("mt=grant");
  });

  it("shows the seed while the grant is still in flight", () => {
    // §4: the tapped item opens on the picture it was already showing. An
    // unresolved key must not blank the item on the way to resolving it.
    const seed = item();
    const [viewerItem] = galleryViewerItems([seed], {}, labels);
    expect(viewerItem.url).toBe(seed.url);
    expect(viewerItem.thumbnailUrl).toBe(seed.thumbnailUrl);
  });

  it("reports a genuinely unavailable item as unavailable rather than showing a stale seed", () => {
    // The one case where discarding the seed is right: the server has said this
    // media is gone. "Not available" is honest; a seed that will never load
    // again is not.
    const seed = item();
    const [viewerItem] = galleryViewerItems(
      [seed],
      { [seed.key]: { url: "", thumbnailUrl: "", unavailable: true } },
      labels
    );
    expect(viewerItem.url).toBe("");
    expect(viewerItem.subtitle).toContain("Not available");
  });

  it("keys the cache on media identity, never on the item's position or its bare id", () => {
    // `attachment_id` and `media_upload_id` are autoincrements from different
    // tables whose ranges overlap, so a bare integer names two different files.
    // Save, share and offline reuse all key on this.
    const [viewerItem] = galleryViewerItems([item()], {}, labels);
    expect(viewerItem.cacheIdentity).toBe("media_upload:87");
  });

  it("carries the MIME type forward, which is what names the file on disk", () => {
    // The access URL's path ends in `/download`, so the MIME type is the only
    // thing left that can give the cached file an extension — and Photos routes
    // on the extension, not on the bytes. Dropping it here is why "Save to
    // Photos" failed on a photo the user was looking at.
    const [viewerItem] = galleryViewerItems([item({ mimeType: "image/jpeg" })], {}, labels);
    expect(viewerItem.mimeType).toBe("image/jpeg");
  });
});

/**
 * The second half of the same chooser: which URL is the FILE.
 *
 * Everything above is about what the player is pointed at. Once conversation
 * video started playing back through Mux, `item.url` stopped being a file at
 * all — it is an HLS manifest, a text playlist naming segments. That is the
 * point of it (§9/§21: first frame from the manifest plus one segment, never a
 * full download), and it is also why Save to Photos and Share cannot use it.
 *
 * The failure mode is what makes this worth testing rather than reasoning
 * about: handing a manifest to the downloader does not raise. The transfer
 * succeeds, the cache gets a `.m3u8`, and Photos rejects the write — so the
 * user is told their library refused a video they are watching, and the only
 * error surfaces four layers from the cause.
 */
describe("playback source and downloadable file are chosen separately", () => {
  const grant = {
    url: "https://pulsesoc.com/api/messages/media/87/download?mt=grant",
    thumbnailUrl: "",
    unavailable: false
  };

  it("plays the manifest even though the grant is loadable", () => {
    // Everywhere else in this function the grant wins, because it is the
    // freshest credential. Here it must not: a grant is a progressive transfer
    // of the whole file, so preferring it over the manifest throws away the
    // only mechanism that starts a video on one segment. The manifest is
    // already signed and membership-gated by the time the server sends it, so
    // nothing is given up.
    const seed = item({ url: "https://stream.mux.com/pb601.m3u8?token=eyJ.abc.def" });
    const [viewerItem] = galleryViewerItems([seed], { [seed.key]: grant }, labels);
    expect(viewerItem.url).toBe("https://stream.mux.com/pb601.m3u8?token=eyJ.abc.def");
  });

  it("downloads the grant, never the manifest", () => {
    const seed = item({ url: "https://stream.mux.com/pb601.m3u8?token=eyJ.abc.def" });
    const [viewerItem] = galleryViewerItems([seed], { [seed.key]: grant }, labels);
    expect(viewerItem.downloadUrl).toBe(grant.url);
  });

  it("recognises a manifest whose token hides the extension behind a query string", () => {
    // The trap this is here for. A signed manifest is `.m3u8?token=<jwt>`, so
    // any check against the whole URL — `endsWith(".m3u8")` on the raw string —
    // stops recognising manifests at exactly the moment messenger starts using
    // them, and does it silently. Asserted through the unsigned/signed pair so
    // the test cannot pass by accident.
    const unsigned = item({ url: "https://stream.mux.com/pb601.m3u8" });
    const signed = item({ url: "https://stream.mux.com/pb601.m3u8?token=eyJ.abc.def" });
    expect(galleryViewerItems([unsigned], { [unsigned.key]: grant }, labels)[0].url).toBe(unsigned.url);
    expect(galleryViewerItems([signed], { [signed.key]: grant }, labels)[0].url).toBe(signed.url);
  });

  it("still prefers the grant for playback when the item is not a stream", () => {
    // The control. Without this, "the seed always wins for video" would pass
    // every test above while reintroducing the site-relative-grant bug for the
    // progressive path that most conversation video is still served over.
    const seed = item();
    const [viewerItem] = galleryViewerItems([seed], { [seed.key]: grant }, labels);
    expect(viewerItem.url).toBe(grant.url);
  });

  it("falls back to the wire's download URL, absolutized, when no grant has resolved", () => {
    // A relative URL is the original black-screen bug in a different costume:
    // the native downloader has no origin to resolve it against and fails
    // without a useful error.
    const seed = item({
      url: "https://stream.mux.com/pb601.m3u8?token=eyJ.abc.def",
      downloadUrl: "/api/messages/media/87/download"
    });
    const [viewerItem] = galleryViewerItems([seed], {}, labels);
    expect(viewerItem.downloadUrl).toBe("https://pulsesoc.com/api/messages/media/87/download");
  });

  it("reports no file at all rather than offering the playlist as one", () => {
    // Empty is honest: the viewer falls back to `url` and the download fails
    // where it can be reported. Returning the manifest would instead assert
    // that a playlist is a movie, which is the silent failure.
    const seed = item({ url: "https://stream.mux.com/pb601.m3u8?token=eyJ.abc.def", downloadUrl: "" });
    const [viewerItem] = galleryViewerItems([seed], {}, labels);
    expect(viewerItem.downloadUrl).toBe("");
  });

  it("offers no file for an item the server says is gone", () => {
    const seed = item({ downloadUrl: "/api/messages/media/87/download" });
    const [viewerItem] = galleryViewerItems(
      [seed],
      { [seed.key]: { url: "", thumbnailUrl: "", unavailable: true } },
      labels
    );
    expect(viewerItem.downloadUrl).toBe("");
  });

  it("leaves a photo's single URL as both", () => {
    // Images have one resource. The split must not make Save depend on a field
    // the server only sends for video.
    const seed = item({ kind: "image", url: "https://cdn.example/601.jpg", downloadUrl: "" });
    const [viewerItem] = galleryViewerItems([seed], {}, labels);
    expect(viewerItem.url).toBe("https://cdn.example/601.jpg");
    expect(viewerItem.downloadUrl).toBe("https://cdn.example/601.jpg");
  });
});
