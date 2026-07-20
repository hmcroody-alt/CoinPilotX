import { useEffect, useRef } from "react";
import { Animated, StyleSheet, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { colors } from "../../theme/colors";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

type Node = { top: `${number}%`; left: `${number}%`; size: number; color: string };

const NODES: Node[] = [
  { top: "12%", left: "18%", size: 5, color: colors.accentStrong },
  { top: "22%", left: "78%", size: 4, color: colors.accent },
  { top: "38%", left: "8%", size: 3, color: colors.accentStrong },
  { top: "58%", left: "88%", size: 4, color: colors.crypto },
  { top: "74%", left: "14%", size: 3, color: colors.accent },
  { top: "86%", left: "68%", size: 5, color: colors.accentStrong }
];

export function LoginBackground() {
  const reducedMotion = useLogiNexusReducedMotion();
  const orbA = useRef(new Animated.Value(0)).current;
  const orbB = useRef(new Animated.Value(0)).current;
  const twinkle = useRef(new Animated.Value(reducedMotion ? 1 : 0.35)).current;

  useEffect(() => {
    if (reducedMotion) return;

    const loop = (value: Animated.Value, duration: number) =>
      Animated.loop(
        Animated.sequence([
          Animated.timing(value, { toValue: 1, duration, useNativeDriver: true }),
          Animated.timing(value, { toValue: 0, duration, useNativeDriver: true })
        ])
      );

    const twinkleLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(twinkle, { toValue: 1, duration: 2600, useNativeDriver: true }),
        Animated.timing(twinkle, { toValue: 0.35, duration: 2600, useNativeDriver: true })
      ])
    );

    const a = loop(orbA, 9000);
    const b = loop(orbB, 11000);
    a.start();
    b.start();
    twinkleLoop.start();
    return () => {
      a.stop();
      b.stop();
      twinkleLoop.stop();
    };
  }, [orbA, orbB, twinkle, reducedMotion]);

  const orbATranslate = orbA.interpolate({ inputRange: [0, 1], outputRange: [-18, 18] });
  const orbBTranslate = orbB.interpolate({ inputRange: [0, 1], outputRange: [16, -16] });

  return (
    <View style={StyleSheet.absoluteFill} pointerEvents="none">
      <LinearGradient
        colors={[colors.background, "#071018", colors.background]}
        start={{ x: 0.1, y: 0 }}
        end={{ x: 0.9, y: 1 }}
        style={StyleSheet.absoluteFill}
      />
      <Animated.View
        style={[
          styles.orb,
          {
            top: "6%",
            left: "-10%",
            backgroundColor: colors.accentStrong,
            transform: [{ translateY: orbATranslate }]
          }
        ]}
      />
      <Animated.View
        style={[
          styles.orb,
          {
            bottom: "4%",
            right: "-14%",
            backgroundColor: colors.accent,
            transform: [{ translateY: orbBTranslate }]
          }
        ]}
      />
      {NODES.map((node, index) => (
        <Animated.View
          key={index}
          style={[
            styles.node,
            {
              top: node.top,
              left: node.left,
              width: node.size,
              height: node.size,
              borderRadius: node.size,
              backgroundColor: node.color,
              opacity: twinkle
            }
          ]}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  orb: {
    position: "absolute",
    width: 260,
    height: 260,
    borderRadius: 130,
    opacity: 0.1
  },
  node: {
    position: "absolute"
  }
});
