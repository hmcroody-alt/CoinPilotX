/**
 * The last mile: a tag the creator chose has to arrive in the request body.
 *
 * Everything else about this feature can be right and this one link can be
 * missing, with no symptom anywhere. `createPost` builds its body from an
 * explicit whitelist rather than spreading its payload, so a field the composer
 * forgets to pass — or passes under the wrong name — is dropped silently on the
 * client, arrives as an absent key, and is read by the server as "no products".
 * The post publishes. The creator sees success. Nothing logs. That bug existed
 * in this file and was invisible to 9,000 green tests, because every test in the
 * feature ran below the composer.
 *
 * So these tests assert the *argument to `createPost`*, never that the picker
 * rendered or that a handler was called. The picker itself is mocked to a prop
 * recorder: rendering it here would re-test `TaggableProductPicker.test.tsx` and
 * would keep passing if the composer never read its `onChange`.
 *
 * The second claim is about a loss the creator cannot see. Only the feed-post
 * path sends tags; reels and statuses are different server contracts. Tags left
 * in state through a switch to Reel would be dropped at publish with no message,
 * which is the same silent-success shape the whole endpoint exists to remove. So
 * switching modes clears them and says so, and that is asserted rather than
 * trusted.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn().mockResolvedValue(null),
  setItem: jest.fn().mockResolvedValue(undefined),
  removeItem: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("expo-av", () => ({ Audio: { Sound: { createAsync: jest.fn() }, setAudioModeAsync: jest.fn() } }));
jest.mock("../../api/feed", () => ({
  createPost: jest.fn(),
  listFeed: jest.fn().mockResolvedValue({ posts: [] })
}));
jest.mock("../../api/reels", () => ({ createReel: jest.fn(), listReels: jest.fn().mockResolvedValue({ reels: [] }) }));
jest.mock("../../api/status", () => ({ createStatus: jest.fn() }));
jest.mock("../../api/composerMusic", () => ({ suggestComposerMusic: jest.fn().mockResolvedValue([]) }));
jest.mock("../../api/music", () => ({
  composerMusicTrackFromPulseMusic: jest.fn(),
  consumePulseMusicSelection: jest.fn().mockResolvedValue(null)
}));
jest.mock("../../create/createComposerHandoff", () => ({
  consumeCreateCameraCaptureResult: jest.fn().mockResolvedValue(null)
}));
jest.mock("../../sharing/shareComposerHandoff", () => ({
  consumeShareComposerHandoff: jest.fn().mockResolvedValue(null),
  mergeShareIntoComposerBody: jest.fn((body: string) => body)
}));

/**
 * A prop recorder, not the real sheet. The composer's contract is "whatever the
 * picker reports, publish it" — asserting that through the real picker would
 * couple this file to the picker's fetch and its row layout.
 *
 * Typed from the component's own props rather than with an inline shape, so that
 * renaming a prop on the picker fails `tsc` here instead of silently making
 * `pickerProps!.onChange` undefined at runtime — a mock with a hand-written
 * shape is a second declaration of the interface, free to drift from the first.
 * The import is `import type`, erased at compile time, so it does not defeat the
 * `jest.mock` above it.
 */
let pickerProps: TaggableProductPickerProps | null = null;
jest.mock("../../commerce/TaggableProductPicker", () => ({
  TaggableProductPicker: (props: any) => {
    pickerProps = props;
    return null;
  }
}));

import AsyncStorage from "@react-native-async-storage/async-storage";
import { createPost } from "../../api/feed";
import type { TaggableProductPickerProps } from "../../commerce/TaggableProductPicker";
import { HomePulseComposer } from "../HomePulseComposer";

const createPostMock = createPost as jest.MockedFunction<typeof createPost>;

function renderComposer() {
  return render(
    <HomePulseComposer
      onCreated={jest.fn()}
      onOpenCamera={jest.fn()}
      onOpenMusic={jest.fn()}
      onOpenRoute={jest.fn()}
      initiallyExpanded
    />
  );
}

/**
 * Opens the More panel if it is closed, then the product picker inside it.
 *
 * The `more` button is a toggle, so pressing it unconditionally on a second call
 * closes the panel and hides the very button we are about to look for.
 */
async function openPicker(view: ReturnType<typeof renderComposer>) {
  if (!view.queryByTestId("home-composer-tag-products")) {
    await act(async () => {
      fireEvent.press(view.getByTestId("home-composer-more"));
    });
  }
  await act(async () => {
    fireEvent.press(view.getByTestId("home-composer-tag-products"));
  });
  await waitFor(() => expect(pickerProps).not.toBeNull());
}

beforeEach(() => {
  pickerProps = null;
  createPostMock.mockReset();
  createPostMock.mockResolvedValue({
    ok: true,
    post_id: 900,
    post: { id: 900, body: "New mug drop", moderation_status: "approved" }
  } as any);
});

