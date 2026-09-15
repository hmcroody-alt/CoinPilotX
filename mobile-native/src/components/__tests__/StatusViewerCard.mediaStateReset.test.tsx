/**
 * One broken Status must not blacken the ones after it.
 *
 * The viewer keeps a single mounted `StatusViewerCard` and pages a new `status`
 * prop through it. `failed` and `buffering` describe the media of one Status,
 * so unless they are cleared on the way past, the first item whose image or
 * stream refuses to load marks every item the user pages to afterwards as
 * broken too. That is the difference between "one Status is unavailable" and
 * the reported symptom, "Statuses only show a black screen" -- the user pages
 * through a whole tray of perfectly good photos and sees the first one's
 * failure on all of them.
 *
 * Both assertions matter. Recovering on the next Status is the fix; staying
 * failed while the same Status is on screen is the property the fix must not
 * trade away, because a reset keyed on the wrong thing (the `active` flag, a
 * re-render, the poster URL) would clear the error under the user and flip the
 * card back to a black rectangle with no explanation.
 */
import React from "react";
import { act, render } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

jest.mock("expo-av", () => {
  const ReactActual = jest.requireActual("react");
  return {
    ResizeMode: { COVER: "cover", CONTAIN: "contain" },
    Audio: { Sound: { createAsync: jest.fn() }, setAudioModeAsync: jest.fn().mockResolvedValue(undefined) },
    Video: ReactActual.forwardRef((_props: any, ref: any) => {
      ReactActual.useImperativeHandle(ref, () => ({
        playAsync: jest.fn().mockResolvedValue(undefined),
        pauseAsync: jest.fn().mockResolvedValue(undefined),
        stopAsync: jest.fn().mockResolvedValue(undefined)
      }));
      return null;
    })
  };
});

jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn().mockResolvedValue(true),
  releaseMediaPlayback: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("../../media/MediaGestureFeedback", () => {
  const ReactActual = jest.requireActual("react");
  return { LikeBurst: ReactActual.forwardRef(() => null) };
});
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn().mockResolvedValue({ ok: true }) }));
jest.mock("../ContentTranslation", () => {
  const { Text } = jest.requireActual("react-native");
  const ReactActual = jest.requireActual("react");
  return { ContentTranslation: ({ text }: any) => ReactActual.createElement(Text, null, text) };
});

import type { PulseStatus } from "../../api/status";
import { StatusViewerCard } from "../StatusViewerCard";

const CDN = "https://cdn.coinpilotx.app/pulse_media/4/2026/09/14/abc";

const INSETS = {
  frame: { x: 0, y: 0, width: 440, height: 956 },
  insets: { top: 62, left: 0, right: 0, bottom: 34 }
};

function photoStatus(id: number): PulseStatus {
  return {
    id,
    status_type: "photo",
    body: "",
    author: { id: 4, name: "Maria Cherie" },
    media: [
      {
        id: 700 + id,
        media_type: "image",
        mime_type: "image/jpeg",
        valid_url: `${CDN}/${id}.jpg`,
        media_url: `${CDN}/${id}.jpg`,
        cdn_url: `${CDN}/${id}.jpg`,
        poster_url: `${CDN}/${id}-cover-large.jpg`
      }
    ]
  } as unknown as PulseStatus;
}

const noop = () => undefined;

function renderCard(status: PulseStatus) {
  return render(
    <SafeAreaProvider initialMetrics={INSETS}>
      <StatusViewerCard
        status={status}
        active
        muted
        onPrevious={noop}
        onNext={noop}
        onToggleMuted={noop}
        onReact={noop}
        onReply={noop}
        onShare={noop}
        onMore={noop}
        onAuthorPress={noop}
      />
    </SafeAreaProvider>
  );
}

function images(view: ReturnType<typeof renderCard>) {
  // queryAll, not getAll: "no photo is mounted" is an expected outcome here,
  // and getAll throws rather than returning an empty list.
  return view.UNSAFE_queryAllByType(require("react-native").Image).filter((node: any) => {
    const style = Array.isArray(node.props.style) ? Object.assign({}, ...node.props.style) : node.props.style || {};
    return style.position === "absolute";
  });
}

describe("StatusViewerCard media state", () => {
  it("shows the photo, and says so when that photo will not load", () => {
    const view = renderCard(photoStatus(1));
    const [image] = images(view);
    expect(image.props.source).toEqual({ uri: `${CDN}/1.jpg` });

    act(() => image.props.onError());
    expect(view.queryByText("Status media is unavailable.")).not.toBeNull();
    expect(images(view)).toHaveLength(0);
  });

  it("keeps the failure while the same Status is still on screen", () => {
    const view = renderCard(photoStatus(1));
    act(() => images(view)[0].props.onError());
    expect(view.queryByText("Status media is unavailable.")).not.toBeNull();

    // A re-render with the same Status -- the viewer does this on every
    // progress tick -- must not quietly clear the error.
    view.rerender(
      <SafeAreaProvider initialMetrics={INSETS}>
        <StatusViewerCard
          status={photoStatus(1)}
          active
          muted={false}
          progress={0.5}
          onPrevious={noop}
          onNext={noop}
          onToggleMuted={noop}
          onReact={noop}
          onReply={noop}
          onShare={noop}
          onMore={noop}
          onAuthorPress={noop}
        />
      </SafeAreaProvider>
    );
    expect(view.queryByText("Status media is unavailable.")).not.toBeNull();
  });

  it("recovers on the next Status instead of inheriting the last one's failure", () => {
    const view = renderCard(photoStatus(1));
    act(() => images(view)[0].props.onError());
    expect(view.queryByText("Status media is unavailable.")).not.toBeNull();

    view.rerender(
      <SafeAreaProvider initialMetrics={INSETS}>
        <StatusViewerCard
          status={photoStatus(2)}
          active
          muted
          onPrevious={noop}
          onNext={noop}
          onToggleMuted={noop}
          onReact={noop}
          onReply={noop}
          onShare={noop}
          onMore={noop}
          onAuthorPress={noop}
        />
      </SafeAreaProvider>
    );

    expect(view.queryByText("Status media is unavailable.")).toBeNull();
    const [image] = images(view);
    expect(image.props.source).toEqual({ uri: `${CDN}/2.jpg` });
  });

  it("does not clear a Status the backend itself reports as gone", () => {
    // The reset drops a verdict carried over from the previous Status. It must
    // not drop the current Status's own evidence: `is_available: false` is the
    // record saying the bytes are gone, and re-reporting it on arrival is the
    // whole point of reading it from the record instead of waiting for the
    // player to fail.
    const view = renderCard(photoStatus(1));
    expect(images(view)).toHaveLength(1);

    const gone = photoStatus(2);
    (gone.media as any)[0].is_available = false;
    view.rerender(
      <SafeAreaProvider initialMetrics={INSETS}>
        <StatusViewerCard
          status={gone}
          active
          muted
          onPrevious={noop}
          onNext={noop}
          onToggleMuted={noop}
          onReact={noop}
          onReply={noop}
          onShare={noop}
          onMore={noop}
          onAuthorPress={noop}
        />
      </SafeAreaProvider>
    );

    expect(view.queryByText("Status media is unavailable.")).not.toBeNull();
    expect(images(view)).toHaveLength(0);
  });
});
