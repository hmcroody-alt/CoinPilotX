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
 * `goBackFromUndxChat` — the exact function ChatScreen's back button calls.
 */

import React, { useEffect } from "react";
import { Pressable, Text, View } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { act, fireEvent, render } from "@testing-library/react-native";

import {
  assetReturnTarget,
  goBackFromUndxChat,
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
      <Text>Chat:UNDX</Text>
      <Pressable onPress={() => goBackFromUndxChat(navigation, route.params.undxReturn)}>
        <Text>ChatBack</Text>
      </Pressable>
    </View>
  );
}

function TabsStub() {
  return <Text>Tabs:Dashboard</Text>;
}

function makeApp(initial: {
  routeName: keyof TestStackParams;
  chatParams?: UndxChatTarget;
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
        <Stack.Screen name="Tabs" component={TabsStub} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}

beforeEach(() => {
  assetDetailMounts = 0;
});

describe("the contextual drill-in round trip", () => {
  it("AssetDetail → Ask UNDX → Chat → Back lands on the SAME AssetDetail, no restart", async () => {
    const screen = render(makeApp({ routeName: "AssetDetail" }));
    await screen.findByText("AssetDetail:BTC");

    await act(async () => {
      fireEvent.press(screen.getByText("Ask UNDX"));
    });
    // The push keeps the asset screen alive beneath Chat rather than popping it.
    await screen.findByText("Chat:UNDX");

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
    await screen.findByText("Chat:UNDX");

    await act(async () => {
      fireEvent.press(screen.getByText("ChatBack"));
    });
    await screen.findByText("AssetDetail:BTC");
  });

  it("the tab entry, with no origin, lands on the dashboard rather than trapping", async () => {
    // The tab entry replaces itself with Chat, so an untouched stack holds
    // nothing beneath it. Back must still do something.
    const screen = render(makeApp({ routeName: "Chat", chatParams: undxChatTarget() }));
    await screen.findByText("Chat:UNDX");

    await act(async () => {
      fireEvent.press(screen.getByText("ChatBack"));
    });
    await screen.findByText("Tabs:Dashboard");
  });
});
