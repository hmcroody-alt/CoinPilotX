import { Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { GalacticAtmosphere } from "../components/GalacticAtmosphere";

export function GalacticConstructionScreen({ onReturn }: { onReturn: () => void }) {
  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.space} accessibilityRole="summary">
        <GalacticAtmosphere variant="business" testID="construction-galactic-atmosphere" />
        <Text style={styles.eyebrow}>PULSESOC GALACTIC CONSTRUCTION</Text>
        <Text style={styles.title}>THIS PART OF THE PULSESOC GALAXY IS STILL BEING BUILT</Text>
        <Text style={styles.body}>Our engineers are assembling the next generation of Business and Marketplace systems.</Text>
        <Text style={styles.body}>This sector will open once construction has been completed. Thank you for being part of the journey.</Text>
        <View style={styles.progressTrack} accessibilityLabel="Construction progress: infrastructure, security layer and business engine complete">
          <View style={styles.progress} />
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
  eyebrow: { color: "#57D9FF", fontSize: 11, fontWeight: "800", letterSpacing: 2.1, textAlign: "center", marginBottom: 14 },
  title: { color: "#FFFFFF", fontSize: 24, lineHeight: 30, fontWeight: "900", textAlign: "center", maxWidth: 360 },
  body: { color: "#AEBBD2", fontSize: 15, lineHeight: 22, textAlign: "center", marginTop: 12, maxWidth: 370 },
  progressTrack: { width: "100%", maxWidth: 340, height: 7, borderRadius: 7, backgroundColor: "#16213C", marginTop: 28, overflow: "hidden" }, progress: { width: "64%", height: "100%", borderRadius: 7, backgroundColor: "#42D7F5" }, progressLabel: { color: "#6FE5FF", fontSize: 10, letterSpacing: 1.7, fontWeight: "800", marginTop: 9 },
  systems: { marginTop: 18, alignItems: "center" }, complete: { color: "#65E5BB", fontSize: 12, lineHeight: 20 }, active: { color: "#9BA9C4", fontSize: 12, lineHeight: 20, textAlign: "center" },
  button: { marginTop: 30, minWidth: 180, minHeight: 50, borderRadius: 25, alignItems: "center", justifyContent: "center", backgroundColor: "#EDF8FF", shadowColor: "#4ADFFF", shadowOpacity: 0.35, shadowRadius: 16 }, buttonPressed: { opacity: 0.8, transform: [{ scale: 0.98 }] }, buttonText: { color: "#071225", fontSize: 16, fontWeight: "900" }
});
