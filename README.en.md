# Memtrail

[한국어](README.md) | **English** | [中文](README.zh.md)

> **About this public copy** — Development continues in a private repository; cleaned-up changes are brought over here. Last brought over 2026-10-14. Questions and reports are welcome in [Issues](https://github.com/Eum-Jium/memtrail/issues).

> *"The human mind operates by association. ... in accordance with some intricate web of **trails** carried by the cells of the brain."*
> — Vannevar Bush, *"As We May Think"* (1945)

**A personal memory system for AI agents — explicit recall over automatic injection,
verified use over access frequency.**

*Your data never leaves storage you already trust.*

**Who it's for** — people who talk with AI a lot and want to pull back what they decided or thought back then;
who don't want automatic memory leaking into new chats; who need recall even with just a phone in hand.

**What you need** — one always-on PC (a low-power mini PC is enough) · Google Drive · one AI CLI you already subscribe to. No paid vector DB, no API key.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Public copy: 2026-10-14](https://img.shields.io/badge/public_copy-2026--10--14-blue)]()

---

## Trails are made by walking

When Bush sketched the memex in 1945, the core idea was not storage but the **trail.**
Not a prearranged scheme like library classification, but this: the footprints of one person
crossing from this document to that one *are* the structure of the knowledge. That path can be
stored, walked again, and handed to someone else.

Tools in this lineage usually do only ① — the supplementary memory that holds everything you
record. Memtrail is the system that pulls the other piece up into its name.
It is not a system for having a lot of memory. It is a system for **controlling what memory
gets in.**

## Why I built this — context contamination

Most existing memory systems are **push architectures.** The system decides, and pushes past
context into every conversation.

Then a thought from conversation A seeps into conversation B without permission. The user does
not even know it was injected. **A thought that was still exploratory arrives sounding like a
settled position** and bends the direction of the new conversation before it starts.
That is contamination.

Memtrail is a **pull architecture.**

- Recall happens only when the user asks for it.
- What gets fetched is stated explicitly in the query.
- Even what comes back is pinned down in the protocol as *"reference material, not
  instructions."*

**The default state is a clean context.** Having no memory is the default; having some is the
exception. This is the exact opposite choice from automatic memory.

## There is one goal — the conversation must keep going even when all you have is a phone

Once you pick a pull architecture, one requirement remains. If you have to open a laptop to
pull up the past, that's not memory — it's an archive. On a bus, in a café,
holding nothing but your phone, you should be able to ask *"why did we decide it that way back
then?"* and get an answer.

That single requirement dictated the entire structure. **Nothing gets installed on the client** —
the AI app on your phone merely drops a letter into cloud storage, and the search happens
somewhere else. Which means **a computer has to be awake to run that search for you.**

## The shape of it

```
Canonical (always-on PC)      Drive mailbox            Anywhere (web · mobile · CLI)
────────────────────────     ──────────────────       ────────────────────────────
raw conversations       ←─   mailbox/queries/    ←─   drop a query letter
  (immutable)                mailbox/answers/    ─→   coordinates come back
embedding index              mailbox/trails/     ←─   drop a receipt
librarian daemon (2 s)  ─→   raw/                ─→   raw copies (for deep reading)
canon (by hand)
```

The best machine you own at home does the computing, and the cloud holds only **the place
where they meet.**

- Search runs at local speed.
- Fixing it is a local edit — no deploy cycle.
- The only thing reachable from outside is the mailbox.

### Three layers

| Layer | Contents | Updated |
|---|---|---|
| **canon** | Chronology, people. History and dates. | By hand |
| **cards** | Per-conversation summary + recall terms | Auto-generated |
| **raw** | Full conversation text. Immutable. | Append only |

An LLM never gets to reinterpret and overwrite the contents. Which means there is no opening
for distortion to creep in during refinement.

## Components

There are four. The fastest way in is to get straight what each one is and why it exists.

| Name | What it is | What it does | Must it stay on? |
|---|---|---|---|
| **Memtrail server** | One personal PC | Holds the index · librarian daemon · card generation · publishing | **Always on (required)** |
| **Client device** | Mobile · PC — any device running an AI app or the web | Asks for recall, receives answers | Only while in use |
| **Collection source** | A PC that produces code sessions | Collects that machine's conversations and sends them to the server | Once a day |
| **Google Drive** | One cloud storage location | ① **Mailbox** ② **Conversation store** | Always |

**The server and the collection source may be the same machine.** If you can leave your coding
PC on around the clock, that is the simplest setup — and the tailnet in the "Connectivity"
section below disappears entirely.

### The Memtrail server — a personal PC

Where embedding search runs. **For an answer to come back when you ask from your phone, this
machine has to be awake — so always-on is not an optimization, it's a premise.** Letters don't
get lost, but an answer that arrives tomorrow isn't recall.

That said, **it is neither a cloud server nor a VPS.** The workload is embedding search, so one
low-power mini PC in a corner is enough. Embedding, search, and ranking all run locally inside
this box — no paid vector DB, no external search API.

It also needs **an AI CLI you already subscribe to** logged in — used only for generating cards
(conversation summaries), the one and only place in this repo where an LLM is used.
**No separate API key, no per-token billing.**

### Client device — any mobile or PC

**There is nothing to install.** If it can write and read one file on Drive, that's all it
takes. Any AI app with a connector works as-is; without one, Drive API authorization is enough.
It never connects to the server, so it has no need to join the tailnet either.

### Collection — two paths, moving separately

Conversations **land in different places**, so who collects them differs too. This is the most
confusing part of the system, so it is stated plainly in a table.

| Conversation type | Where the record lands | Who collects it | Tools |
|---|---|---|---|
| **Web/app conversations** (claude.ai) | The service account | **The server, directly** | `collect_web_via_cdp.py` (needs a logged-in browser profile)<br>or `download_claude_conversations.js` → `unpack_claude_json.py` |
| **Code session conversations** (Claude Code) | **The disk of the machine that held the conversation** | **That machine collects and pushes to the server** | `unpack_claude_code_jsonl.py` → `collect_and_push.sh` |

The left row is tied to an account, so the server can fetch it on its own; the right row is
local files, and **no matter how awake the server is, it cannot know about them by itself.**
If you coded on a laptop, that laptop has to push the data in. If you only ever code on the
server, the distinction disappears and a single `daily.sh` covers everything.

**Conversations are not the only thing pushed.** The work machine also pushes the **originals**
it froze (`archive/` — session JSONL, received exports). Originals are the only basis for
re-rendering the md (→ [backbone §1](#1-conversations-are-the-canonical-copy)), and session
JSONL exists only on the machine that held the session and disappears after five weeks — leave
it there and it goes with the machine. But it runs to hundreds of MB or more, so unlike
conversations it is not pushed wholesale: **only what was frozen since the last successful push.**

### Google Drive — playing two roles at once

1. **Mailbox** — where client devices drop query letters and the server drops replies
   (`mailbox/`). It is the server's only channel to the outside world that needs no open port.
2. **Conversation store** — copies of the raw text (`raw/`). The librarian returns only
   coordinates, so for a client device to open the original text, a copy has to live here.

The canonical copy always lives on the server; Drive is a read-only replica — **there is no
automatic Drive → local path.**

## Why I didn't open a server

**Not for lack of trying. I tried it, and that is why I don't.**

I built a dedicated MCP server for recall and ran it in the cloud. I lost three things.

- **It was slow.** Embedding search eats compute and memory, and a VM in a price range one
  person can absorb cannot carry it.
- **Changing it was painful.** Every one-line fix rides a deploy cycle.
- **The door was open.** One port, one authentication flaw, and years of conversation walk out
  in a single piece. This is not ordinary data but the entire thinking history of one person —
  the damage is different in kind.

The mailbox architecture **separates compute from contact point** and recovers all three.

> **MCP was not abandoned — it moved.** What was removed is *the recall server I operated
> myself.* The client procedure is implemented as a [**skill**](skills/memex-mailbox/SKILL.md),
> and the letter drop is done by the storage connector that is already installed. In other
> words, **"an MCP where I run the server" became "a skill that uses an MCP that is already
> there."** The point is one fewer server to maintain.

### Security

The librarian daemon does **outbound polling only.**

- Zero open ports. No server hosting. No tunneling. No exposed API endpoint.
- There is simply no door to walk in through over the network.
- The attack surface is the Google account, and that is defended by Google's 2FA and anomalous
  login detection — sturdier than authentication one person built.
- Even if it is breached, what sits on Drive is a replica. Conversely, if the canonical machine
  dies, it is rebuilt from Drive (→ [Rebuilding](#7-rebuilding--when-a-machine-is-gone)).

The honest line: **if the canonical machine itself is compromised, you are had.** That falls to
device hygiene.

What is on by default and what it **does not** do: [`docs/security.md`](docs/security.md)

### Privacy — state the threat model first

**Memtrail adds no new server and no new company to your trust list.**

A cloud memory service ultimately asks you to *"trust this new company."* With Memtrail, data
never leaves the Google account you already use. Authentication, encryption, and 2FA are
Google's burden to carry. **The count of newly trusted parties is zero.**

I won't overstate it. The precise claim is *"no third party is added,"* not *"Google can't see
it either."* Google holds the key for encryption at rest. It can be handed over under a
warrant, and if the account is suspended, access is cut. **"Does not look" and "cannot look"
are different layers.**

Your memory sits in storage you already use. How much you trust that storage is your choice,
and the choice is executed by **swapping the rclone backend.**

### Connectivity — why Tailscale enters the picture

Looking at the recall path alone, the server **never needs to open any port.** It only polls
Drive outbound, and the phone only touches Drive. So far there is no segment where anything
connects to anything directly.

The problem is **collection.** Code session conversations exist only on the work machine's
disk, so that machine has to **push** them to the server — and that is a genuine
machine-to-machine connection. Reach for the usual solutions here and everything protected so
far collapses at once — port forwarding and DDNS put a 24/7 machine holding a lifetime of raw
conversations in range of internet scanners, and a relay VPS puts someone else's
infrastructure into your trust path.

**[Tailscale](https://tailscale.com) makes this connection without ports.** It is a
WireGuard-based private network where the two machines **each dial out and meet in the
middle** — from the server's point of view there are still only outbound connections, and the
open-port count stays at zero. A new device cannot join the tailnet until you approve it
yourself.

The tailnet is used in exactly **two** places: ① work machine → server push, and ② away from
home → server admin (logs, restarts, reindexing). **Client devices (your phone, etc.) never
need to join the tailnet** — they only use Drive. By design, recall never reaches the server
directly.

> **There is a setup that needs no Tailscale at all.** If the PC you code on can stay on,
> **make that PC the server.** The conversations are already on its disk, so there is nothing
> to push, no machine-to-machine connection, no tailnet — a single `daily.sh` covers it.
>
> **And this is the better setup for security, too.** Both setups have zero open ports, but the
> single-machine setup strips away one more layer — there is no reason to run an SSH endpoint
> to receive pushes, no devices to enroll in a tailnet, no keys to keep and manage between
> machines. **A connection that does not exist cannot be breached through.**
>
> The deciding factor is not your resolve to run it "always on" but **whether the machine stays
> put.** A desktop qualifies. A laptop kills recall the moment you carry it out or close the
> lid — and that is when you finally need a separate "machine that stays put," connected over
> the tailnet.

### This is not a coincidence

Serverlessness (security) and contamination prevention (the idea) **fell out of the same design
decision together.**

Because recall is an explicit request, it is implemented as message passing — poll and wait for
a reply. An automatic-injection architecture would need a standing connection, and could not be
built as a mailbox at all.

**Portability came along as a side effect.** A mailbox client's only requirement is *"be able
to write and read one file,"* so the ways to attach are secured twice over — storage connectors
already ship in most AI products, making the integration work zero, and even in an environment
without one, storage API authorization is all it takes. Protocol standards get replaced on a
ten-year cycle; **the contract "write a file" does not change.**

## The backbone of the design

### 1. Conversations are the canonical copy

Accumulate the **original text**, not summaries. Cards (summaries) are only a layer added on
top to make search fast; you can always descend to the original's coordinates (file + turn
number). This structure prevents the failure where the moment you tidy things into notes,
the "why did we reach that conclusion" is gone.

**And even the md is a derivative.** What a converter threw away is always learned afterward —
four such losses surfaced in a single day (canvas bodies, 712 tool-call blocks, attachment
filenames, utterance timestamps). If the original JSON/JSONL is still there, you fix the
converter and re-render; if it is not, that is the end of it. So received originals are never
deleted — they are frozen into `archive/sources/`.

### 2. Recall happens through the mailbox (the only interface)

Search is not a service — it is **asynchronous mail.** Any client drops a query letter (JSON)
into `mailbox/queries/`; the librarian daemon reads it and answers into `mailbox/answers/`
with **coordinates.**

Three of this system's five design principles fell out of this — zero inbound · one-way
canonical flow (no automatic Drive→local path) · whitelist execution (free text is never
interpreted as a command).

The librarian **uses no LLM** — just E5 embeddings + Chroma + BM25, cheap and deterministic.
Design rationale: [ARCHITECTURE.md §0](ARCHITECTURE.md#0-왜-우편함인가)

### 3. Search is not reading — it is querying a prebuilt index

Conversations are not read when a question arrives. **At collection time every conversation has
already been split into chunks, embedded, and loaded into a vector DB (Chroma)** — the
librarian merely queries that index. That is why it finishes in seconds at a scale of tens of
thousands of chunks, with no LLM. It is also why a low-power mini PC is enough for the server.

Relevance is **scored along four tracks and blended.**

| Track | What it measures | Score |
|---|---|---|
| **dense** | Semantic similarity — cosine distance over E5 embeddings | `1/(1+distance)` → 0–1 |
| **BM25** | Whether the actual words overlap | Cut by ratio to the top hit; score is `null` |
| **recent** | Last 90 days ∩ dense top ranks ∩ BM25 floor | `null` |
| **latest** | The **latest mention among the matches** (not an absolute window) | `null` |

The tracks are drawn from in rotation, one item at a time (interleaved), and **each
conversation enters only once** — deduplication has to happen per conversation, not per chunk,
or the top 5 fills up with fragments of a single conversation.

Why not just one? Because measurement taught that one is not enough.

- **dense alone** — E5's similarity band is narrow (0.72–0.83), so unrelated conversations tag
  along.
- **BM25 alone** — off-target conversations that happen to share common words float up
  (measured: a query for *"AI model comparison"* returned a conversation on an entirely
  different subject).
- **The recent track** — with global ranking alone, a topic you have discussed for a long time
  gets its top ranks monopolized by old conversations. So one slot is reserved for chunks from
  the last 90 days that satisfy **both** the semantic and the keyword bar.
- **The latest track** — the recent track is an absolute 90-day window, so it cannot catch a
  *current value that changed longer ago than that.* See "Reading deliberation as decision"
  below.

If date or source conditions are given, they filter at the index level first, before scoring.

Along with the score, each answer carries **which track caught it** (`match`). BM25, recent,
and latest hits have `null` scores — they were caught not by similarity but because **the
phrase is actually there**, so for questions with proper nouns or exact wording, these are
often the right answers.

### 4. Well-trodden paths grow thicker (trail)

See the [dedicated section](#trail--verified-use-not-access) below. Where the scores above are
*"similarity computed right now,"* trail is *"a history of having actually been useful in the
past"* — a different kind of signal. They ship together.

### 5. The canonical layer (canon)

Facts that **must not be wrong** — history, decisions, people, chronology — are not
re-excavated by search. A single hand-maintained `canon.md` is read first. Every entry carries
an evidence grade, so confirmed facts are never mixed with unverified conjecture.

Before this layer existed, reconstructing an old piece of history took 3 searches plus 2 direct reads
of original text — and the facts still came out wrong. **The layer was built as an admission
that there are things search cannot do.**

## trail — verified use, not access

Injection in automatic memory is **repetition without verification.** Once something is stored,
right or wrong, it keeps arriving — and the more it is injected, the harder it sets.

trail is the opposite. It accrues **only when a recall was actually used in an answer**, and
only when that use was explicitly confirmed by a receipt.

```json
{"type":"trail_receipt","q":"the original query",
 "used":[{"file":"conversationA.md","depth":"deep"}],
 "edges":[{"from":"conversationA.md","to":"conversationB.md","relation":"근거"}]}
```

- `uses` — how often it was pulled out (**frequency**)
- `deep_uses` — of those, how often it went past the card summary into the original (**depth**)
- `edges` — only relations **explicitly confirmed in the conversation**
  (`근거` evidence · `후속` follow-up · `연관` related). Nothing is linked on a guess.

Search results carry this thickness alongside the scores. Instead of recomputing similarity
every time, it is a device that **turns a once-verified connection into a lookup.** This is
exactly the trail Bush described.

Reinforcement-style implementations mostly **increment a counter the moment something is
retrieved — that is access frequency.** Memtrail filters on whether it was actually used in an
answer. That distinction is the center of this system. (The structure resembles biological
reconsolidation — a path strengthens each time it is used, unused links weaken on their own,
and deeply processed memories set harder. But that is an analogy, not a model.)

One rule: **do not widen a path before it is walked.** No automatic edge generation (LLM
inference), no graph database. An edge comes into being only when a recall actually used two
documents together.
Design rationale: [`docs/specs/2026-08-08_trail_spec_v1.md`](docs/specs/2026-08-08_trail_spec_v1.md)

## What the memory knows and what it doesn't

For recall to be honest, it has to be able to **say it doesn't know.** There are dedicated
mechanisms for that.

- **Evidence grades** — canon entries split into `[dated by the owner]` and `[unverified]`.
  Gaps are not filled with conjecture.
- **coverage** — every reply carries the latest index date per source. *"Not indexed yet"* and
  *"nothing recorded that day"* are different statements and are never blurred together.
- **Genealogy mode** — *"when did I first say this?"* cannot be caught by score-ranked search,
  because the most matured conversation takes the top ranks. It needed a separate mode that
  gathers only conversations with confirmed word overlap and returns them in ascending date
  order.
- **`summary_kind`** — distinguishes whether what came back is an LLM summary or a raw excerpt.
  Inferring a conversation's conclusion from a raw excerpt gets it wrong.

## Failures, as measured

This may be the most useful part of the repository. **Every piece of the design came out of an
accident.**

**Wrong recall terms (2026-08-15)** — asked about a matter using **the formal words a
dictionary would give** and every one of them spun uselessly. The original text was written in
the **colloquialisms and abbreviations** actually used while living through it. Same subject,
not one character in common. Not a collection gap but a vocabulary failure.
→ why cards gained a **recall-terms field** — read the whole conversation, then write down
separately, on the card, the words someone would plausibly use to look for this later.

**First mention not caught (2026-08-20)** — answered that a concept started on 6/27 when it
actually started on 5/22. The score spread across the top 400 dense hits is 0.07, which cannot
separate relevant from irrelevant. That noise is buried in ordinary search but rises to the
front the moment you order by date. → why **genealogy mode requires word-overlap confirmation.**

**Reading deliberation as decision (2026-08-25)** — asked *"what's my \<current value> again?"* and
got back a top-scoring **deliberation** conversation from years earlier, which the
answer simply repeated. The real one was different. **Deliberation is long and decision is
short** — a candidate that took twenty turns to choose beats, on score, the real thing that got
one passing line after the decision. → why replies gained an **ascending-date timeline view** and a
**latest track.**

**Trusting the title and discarding the hit (2026-08-25)** — under an auto-generated title
taken from the first question sat a 222-turn conversation. Search brought back the right thing and
**the reading side threw it away.** → why cards now **re-title** a conversation after reading
the whole of it.

**Misdiagnosing why the originals vanished (2026-08-26)** — code session JSONL was missing only
before a certain date, so I wrote that *"it expires after five weeks."* Wrong. The work machine
had been replaced, and the old one was wiped. The implication is the opposite — with
expiration you only need to collect within five weeks, but **machine replacement is guaranteed
to happen again, the loss is 100%, and there is no warning.** 250 conversations survived
because they were converted to md and pushed in daily; what was lost was the utterance
timestamps and tool-call detail that existed only in the originals — **converter improvements
can never be applied retroactively to those 250.** → why originals are now frozen into
`archive/sources/`.

**Asking about a person with attribute words (2026-08-27)** — asked *"what is my \<personal attribute>?"*
with that attribute word and its synonyms and got it wrong three times running. People who
do the thing don't use the word for the attribute — they just do the thing. The real answer was
nothing like the guesses, and the corpus had records of it piling up for months.
**A person's attributes are not in the conversations where that word appears.** → why the
client procedure now asks with **verbs of doing**, not attribute nouns.

These are findings from a sample of one. Once other people run it, failure cases pile up
tenfold, and that becomes the raw material for improving the design.

## Current status (2026-08)

**Working**

- ✅ Conversation ingest — claude.ai (browser console pipe / official export) / Claude Code
  JSONL / cowork sessions
- ✅ Other-service import — ChatGPT official export · Gemini Takeout. Provenance survives as a
  filename tag, and every layer behind it (indexing, cards, search, trail) makes no distinction
  by source
- ✅ ChromaDB semantic search (`intfloat/multilingual-e5-base`, Korean)
- ✅ Hybrid search — dense + BM25 interleave + last-90-days slot + latest slot + date prefilter
- ✅ Mailbox protocol — librarian daemon polling every 2 s, measured round trip **4.3–5.6 s**
  (storage connector path; the rclone fallback is 10.5 s, dominated by process startup)
- ✅ Card layer — structured per-conversation summaries (decisions / open questions /
  reversals / recall terms / topic tags / events) + titles
- ✅ Card recall-term bridge — a card's recall terms and topic tags ride along in search, so a
  conversation is caught even by an alias that never appears in the original, and the
  coordinates still land on the original rather than the card
- ✅ canon layer — history · decisions · people · chronology
- ✅ trail P0/P1/P1.5 — receipts settle into state; frequency and depth shown alongside search
  results
- ✅ Original archival — received exports and session JSONL are frozen into `archive/sources/`,
  and fixing a converter applies retroactively via `--rerender`
- ✅ Quarantine — files that flow back in through the sync folder cannot enter the index
  without approval
- ✅ Self-contamination guard — broke the recursion where card-generation LLM calls left
  session logs that got re-ingested

**Watching**

- 🔍 trail P2 (1-hop expansion) — on hold; edges are structurally at zero.
  The leading indicator is not edge count but the rate of **receipts whose `used` list has 2+
  entries** (`scripts/trail_report.py`)

**Planned**

- 🚧 Time awareness (validity windows for facts — `valid_from` / `valid_to`)
- 🚧 Memory tiers (Hot / Warm / Cold)
- 🚧 Decay + reinforcement

**Retired**

- ❌ Cloud MCP server (Railway) — zero real-world use
- ❌ Local MCP recall server — unified into the mailbox protocol (2026-08-03)

Roadmap: [ROADMAP.md](ROADMAP.md) · Design: [ARCHITECTURE.md](ARCHITECTURE.md)

## Architecture overview

```
   Client device (mobile · PC)      ← nothing to install; Drive access is enough
         │  q_{id}.json          ▲  a_{id}.json
         ▼                       │
  ┌─────────────────────────────────────────┐
  │  Google Drive                           │
  │   mailbox/  ← the mailbox               │
  │     queries/ answers/ commands/ trails/ │
  │   raw/      ← conversation store        │
  │               (for deep reading)        │
  └─────────────────────────────────────────┘
         │                       ▲
         ▼  2 s polling          │  coordinates (card summary + file + anchor)
  ╔═════════════════════════════════════════╗
  ║  Memtrail server — personal PC, 0 ports ║
  ║                                         ║
  ║   ┌─────────────────────────────────┐   ║
  ║   │ librarian daemon  (0 LLMs)      │   ║
  ║   │ validate → E5 embed → hybrid    │   ║
  ║   │ → attach trail → tally receipts │   ║
  ║   └───────────────┬─────────────────┘   ║
  ║                   │                     ║
  ║   ┌───────┬───────┼────────┬─────────┐  ║
  ║   ▼       ▼       ▼        ▼         │  ║
  ║ [Chroma][BM25][trail_state][canon.md]│  ║
  ║  semantic lexical uses·depth  by hand│  ║
  ║                                      │  ║
  ║   conversations/*.md  ← canonical ◀──┘  ║
  ║   cards/*.md          ← summary per conv║
  ║   archive/sources/    ← originals (immutable)
  ║              ▲                          ║
  ╚══════════════╪══════════════════════════╝
                 │ tar over SSH (only inside the tailnet)
        ┌────────┴──────────┐
        │  Collection source│  code session conversations remain only
        │  (laptop, etc.)   │  on this machine's disk — it must push
        └───────────────────┘  them in itself
```

## Quick start

### 1. Clone + environment

```bash
git clone https://github.com/Eum-Jium/memtrail.git
cd memtrail

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Point at your data location

Code and data are separated. Data lives **outside the repo.**

```bash
cp .env.example .env
# set MEMEX_DATA_DIR in .env to your own data location
```

```
$MEMEX_DATA_DIR/
├── conversations/   # raw conversations (the canonical copy)
├── cards/           # one card per conversation
├── memory_db/       # ChromaDB
├── canon/           # hand-written canonical layer
├── archive/         # originals (export zips · session JSONL · attachments)
└── trail/           # receipt sediment
```

### 3. First ingest

```bash
# claude.ai — paste download_claude_conversations.js into the F12 console of a
# logged-in browser, then unpack the downloaded json
python unpack_claude_json.py ~/Downloads/claude_conversations_<date>.json

# Claude Code sessions (JSONL → md)
python unpack_claude_code_jsonl.py --out $MEMEX_DATA_DIR/conversations

# indexing
python memory_pipeline.py $MEMEX_DATA_DIR/conversations $MEMEX_DATA_DIR/memory_db
```

### 4. Card generation (optional)

```bash
python scripts/build_memory_cards.py
```

**It runs on the AI you already subscribe to.** It calls the logged-in `claude` CLI with `-p`,
so there is no separate API key and no per-token billing. This is the one place in this repo
where an LLM is used.

Without it, the script degrades quietly: no CLI login → `ANTHROPIC_API_KEY` (this path is
billed); none of that → an **excerpt stub** — the cards just come out plainer; the pipeline
does not stop. The default model is haiku (quality confirmed by backfilling 98 cards).

Topic tags are not free-form: they are picked from the controlled vocabulary in
[`config/topic_vocab.md`](config/topic_vocab.md). Left free-form, 81% of 344 unique tags
appeared exactly once, making frequency counting itself impossible.

The list in the repo is an example. **Put your own topics in
`$MEMEX_DATA_DIR/config/topic_vocab.md` and that copy takes precedence** — no need to commit
your personal interests to the repo.

### 5. Standing up the mailbox

**To actually use recall, you have to get this far.** Everything up to here piles data up;
the mailbox is the only window that takes it back out.

On the server side, all mailbox round-trips go through [rclone](https://rclone.org) —
**addressed by rclone remote path, not folder IDs.** ① Install rclone → ② create a Drive
remote with `rclone config` (e.g. `gdrive:`) → ③ create
`queries/ answers/ commands/ trails/ receipts/ processed/` under `ClaudeMemory/mailbox/`
on Drive, and if your remote name differs, set `MEMEX_DRIVE_REMOTE` in `.env`. Then:

```bash
python scripts/mailbox_daemon.py
```

(The folder IDs that go into the client skill (SKILL.md) are **client-side**
addresses — the parentId that the connector's `create_file` uses. The server
never uses IDs.)

**This is the always-on PC.** For an answer to come back when you ask from your phone, this
process has to be awake. (Letters do not get lost while it is down — they sit in the folder and
the backlog gets processed once it comes back up. But *an answer that arrives tomorrow is not
recall.* A feature you cannot use mid-conversation does not exist.)

For auto-start on boot: `scripts/com.memex.mailboxd.plist` (launchd) on macOS, a systemd unit
on Linux, Task Scheduler on Windows — just wire up the same command.

On the client side there is nothing to install. If it can reach that storage — by connector or
by API — that is all it takes.

Protocol details: [`docs/mailbox.md`](docs/mailbox.md) ·
Client procedure: [`skills/memex-mailbox/SKILL.md`](skills/memex-mailbox/SKILL.md)

### 6. Wiring up the daily jobs

**On the always-on PC** — collect → incremental indexing → (monthly profile) → card
regeneration → secret scan → upload → manifest:

```bash
bash scripts/daily.sh
```

**If there is a separate work machine (a laptop, etc.)** — CLI conversations held on that
machine exist only on its disk, so collect them and push them to the always-on PC:

```bash
bash scripts/collect_and_push.sh
```

Record the always-on PC's ssh alias (`MEMTRAIL_SERVER`) in `.env`. Transfers happen only
inside the tailnet. If nothing changed, both scripts skip everything wholesale.

Hook both into the OS scheduler — and **be sure to enable the catch-up-missed-runs option**
(Task Scheduler's `StartWhenAvailable` and the like). Above all, do not schedule the laptop
job in the small hours: the machine is off overnight and misses every run.

On a Windows work machine, register
[`scripts/run_collect_and_push_hidden.vbs`](scripts/run_collect_and_push_hidden.vbs) in Task
Scheduler — scheduling bash directly flashes a console window on every run; this wrapper runs
it in a hidden window and passes the exit code through to the scheduler unchanged. The
registration command is in the comment at the top of the file.

The librarian daemon (step 5) is a resident process separate from these jobs. These pile the
data up; that one takes it out.
Pipeline details: [`docs/drive_sync.md`](docs/drive_sync.md)

### 7. Rebuilding — when a machine is gone

**The way to find out whether this document is correct is to rebuild from the document alone.**
The folders were actually deleted and rebuilt three times following only this section.
**If you get stuck, that is a documentation bug — fix it and commit.**

The procedure is the same as the quick start with **exactly one step different** — restore
instead of ③ first ingest:

```bash
# ①② clone + environment + .env are unchanged. Connect the rclone remote (see ⑤) first, then:
python scripts/restore_from_drive.py                 # Drive → local (the target folder must be empty)
python scripts/dedupe_sessions.py --apply --cards    # Drive still holds deleted duplicate sessions; they come back
python scripts/rebuild_memory_db.py --md $MEMEX_DATA_DIR/conversations --out $MEMEX_DATA_DIR/memory_db
                                                     # the index is a derivative, so rebuild it (measured ~16 min)
```

If you don't even have the repo — pasting [the seven rclone lines in
`docs/drive_sync.md`](docs/drive_sync.md) brings the data back on its own (no Python needed).

The only things that cannot be automated are **the three keys a human holds**: the Google
account (rclone re-auth), the claude CLI login, and a few lines of `.env`. You cannot keep the
safe's key inside the safe — hold on to those three and everything else is rebuilt by the four
lines above (measured 2026-08-15: server data deleted entirely → back in 33 minutes; work
machine folder deleted entirely → conversation set matched exactly after restore).

## Mailbox protocol

```
Client                               Librarian daemon
─────────────                        ─────────────────
q_{id}.json ─→ queries/  ─────────→  2 s polling → validate (16KB · schema · filename)
                                     → E5 embedding + Chroma + BM25 interleave
                                     → attach trail thickness
a_{id}.json ←─ answers/  ←─────────  returns coordinates (card summary · file ID · anchor)
                                     → processed letters kept in processed/

t_{id}.json ─→ trails/   ─────────→  receipt tally (no reply)
c_{id}.json ─→ commands/ ─────────→  admin commands (whitelist of 4)
```

The admin commands are exactly four — `promote_thread` · `quarantine_list` ·
`quarantine_adopt` · `reindex` — and everything else is bounced with a rejected receipt.
**The mailbox is a trust boundary.**

## Project layout

```
memtrail/
├── memory_pipeline.py               # embedding + ChromaDB pipeline
├── analyze_pipeline.py              # conversation analysis
├── generate_profile.py              # profile generator
├── unpack_claude_json.py            # claude.ai JSON unpacker
├── unpack_claude_code_jsonl.py      # Claude Code JSONL unpacker
├── download_claude_conversations.js # claude.ai downloader (browser console)
├── scripts/
│   ├── mailbox_daemon.py            # the mailbox librarian (resident, no LLM)
│   ├── build_memory_cards.py        # card generation
│   ├── trail_store.py               # settle receipts into trail_state
│   ├── trail_report.py              # trail observation (for the P2 go/no-go call)
│   ├── archive_sources.py           # move received originals into archive/sources/
│   ├── archive_code_sessions.py     # freeze session JSONL
│   ├── quarantine.py                # quarantine for backflow files
│   ├── index_incremental.py         # incremental indexing
│   ├── monthly_update.py            # monthly indexing + profile resynthesis
│   ├── daily.sh                     # always-on PC: collect → index → publish
│   └── collect_and_push.sh          # work machine: collect → push to the always-on PC
├── config/topic_vocab.md            # controlled vocabulary for topic tags
├── skills/memex-mailbox/SKILL.md    # client recall procedure
├── docs/
│   ├── mailbox.md                   # mailbox operations
│   ├── security.md                  # security defaults · remote admin (Tailscale)
│   ├── drive_sync.md                # publish pipeline (one-way)
│   └── specs/                       # specs (mailbox v1–v3, trail v1, titles v1)
└── ARCHITECTURE.md · ROADMAP.md · CONTRIBUTING.md
```

## Tech stack

- **Language** — Python 3.10+
- **Vector DB** — [ChromaDB](https://www.trychroma.com/) (`sqlite-vec` under review long-term)
- **Embeddings** — `intfloat/multilingual-e5-base` (strong on Korean, 768 dimensions)
- **Keyword search** — BM25 (interleaved with dense)
- **Card generation** — the subscribed `claude` CLI (`-p`). The only layer that uses an LLM;
  falls back to a stub without it
- **Librarian daemon** — one Python process, hosted on the always-on personal PC
- **Storage formats** — Markdown, SQLite, and JSON only. It has to still be readable 30 years
  from now

## Design principles

1. **A lifetime asset** — built on the premise of 30+ years of operation. Format longevity
   comes before performance.
2. **Data isolation** — code and data separated; data location set by environment variable.
3. **Local first** — embedding and search are entirely local computation. An external LLM only
   for card generation, and even that is optional.
4. **Do not widen a path before it is walked** — no structure is built for needs that have not
   been observed.
5. **Failures leave logs** — otherwise the cause gets misdiagnosed.
   (71 cases assumed to be timeouts were all prompt bugs.)

## What this is not

- **Not an all-purpose memory.** It presupposes operational discipline. The canonical machine
  has to stay on, and canon has to be maintained by hand.
- **Reading is not free.** A polished wiki-style document is done once you load it into
  context; Memtrail has a search round trip and the risk of picking the wrong recall terms.
  **It is a design that chose fidelity and paid for it in read speed.** canon is the device
  that reduces that payment.
- **Weak on synthesis questions.** *"How does my overall position on this topic shape up?"* is
  better served by a refined document than by a hit list. If that starts hurting often, add
  more refined layers like canon, one per topic — the extension path is already inside.
- **Not a general-purpose library.** It is the public copy of one person's daily-driver system.
  Things like `config/topic_vocab.md` or the mailbox addresses must be swapped for your own.
  That is not a restriction but **an invitation to fork it and tear it apart** — it's Apache 2.0.

## Swappable parts

| Part | Default | Alternatives |
|---|---|---|
| Storage | Google Drive | anything rclone backs (Dropbox, OneDrive, S3, NAS) |
| Librarian | embedding searcher (E5 + Chroma + BM25) | replaceable wholesale — the contract is the letter schema |
| Card generation | subscribed `claude` CLI | just swap the CLI |
| Collectors | Claude-family unpackers | drop md in the same format into `conversations/` and the layers behind it don't care about the source |

What remains is **the mailbox protocol** and **the receipt/trail schema.**
Memtrail's identity is not the code but the protocol.

## Systems that inspired this

- [Vannevar Bush, "As We May Think" (1945)](https://www.theatlantic.com/magazine/archive/1945/07/as-we-may-think/303881/) — the original memex concept, and the associative trail
- [Mem0](https://github.com/mem0ai/mem0) — hybrid store + fused retrieval
- [Zep / Graphiti](https://github.com/getzep/graphiti) — time-aware graph
- [Letta (MemGPT)](https://github.com/letta-ai/letta) — OS-style memory hierarchy
- [Cognee](https://github.com/topoteretes/cognee) — six-line knowledge engine
- [OpenMemory](https://github.com/CaviraOSS/OpenMemory) — cognitive memory model

## About the name

For a long time this project grew up under the name `memex` — taken from the *memory extender*
Bush sketched in 1945 — and identifiers in the code (`MEMEX_DATA_DIR`, `skills/memex-mailbox`,
and so on) still carry that name.

The public name changed to **Memtrail** for two reasons.

1. `MEMEX` is an already-registered trademark in Korea under the software class (class 9).
   Too risky a name to ship under.
2. The more important reason — **trail is this system's actual differentiator.** Bush's vision
   stood on two pillars: ① a supplementary memory that holds everything you record, and
   ② the *associative trail*. Tools in this lineage usually do only ①. Memtrail
   [actually implements](#trail--verified-use-not-access) ②.

`mem` stayed and `trail` was added. Measured by what the name means, the rename lands closer to
the original vision.

The new name was cleared too. `jrfonseca/memtrail`, an LD_PRELOAD-based Linux memory profiler,
came first — but the domains are entirely different (systems programming vs. AI agent memory),
so no real user will confuse them. There is no registered trademark under the same name.

## Good to know

- Docs and code comments are in Korean; this English README is the entry point for non-Korean
  readers. The embedding model was also chosen with Korean as the primary language.
- Data (conversations, cards, profiles, indexes, originals) never enters the repo.
  See `.gitignore`.
- The SSL verification bypass is opt-in (`MEMEX_DISABLE_SSL_VERIFY=1`) — for proxy environments
  with self-signed certificates.

## License

[Apache 2.0](LICENSE)

The early memory layer (`memory_pipeline.py` · `generate_profile.py` · `test_embeddings.py` · `test_memory.py`)
started from [nir3y/Memory-Layer](https://github.com/nir3y/Memory-Layer) (Apache 2.0), built together with
[nir3y](https://github.com/nir3y), and has been modified here.

## Contributing

Issues (questions, bugs, reports) are welcome. Pull requests are not accepted — the code is brought over from a private source. Forking and adapting it is welcome (Apache 2.0).
Guide: [CONTRIBUTING.md](CONTRIBUTING.md) · [Code of conduct](CODE_OF_CONDUCT.md)
