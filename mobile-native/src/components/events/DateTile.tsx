/**
 * The calendar date tile on the left of an event row. A navy month band for
 * upcoming events, a muted band for past ones — the band colour is the one signal
 * that tells upcoming from past at a glance before the meta line is read.
 *
 * The month/day are formatted from the event's own ISO offset (via the caller),
 * so a seller in a different timezone still sees the event's local date.
 */

import { StyleSheet, Text, View } from "react-native";
import { eventsLight } from "../../theme/eventsLight";

export function DateTile({ month, day, past }: { month: string; day: string; past?: boolean }) {
  return (
    <View style={styles.tile} accessibilityLabel={`${month} ${day}`}>
      <View style={[styles.band, { backgroundColor: past ? eventsLight.dateTile.pastBand : eventsLight.dateTile.upcomingBand }]}>
        <Text style={styles.month}>{month.toUpperCase()}</Text>
      </View>
      <View style={styles.body}>
        <Text style={[styles.day, past ? styles.dayMuted : null]}>{day}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  tile: {
    width: 48,
    borderRadius: eventsLight.radius.thumb,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: eventsLight.dateTile.bodyBorder,
    overflow: "hidden",
    backgroundColor: eventsLight.dateTile.bodyBg
  },
  band: {
    alignItems: "center",
    paddingVertical: 2
  },
  month: {
    color: eventsLight.dateTile.bandText,
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 0.5
  },
  body: {
    alignItems: "center",
    paddingVertical: 4
  },
  day: {
    color: eventsLight.dateTile.day,
    fontSize: 18,
    fontWeight: "800"
  },
  dayMuted: {
    color: eventsLight.dateTile.dayMuted
  }
});
