import { AccessibilityInfo, Animated, Easing } from "react-native";
import { useEffect, useState } from "react";
import { logiNexus } from "./logiNexus";

export const logiNexusMotion = {
  duration: logiNexus.motion,
  easing: {
    standard: Easing.inOut(Easing.quad),
    exit: Easing.out(Easing.quad),
    enter: Easing.in(Easing.quad)
  }
};

export function useLogiNexusReducedMotion() {
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((enabled) => {
        if (mounted) setReducedMotion(Boolean(enabled));
      })
      .catch(() => undefined);
    const subscription = AccessibilityInfo.addEventListener("reduceMotionChanged", (enabled) => {
      setReducedMotion(Boolean(enabled));
    });
    return () => {
      mounted = false;
      subscription.remove();
    };
  }, []);

  return reducedMotion;
}

export function createLogiNexusAmbientPulse(
  value: Animated.Value,
  options: {
    duration?: number;
    toValue?: number;
    useNativeDriver?: boolean;
  } = {}
) {
  const duration = options.duration ?? logiNexus.motion.ambient;
  const toValue = options.toValue ?? 1;
  const useNativeDriver = options.useNativeDriver ?? true;
  return Animated.loop(
    Animated.sequence([
      Animated.timing(value, {
        toValue,
        duration,
        easing: logiNexusMotion.easing.standard,
        useNativeDriver
      }),
      Animated.timing(value, {
        toValue: 0,
        duration,
        easing: logiNexusMotion.easing.standard,
        useNativeDriver
      })
    ])
  );
}
