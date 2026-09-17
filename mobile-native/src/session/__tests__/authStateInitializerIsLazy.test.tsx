import { readFileSync } from "fs";
import { join } from "path";
import React, { useState } from "react";
import { Text } from "react-native";
import { render } from "@testing-library/react-native";

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

/**
 * `stateFor` is a constructor with module-level side effects: it sets the media
 * cache scope, sets the mutation outbox scope, and cancels any in-flight message
 * notification reconciliation. That is deliberate -- auth.ts explains that
 * centralising the scope assignment is what stops six separate call sites from
 * forgetting one.
 *
 * The consequence is that *evaluating* `stateFor(...)` is not free, and React's
 * `useState(value)` evaluates its argument on every render. It only *uses* the
 * result on the first render, which is what makes the mistake invisible: the
 * state is correct, and the damage is entirely in the discarded side effects.
 *
 * In AppRoot the discarded call is `stateFor("BOOTSTRAPPING")`, which resolves
 * to a null scope. So on every re-render of the app root the outbox scope was
 * reset to anonymous and the reconciliation generation was bumped -- for the
 * whole session, because nothing re-asserts the scope until the next auth
 * transition. `reconcileMessageNotifications` early-returns when the scope is
 * not `u<id>`, so read-message dismissal silently never ran for a signed-in
 * user after the app root re-rendered even once.
 */
describe("a side-effecting useState initializer", () => {
  function Probe({ initializer, label }: { initializer: () => unknown; label: string }) {
    const [value] = useState(initializer as () => never);
    return <Text>{`${label}:${String(value)}`}</Text>;
  }

  function EagerProbe({ effect }: { effect: () => string }) {
    // The shape AppRoot used: the call happens, then React throws the value away.
    const [value] = useState<string>(effect());
    return <Text>{value}</Text>;
  }

  it("re-runs on every render when it is not wrapped in a function", () => {
    const effect = jest.fn(() => "phase");
    const view = render(<EagerProbe effect={effect} />);
    expect(effect).toHaveBeenCalledTimes(1);
    view.rerender(<EagerProbe effect={effect} />);
    view.rerender(<EagerProbe effect={effect} />);
    // Three renders, three calls -- two of which React discarded.
    expect(effect).toHaveBeenCalledTimes(3);
  });

  it("runs exactly once when it is wrapped -- the positive control", () => {
    const effect = jest.fn(() => "phase");
    const view = render(<Probe initializer={effect} label="lazy" />);
    view.rerender(<Probe initializer={effect} label="lazy" />);
    view.rerender(<Probe initializer={effect} label="lazy" />);
    expect(effect).toHaveBeenCalledTimes(1);
  });
});

describe("AppRoot's auth state initializer", () => {
  const source = readFileSync(join(__dirname, "..", "..", "..", "App.tsx"), "utf8");

  it("is lazy, so stateFor's scope reset cannot fire on re-render", () => {
    const initializer = source.match(/useState<AuthState>\((.*?)\);/);
    expect(initializer).not.toBeNull();
    const argument = (initializer as RegExpMatchArray)[1];
    // `useState(stateFor("BOOTSTRAPPING"))` calls it every render.
    // `useState(() => stateFor("BOOTSTRAPPING"))` calls it once.
    expect(argument).toMatch(/^\(\s*\)\s*=>/);
  });

  it("still initialises from BOOTSTRAPPING rather than some other phase", () => {
    // Guards the obvious wrong fix: making it lazy but changing the phase.
    expect(source).toMatch(/useState<AuthState>\(\(\)\s*=>\s*stateFor\("BOOTSTRAPPING"\)\)/);
  });
});
