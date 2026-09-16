/**
 * The return path, proven against React Navigation itself.
 *
 * The unit tests in `undxChatTarget.test.ts` prove the params are right; this
 * file proves the *stack* is right, because the original trap-screen bug lived
 * in stack behaviour that no unit test could see: the params were correct, the
 * chip said the right words, and Back still did nothing. So these tests mount a
 * real `NavigationContainer` with a real native-stack navigator, drive it with
 * presses, and assert on which screen is on top afterwards.
 *
 * The screens are stubs, deliberately: mounting the real AssetDetail and Chat
 * screens would drag in the market API, sockets, and recording — none of which
 * decide where Back goes. What is real here is everything this regression is
 * about: the navigator, `undxChatTarget` / `assetReturnTarget` building the
 * route, `navigation.push` keeping the origin beneath Chat, and
 * `goBackFromChat` — the exact function ChatScreen's back button calls.
 *
 * The file has since grown a second regression, found on device rather than in
 * a test: the floor written for UNDX was being applied to every conversation,
 * so a thread opened from a notification or a deep link answered Back with
 * Mission Control. That one is a stack bug too — it only appears when the stack
 * is empty — so it belongs here, next to the trap screen, and for the same
 * reason: nothing about the params is wrong in either case.
 */

import React, { useEffect } from "react";
import { Pressable, Text, View } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { act, fireEvent, render } from "@testing-library/react-native";

import { PULSE_AI_CONVERSATION_ID } from "../../api/messenger";
import {
  assetReturnTarget,
  goBackFromChat,
  undxChatTarget,
  UndxChatTarget
} from "../undxChatTarget";

jest.mock("expo-file-system", () => ({ File: class {} }));

type TestStackParams = {
  AssetDetail: { symbol: string; name?: string };
  Chat: UndxChatTarget;
  Tabs: { screen?: string } | undefined;
};

const Stack = createNativeStackNavigator<TestStackParams>();

/** Counts mounts so "returned to the same screen" is provable, not assumed:
 * a goBack that reveals the original screen never remounts it, while a
 * navigate that rebuilt it would. */
let assetDetailMounts = 0;
/** Same trick for the tab host: proves Back popped to the live list rather than
 * navigating to a fresh one that happens to look the same. */
let tabsMounts = 0;

function AssetDetailStub({ route, navigation }: NativeStackScreenProps<TestStackParams, "AssetDetail">) {
  useEffect(() => {
    assetDetailMounts += 1;
  }, []);
  return (
    <View>
      <Text>{`AssetDetail:${route.params.symbol}`}</Text>
      <Pressable
        onPress={() =>
          navigation.push(
            "Chat",
            undxChatTarget({
              returnTo: assetReturnTarget({
                symbol: route.params.symbol,
                name: route.params.name
              })!
            })
          )
        }
      >
        <Text>Ask UNDX</Text>
      </Pressable>
    </View>
  );
}

function ChatStub({ route, navigation }: NativeStackScreenProps<TestStackParams, "Chat">) {
  return (
    <View>
      <Text>{`Chat:${route.params.conversationId}`}</Text>
      <Pressable
        onPress={() =>
          goBackFromChat(navigation, {
            conversationId: route.params.conversationId,
            undxReturn: route.params.undxReturn
          })
        }
      >
        <Text>ChatBack</Text>
      </Pressable>
    </View>
  );
}

/**
 * Renders the tab it was actually sent to.
 *
 * It used to render the literal string "Tabs:Dashboard" regardless of params,
 * which is why the suite could not see the bug: every floor looked like the
 * dashboard because the stub could not say anything else. A stub that answers
 * the same way for every input is not asserting, it is agreeing.
 */
function TabsStub({ route, navigation }: NativeStackScreenProps<TestStackParams, "Tabs">) {
  useEffect(() => {
    tabsMounts += 1;
  }, []);
  return (
    <View>
      <Text>{`Tabs:${route.params?.screen || "none"}`}</Text>
      {/* What MessengerScreen does when a row is tapped: a plain push onto the
          root stack, leaving the list alive underneath. */}
      <Pressable
        onPress={() => navigation.push("Chat", { conversationId: 6, title: "ROODY CHERIE" })}
      >
        <Text>OpenConversation</Text>
      </Pressable>
      <Pressable
        onPress={() =>
          navigation.push("Chat", {
            conversationId: 6,
            title: "ROODY CHERIE",
            undxReturn: assetReturnTarget({ symbol: "BTC", name: "Bitcoin" })!
          })
        }
      >
        <Text>OpenConversationWithOrigin</Text>
      </Pressable>
    </View>
  );
}

