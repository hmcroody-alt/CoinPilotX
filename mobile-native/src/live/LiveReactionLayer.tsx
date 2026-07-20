import { forwardRef, memo, useCallback, useImperativeHandle, useRef, useState } from "react";
import { Animated, Easing, StyleSheet, Text, View } from "react-native";

/**
 * LiveReactionLayer
 *
 * A lightweight floating-reaction layer for the live stage. Reactions rise from
 * the lower-right, drift horizontally, scale up then fade — the classic live
 * "heart burst" — driven entirely by the native driver so it never touches the
 * JS thread during the stream.
 *
 * Imperative API via ref: `ref.current.burst("❤️")`. Each particle owns its own
 * Animated.Value and removes itself on completion, so there are no timers or
 * shared animation state to leak.
 */

export type ReactionLayerHandle = {
  burst: (emoji?: string) => void;
};

const EMOJIS = ["❤️", "🔥", "👏", "💜", "✨", "🙌"];

let particleSeq = 0;

type Particle = {
  id: number;
  emoji: string;
  startX: number;
  drift: number;
};

function Heart({ particle, onDone }: { particle: Particle; onDone: (id: number) => void }) {
  const progress = useRef(new Animated.Value(0)).current;

  const run = useRef(() => {
    Animated.timing(progress, {
      toValue: 1,
      duration: 2200 + Math.random() * 800,
      easing: Easing.out(Easing.quad),
      useNativeDriver: true
    }).start(() => onDone(particle.id));
  });

  // Kick the animation exactly once on mount.
  const started = useRef(false);
  if (!started.current) {
    started.current = true;
    requestAnimationFrame(() => run.current());
  }

  const translateY = progress.interpolate({ inputRange: [0, 1], outputRange: [0, -220] });
  const translateX = progress.interpolate({ inputRange: [0, 0.5, 1], outputRange: [0, particle.drift, particle.drift * 0.4] });
  const opacity = progress.interpolate({ inputRange: [0, 0.15, 0.75, 1], outputRange: [0, 1, 1, 0] });
  const scale = progress.interpolate({ inputRange: [0, 0.3, 1], outputRange: [0.6, 1.15, 0.9] });

  return (
    <Animated.View
      pointerEvents="none"
      style={[styles.particle, { right: particle.startX, transform: [{ translateY }, { translateX }, { scale }], opacity }]}
    >
      <Text style={styles.emoji}>{particle.emoji}</Text>
    </Animated.View>
  );
}

export const LiveReactionLayer = memo(
  forwardRef<ReactionLayerHandle, unknown>(function LiveReactionLayer(_props, ref) {
    const [particles, setParticles] = useState<Particle[]>([]);

    const remove = useCallback((id: number) => {
      setParticles((current) => current.filter((particle) => particle.id !== id));
    }, []);

    useImperativeHandle(
      ref,
      () => ({
        burst: (emoji?: string) => {
          const chosen = emoji || EMOJIS[Math.floor(Math.random() * EMOJIS.length)];
          const next: Particle = {
            id: ++particleSeq,
            emoji: chosen,
            startX: 6 + Math.random() * 22,
            drift: -18 - Math.random() * 30
          };
          setParticles((current) => (current.length > 26 ? [...current.slice(-24), next] : [...current, next]));
        }
      }),
      []
    );

    return (
      <View pointerEvents="none" style={styles.root}>
        {particles.map((particle) => (
          <Heart key={particle.id} particle={particle} onDone={remove} />
        ))}
      </View>
    );
  })
);

const styles = StyleSheet.create({
  root: {
    bottom: 150,
    height: 260,
    position: "absolute",
    right: 0,
    width: 90
  },
  particle: {
    bottom: 0,
    position: "absolute"
  },
  emoji: {
    fontSize: 26
  }
});
