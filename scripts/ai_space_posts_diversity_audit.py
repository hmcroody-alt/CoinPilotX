from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.pulse_ai.space_ai_engine import generate_space_post  # noqa: E402


SPACES = [
    {"slug": "ethical-hackers", "name": "Ethical Hackers", "category": "cybersecurity", "region": "Global", "tags": ["hacking", "security"], "trust_score": 91, "energy_score": 82},
    {"slug": "bitcoin-strategy", "name": "Bitcoin Strategy", "category": "crypto", "region": "Global", "tags": ["bitcoin", "strategy"], "trust_score": 91, "energy_score": 85},
    {"slug": "ai-builders", "name": "AI Builders", "category": "AI", "region": "Global", "tags": ["agents", "automation"], "trust_score": 90, "energy_score": 86},
    {"slug": "marketplace", "name": "Marketplace", "category": "commerce", "region": "Global", "tags": ["seller", "buyer"], "trust_score": 88, "energy_score": 81},
    {"slug": "reels-music", "name": "Reels & Music", "category": "creators", "region": "Global", "tags": ["reels", "music"], "trust_score": 88, "energy_score": 89},
    {"slug": "cybersecurity", "name": "Cybersecurity", "category": "cybersecurity", "region": "Global", "tags": ["blue-team"], "trust_score": 91, "energy_score": 84},
]

VOCAB = {
    "ethical-hackers": ["threat model", "payload", "privilege escalation", "mitigation", "detection", "ioc", "sandbox", "phishing kit"],
    "bitcoin-strategy": ["halving", "liquidity", "custody", "utxo", "fees", "cold wallet", "macro", "volatility", "dca"],
    "ai-builders": ["workflow", "context window", "retrieval", "evals", "agentic loop", "latency", "api cost"],
    "marketplace": ["listing quality", "disputes", "escrow", "reviews", "fulfillment", "buyer protection"],
    "reels-music": ["hook", "drop", "remix", "retention", "caption", "trend", "loop"],
    "cybersecurity": ["siem", "endpoint", "persistence", "lateral movement", "phishing", "malware", "detection rule"],
}


def main():
    failures = []
    posts = [generate_space_post(space, post_type="hot_take", schedule_slot="audit", attempt=index) for index, space in enumerate(SPACES)]
    bodies = [post["body"].lower() for post in posts]
    if any("the community that wins is not the loudest one" in body for body in bodies):
        failures.append("old repeated hot-take body still appears")
    if len({body[:180] for body in bodies}) != len(bodies):
        failures.append("repeated body opening across spaces")
    for space, post in zip(SPACES, posts):
        slug = space["slug"]
        body = post["body"].lower()
        tags = set(post["tags"])
        if not any(term in body for term in VOCAB[slug]):
            failures.append(f"{slug} missing category-specific vocabulary")
        if len(tags) < 5:
            failures.append(f"{slug} has weak hashtag diversity")
        if "diversity" not in post.get("metadata", {}):
            failures.append(f"{slug} missing diversity metadata")
    if failures:
        print("AI space posts diversity audit failed:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("AI space posts diversity audit passed.")


if __name__ == "__main__":
    main()
