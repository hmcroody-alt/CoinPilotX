import { Image, StyleSheet } from "react-native";
import { PulseReel, reelPosterUrl } from "../../api/reels";
import { reelMediaSlides } from "../../reels/reelMediaKind";

/**
 * A single still image that stays on screen until the user scrolls away. There
 * is no timeline, no autoplay, and no audio — the photo simply holds. The
 * blurred poster already painted behind this surface by `ReelPlayerCard`
 * provides the letterbox fill for non-portrait photos rendered with `contain`.
 */
export function ReelPhotoSurface({ reel }: { reel: PulseReel }) {
  const slide = reelMediaSlides(reel)[0];
  const uri = slide?.url || slide?.poster || reelPosterUrl(reel);
  if (!uri) return null;
  return <Image source={{ uri }} style={StyleSheet.absoluteFill} resizeMode="contain" />;
}
