# Pulse Mobile State Management Recommendation

## Recommendation

Use Zustand for lightweight app state and keep server data behind focused hooks/services until the mobile product needs a dedicated query cache.

## Why

- The current foundation needs auth state, session readiness, and small screen-level resource state.
- Zustand keeps the auth/session layer simple and avoids over-building before native workflows stabilize.
- API wrappers in `services/` keep backend contracts explicit and make it easy to introduce React Query later for feed pagination, reels prefetching, message sync, and notification badges.

## Suggested Growth Path

1. Keep auth/session in `store/authStore.ts`.
2. Keep endpoint wrappers in `services/`.
3. Use local screen state for simple previews.
4. Add TanStack Query once the app needs cache invalidation, infinite scroll, optimistic updates, or background refetch policies.
5. Keep secure credentials in Expo SecureStore only; do not persist tokens in AsyncStorage.
