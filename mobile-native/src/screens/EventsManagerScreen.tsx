/**
 * Events — the seller's hosted-events manager (Business card #9).
 *
 * This is the workshops / live-sales / pop-ups surface, NOT the live discovery
 * feed (`EventsScreen`) and NOT the notification inbox (Activity, reached from
 * the bell). It renders the `eventsData` model through the pure `eventsManager`
 * derivation and the shared Events components:
 *
 *   • Header — back / "Events" / Calendar pill / segmented Upcoming·Past·Drafts
 *     (selection persisted per session).
 *   • Live-now banner — only when a real event is live; real viewer stats only,
 *     never a fabricated "orders" number.
 *   • Next-event hero — the soonest published event, with countdown, RSVP stack
 *     and capacity bar.
 *   • Upcoming / Past / Drafts lists — status LEDs (published/promoted/draft) and
 *     past-results metrics, all derived, none invented.
 *   • Footer — "＋ Create event" CTA.
 *
 * The screen computes no event facts itself: every status, countdown, capacity,
 * attendee summary and result line comes from `eventsManager`. Promotion reach
 * reconciles to the SAME Advertising campaign figure — no second metric here.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Animated, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  type EventTab,
  type HostedEvent,
  deriveCapacity,
  deriveCountdown,
  deriveEventResults,
  deriveEventStatus,
  deriveLiveBanner,
  eventMatchesTab,
  eventTypeTag,
  liveEvent,
  nextEventHero,
  summarizeAttendees
} from "../api/eventsManager";
import { type EventsModel, loadEventsModel } from "../api/eventsData";
import { EventHero, EventRow, EventsHeader, LiveNowBanner } from "../components/events";
import { registerSyncInvalidation } from "../core/eventSync";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { eventsLight } from "../theme/eventsLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";

const TAB_KEY = "pulsesoc.native.events.tab";
const VALID_TABS: EventTab[] = ["upcoming", "past", "drafts"];

type Props = {
  route?: { params?: RootStackParamList["BusinessOsEvents"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function EventsManagerScreen({ route, navigation }: Props) {
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();

  const [model, setModel] = useState<EventsModel | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<EventTab>("upcoming");
  const [now, setNow] = useState(() => Date.now());

  /* -------------------------------------------------------------- *
   * Persisted tab (per session)
   * -------------------------------------------------------------- */
  useEffect(() => {
    AsyncStorage.getItem(TAB_KEY)
      .then((value) => {
        if (value && (VALID_TABS as string[]).includes(value)) setTab(value as EventTab);
      })
      .catch(() => undefined);
  }, []);

  const changeTab = useCallback((next: EventTab) => {
    setTab(next);
    AsyncStorage.setItem(TAB_KEY, next).catch(() => undefined);
  }, []);

  /* -------------------------------------------------------------- *
   * Load + refresh
   * -------------------------------------------------------------- */
  const load = useCallback(async (kind: "initial" | "refresh" = "initial") => {
    if (kind === "initial") setLoading(true);
    const nowMs = Date.now();
    const next = await loadEventsModel(nowMs).catch(() => null);
    setNow(nowMs);
    if (next) setModel(next);
    setLoading(false);
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  useEffect(() => {
    // A settled live sale, a new RSVP, or a promoted-listing change all touch
    // surfaces the events model reads. There is no dedicated "events" sync
    // subsystem — the backend never tags one — so wiring to it would be a dead
    // handler that never fires. Instead we subscribe to the real subsystems that
    // carry those signals: orders (a live sale settles as an order), marketplace
    // (a promoted listing changes), and activity/notifications (an RSVP or event
    // reminder lands as a notification).
    const refresh = () => load("refresh").catch(() => undefined);
    const unregister = [
      registerSyncInvalidation("orders", refresh),
      registerSyncInvalidation("marketplace", refresh),
      registerSyncInvalidation("activity", refresh),
      registerSyncInvalidation("notifications", refresh)
    ];
    return () => unregister.forEach((fn) => fn());
  }, [load]);

  // Re-tick the countdown once a minute so the hero stays live without a reload.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60 * 1000);
    return () => clearInterval(id);
  }, []);

  /* -------------------------------------------------------------- *
   * Derived — all via eventsManager, nothing invented here
   * -------------------------------------------------------------- */
  const events = model?.events || [];

  const live = useMemo(() => liveEvent(events), [events]);
  const banner = useMemo(
    () => (live ? deriveLiveBanner(live, model?.liveStats?.[live.id]) : null),
    [live, model]
  );

  const hero = useMemo(() => (tab === "upcoming" ? nextEventHero(events, now) : null), [events, tab, now]);

  const rows = useMemo(
    () => events.filter((e) => eventMatchesTab(e, tab) && e.id !== hero?.id),
    [events, tab, hero]
  );

  /* -------------------------------------------------------------- *
   * Navigation — every destination is an existing route.
   * -------------------------------------------------------------- */
  const openLive = useCallback(() => {
    if (live?.liveId) navigation?.navigate("LiveDetail", { liveId: live.liveId, title: live.title });
  }, [live, navigation]);

  const openEvent = useCallback(
    (event: HostedEvent) => {
      const numericId = Number(String(event.id).replace(/[^0-9]/g, ""));
      if (event.liveId) {
        navigation?.navigate("LiveDetail", { liveId: event.liveId, title: event.title });
        return;
      }
      if (numericId > 0) {
        navigation?.navigate("EventDetail", { eventId: numericId, title: event.title });
        return;
      }
      // Mock/sampled events carry no backend id; route to the events surface
      // rather than a detail screen that can't resolve.
      navigation?.navigate("Events", { title: event.title });
    },
    [navigation]
  );

  const openCreate = useCallback(() => {
    navigation?.navigate("LiveEventCreateGateway", { title: "Create event" });
  }, [navigation]);

  const openCalendar = useCallback(() => {
    navigation?.navigate("Events", { mode: "schedule", title: "Calendar" });
  }, [navigation]);

  /* -------------------------------------------------------------- *
   * Body
   * -------------------------------------------------------------- */
  const showHero = tab === "upcoming" && hero;

  return (
    <View style={styles.root}>
      <EventsHeader
        title={route?.params?.title || "Events"}
        tab={tab}
        onChangeTab={changeTab}
        onBack={() => navigation?.goBack?.()}
        onCalendar={openCalendar}
      />

      <ScrollView
        contentContainerStyle={[styles.content, { paddingBottom: bottomPad(insets.bottom) }]}
        showsVerticalScrollIndicator={false}
      >
        {model?.offline ? (
          <View style={styles.notice}>
            <Ionicons name="cloud-offline-outline" size={16} color={eventsLight.text.muted} />
            <Text style={styles.noticeText}>{model.error || "You're offline. Showing the last synced events."}</Text>
          </View>
        ) : null}

        {tab === "upcoming" && banner ? (
          <View style={styles.block}>
            <LiveNowBanner banner={banner} reducedMotion={reducedMotion} onOpen={openLive} />
          </View>
        ) : null}

        {showHero ? (
          <View style={styles.block}>
            <EventHero
              event={hero as HostedEvent}
              typeTag={eventTypeTag(hero as HostedEvent)}
              whenLine={whenLine(hero as HostedEvent)}
              whereLine={whereLine(hero as HostedEvent)}
              countdown={deriveCountdown(hero as HostedEvent, now)}
              capacity={deriveCapacity((hero as HostedEvent).goingCount, (hero as HostedEvent).capacity)}
              attendees={summarizeAttendees((hero as HostedEvent).attendees, (hero as HostedEvent).goingCount)}
              onManage={openEvent}
              onShare={openEvent}
            />
          </View>
        ) : null}

        <View style={styles.list}>
          {loading ? (
            <Text style={styles.state}>Loading events…</Text>
          ) : rows.length === 0 && !showHero ? (
            <EmptyTab tab={tab} />
          ) : (
            rows.map((event) => {
              const { month, day } = dateTileParts(event);
              const isPast = tab === "past";
              return (
                <EventRow
                  key={event.id}
                  event={event}
                  month={month}
                  day={day}
                  meta={rowMeta(event)}
                  status={isPast ? undefined : deriveEventStatus(event)}
                  results={isPast ? deriveEventResults(event) : undefined}
                  onPress={openEvent}
                />
              );
            })
          )}
        </View>
      </ScrollView>

      <View style={[styles.footer, { paddingBottom: Math.max(insets.bottom, 10) }]}>
        <Pressable
          style={styles.createBtn}
          accessibilityRole="button"
          accessibilityLabel="Create event"
          onPress={openCreate}
        >
          <Ionicons name="add" size={18} color={eventsLight.cta.text} />
          <Text style={styles.createText}>Create event</Text>
        </Pressable>
      </View>
    </View>
  );
}

