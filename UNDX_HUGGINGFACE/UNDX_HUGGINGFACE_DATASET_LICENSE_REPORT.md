# UNDX Hugging Face dataset licence report

**Date:** 2026-08-29
**Artifacts downloaded:** 0
**Gated agreements accepted:** 0
**Hugging Face token read, printed, stored or transmitted:** no

This report covers the 31 datasets and 3 models evaluated during the UNDX intelligence
corpus expansion. It is a licence review of candidates, not a manifest of holdings —
nothing was acquired, for reasons given in the companion training report.

## How discovery was done, and why it matters to the licence question

The sandbox that runs this repository's tooling cannot reach Hugging Face. Both
`huggingface.co` and `datasets-server.huggingface.co` return `HTTP 403 from proxy after
CONNECT`. Neither `huggingface_hub` nor `datasets` is installed, no token exists on
disk, and there are no `HF_*` variables in the environment.

Listings were therefore read through the authenticated browser session against the
public `/api/datasets` and `/api/models` endpoints, sorted by download count. This has
a consequence the reader should hold onto: **every licence string below is a repository
tag, not a licence review.** A `license:apache-2.0` tag tells you what the uploader
asserted in their card metadata. It does not tell you that they had the right to assert
it, that the contents match the assertion, or that a redistributed subset carries the
same terms as its source. Where that distinction changes the decision it is called out
in the entry.

## Summary of decisions

| Decision | Count | Meaning |
|---|---:|---|
| `REJECT` | 15 | Not acquired. |
| `OWNER_REVIEW_REQUIRED` | 10 | Blocked pending a human licence decision. |
| `EVALUATION_ONLY` | 3 | Usable to measure UNDX, never to teach it. |
| `SAFETY_TEST_ONLY` | 3 | Adversarial inputs replayed against the guard path. |
| `FINE_TUNE` | 0 | Unavailable — see below. |
| `DISTILL` | 0 | Unavailable — see below. |
| `RAG` | 0 | Nothing external qualified. |

`FINE_TUNE` and `DISTILL` are zero because they are not decisions this repository can
take. `torch`, `transformers`, `peft`, `Trainer` and `TrainingArguments` appear nowhere
in `services/`, `scripts/` or the root modules. The single grep match for `torch` is
`advanced:[{torch:true}]` at `bot.py:51377` — a camera flashlight constraint in inline
JavaScript. `undx_router` calls OpenAI, Claude, Gemini, DeepSeek and Groq over HTTP.
There are no weights here to fine-tune.

`RAG` is zero for a different reason, and it is a judgement rather than a fact:
`services/undx_brain/corpus.py` exists and would accept external records, but every
candidate that would have gone into it teaches UNDX to speak fluently about a domain
where PulseSoc has its own answer. That argument is made in full in the training report.

## The ten blocked on licence

No `UNKNOWN`-licence artifact goes into production. Ten candidates are held there:

| Dataset | Downloads | Tag | Why blocked |
|---|---:|---|---|
| `zake7749/Qwen-3.6-plus-agent-tool-calling-trajectory` | 2,320 | none | No licence tag. Highest-adoption agentic trajectory result. |
| `xTRam1/safe-guard-prompt-injection` | 1,429 | none | No licence tag. |
| `sunnydubey1111/agent-trajectory-sentinel` | 1,920 | `other` | `other` names a file, not a permission. Repo LICENSE must be read. |
| `livebench/reasoning` | 7,277 | none | No licence tag despite high adoption. |
| `livebench/instruction_following` | 5,523 | none | No licence tag despite high adoption. |
| `nvidia/Nemotron-SFT-Instruction-Following-Chat-v3` | 9,594 | four tags | `odc-by`, `cc-by-4.0`, `apache-2.0`, `other` on one repo — terms are per-subset. |
| `zake7749/deepseek-v4-pro-agent-tool-calling-trajectory` | 408 | none | No licence tag. |
| `Salesforce/xlam-function-calling-60k` | 30,964 | `cc-by-4.0` | Gated. Agreement not accepted. |
| `NebulaByte/E-Commerce_Customer_Support_Conversations` | 462 | none | No tag, and support transcripts are a PII-bearing shape. |
| `saillab/alpaca-haitian_creole-cleaned` | 19 | none | No licence tag. |

