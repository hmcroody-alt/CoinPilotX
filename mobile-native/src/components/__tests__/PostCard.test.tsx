import React from "react";
import { Alert, StyleSheet } from "react-native";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

jest.mock("expo-haptics", () => ({
  impactAsync: jest.fn().mockResolvedValue(undefined),
  notificationAsync: jest.fn().mockResolvedValue(undefined),
  selectionAsync: jest.fn().mockResolvedValue(undefined),
  ImpactFeedbackStyle: { Light: "light", Medium: "medium", Heavy: "heavy" },
  NotificationFeedbackType: { Success: "success", Warning: "warning", Error: "error" }
}));

jest.mock("expo-av", () => ({
  ResizeMode: { COVER: "cover", CONTAIN: "contain" },
  Video: () => null
}));

jest.mock("@expo/vector-icons", () => ({
  Ionicons: ({ name }: { name: string }) => name
}));

jest.mock("../NativeMediaViewer", () => ({
  NativeMediaViewer: () => null,
  mediaViewerItemFromPulseMedia: jest.fn()
}));

jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn(),
  releaseMediaPlayback: jest.fn()
}));

jest.mock("../../media/mediaAccess", () => ({
  canonicalMediaPlaybackUrl: (url: string) => url,
  refreshCanonicalMediaAccess: jest.fn().mockResolvedValue(undefined)
}));

import { PulsePost } from "../../api/feed";
import { resetSavedStoreForTests } from "../../social/savedStore";
import { PostCard, computeMediaBleedStyle } from "../PostCard";

function basePost(overrides: Partial<PulsePost> = {}): PulsePost {
  return {
    id: 42,
    body: "Hello from the feed",
    author: { display_name: "Ada", username: "ada" },
    created_at: new Date().toISOString(),
    ...overrides
  } as PulsePost;
}

describe("computeMediaBleedStyle", () => {
  it("returns no horizontal margin for inset layout", () => {
    expect(computeMediaBleedStyle("inset", 390, 366)).toEqual({ marginHorizontal: 0 });
  });

  it("computes symmetric negative bleed and full window width for fullBleed", () => {
    // window 390, card 366 -> bleed (390-366)/2 = 12
    expect(computeMediaBleedStyle("fullBleed", 390, 366)).toEqual({
      marginHorizontal: -12,
      width: 390
    });
  });

  it("falls back to no bleed when measurements are not ready", () => {
    expect(computeMediaBleedStyle("fullBleed", 0, 0)).toEqual({ marginHorizontal: 0 });
    expect(computeMediaBleedStyle("fullBleed", 390, 0)).toEqual({ marginHorizontal: 0 });
  });

  it("returns no bleed when the card already fills the window", () => {
    expect(computeMediaBleedStyle("fullBleed", 390, 390)).toEqual({ marginHorizontal: 0 });
  });
});