describe("a chosen tag reaches the request body", () => {
  it("sends the listing ids the picker reported", async () => {
    const view = renderComposer();
    fireEvent.changeText(view.getByTestId("home-composer-input"), "New mug drop");
    await openPicker(view);

    await act(async () => {
      pickerProps!.onChange([43, 44]);
    });
    await act(async () => {
      fireEvent.press(view.getByTestId("home-composer-publish"));
    });

    await waitFor(() => expect(createPostMock).toHaveBeenCalled());
    expect(createPostMock.mock.calls[0][0]).toMatchObject({ product_listing_ids: [43, 44] });
  });

  it("sends an empty array when nothing was tagged", async () => {
    // "No products" is a statement, not an absence. The server treats a missing
    // key and an empty list identically, so this is about the field existing at
    // all: an omitted key is indistinguishable from the whitelist bug.
    const view = renderComposer();
    fireEvent.changeText(view.getByTestId("home-composer-input"), "Just a thought");
    await act(async () => {
      fireEvent.press(view.getByTestId("home-composer-publish"));
    });
    await waitFor(() => expect(createPostMock).toHaveBeenCalled());
    expect(createPostMock.mock.calls[0][0]).toMatchObject({ product_listing_ids: [] });
  });

  it("keeps the selection after the sheet closes", async () => {
    // Selection is lifted to the composer precisely so dismissing the sheet does
    // not discard it; held inside the picker it would vanish on unmount.
    const view = renderComposer();
    fireEvent.changeText(view.getByTestId("home-composer-input"), "New mug drop");
    await openPicker(view);
    await act(async () => {
      pickerProps!.onChange([43]);
    });
    await act(async () => {
      pickerProps!.onClose();
    });
    await openPicker(view);
    expect(pickerProps!.selectedIds).toEqual([43]);
  });
});

describe("a recovered draft keeps its tags", () => {
  function storedDraft(productListingIds: unknown) {
    return JSON.stringify({
      body: "New mug drop",
      mode: "post",
      visibility: "public",
      topic: "",
      productListingIds,
      savedAt: new Date().toISOString()
    });
  }

  it("restores the listings that were tagged before the app closed", async () => {
    // Dropping them here is a silent loss with no symptom: the creator reopens a
    // recovered draft, sees their text, and publishes a post they believe is
    // tagged. The music track is restored for the same reason.
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(storedDraft([43, 44]));
    const view = renderComposer();
    await waitFor(() => expect(view.getByTestId("home-composer-clear-draft")).toBeTruthy());
    await openPicker(view);
    expect(pickerProps!.selectedIds).toEqual([43, 44]);
  });

  it("sanitises what came back off disk instead of trusting it", async () => {
    // This is JSON from storage, so a truncated or hand-edited draft can hold
    // anything. A `NaN` reaching the payload would serialise to `null` and be
    // refused server-side for the wrong reason; a duplicate would ask the server
    // to store a set member twice.
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(storedDraft([43, "44", 0, -7, null, 43, "nope"]));
    const view = renderComposer();
    await waitFor(() => expect(view.getByTestId("home-composer-clear-draft")).toBeTruthy());
    await openPicker(view);
    expect(pickerProps!.selectedIds).toEqual([43, 44]);
  });
});

describe("a mode that cannot carry tags says so", () => {
  it("clears the tags when the creator switches to Reel", async () => {
    const view = renderComposer();
    fireEvent.changeText(view.getByTestId("home-composer-input"), "New mug drop");
    await openPicker(view);
    await act(async () => {
      pickerProps!.onChange([43]);
    });

    await act(async () => {
      fireEvent.press(view.getByTestId("home-composer-mode-reel"));
    });
    // Asserted through its own element, not through the status panel: that panel
    // mounts only when there is an error, a recovered draft, a failed publish or
    // queued media, so a `setNote` alone would be a message nobody renders —
    // which is what the first version of this did.
    expect(view.getByTestId("home-composer-product-tags-cleared")).toBeTruthy();
    // The text assertion is load-bearing now that this string is a catalogue key
    // rather than a literal, and it is the only check that can fail on a wrong
    // one. A key that does not resolve is not an error: the engine humanises the
    // last segment, so `commerce:discovery.tagging.cleared` under a mistyped
    // namespace renders the plausible-looking "Cleared" while `validate-i18n`
    // still reports 11 locales at 100% — it compares catalogues to `en` and never
    // asks what the app looks up. Pinning the English is what distinguishes
    // "translated" from "silently humanised".
    expect(view.getByText("Product tags removed: only feed posts carry them.")).toBeTruthy();

    // And the button is gone with them, rather than offering a choice that the
    // publish path would discard.
    expect(view.queryByTestId("home-composer-tag-products")).toBeNull();
  });

  it("does not announce a removal that did not happen", async () => {
    const view = renderComposer();
    await act(async () => {
      fireEvent.press(view.getByTestId("home-composer-mode-reel"));
    });
    expect(view.queryByTestId("home-composer-product-tags-cleared")).toBeNull();
  });

  it("retires the notice when the creator switches back to Feed", async () => {
    const view = renderComposer();
    await openPicker(view);
    await act(async () => {
      pickerProps!.onChange([43]);
    });
    await act(async () => {
      fireEvent.press(view.getByTestId("home-composer-mode-reel"));
    });
    expect(view.getByTestId("home-composer-product-tags-cleared")).toBeTruthy();
    await act(async () => {
      fireEvent.press(view.getByTestId("home-composer-mode-post"));
    });
    // Left up, it would read as a statement about the mode they are now in.
    expect(view.queryByTestId("home-composer-product-tags-cleared")).toBeNull();
  });
});
