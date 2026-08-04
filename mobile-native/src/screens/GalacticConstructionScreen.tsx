import { useEffect, useRef } from "react";
import { Animated, Easing, Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

export function GalacticConstructionScreen({ onReturn }: { onReturn: () => void }) {
  const pulse = useRef(new Animated.Value(0)).current;
  const drift = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const animation = Animated.parallel([
      Animated.loop(Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 1800, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 1800, easing: Easing.inOut(Easing.ease), useNativeDriver: true })
      ])),
      Animated.loop(Animated.timing(drift, { toValue: 1, duration: 12000, easing: Easing.linear, useNativeDriver: true }))
    ]);
    animation.start();
    return () => animation.stop();
  }, [drift, pulse]);

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.space} accessibilityRole="summary">
        {[...Array(20)].map((_, index) => <View key={index} style={[styles.star, { left: `${(index * 37) % 96}%`, top: `${(index * 53) % 88}%`, opacity: 0.25 + (index % 5) * 0.13 }]} />)}
        <Animated.View style={[styles.orbit, { transform: [{ rotate: drift.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "360deg"] }) }] }]}>
          <View style={styles.planet} />
        </Animated.View>
        <Animated.Text style={[styles.galaxy, { transform: [{ scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.96, 1.04] }) }] }]}>🌌</Animated.Text>
        <Text style={styles.eyebrow}>PULSESOC GALACTIC CONSTRUCTION</Text>
        <Text style={styles.title}>THIS PART OF THE PULSESOC GALAXY IS STILL BEING BUILT</Text>
        <Text style={styles.body}>Our engineers are assembling the next generation of Business and Marketplace systems.</Text>
        <Text style={styles.body}>This sector will open once construction has been completed. Thank you for being part of the journey.</Text>
        <View style={styles.progressTrack} accessibilityLabel="Construction progress: infrastructure, security layer and business engine complete">
          <Animated.View style={[styles.progress, { opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.7, 1] }) }]} />
        </View>
        <Text style={styles.progressLabel}>FOUNDATION SYSTEMS ONLINE</Text>
        <View style={styles.systems}>
          <Text style={styles.complete}>✓ Infrastructure     ✓ Security Layer</Text>
          <Text style={styles.active}>● Business Engine     • Marketplace     • Commerce</Text>
        </View>
        <Pressable accessibilityRole="button" onPress={onReturn} style={({ pressed }) => [styles.button, pressed && styles.buttonPressed]}>
          <Text style={styles.buttonText}>Return</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#030716" }, space: { flex: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: 28, overflow: "hidden" },
  star: { position: "absolute", width: 3, height: 3, borderRadius: 2, backgroundColor: "#D9F7FF" },
  galaxy: { fontSize: 62, marginBottom: 12 }, orbit: { position: "absolute", top: "19%", width: 210, height: 210, borderRadius: 105, borderWidth: 1, borderColor: "rgba(83,211,255,0.18)" }, planet: { width: 14, height: 14, borderRadius: 7, backgroundColor: "#9B7BFF", shadowColor: "#A68AFF", shadowOpacity: 1, shadowRadius: 12 },
  eyebrow: { color: "#57D9FF", fontSize: 11, fontWeight: "800", letterSpacing: 2.1, textAlign: "center", marginBottom: 14 },
  title: { color: "#FFFFFF", fontSize: 24, lineHeight: 30, fontWeight: "900", textAlign: "center", maxWidth: 360 },
  body: { color: "#AEBBD2", fontSize: 15, lineHeight: 22, textAlign: "center", marginTop: 12, maxWidth: 370 },
  progressTrack: { width: "100%", maxWidth: 340, height: 7, borderRadius: 7, backgroundColor: "#16213C", marginTop: 28, overflow: "hidden" }, progress: { width: "64%", height: "100%", borderRadius: 7, backgroundColor: "#42D7F5" }, progressLabel: { color: "#6FE5FF", fontSize: 10, letterSpacing: 1.7, fontWeight: "800", marginTop: 9 },
  systems: { marginTop: 18, alignItems: "center" }, complete: { color: "#65E5BB", fontSize: 12, lineHeight: 20 }, active: { color: "#9BA9C4", fontSize: 12, lineHeight: 20, textAlign: "center" },
  button: { marginTop: 30, minWidth: 180, minHeight: 50, borderRadius: 25, alignItems: "center", justifyContent: "center", backgroundColor: "#EDF8FF", shadowColor: "#4ADFFF", shadowOpacity: 0.35, shadowRadius: 16 }, buttonPressed: { opacity: 0.8, transform: [{ scale: 0.98 }] }, buttonText: { color: "#071225", fontSize: 16, fontWeight: "900" }
});
