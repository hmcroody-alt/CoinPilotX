# Economy Button Audit

## Result

Economy cards no longer use generic `Open` labels. Mission Control and the Economy Hub use contextual calls to action.

## Button Map

| Module | Button |
| --- | --- |
| Wallet | Manage Wallet |
| Earnings | View Earnings |
| Marketplace | Marketplace Center |
| Seller Tools | Become a Seller |
| Subscriptions | Manage Subscription |
| Premium | Premium Center |
| Creator Revenue | Revenue Center |
| Payouts | Payout Center |
| Revenue Analytics | Revenue Intelligence |
| Ad Revenue | Advertising Revenue |
| Affiliate Revenue | Affiliate Center |
| Store Analytics | Store Intelligence |
| Product Intelligence | Product Intelligence |
| Revenue Forecasting | Revenue Forecast |

## State Labels

Supported states are strict and finite:

- READY
- ACTION REQUIRED
- REVIEW
- WARNING
- LOCKED
- PREMIUM
- ADMIN
- PARTIAL
- BETA
- COMING SOON

`ACTIVE` is not used as an Economy state label.

## Route Behavior

Every Economy button points to `/dashboard/economy/...` and resolves to a real authenticated subsystem route. Locked dashboard behavior is still controlled by Mission Control entitlement logic.
