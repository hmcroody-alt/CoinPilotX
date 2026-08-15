import { ReactElement, useCallback, useEffect, useMemo, useRef } from "react";
import {
  Animated,
  FlatList,
  ListRenderItemInfo,
  NativeScrollEvent,
  NativeSyntheticEvent,
  StyleSheet,
  View
} from "react-native";
import { useTheme } from "../theme/ThemeContext";
import { settledIndexForOffset } from "./pagerMath";

/**
 * `Animated.FlatList`'s generic typings reject plain `T[]` data
 * (`WithAnimatedObject` index-signature clash — a known RN typings gap).
 * Runtime component is unchanged; only the generic is restored.
 */
const AnimatedFlatList = Animated.FlatList as unknown as typeof FlatList;

/**
 * Imperative surface for non-touch navigation (tilt commits). Tilt shares the
 * same settled-index pipeline as swipe: an animated snap to the target page,
 * after which `onIndexSettled` fires from the pager itself.
 */
export type SpatialPagerController = {
  /** Animated snap to a page. No-ops outside [0, pageCount). */
  commitToIndex: (index: number) => void;
};

export type SpatialPagerProps<T> = {
  data: T[];
  keyExtractor: (item: T, index: number) => string;
  /** Renders one page. `active` is true only for the settled, centered page. */
  renderPage: (item: T, index: number, active: boolean) => ReactElement | null;
  /** Viewport width; each page fills it edge-to-edge. */
  pageWidth: number;
  /** Fixed page height (the pager never scrolls vertically itself). */
  pageHeight: number;
  /** Currently settled index (controlled). */
  index: number;
  /** Fired when a swipe/momentum settles on a page. */
  onIndexSettled: (index: number) => void;
  /** Fired when any horizontal drag begins (used by the immersive navigator). */
  onDragStart?: () => void;
  /** Infinite-scroll hook, mirrors FlatList semantics. */
  onEndReached?: () => void;
  onEndReachedThreshold?: number;
  /** Bump to imperatively snap back to `index` without animation (refresh reset). */
  resetNonce?: number;
  /** Receives the imperative controller (tilt navigation drives this). */
  controllerRef?: { current: SpatialPagerController | null };
  accessibilityLabel?: string;
  testID?: string;
};

/**
 * Edge-to-edge horizontal pager for the spatial console.
 *
 * Mirrors the production-proven Reels pager (FlatList + pagingEnabled + tight
 * virtualization) rotated horizontally, plus a scroll-driven depth treatment:
 * at rest one page fills the viewport exactly and neighbors are fully
 * off-screen; during a drag the incoming/outgoing pages pick up a restrained
 * scale/opacity falloff that returns to identity on settle. Under Reduce
 * Motion the depth treatment is disabled entirely and paging still snaps.
 */
