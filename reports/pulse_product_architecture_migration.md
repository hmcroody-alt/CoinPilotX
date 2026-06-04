# Pulse Product Architecture Migration

Pulse is now the primary logged-in product surface. Login and signup default to `/pulse`; creator, video, live, messages, saved, profile, groups, marketplace, Roast Battle, and Premium remain Pulse destinations.

The public `/` route remains a marketing/sign-up surface. Legacy systems are preserved behind redirects or direct URLs rather than deleted.
