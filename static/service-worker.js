// Tombstone. This file used to be a full second service worker -- a fork of
// static/sw.js -- and it is now a worker whose only job is to remove itself.
//
// It cannot simply be deleted. A registered service worker keeps running from
// the browser's stored copy; the file being gone from the server does not
// unregister anything. The browser only replaces a worker when it re-fetches
// the script and the bytes differ, and per spec a script fetch that 404s makes
// the update job *fail*, leaving the old worker installed and in control. So
// deleting the file would strand the fork on every device that has it -- which
// is the opposite of consolidating onto one worker. The file has to stay and
// has to say "unregister me".
//
// Why there were two: this was a fork of sw.js that diverged at aa70aa7e and
// picked up push hardening sw.js never got. Those changes have been merged back
// into sw.js, which is the worker registered at scope "/".
//
// The push subscription is the part that needs care. Subscriptions belong to a
// registration, not to an origin, and static/notifications.js used to subscribe
// against *this* registration -- so unregistering here destroys the user's push
// subscription. notifications.js migrates it first (see
// migrateLegacyPushRegistration), but a client that never runs that code still
// reaches this file, because delivering a push triggers a soft update of the
// registration. For that client, unsubscribing here and telling the server is
// the difference between a subscription the server knows is gone and a dead
// endpoint it keeps pushing to for weeks.

self.addEventListener("install", () => {
  // Do not wait for the old worker's clients to close. There is nothing to
  // preserve here and the whole point is to stop being installed.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    // Drop the push subscription *before* unregistering, and tell the server,
    // so it stops sending to an endpoint that can no longer be delivered.
    // Unregistering first would destroy the subscription object along with the
    // registration and leave nothing to report.
    try {
      const subscription = await self.registration.pushManager.getSubscription();
      if (subscription) {
        const endpoint = subscription.endpoint;
        await subscription.unsubscribe().catch(() => {});
        // preserve_preferences matters more than it looks. Without it the
        // endpoint also sets enable_push_notifications = false on the account,
        // because that flag is what the *user-initiated* "turn push off"
        // button relies on. Retiring a worker is not the user turning push
        // off. Omitting it here would mean this cleanup silently disabled web
        // push for everyone it reached, and nothing would ever turn it back
        // on: this worker is gone afterwards, so there is no second pass. The
        // native app makes the same distinction -- see preservePreferences in
        // mobile-native/src/api/push.ts, which every non-user-initiated
        // unregister passes.
        await fetch("/api/push/unsubscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ endpoint, preserve_preferences: true })
        }).catch(() => {});
      }
    } catch (error) {
      // Best effort. A failure here must not stop the unregister below,
      // otherwise the fork survives to handle another push.
    }

    // This worker's scope is /static/, so it controls essentially no
    // navigations and there is nothing to reload. Clients are deliberately not
    // navigated: sw.js already owns scope "/".
    await self.registration.unregister().catch(() => {});
  })());
});

// No fetch handler. A tombstone that intercepted requests would keep serving
// the fork's caching behaviour for as long as it took to activate.