export function SpatialPager<T>({
  data,
  keyExtractor,
  renderPage,
  pageWidth,
  pageHeight,
  index,
  onIndexSettled,
  onDragStart,
  onEndReached,
  onEndReachedThreshold = 0.6,
  resetNonce = 0,
  controllerRef,
  accessibilityLabel,
  testID
}: SpatialPagerProps<T>) {
  const { reduceMotion } = useTheme();
  const listRef = useRef<FlatList<T>>(null);
  const scrollX = useRef(new Animated.Value(index * pageWidth)).current;
  const settledIndexRef = useRef(index);
  const lastResetNonce = useRef(resetNonce);

  useEffect(() => {
    if (!controllerRef) return;
    controllerRef.current = {
      commitToIndex: (target: number) => {
        if (target < 0 || target >= data.length || target === settledIndexRef.current) return;
        listRef.current?.scrollToOffset({ offset: target * pageWidth, animated: !reduceMotion });
        // Android does not reliably fire onMomentumScrollEnd for programmatic
        // scrolls, so settle explicitly; the iOS momentum-end duplicate is a
        // same-index settle, which listeners already tolerate.
        settledIndexRef.current = target;
        onIndexSettled(target);
      }
    };
    return () => {
      controllerRef.current = null;
    };
  }, [controllerRef, data.length, onIndexSettled, pageWidth, reduceMotion]);

  useEffect(() => {
    if (resetNonce === lastResetNonce.current) return;
    lastResetNonce.current = resetNonce;
    settledIndexRef.current = index;
    listRef.current?.scrollToOffset({ offset: index * pageWidth, animated: false });
  }, [resetNonce, index, pageWidth]);

  const onScroll = useMemo(
    () =>
      Animated.event([{ nativeEvent: { contentOffset: { x: scrollX } } }], {
        useNativeDriver: true
      }),
    [scrollX]
  );

  const handleMomentumEnd = useCallback(
    (event: NativeSyntheticEvent<NativeScrollEvent>) => {
      const next = settledIndexForOffset(event.nativeEvent.contentOffset.x, pageWidth, data.length);
      if (next !== settledIndexRef.current) {
        settledIndexRef.current = next;
        onIndexSettled(next);
      } else {
        // Settling back onto the same page still counts as a completed gesture
        // for listeners that only care about "a swipe finished".
        onIndexSettled(next);
      }
    },
    [data.length, onIndexSettled, pageWidth]
  );

  const getItemLayout = useCallback(
    (_: ArrayLike<T> | null | undefined, itemIndex: number) => ({
      length: pageWidth,
      offset: pageWidth * itemIndex,
      index: itemIndex
    }),
    [pageWidth]
  );

  const renderItem = useCallback(
    ({ item, index: itemIndex }: ListRenderItemInfo<T>) => {
      const inputRange = [(itemIndex - 1) * pageWidth, itemIndex * pageWidth, (itemIndex + 1) * pageWidth];
      const depthStyle = reduceMotion
        ? null
        : {
            transform: [
              {
                scale: scrollX.interpolate({
                  inputRange,
                  // Identity at rest on either side; restrained 4% dip mid-drag
                  // is produced by the neighbor being between stops.
                  outputRange: [0.96, 1, 0.96],
                  extrapolate: "clamp"
                })
              }
            ],
            opacity: scrollX.interpolate({
              inputRange,
              outputRange: [0.82, 1, 0.82],
              extrapolate: "clamp"
            })
          };
      return (
        <Animated.View style={[{ width: pageWidth, height: pageHeight }, styles.page, depthStyle]}>
          {renderPage(item, itemIndex, itemIndex === index)}
        </Animated.View>
      );
    },
    [index, pageHeight, pageWidth, reduceMotion, renderPage, scrollX]
  );

  if (pageWidth <= 0) {
    return <View style={{ height: pageHeight }} />;
  }

  return (
    <AnimatedFlatList
      ref={listRef}
      testID={testID}
      accessibilityLabel={accessibilityLabel}
      accessibilityRole="adjustable"
      horizontal
      data={data}
      keyExtractor={keyExtractor}
      renderItem={renderItem}
      getItemLayout={getItemLayout}
      pagingEnabled
      snapToInterval={pageWidth}
      snapToAlignment="start"
      disableIntervalMomentum
      decelerationRate="fast"
      showsHorizontalScrollIndicator={false}
      initialNumToRender={2}
      maxToRenderPerBatch={2}
      windowSize={3}
      removeClippedSubviews
      onScroll={onScroll}
      scrollEventThrottle={16}
      onScrollBeginDrag={onDragStart}
      onMomentumScrollEnd={handleMomentumEnd}
      onEndReached={onEndReached}
      onEndReachedThreshold={onEndReachedThreshold}
      style={{ height: pageHeight }}
      contentContainerStyle={styles.content}
      nestedScrollEnabled
    />
  );
}

const styles = StyleSheet.create({
  page: {
    overflow: "hidden"
  },
  content: {
    flexGrow: 0
  }
});