describe("PostCard blank-media collapse", () => {
  // Regression for the reported giant empty block on a PulseSoc Insight post.
  // The card used to gate its media container on `post.media.length`, so an
  // attachment whose URL never materialized counted as media and reserved a
  // full-bleed 4:5 box around nothing. The gate is now renderability.
  beforeEach(() => {
    resetSavedStoreForTests();
  });

  it("renders no media container when the only attachment has no usable url", () => {
    const { queryByTestId } = render(
      <PostCard
        post={basePost({
          media: [{ id: 7, media_type: "image", media_url: "", valid_url: "", width: 0, height: 0 }]
        })}
      />
    );
    expect(queryByTestId("home-feed-media-42-0")).toBeNull();
  });

  it("still renders media when a usable url is present", () => {
    const { getByTestId } = render(
      <PostCard
        post={basePost({
          media: [
            {
              id: 8,
              media_type: "image",
              media_url: "https://cdn.example/insight.png",
              valid_url: "https://cdn.example/insight.png",
              width: 1024,
              height: 1280
            }
          ]
        })}
      />
    );
    expect(getByTestId("home-feed-media-42-0")).toBeTruthy();
  });

  it("collapses the media region when the image fails to load", () => {
    const { getByTestId, queryByTestId } = render(
      <PostCard
        post={basePost({
          media: [
            { id: 9, media_type: "image", media_url: "https://cdn.example/gone.png", width: 1024, height: 1280 }
          ]
        })}
      />
    );
    const media = getByTestId("home-feed-media-42-0");
    fireEvent(media.findByType("Image" as never), "error");
    expect(queryByTestId("home-feed-media-42-0")).toBeNull();
  });

  it("keeps a whitespace-only url from counting as media", () => {
    const { queryByTestId } = render(
      <PostCard post={basePost({ media: [{ id: 10, media_type: "image", media_url: "   " }] })} />
    );
    expect(queryByTestId("home-feed-media-42-0")).toBeNull();
  });

  it("renders no media for the serializer's failed-image signature, without waiting on onError", () => {
    // This is the owner screenshot: an automated Insight post whose image never
    // generated. The feed serializer still hands back a populated `media_url`
    // (source path, unconditional) but blanks `valid_url`, marks the row
    // `is_available: false`, and zeroes the dimensions. The URL gate alone would
    // mount an <Image> around the broken source and only collapse after a load
    // error. The image gate refuses to mount it at all -- no box, no flash.
    const { queryByTestId, getAllByText } = render(
      <PostCard
        post={basePost({
          body: "Automated insight, image failed",
          media: [
            {
              id: 13,
              media_type: "image",
              media_url: "https://cdn.example/insight-13.png",
              valid_url: "",
              is_available: false,
              width: 0,
              height: 0
            }
          ]
        })}
      />
    );
    expect(queryByTestId("home-feed-media-42-0")).toBeNull();
    expect(getAllByText("Automated insight, image failed").length).toBeGreaterThan(0);
  });

  it("renders no media for an image carrying a url but zero dimensions and no aspect", () => {
    const { queryByTestId } = render(
      <PostCard
        post={basePost({
          media: [{ id: 14, media_type: "image", media_url: "https://cdn.example/nodims.png", width: 0, height: 0 }]
        })}
      />
    );
    expect(queryByTestId("home-feed-media-42-0")).toBeNull();
  });

  it("renders media when the only usable url is a gate-honored field the resolver used to ignore", () => {
    // The renderability gate accepts `valid_url`/`cdn_url`/`mux_hls_url`, but the
    // display resolver used to read none of them -- so this record passed the
    // gate, reserved its aspect box, and drew an <Image uri="">. The resolver now
    // covers every gate field, so it draws a real image instead of a blank one.
    const { getByTestId } = render(
      <PostCard
        post={basePost({
          media: [
            {
              id: 11,
              media_type: "image",
              media_url: "",
              valid_url: "https://cdn.example/valid-only.png",
              width: 1024,
              height: 1280
            }
          ]
        })}
      />
    );
    expect(getByTestId("home-feed-media-42-0")).toBeTruthy();
  });

  it("keeps the post text and actions readable after the image fails to load", () => {
    // Collapsing the media must never take the post down with it: body and the
    // shared action row stay mounted, and nothing throws.
    const { getAllByText, getByTestId, queryByTestId } = render(
      <PostCard
        post={basePost({
          body: "Insight still worth reading",
          media: [{ id: 12, media_type: "image", media_url: "https://cdn.example/gone.png", width: 1024, height: 1280 }]
        })}
        onSave={jest.fn()}
      />
    );
    fireEvent(getByTestId("home-feed-media-42-0").findByType("Image" as never), "error");
    expect(queryByTestId("home-feed-media-42-0")).toBeNull();
    expect(getAllByText("Insight still worth reading").length).toBeGreaterThan(0);
    expect(getByTestId("home-feed-save-42")).toBeTruthy();
  });
});

