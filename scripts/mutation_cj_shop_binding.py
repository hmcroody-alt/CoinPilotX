#!/usr/bin/env python3
"""Mutation battery for the CJ fulfilment-shop binding surface (gap 5).

Each mutation removes one invariant the new code establishes, then runs the
suite that is supposed to notice. A mutation that survives means the test
asserting it does not actually hold it up -- the invariant is decoration.

Read-only against the repo: every mutation is written, tested, and reverted
from an in-memory copy of the original file, including on failure.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "mobile-native"
VENV = ROOT / ".venv" / "bin" / "python3"

CONNECTIONS = ROOT / "services/business_os/suppliers/connections.py"
API = NATIVE / "src/api/dropshipping.ts"
SCREEN = NATIVE / "src/screens/dropshipping/SuppliersScreen.tsx"
STATES = NATIVE / "src/components/dropshipping/DropshippingStates.tsx"

PY_SUITE = "tests/business_os/test_cj_shop_binding.py"
API_SUITE = "src/api/__tests__/dropshipping.test.ts"
SCREEN_SUITE = "src/screens/dropshipping/__tests__/DropshippingScreens.test.tsx"


def pytest(suite: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(VENV), "-m", "pytest", suite, "-q", "-x"],
                          cwd=ROOT, capture_output=True, text=True)


def jest(suite: str) -> subprocess.CompletedProcess:
    return subprocess.run(["npx", "jest", suite, "--silent"],
                          cwd=NATIVE, capture_output=True, text=True)


# (name, file, old, new, runner, suite, what the surviving mutant would mean)
MUTATIONS = [
    (
        "connection_shops re-raises CJ's no-storefront rejection",
        CONNECTIONS,
        '        if str(getattr(exc, "code", "")).upper() != "SUPPLIER_REJECTED":\n'
        "            raise\n"
        "        shops = []",
        "        raise",
        pytest, PY_SUITE,
        "an account that owns no shop reads as a 422, which the client shows as "
        "'Something went wrong' -- this merchant's exact live state",
    ),
    (
        "connection_shops softens every provider failure into an empty list",
        CONNECTIONS,
        '        if str(getattr(exc, "code", "")).upper() != "SUPPLIER_REJECTED":\n'
        "            raise\n"
        "        shops = []",
        "        shops = []",
        pytest, PY_SUITE,
        "a throttle, an outage or a dead credential all print 'you own no shops', "
        "which is a false instruction",
    ),
    (
        "connection_shops softens our own refusal of an unsafe list",
        CONNECTIONS,
        "    except SupplierConnectionError:\n"
        "        raise  # Our own refusal of an unsafe or malformed list is never survivable.\n"
        "    except SupplierError as exc:\n",
        "    except SupplierConnectionError:\n        shops = []\n    except SupplierError as exc:\n",
        pytest, PY_SUITE,
        "a shop list echoing our own vaulted secret is reported as no shops at all",
    ),
    (
        "connectionCanFulfil stops requiring a bound shop",
        API,
        "  return connectionIsUsable(connection) && Boolean(connection.externalShopId);",
        "  return connectionIsUsable(connection);",
        jest, API_SUITE,
        "every surface calls an unbound connection fully working, which is the "
        "defect gap 5 exists to remove",
    ),
    (
        "connectionCanFulfil stops requiring a working connection",
        API,
        "  return connectionIsUsable(connection) && Boolean(connection.externalShopId);",
        "  return Boolean(connection.externalShopId);",
        jest, API_SUITE,
        "a connection with an expired credential counts as able to fulfil "
        "because a shop id is still recorded on it",
    ),
    (
        "the shop list trusts a truthy fulfillable instead of a true one",
        API,
        "      fulfillable: raw.fulfillable === true,",
        "      fulfillable: Boolean(raw.fulfillable),",
        jest, API_SUITE,
        'the string "false" makes an unfulfillable shop choosable',
    ),
    (
        "stateForError forgets the binding requirement",
        API,
        '  if (code === "shop_binding_required" || code === "shop_required") return "SHOP_BINDING_REQUIRED";\n',
        "",
        jest, API_SUITE,
        "the one refusal a merchant meets by accident falls to a bare 409 and "
        "renders as 'Something went wrong'",
    ),
    (
        "stateForError lets the status decide before the code does",
        API,
        '  if (code === "shop_not_authorized") return "SHOP_NOT_AUTHORIZED";\n',
        "",
        jest, API_SUITE,
        "a 403 about a shop reads as 'you are not signed in to this store any more'",
    ),
    (
        "the picker offers a button on a shop that cannot take orders",
        SCREEN,
        "                {shop.fulfillable ? (",
        "                {true ? (",
        jest, SCREEN_SUITE,
        "binding says yes and the merchant learns one lost order later that the "
        "shop was never a destination",
    ),
    (
        "the picker is offered on a connection that already has a shop",
        SCREEN,
        "              connectionIsUsable(item) && !connectionCanFulfil(item) ? () => openPicker(item) : null",
        "              connectionIsUsable(item) ? () => openPicker(item) : null",
        jest, SCREEN_SUITE,
        "a bound connection invites a rebind that the server refuses with "
        "connection_binding_conflict every time",
    ),
    (
        "SHOP_BINDING_REQUIRED gets a Try again",
        STATES,
        "          onRetry={onFixConnection || null}",
        "          onRetry={onFixConnection || onRetry}",
        jest, SCREEN_SUITE,
        "a retry that cannot ever answer differently invites tapping instead of "
        "going to the screen that fixes it",
    ),
]


def main() -> int:
    survivors = []
    for index, (name, path, old, new, runner, suite, meaning) in enumerate(MUTATIONS, 1):
        original = path.read_text()
        if original.count(old) != 1:
            print(f"{index:2}. ERROR   {name}\n        anchor matched "
                  f"{original.count(old)} times in {path.name}")
            survivors.append((name, "anchor did not match exactly once"))
            continue
        path.write_text(original.replace(old, new, 1))
        try:
            result = runner(suite)
        finally:
            path.write_text(original)
        if result.returncode == 0:
            print(f"{index:2}. SURVIVED {name}")
            survivors.append((name, meaning))
        else:
            print(f"{index:2}. caught   {name}")

    print()
    if survivors:
        print(f"{len(survivors)} of {len(MUTATIONS)} mutations survived:")
        for name, meaning in survivors:
            print(f"  - {name}\n      would mean: {meaning}")
        return 1
    print(f"All {len(MUTATIONS)} mutations caught.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