function makeApp(initial: {
  routeName: keyof TestStackParams;
  chatParams?: UndxChatTarget;
  tabsParams?: { screen?: string };
}) {
  return (
    <NavigationContainer>
      <Stack.Navigator initialRouteName={initial.routeName}>
        <Stack.Screen
          name="AssetDetail"
          component={AssetDetailStub}
          initialParams={{ symbol: "BTC", name: "Bitcoin" }}
        />
        <Stack.Screen name="Chat" component={ChatStub} initialParams={initial.chatParams} />
        <Stack.Screen name="Tabs" component={TabsStub} initialParams={initial.tabsParams} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}

beforeEach(() => {
  assetDetailMounts = 0;
  tabsMounts = 0;
});

describe("the contextual drill-in round trip", () => {
  it("AssetDetail → Ask UNDX → Chat → Back lands on the SAME AssetDetail, no restart", async () => {
    const screen = render(makeApp({ routeName: "AssetDetail" }));
    await screen.findByText("AssetDetail:BTC");

    await act(async () => {
      fireEvent.press(screen.getByText("Ask UNDX"));
    });
    // The push keeps the asset screen alive beneath Chat rather than popping it.
    await screen.findByText(`Chat:${PULSE_AI_CONVERSATION_ID}`);

    await act(async () => {
      fireEvent.press(screen.getByText("ChatBack"));
    });
    await screen.findByText("AssetDetail:BTC");
    // One mount total: Back revealed the original screen, it did not rebuild a
    // lookalike. This is the assertion the trap-screen bug would have failed.
    expect(assetDetailMounts).toBe(1);
  });
});

describe("when the stack cannot answer", () => {
  it("a lone Chat with a recorded origin goes back to that asset screen", async () => {
    // Simulates a restored session / deep link: Chat is the only entry, so
    // canGoBack() is false and the recorded undxReturn is the answer.
    const screen = render(
      makeApp({
        routeName: "Chat",
        chatParams: undxChatTarget({
          returnTo: assetReturnTarget({ symbol: "BTC", name: "Bitcoin" })!
        })
      })
    );
    await screen.findByText(`Chat:${PULSE_AI_CONVERSATION_ID}`);

    await act(async () => {
      fireEvent.press(screen.getByText("ChatBack"));
    });
    await screen.findByText("AssetDetail:BTC");
  });

  it("the UNDX tab entry, with no origin, lands on the dashboard rather than trapping", async () => {
    // The tab entry replaces itself with Chat, so an untouched stack holds
    // nothing beneath it. Back must still do something.
    const screen = render(makeApp({ routeName: "Chat", chatParams: undxChatTarget() }));
    await screen.findByText(`Chat:${PULSE_AI_CONVERSATION_ID}`);

    await act(async () => {
      fireEvent.press(screen.getByText("ChatBack"));
    });
    await screen.findByText("Tabs:Dashboard");
  });

  it("a conversation with a person lands on the conversations list, not the dashboard", async () => {
    // The device repro, as a test. `pulsesoc://pulse/messages/6` cold-launches
    // straight onto Chat — and a tapped message notification performs the same
    // `navigate` — so the stack is empty and there is no `undxReturn` to fall
    // back to. Before this, the floor written for UNDX answered, and the member
    // landed on Mission Control from a thread they had opened on purpose.
    const screen = render(
      makeApp({ routeName: "Chat", chatParams: { conversationId: 6, title: "ROODY CHERIE" } })
    );
    await screen.findByText("Chat:6");

    await act(async () => {
      fireEvent.press(screen.getByText("ChatBack"));
    });
    await screen.findByText("Tabs:Messenger");
    // Named rather than implied: "not the dashboard" is the whole defect, and a
    // findByText that passes tells you where it went but not where it didn't.
    expect(screen.queryByText("Tabs:Dashboard")).toBeNull();
  });

  it("a room conversation gets the conversations list too", async () => {
    // Rooms and groups render through the same screen and appear in the same
    // list. Nothing about the floor should depend on how many people are in the
    // thread — only on whether it is the one conversation that has no list.
    const screen = render(
      makeApp({ routeName: "Chat", chatParams: { conversationId: 29, title: "Live Stage", roomId: "4" } })
    );
    await screen.findByText("Chat:29");

    await act(async () => {
      fireEvent.press(screen.getByText("ChatBack"));
    });
    await screen.findByText("Tabs:Messenger");
  });
});

describe("the stack still outranks the floor", () => {
  it("a conversation pushed on top of the messages list goes back by popping it", async () => {
    // The ordinary path, and the one that was never broken — pinned because the
    // fix touched the same function. A real entry beneath Chat must win over
    // both the recorded origin and the floor, since it is the only one of the
    // three that knows where the member actually came from.
    const screen = render(makeApp({ routeName: "Tabs", tabsParams: { screen: "Messenger" } }));
    await screen.findByText("Tabs:Messenger");

    await act(async () => {
      fireEvent.press(screen.getByText("OpenConversation"));
    });
    await screen.findByText("Chat:6");

    await act(async () => {
      fireEvent.press(screen.getByText("ChatBack"));
    });
    await screen.findByText("Tabs:Messenger");
    // A pop, not a re-navigate: the list underneath was never rebuilt.
    expect(tabsMounts).toBe(1);
  });

  it("a live stack entry beats a recorded origin for a human conversation as well", async () => {
    // `undxReturn` is a belt for when the stack is empty, never a preference.
    // A conversation carrying one — a future caller, a restored param — must
    // still pop to what is really underneath.
    const screen = render(makeApp({ routeName: "Tabs", tabsParams: { screen: "Messenger" } }));
    await screen.findByText("Tabs:Messenger");

    await act(async () => {
      fireEvent.press(screen.getByText("OpenConversationWithOrigin"));
    });
    await screen.findByText("Chat:6");

    await act(async () => {
      fireEvent.press(screen.getByText("ChatBack"));
    });
    await screen.findByText("Tabs:Messenger");
    expect(screen.queryByText("AssetDetail:BTC")).toBeNull();
  });
});
