/**
 * A presence dot — a green dot with a white ring, drawn only when the product
 * actually exposes mutual presence (the screen gates this behind a flag; the
 * component just draws). Green is the shared "positive / live" idea used by the
 * typing indicator and reply-time accent too.
 *
 * Motion: a slow 2.2s ping ring expands and fades behind the dot. Under
 * reduce-motion the ring is not scheduled and the dot is solid — presence is
 * still conveyed, just without animation. The ping also stops while the app is
 * backgrounded so a hidden screen never burns a frame callback.
 */

import { useEffect, useRef } from "react";
import { Animated, StyleSheet, View } from "react-native";
import { messagesLight } from "../../theme/messagesLight";
import { useAppForegrounded } from "../../theme/storeMotion";

const PING_MS = 2200;

export function PresenceDot({
  reducedMotion,
  size = messagesLight.size.presenceDot
}: {
  reducedMotion: boolean;
  size?: number;
}) {
  const ping = useRef(new Animated.Value(0)).current;
  const foreground = useAppForegrounded();

  useEffect(() => {
    if (reducedMotion || !foreground) {
      ping.setValue(0);
      return;
    }
    const loop = Animated.loop(
      Animated.timing(ping, {
        toValue: 1,
        duration: PING_MS,
        useNativeDriver: true
      })
    );
    loop.start();
    return () => loop.stop();
  }, [ping, reducedMotion, foreground]);

  const ringScale = ping.interpolate({ inputRange: [0, 1], outputRange: [1, 2.4] });
  const ringOpacity = ping.interpolate({ inputRange: [0, 1], outputRange: [0.45, 0] });

  return (
    <View style={{ width: size, height: size }} pointerEvents="none">
      {!reducedMotion ? (
        <Animated.View
          style={[
            styles.ring,
            {
              width: size,
              height: size,
              borderRadius: size / 2,
              opacity: ringOpacity,
              transform: [{ scale: ringScale }]
            }
          ]}
        />
      ) : null}
      <View
        style={[
          styles.dot,
          {
            width: size,
            height: size,
            borderRadius: size / 2,
            borderWidth: Math.max(2, Math.round(size / 6))
          }
        ]}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  ring: {
    position: "absolute",
    backgroundColor: messagesLight.presence.dot
  },
  dot: {
    position: "absolute",
    backgroundColor: messagesLight.presence.dot,
    borderColor: messagesLight.presence.ring
  }
});