describe("PostCard save action", () => {
  // The card reads its saved state from a module-level store so that every copy
  // of the same post agrees. That store outlives a render by design, so it has
  // to be cleared here or the first test to mention post 42 decides what every
  // later test sees.
  beforeEach(() => {
    resetSavedStoreForTests();
  });

  it("renders Save inside the shared action row when onSave is provided", () => {
    const { getByTestId } = render(<PostCard post={basePost()} onSave={jest.fn()} onComment={jest.fn()} />);
    expect(getByTestId("home-feed-save-42")).toBeTruthy();
  });

  it("renders Save even when no screen passes onSave, because Save belongs to the content", () => {
    // This assertion is the inverse of the one it replaces. Save used to render
    // only when a screen supplied `onSave`, which made the control a property of
    // the screen: the same post offered Save in the feed and nothing at all in
    // search results or an activity row. The card owns the action now, so a
    // surface can no longer drop it by forgetting a prop.
    const { getByTestId } = render(<PostCard post={basePost()} onComment={jest.fn()} />);
    expect(getByTestId("home-feed-save-42")).toBeTruthy();
  });

  it("shows unsaved state (Save label, not selected) by default", () => {
    const onSave = jest.fn();
    const { getByTestId, getByText } = render(<PostCard post={basePost({ saved: false })} onSave={onSave} />);
    const button = getByTestId("home-feed-save-42");
    expect(button.props.accessibilityState.selected).toBe(false);
    expect(getByText("Save")).toBeTruthy();
    fireEvent.press(button, { stopPropagation: jest.fn() });
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("shows saved state (Saved label, selected) when post is saved", () => {
    const { getByTestId, getByText } = render(<PostCard post={basePost({ saved: true })} onSave={jest.fn()} />);
    const button = getByTestId("home-feed-save-42");
    expect(button.props.accessibilityState.selected).toBe(true);
    expect(getByText("Saved")).toBeTruthy();
  });
});

describe("PostCard automated identity", () => {
  it("labels the official automated author without presenting a follow action", () => {
    const { getByText, getByLabelText, queryByTestId } = render(
      <PostCard post={basePost({ author: { display_name: "PulseSoc Insight", username: "pulsesoc_insight", automated: true, account_type: "PULSESOC_AUTOMATED" } })} onFollow={jest.fn()} />
    );
    expect(getByText("AUTOMATED")).toBeTruthy();
    expect(getByLabelText("Automated PulseSoc account")).toBeTruthy();
    expect(queryByTestId("home-feed-follow-42")).toBeNull();
  });
});

describe("PostCard options menu", () => {
  const handlers = {
    onPromote: jest.fn(),
    onReport: jest.fn(),
    onHide: jest.fn(),
    onBlock: jest.fn(),
    onMute: jest.fn(),
    onDelete: jest.fn()
  };

  function openMenu(props: Record<string, unknown> = handlers) {
    const utils = render(<PostCard post={basePost()} {...props} />);
    fireEvent.press(utils.getByTestId("home-feed-overflow-42"), { stopPropagation: jest.fn() });
    return utils;
  }

  beforeEach(() => {
    Object.values(handlers).forEach((fn) => fn.mockClear());
  });

  it("stacks every action vertically instead of wrapping them across the post", () => {
    // The bug this replaces: the menu was a `flexDirection: "row"` +
    // `flexWrap: "wrap"` strip, so six actions ran horizontally across the
    // bottom of the post and clipped on narrow screens. Each row now stretches
    // to the sheet width, which is what makes the list vertical.
    const { getByTestId } = openMenu();
    for (const key of ["promote", "report", "hide", "block", "mute", "delete"]) {
      const style = StyleSheet.flatten(getByTestId(`home-feed-${key}-42`).props.style);
      expect(style.alignSelf).toBe("stretch");
      expect(style.flexDirection).not.toBe("row");
    }
  });

  it("keeps every action reachable and still wired to its own handler", () => {
    // One render per action: tapping any of them closes the sheet, which is
    // the intended behaviour and also unmounts its siblings.
    for (const [key, handler] of [
      ["promote", handlers.onPromote],
      ["report", handlers.onReport],
      ["hide", handlers.onHide],
      ["block", handlers.onBlock],
      ["mute", handlers.onMute]
    ] as const) {
      const { getByTestId, queryByTestId, unmount } = openMenu();
      fireEvent.press(getByTestId(`home-feed-${key}-42`), { stopPropagation: jest.fn() });
      expect(handler).toHaveBeenCalledTimes(1);
      expect(queryByTestId(`home-feed-${key}-42`)).toBeNull();
      unmount();
    }
  });

  it("still confirms before deleting rather than deleting on tap", () => {
    const spy = jest.spyOn(Alert, "alert").mockImplementation(() => undefined);
    const { getByTestId } = openMenu();
    fireEvent.press(getByTestId("home-feed-delete-42"), { stopPropagation: jest.fn() });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(handlers.onDelete).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("withholds Delete from a post the viewer does not own", () => {
    // Ownership is decided by the screen, which passes `onDelete` only to the
    // owner. The menu must not synthesize the row from anything else.
    const { onDelete, ...withoutDelete } = handlers;
    const { queryByTestId } = openMenu(withoutDelete);
    expect(queryByTestId("home-feed-delete-42")).toBeNull();
    expect(queryByTestId("home-feed-report-42")).toBeTruthy();
  });

  it("closes when the backdrop outside the sheet is tapped", () => {
    const { getByTestId, queryByTestId } = openMenu();
    fireEvent.press(getByTestId("home-feed-overflow-dismiss-42"));
    expect(queryByTestId("home-feed-report-42")).toBeNull();
  });
});
