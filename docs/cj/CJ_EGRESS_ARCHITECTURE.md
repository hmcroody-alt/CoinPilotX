# CJ egress architecture — what we measured, and what it means

CJ enforces two ceilings **per outbound IP address**, with no whitelist
exemption:

| Ceiling | Value |
| --- | --- |
| CJ accounts per egress IP | 3 |
| Business API calls per second per egress IP | 10 |

`services/business_os/suppliers/quota.py` enforces both. It cannot enforce them
against an IP, because a process does not know its own NAT address. It enforces
them against `CJ_EGRESS_GROUP` — a label a human types. Everything below is
about the gap between that label and the thing CJ actually counts.

## What was measured

Environment: Railway project `pulsesoc-cj-staging`, services
`pulsesoc-staging-backend` and `pulsesoc-staging-supplier-worker`, both with
`CJ_EGRESS_GROUP=pulsesoc-isolated-staging-sfo-pool` and both sharing one
Postgres. Outbound address read from inside each container via
`https://api.ipify.org`.

| Sample | Service | Observed egress IP |
| --- | --- | --- |
| 6 consecutive calls, deploy A | backend | `152.55.177.193` (all six) |
| 3 consecutive calls | supplier worker | `152.55.177.192` (all three) |
| 4 consecutive calls, deploy B | backend | `152.55.176.240` (all four) |

Three findings, in order of how much they cost us:

**1. The two services egress from different addresses at the same time.**
Backend `…177.193` and worker `…177.192` were live together, under one label,
sharing one quota table.

**2. The backend's address changed across a redeploy.** `…177.193` before,
`…176.240` after. Nothing about the service changed but the image.

**3. Within one container instance the address is stable.** Ten samples across
two instances, no variation. This is the only one of the three that is good
news, and it is the weakest: it says nothing about what happens on the next
deploy, restart, or scale event.

## What this does and does not break

It does **not** break the account ceiling in the dangerous direction. One label
spanning several real IPs means each real IP carries *fewer* accounts than the
label counts, never more. Three accounts under one label is at most three
accounts on any one address.

It does not break the rate ceiling either, for the same reason: the limiter
paces the whole label at 8.5 calls per second (see `EGRESS_MIN_INTERVAL`), and
that budget is then split across however many addresses the label really spans.
Each address sees less than the label spends.

What it breaks is the **claim**. We do not enforce "three accounts per CJ egress
IP". We enforce "three accounts per label", and we have now measured that the
label is not an IP. The enforcement happens to be conservative today by
accident of topology, not by construction — and an accident is not a control.

Two specific things we cannot rule out:

- **The address may be shared with other Railway tenants.** `152.55.176.0/21`
  is not documented anywhere as dedicated to this project. If another tenant
  egresses from the same NAT address and also uses CJ, their accounts count
  against the same ceiling as ours. Our counter would say three; CJ could see
  six. Nothing in this repository can detect that.
- **We do not know how CJ ages its per-IP account registry.** Our address
  changes on every deploy, so over a month CJ sees the same accounts arriving
  from a growing set of addresses. Whether that reads as a live registry, a
  rolling window, or an accumulating one is not stated in CJ's documentation.

## Why the blocker stands

`policy.multi_merchant_scale_blocker()` returns
`BLOCKED_BY_EGRESS_ARCHITECTURE` until `CJ_EGRESS_IP_ATTESTED` is set. Before
this measurement that was a precaution about something undocumented. It is now
a finding: the label demonstrably is not an IP, and it demonstrably does not
survive a deploy.

The flag gates **multi-merchant scale only**, not the single-merchant case, and
that asymmetry is deliberate. One CJ account cannot exceed a three-account
ceiling however the addresses fall, so a single-merchant deployment is safe
under any topology. The moment a second and third merchant connect, the
question of what an address really is stops being academic.

## What would clear it

Setting `CJ_EGRESS_IP_ATTESTED` is an operator asserting they have checked, not
a switch that makes the problem go away. It should not be set without at least:

1. A static or dedicated outbound address for every process that calls CJ —
   backend and worker both, since they were measured egressing separately.
2. Evidence that the address survives a redeploy. The measurement above is the
   test: read it, redeploy, read it again.
3. Written confirmation from the infrastructure provider that the address is
   not shared with other tenants, or an egress path we control end to end.
4. One label per real address, or one address per label. Today one label spans
   two addresses; that must become a fact someone verified rather than a
   coincidence someone tolerated.

Until then the honest statement is the one the code returns: multi-merchant CJ
is blocked by egress architecture, and the block is not a policy preference.

## Reproducing the measurement

```
railway ssh --service pulsesoc-staging-backend "python -c \"
import urllib.request
print([urllib.request.urlopen('https://api.ipify.org', timeout=10).read().decode() for _ in range(4)])\""
```

Run it against each service that calls CJ, then redeploy and run it again. If
any two readings differ, the label is not an address.