function EmptyTab({ tab }: { tab: EventTab }) {
  const copy =
    tab === "upcoming"
      ? "No upcoming events. Create one to start selling live or in person."
      : tab === "past"
        ? "No past events yet. Results appear here after an event ends."
        : "No drafts. Start an event and save it to come back to it later.";
  return (
    <View style={styles.empty}>
      <Ionicons name="calendar-outline" size={28} color={eventsLight.text.muted} />
      <Text style={styles.emptyText}>{copy}</Text>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Display formatting — from the event's own ISO wall-clock, so a seller
 * in another timezone still sees the event's local date/time. Pure string
 * parsing avoids device-timezone drift.
 * ------------------------------------------------------------------ */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

type WallClock = { year: number; month: number; day: number; hour: number; minute: number };

function wallClock(iso: string): WallClock | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  if (m) {
    return { year: +m[1], month: +m[2], day: +m[3], hour: +m[4], minute: +m[5] };
  }
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return { year: d.getFullYear(), month: d.getMonth() + 1, day: d.getDate(), hour: d.getHours(), minute: d.getMinutes() };
}

function dateTileParts(event: HostedEvent): { month: string; day: string } {
  const wc = wallClock(event.startsAt);
  if (!wc) return { month: "—", day: "—" };
  return { month: MONTHS[wc.month - 1] || "—", day: String(wc.day) };
}

function whenLine(event: HostedEvent): string {
  const wc = wallClock(event.startsAt);
  if (!wc) return "Time to be set";
  const weekday = WEEKDAYS[new Date(Date.UTC(wc.year, wc.month - 1, wc.day)).getUTCDay()];
  const h12 = ((wc.hour + 11) % 12) + 1;
  const ampm = wc.hour < 12 ? "AM" : "PM";
  const minute = String(wc.minute).padStart(2, "0");
  return `${weekday}, ${MONTHS[wc.month - 1]} ${wc.day} · ${h12}:${minute} ${ampm}`;
}

function whereLine(event: HostedEvent): string | undefined {
  if (event.type === "in_person") return event.venue;
  return event.streamUrl ? "Livestream" : undefined;
}

function rowMeta(event: HostedEvent): string | undefined {
  const bits: string[] = [];
  bits.push(event.type === "livestream" ? "Livestream" : event.venue || "In person");
  if (event.keyDetail) bits.push(event.keyDetail);
  return bits.join(" · ");
}

function bottomPad(inset: number) {
  return Math.max(inset, 16) + BOTTOM_NAV_CONTENT_CLEARANCE + 64;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: eventsLight.bg.page },
  content: { paddingTop: 12, gap: 12 },
  block: { paddingHorizontal: eventsLight.space.card },
  list: { gap: 10, paddingHorizontal: eventsLight.space.card, paddingTop: 2 },
  notice: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginHorizontal: eventsLight.space.card,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: eventsLight.radius.control,
    backgroundColor: eventsLight.bg.strip
  },
  noticeText: { flex: 1, fontSize: 12, color: eventsLight.text.muted },
  state: { paddingVertical: 24, textAlign: "center", fontSize: 13, color: eventsLight.text.muted },
  empty: { alignItems: "center", gap: 10, paddingVertical: 40, paddingHorizontal: 24 },
  emptyText: { fontSize: 13, color: eventsLight.text.muted, textAlign: "center", lineHeight: 19 },
  footer: {
    paddingHorizontal: eventsLight.space.card,
    paddingTop: 10,
    backgroundColor: eventsLight.bg.card,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: eventsLight.border.hairline
  },
  createBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    minHeight: eventsLight.size.tapTarget,
    borderRadius: eventsLight.radius.control,
    backgroundColor: eventsLight.cta.from
  },
  createText: { fontSize: 15, fontWeight: "800", color: eventsLight.cta.text }
});

export default EventsManagerScreen;
