import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn()
}));

// ContentTranslation reaches for the translation API and a provider. The thread
// contract under test is which controls appear and what they do, not how a body
// is translated, so it is stubbed down to the text it renders.
jest.mock("../../components/ContentTranslation", () => {
  const { Text } = require("react-native");
  return { ContentTranslation: ({ text }: { text: string }) => <Text>{text}</Text> };
});

import type { PulseComment } from "../../api/feed";
import { CommentThread, replyToggleLabel, viewerMayDeleteComment, viewerMayEditComment } from "../CommentThread";

const VIEWER = 7;

function comment(overrides: Partial<PulseComment> = {}): PulseComment {
  return {
    id: 1,
    comment_id: 1,
    body: "hello",
    created_at: "2026-07-25T00:00:00",
    author: { user_id: VIEWER, username: "viewer", display_name: "Viewer" },
    user_id: VIEWER,
    ...overrides
  } as PulseComment;
}

function allHandlers() {
  return {
    onReply: jest.fn(),
    onReact: jest.fn(),
    onBeginEdit: jest.fn(),
    onDelete: jest.fn(),
    onReport: jest.fn(),
    onToggleReplies: jest.fn(),
    onAuthorPress: jest.fn(),
    onMentionPress: jest.fn()
  };
}

describe("permission precedence", () => {
  // The whole point of adding viewer-scoped can_edit/can_delete to the server
  // was so the client stops guessing. An OR against the local user id — which is
  // what ReelsScreen did — reintroduces the guess and renders controls the
  // server will refuse.

  it("honors an explicit server refusal even when the viewer wrote the comment", () => {
    const target = comment({ can_edit: false, can_delete: false, user_id: VIEWER });
    expect(viewerMayEditComment(target, VIEWER)).toBe(false);
    expect(viewerMayDeleteComment(target, VIEWER)).toBe(false);
  });

  it("honors an explicit server grant even when the ids do not match", () => {
    const target = comment({ can_edit: true, can_delete: true, user_id: 999, author: { user_id: 999 } });
    expect(viewerMayEditComment(target, VIEWER)).toBe(true);
    expect(viewerMayDeleteComment(target, VIEWER)).toBe(true);
  });

  it("falls back to ownership only when the server stated nothing", () => {
    expect(viewerMayEditComment(comment({ user_id: VIEWER }), VIEWER)).toBe(true);
    expect(viewerMayEditComment(comment({ user_id: 999, author: { user_id: 999 } }), VIEWER)).toBe(false);
  });

  it("never treats an anonymous viewer as the author of an unattributed comment", () => {
    // 0 === 0 would otherwise grant edit on every authorless comment.
    const orphan = comment({ user_id: 0, author: {} });
    expect(viewerMayEditComment(orphan, 0)).toBe(false);
    expect(viewerMayDeleteComment(orphan, 0)).toBe(false);
  });

  it("lets a moderator delete a comment they did not write", () => {
    const target = comment({ can_moderate: true, user_id: 999, author: { user_id: 999 } });
    expect(viewerMayDeleteComment(target, VIEWER)).toBe(true);
  });
});

