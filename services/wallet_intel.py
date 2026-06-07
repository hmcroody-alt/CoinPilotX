import os
import re

BTC_RE = re.compile(r"^(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62}$")
ETH_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
TX_RE = re.compile(r"^[a-fA-F0-9]{64}$")


def detect_chain(value):
    value = (value or "").strip()
    if BTC_RE.match(value):
        return "BTC"
    if ETH_RE.match(value):
        return "ETH"
    if TX_RE.match(value):
        return "BTC_TX"
    return "unknown"


def explorer_link(chain, value):
    if chain == "BTC":
        return f"https://mempool.space/address/{value}"
    if chain == "BTC_TX":
        return f"https://mempool.space/tx/{value}"
    if chain == "ETH":
        return f"https://etherscan.io/address/{value}"
    return ""


def analyze_public_identifier(value):
    value = (value or "").strip()
    chain = detect_chain(value)
    if chain == "unknown":
        return {
            "ok": False,
            "response": "Send a public BTC/ETH wallet address or public BTC TXID. Never send seed phrases, private keys, recovery phrases, wallet passwords, or signing credentials.",
        }
    api_note = ""
    extra_lines = []
    if chain == "ETH" and not os.getenv("ETHERSCAN_API_KEY"):
        api_note = "ETHERSCAN_API_KEY is not connected yet, so this uses a public explorer fallback."
    if chain == "ETH":
        evm_sources = []
        if os.getenv("ETHERSCAN_API_KEY"):
            evm_sources.append("Ethereum")
        if os.getenv("BSCSCAN_API_KEY"):
            evm_sources.append("BNB Smart Chain")
            extra_lines.append(f"BNB Chain explorer: https://bscscan.com/address/{value}")
        if os.getenv("POLYGONSCAN_API_KEY"):
            evm_sources.append("Polygon")
            extra_lines.append(f"Polygon explorer: https://polygonscan.com/address/{value}")
        if evm_sources:
            api_note = "Connected EVM source(s): " + ", ".join(evm_sources) + "."
    if chain in {"BTC", "BTC_TX"}:
        api_note = "BTC public explorer fallback is available. Deeper transaction parsing depends on public explorer availability."
    response = (
        "👛 Wallet Intel\n\n"
        f"Chain detected: {chain}\n"
        f"Explorer: {explorer_link(chain, value)}\n"
        + ("\n".join(extra_lines) + "\n" if extra_lines else "")
        + "\n"
        f"Public activity summary: {api_note or 'Live data source is connected.'}\n\n"
        "Risk flags:\n"
        "• Only public wallet/TXID data should be used.\n"
        "• High transaction activity, new approvals, or unknown counterparties should be reviewed carefully.\n\n"
        "Safety reminder: PulseSoc will never ask for seed phrases, private keys, recovery phrases, wallet passwords, or signing credentials.\n"
        "Educational only — not financial advice."
    )
    return {"ok": True, "chain": chain, "explorer": explorer_link(chain, value), "response": response}
