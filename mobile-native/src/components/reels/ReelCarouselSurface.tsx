import { ResizeMode, Video } from "expo-av";
import { useCallback, useMemo, useRef, useState } from "react";
import { FlatList, Image, StyleSheet, Text, View, type ViewToken } from "react-native";
import { PulseReel } from "../../api/reels";
import { reelMediaSlides, type ReelMediaSlide } from "../../reels/reelMediaKind";
import { colors } from "../../theme/colors";

/**
 * Multiple slides swiped horizontally inside a single Reel. Images hold; video
 * slides autoplay only while their slide is the current one AND the whole card
 * is active, so scrolling the vertical feed or swiping to another slide always
 * pauses the clips you left behind. Dots mark position without stealing the
 * action rail.
 */
export function ReelCarouselSurface({ reel, active, muted, muteOriginal = false }: { reel: PulseReel; active: boolean; muted: boolean; muteOriginal?: boolean }) {
  const slides = useMemo(() => reelMediaSlides(reel), [reel]);
  const [index, setIndex] = useState(0);
  const [width, setWidth] = useState(0);
  const viewabilityConfig = useRef({ itemVisiblePercentThreshold: 60 }).current;
  const onViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: ViewToken[] }) => {
    const first = viewableItems.find((token) => token.isViewable);
    if (first && typeof first.index === "number") setIndex(first.index);
  }).current;

  const renderItem = useCallback(
    ({ item, index: slideIndex }: { item: ReelMediaSlide; index: number }) => (
      <CarouselSlide slide={item} width={width} active={active && slideIndex === index} muted={muted || muteOriginal} />
    ),
    [active, index, muteOriginal, muted, width]
  );

  if (!slides.length) return null;

  return (
    <View style={StyleSheet.absoluteFill} onLayout={(event) => setWidth(event.nativeEvent.layout.width)}>
      {width > 0 ? (
        <FlatList
          data={slides}
          horizontal
          pagingEnabled
          showsHorizontalScrollIndicator={false}
          keyExtractor={(slide) => slide.key}
          renderItem={renderItem}
          extraData={`${active}:${index}:${muted}:${muteOriginal}:${width}`}
          onViewableItemsChanged={onViewableItemsChanged}
          viewabilityConfig={viewabilityConfig}
          getItemLayout={(_, i) => ({ length: width, offset: width * i, index: i })}
        />
      ) : null}
      <View style={styles.dots} pointerEvents="none">
        {slides.map((slide, dotIndex) => (
          <View key={slide.key} style={[styles.dot, dotIndex === index && styles.dotActive]} />
        ))}
      </View>
      {slides.length > 1 ? (
        <View style={styles.counter} pointerEvents="none">
          <View style={styles.counterPill}>
            <Text style={styles.counterText} accessibilityLabel={`Slide ${index + 1} of ${slides.length}`}>{`${index + 1}/${slides.length}`}</Text>
          </View>
        </View>
      ) : null}
    </View>
  );
}

function CarouselSlide({ slide, width, active, muted }: { slide: ReelMediaSlide; width: number; active: boolean; muted: boolean }) {
  if (slide.kind === "video") {
    return (
      <View style={{ width, height: "100%" }}>
        <Video
          source={{ uri: slide.url }}
          style={StyleSheet.absoluteFill}
          resizeMode={ResizeMode.CONTAIN}
          shouldPlay={active}
          isLooping
          isMuted={muted}
          usePoster={Boolean(slide.poster)}
          posterSource={slide.poster ? { uri: slide.poster } : undefined}
        />
      </View>
    );
  }
  return (
    <View style={{ width, height: "100%" }}>
      <Image source={{ uri: slide.url }} style={StyleSheet.absoluteFill} resizeMode="contain" />
    </View>
  );
}

const styles = StyleSheet.create({
  counter: {
    position: "absolute",
    right: 12,
    top: 76,
    zIndex: 3
  },
  counterPill: {
    backgroundColor: "rgba(2,9,18,0.62)",
    borderColor: "rgba(109,244,229,0.22)",
    borderRadius: 12,
    borderWidth: 1,
    paddingHorizontal: 9,
    paddingVertical: 4
  },
  counterText: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "800"
  },
  dot: {
    backgroundColor: "rgba(244,247,251,0.42)",
    borderRadius: 3,
    height: 6,
    width: 6
  },
  dotActive: {
    backgroundColor: colors.accent,
    width: 18
  },
  dots: {
    alignItems: "center",
    alignSelf: "center",
    bottom: 120,
    flexDirection: "row",
    gap: 6,
    position: "absolute",
    zIndex: 3
  }
});
