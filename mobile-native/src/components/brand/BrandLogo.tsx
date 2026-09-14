import { Image, ImageStyle, StyleProp } from "react-native";

export type BrandLogoVariant = "primary" | "horizontal" | "monochrome" | "mark";

const SOURCES: Record<BrandLogoVariant, number> = {
  primary: require("../../assets/brand/pulsesoc-primary.png"),
  horizontal: require("../../assets/brand/pulsesoc-horizontal.png"),
  monochrome: require("../../assets/brand/pulsesoc-monochrome.png"),
  mark: require("../../assets/brand/pulsesoc-mark.png")
};

// Intrinsic ratio of each runtime asset. Callers give one dimension and the
// other is derived, so no lockup can be squeezed into the wrong shape.
const ASPECT: Record<BrandLogoVariant, number> = {
  primary: 768 / 744,
  horizontal: 1024 / 312,
  monochrome: 768 / 723,
  mark: 768 / 572
};

export function brandLogoHeight(variant: BrandLogoVariant, width: number) {
  return width / ASPECT[variant];
}

type Props = {
  /** primary: splash/auth/hero. horizontal: wide headers. mark: compact square. monochrome: restrained surfaces. */
  variant: BrandLogoVariant;
  width: number;
  /** Omit on decorative duplicates so screen readers are not read the brand twice. */
  label?: string;
  style?: StyleProp<ImageStyle>;
  testID?: string;
};

export function BrandLogo({ variant, width, label, style, testID }: Props) {
  return (
    <Image
      source={SOURCES[variant]}
      style={[{ width, height: brandLogoHeight(variant, width) }, style]}
      resizeMode="contain"
      fadeDuration={0}
      testID={testID}
      accessible={Boolean(label)}
      accessibilityRole={label ? "image" : undefined}
      accessibilityLabel={label}
    />
  );
}