describe("rendered controls", () => {
  it("offers Delete to the author and Report to everyone else, never both", () => {
    const handlers = allHandlers();
    const mine = render(<CommentThread comment={comment({ can_delete: true })} currentUserId={VIEWER} handlers={handlers} />);
    expect(mine.queryByTestId("comment-delete-1")).not.toBeNull();
    expect(mine.queryByTestId("comment-report-1")).toBeNull();

    const theirs = render(<CommentThread comment={comment({ can_delete: false })} currentUserId={VIEWER} handlers={handlers} />);
    expect(theirs.queryByTestId("comment-delete-1")).toBeNull();
    expect(theirs.queryByTestId("comment-report-1")).not.toBeNull();
  });

  it("omits a control entirely when the screen supplies no handler for it", () => {
    // A button that does nothing is worse than an absent button.
    const { queryByTestId } = render(<CommentThread comment={comment({ can_delete: true })} currentUserId={VIEWER} handlers={{}} />);
    expect(queryByTestId("comment-reply-1")).toBeNull();
    expect(queryByTestId("comment-react-1")).toBeNull();
    expect(queryByTestId("comment-delete-1")).toBeNull();
    expect(queryByTestId("comment-report-1")).toBeNull();
  });

  it("passes the tapped comment to the handler, not just a signal", () => {
    const handlers = allHandlers();
    const target = comment({ id: 42, comment_id: 42, can_delete: true });
    const { getByTestId } = render(<CommentThread comment={target} currentUserId={VIEWER} handlers={handlers} />);
    fireEvent.press(getByTestId("comment-reply-42"));
    fireEvent.press(getByTestId("comment-react-42"));
    fireEvent.press(getByTestId("comment-delete-42"));
    expect(handlers.onReply).toHaveBeenCalledWith(target);
    expect(handlers.onReact).toHaveBeenCalledWith(target);
    expect(handlers.onDelete).toHaveBeenCalledWith(target);
  });

  it("refuses a second tap while that comment's request is in flight", () => {
    const handlers = allHandlers();
    const { getByTestId } = render(
      <CommentThread comment={comment({ can_delete: true })} currentUserId={VIEWER} handlers={handlers} busyIds={new Set([1])} />
    );
    fireEvent.press(getByTestId("comment-react-1"));
    fireEvent.press(getByTestId("comment-delete-1"));
    expect(handlers.onReact).not.toHaveBeenCalled();
    expect(handlers.onDelete).not.toHaveBeenCalled();
  });

  it("marks a busy control as busy for assistive technology", () => {
    const { getByTestId } = render(
      <CommentThread comment={comment()} currentUserId={VIEWER} handlers={allHandlers()} busyIds={new Set([1])} />
    );
    expect(getByTestId("comment-react-1").props.accessibilityState).toMatchObject({ busy: true, disabled: true });
  });

  it("reports the viewer's own reaction as selected rather than only recoloring it", () => {
    const { getByTestId } = render(
      <CommentThread comment={comment({ viewer_reaction: "like" })} currentUserId={VIEWER} handlers={allHandlers()} />
    );
    expect(getByTestId("comment-react-1").props.accessibilityState).toMatchObject({ selected: true });
  });

  it("shows an Edited marker only once the server records an edit", () => {
    expect(render(<CommentThread comment={comment()} />).queryByText("Edited")).toBeNull();
    expect(render(<CommentThread comment={comment({ edited_at: "now" })} />).queryByText("Edited")).not.toBeNull();
  });
});

describe("reaction total", () => {
  it("sums every reaction type rather than showing only likes", () => {
    const { queryByText } = render(
      <CommentThread comment={comment({ reaction_counts: { like: 2, fire: 3 } })} currentUserId={VIEWER} handlers={allHandlers()} />
    );
    expect(queryByText("Like 5")).not.toBeNull();
  });

  it("falls back to like_count when no breakdown is supplied", () => {
    const { queryByText } = render(
      <CommentThread comment={comment({ like_count: 4 })} currentUserId={VIEWER} handlers={allHandlers()} />
    );
    expect(queryByText("Like 4")).not.toBeNull();
  });

  it("shows no number at all at zero instead of a bare 0", () => {
    const { queryByText } = render(<CommentThread comment={comment()} currentUserId={VIEWER} handlers={allHandlers()} />);
    expect(queryByText("Like")).not.toBeNull();
    expect(queryByText("Like 0")).toBeNull();
  });
});