The Nemotron entry is the one most likely to be waved through by someone in a hurry.
Four licence tags on a single repository means the terms vary by split, so any blanket
decision is a guess about which split the work lands on. It needs the per-split mapping
read before a subset is used, and that is a different task from reading one LICENSE file.

## Gated datasets, and what was not done with them

Four gated repositories appeared in results: `Salesforce/xlam-function-calling-60k`,
`rogue-security/prompt-injections-benchmark`,
`centrepourlasecuriteia/content-moderation-input-dataset` and
`farabi-lab/Content-Moderation-and-Safety`. **No agreement was accepted for any of
them.** The mission bars bypassing gated agreements, and acceptance is an act that
belongs to the account holder rather than to an agent working on their behalf.

One case deserves naming because the bypass was available and obvious.
`Salesforce/xlam-function-calling-60k` is gated; three ungated re-uploads of the same
content appeared in the same result page (`lockon/…`, `minpeter/…-parsed`,
`product-science/…-raw`), each carrying its own `cc-by-4.0` tag. Acquiring one of those
would obtain by mirror exactly what the agreement withholds directly. All three are
`REJECT`, on that ground rather than on their own tags. A mirror's licence tag is the
mirrorer's assertion about content they did not create.

## Rejections that were licence-driven, not preference

Three entries were rejected on terms alone, independent of whether they would have been
useful:

- **`OzymandisLi/ChemCraft-Agent-Trajectory`** — `cc-by-nc-nd-4.0`. NoDerivatives forbids
  the filtering and normalisation any ingestion pipeline performs, so there is no
  compliant way to use it even for evaluation. NonCommercial bars it separately.
- **`Tobi-Bueck/customer-support-tickets`** — `cc-by-nc-4.0`. NonCommercial is
  incompatible with a commercial product. It also consists of real support tickets,
  which is a PII shape the mission prohibits ingesting.
- **`rogue-security/prompt-injections-benchmark`** — `cc-by-nc-4.0` *and* gated. Either
  alone would be sufficient.

## PII

No dataset containing personal information was acquired. Two candidates were identified
as PII-bearing by shape rather than by inspection — real customer support transcripts in
`Tobi-Bueck/customer-support-tickets` and
`NebulaByte/E-Commerce_Customer_Support_Conversations` — and both are blocked. Since
nothing was downloaded, no PII scan was run; that is a gap only in the sense that it
would become required the moment an acquisition is approved, and it is named here so
that requirement is not discovered afterwards.

## Attribution obligations, if anything is later acquired

Of the artifacts that are usable in principle, the `apache-2.0` and `mit` entries
require preserving the licence text and notices. The `cc-by-4.0` entries
(`3nesdeniz/agentic-prompt-injection-boundary-pairs`) require attribution to the
creator on any redistribution. `cdla-sharing-1.0`
(`bitext/Bitext-customer-support-llm-chatbot-training-dataset`) additionally requires
that derived data be shared under the same terms, which for a private product means the
obligation should be understood before rather than after ingestion. None of these
obligations are live today because nothing has been acquired.

## Vendor independence

Nothing in this mission created a dependency on Hugging Face. No `huggingface_hub`
import was added, no dataset was vendored, no model was pinned. The one artifact class
that would create such a dependency — a sentence-embedding model for semantic routing —
is recorded in the registry as `NOT_ADOPTED` with its blocker stated, precisely so that
adopting it stays a decision someone makes rather than a fact someone discovers.

---

*Companion documents: `UNDX_HUGGINGFACE_TRAINING_REPORT.md` (what was built and what was
measured), `undx_external_dataset_registry.yaml` (per-artifact decisions with rationale).*
