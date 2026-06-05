# Account Trust Profile System

## Trust Levels
- Basic User
- Verified User
- Trusted User
- Creator Verified
- Teacher Verified
- Seller Verified
- Admin Verified

## Current Foundation
Trust is calculated separately from the security score and uses email/phone verification, security score, creator badge, and admin state. Higher-risk role-specific verification can build on the `user_verifications` table.

## Admin Safety
Admins should see trust level, verification status, safety flags, account age, and report history. Recovery codes and sensitive verification files must remain hidden unless an explicitly permissioned workflow is added.