describe("nesting", () => {
  function withReplies(): PulseComment {
    return comment({
      id: 1,
      comment_id: 1,
      replies: [
        comment({ id: 2, comment_id: 2, body: "reply two", parent_comment_id: 1, replies: [comment({ id: 3, comment_id: 3, body: "nested three", parent_comment_id: 2 })] })
      ]
    });
  }

  it("hides replies until the thread is expanded", () => {
    const { queryByTestId } = render(<CommentThread comment={withReplies()} currentUserId={VIEWER} />);
    expect(queryByTestId("comment-1")).not.toBeNull();
    expect(queryByTestId("comment-2")).toBeNull();
  });

  it("renders replies when the parent id is in the expanded set", () => {
    const { queryByTestId } = render(<CommentThread comment={withReplies()} currentUserId={VIEWER} expandedIds={new Set([1])} />);
    expect(queryByTestId("comment-2")).not.toBeNull();
    // The grandchild stays hidden: expansion is per-comment, not cascading.
    expect(queryByTestId("comment-3")).toBeNull();
  });

  it("renders a grandchild once its own parent is expanded too", () => {
    const { queryByTestId } = render(<CommentThread comment={withReplies()} currentUserId={VIEWER} expandedIds={new Set([1, 2])} />);
    expect(queryByTestId("comment-3")).not.toBeNull();
  });

  it("offers no expand affordance for a comment with no replies", () => {
    const { queryByTestId } = render(<CommentThread comment={comment()} currentUserId={VIEWER} handlers={allHandlers()} />);
    expect(queryByTestId("comment-toggle-replies-1")).toBeNull();
  });

  it("reports expansion state to assistive technology", () => {
    const collapsed = render(<CommentThread comment={withReplies()} handlers={allHandlers()} />);
    expect(collapsed.getByTestId("comment-toggle-replies-1").props.accessibilityState).toMatchObject({ expanded: false });
    const open = render(<CommentThread comment={withReplies()} handlers={allHandlers()} expandedIds={new Set([1])} />);
    expect(open.getByTestId("comment-toggle-replies-1").props.accessibilityState).toMatchObject({ expanded: true });
  });

  it("passes the comment to toggle so one handler can serve every depth", () => {
    const handlers = allHandlers();
    const { getByTestId } = render(<CommentThread comment={withReplies()} handlers={handlers} expandedIds={new Set([1])} />);
    fireEvent.press(getByTestId("comment-toggle-replies-2"));
    expect(handlers.onToggleReplies).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }));
  });

  it("pluralizes the reply count correctly", () => {
    expect(replyToggleLabel(1, false)).toBe("View 1 reply");
    expect(replyToggleLabel(3, false)).toBe("View 3 replies");
    expect(replyToggleLabel(3, true)).toBe("Hide replies");
  });

  it("stops indenting past the cap but still renders the comment", () => {
    // Unbounded indentation walks a deep thread off the right edge of a phone.
    let deepest = comment({ id: 9, comment_id: 9, body: "deepest" });
    for (let id = 8; id >= 1; id -= 1) deepest = comment({ id, comment_id: id, body: `c${id}`, replies: [deepest] });
    const expanded = new Set([1, 2, 3, 4, 5, 6, 7, 8]);
    const { getByTestId } = render(<CommentThread comment={deepest} expandedIds={expanded} />);
    expect(getByTestId("comment-9")).not.toBeNull();
    const marginAt = (id: number) => {
      const style = getByTestId(`comment-${id}`).props.style.flat().filter(Boolean);
      return style.reduce((found: number, entry: { marginLeft?: number }) => (entry?.marginLeft ?? found), 0);
    };
    expect(marginAt(5)).toBe(marginAt(9));
  });
});

describe("mentions", () => {
  it("renders a mention as its own tappable span carrying the username", () => {
    const handlers = allHandlers();
    const { getByTestId } = render(
      <CommentThread comment={comment({ body: "thanks @roody for this" })} handlers={handlers} />
    );
    fireEvent.press(getByTestId("comment-mention-1-roody"));
    expect(handlers.onMentionPress).toHaveBeenCalledWith("roody");
  });

  it("does not turn an email address into a mention", () => {
    const { queryByTestId } = render(
      <CommentThread comment={comment({ body: "mail me at me@example.com" })} handlers={allHandlers()} />
    );
    expect(queryByTestId("comment-mention-1-example")).toBeNull();
  });

  it("keeps the body readable when a screen wires no mention handler", () => {
    const { queryByText } = render(<CommentThread comment={comment({ body: "thanks @roody" })} handlers={{}} />);
    expect(queryByText("thanks @roody")).not.toBeNull();
  });
});

describe("inline edit session", () => {
  function session(overrides = {}) {
    return { comment: comment(), body: "revised", busy: false, onChangeBody: jest.fn(), onSubmit: jest.fn(), onCancel: jest.fn(), ...overrides };
  }

  it("swaps the body for an input only on the comment being edited", () => {
    const edit = session({ comment: comment({ id: 2, comment_id: 2 }) });
    const { queryByTestId } = render(<CommentThread comment={comment()} currentUserId={VIEWER} edit={edit} handlers={allHandlers()} />);
    expect(queryByTestId("comment-edit-input-1")).toBeNull();
  });

  it("shows the draft and submits it", () => {
    const edit = session();
    const { getByTestId } = render(<CommentThread comment={comment()} currentUserId={VIEWER} edit={edit} handlers={allHandlers()} />);
    expect(getByTestId("comment-edit-input-1").props.value).toBe("revised");
    fireEvent.press(getByTestId("comment-edit-save-1"));
    expect(edit.onSubmit).toHaveBeenCalled();
  });

  it("refuses to submit an empty edit, which would read as a silent delete", () => {
    const edit = session({ body: "   " });
    const { getByTestId } = render(<CommentThread comment={comment()} currentUserId={VIEWER} edit={edit} handlers={allHandlers()} />);
    fireEvent.press(getByTestId("comment-edit-save-1"));
    expect(edit.onSubmit).not.toHaveBeenCalled();
  });

  it("refuses a second submit while the first is saving", () => {
    const edit = session({ busy: true });
    const { getByTestId } = render(<CommentThread comment={comment()} currentUserId={VIEWER} edit={edit} handlers={allHandlers()} />);
    fireEvent.press(getByTestId("comment-edit-save-1"));
    expect(edit.onSubmit).not.toHaveBeenCalled();
  });
});
