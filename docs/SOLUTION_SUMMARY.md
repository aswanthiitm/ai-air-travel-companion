# Solution Summary — Traveler Twin: AI Air Travel Companion

**Expedia Group Innovation Hackathon — Problem Statement 1: AI Air Travel Companion**

## The problem

Every flight search tool answers the same question — *what flights exist?* —
identically for every user, on every query, with no memory of who is asking.
The result is 400 near-identical results and a traveler left to guess which
trade-off (price vs. time vs. layovers vs. comfort) actually matters for
*them*. The hackathon brief asks for something different: a companion that
infers preferences from a user's profile and messy history, reasons about
multi-city trips and date flexibility, and explains its trade-offs — not
another search box.

## The user / business problem

Two groups are underserved by today's flight search:

- **Travelers** get generic rankings that ignore their own stated and
  demonstrated preferences (loyalty, layover tolerance, redeye aversion,
  budget discipline), and get no explanation for *why* one option is "best."
- **Expedia**, in an era where AI agents can book travel through any
  provider's API, has one asset a wrapper-around-an-LLM cannot replicate:
  two decades of proprietary traveler behavior data. Without a product that
  turns that data into visibly better, explainable recommendations, that
  advantage stays latent.

## The proposed solution

**Traveler Twin** is a glass-box digital twin of each traveler that
negotiates flight trade-offs on their behalf and shows its work. It follows
one governing principle: *the AI reasons and communicates; Python computes;
the Twin remembers.* Concretely:

- **A deterministic recommendation core** (unchanged since early milestones,
  fully tested) performs all filtering, scoring, multi-city route
  optimization, and trade-off arithmetic — the LLM never ranks or scores a
  flight.
- **Preference extraction from messy data**: a 38-rule lexicon reads each
  traveler's free-text booking history — not just structured columns — and
  turns lines like *"took a 7hr layover in SIN to save $120"* into a
  quantified personal value of time (~$17/hr), used to judge every future
  trade-off in that traveler's own terms.
- **A hybrid natural-language layer**: deterministic parsing handles most
  requests instantly and for free; a language model is invoked only when
  wording is genuinely ambiguous ("the week after next," "under two grand"),
  and everything it returns is re-validated before reaching the planner —
  the model never bypasses validation or invents a fact.
- **Honest negotiation**: when a traveler's constraints cannot all be met
  (e.g. no direct flight exists on a route at any date in the dataset), the
  system relaxes constraints step by step and states exactly what it gave up,
  instead of failing or silently ignoring the request.
- **A Living Twin**: every accepted or rejected recommendation is an event
  that updates the traveler's profile — with asymmetric confidence weighting,
  so one clear contradiction (a rejected redeye) shifts the model faster than
  one confirmation reinforces it — visible to the user as "Twin updated"
  moments, not a black box.
- **Two audiences, one interface**: a Traveler View shows a clean,
  plain-language recommendation, alternatives, and route map; a
  Judge/Developer View exposes the full reasoning pipeline — the same data,
  presented for the audience that needs it.

## Expected value or impact

- **For travelers**: recommendations that visibly reflect who they are, with
  every claim traceable to real evidence — closing the trust gap that
  drives choice paralysis in generic search results.
- **For Expedia**: a demonstrated pattern for turning historical behavior
  data into a durable, explainable personalization layer that a
  general-purpose AI assistant cannot replicate by calling a booking API —
  directly answering the disintermediation risk of agentic AI in travel.
- **Portability**: the Traveler Twin concept (evidence-backed preferences,
  deterministic optimization, LLM-gated understanding) is not
  flight-specific — the same architecture applies to hotels, cars, and
  packages across Expedia's brands.

## Verification

175 automated tests (dataset invariants, preference extraction, multi-city
optimization, grounding/anti-hallucination checks) pass with no LLM
required; `python -m src.evaluation` reproduces all six benchmark prompts
deterministically for grading. See [docs/BENCHMARKS.md](BENCHMARKS.md) for
full output and [docs/ARCHITECTURE.md](ARCHITECTURE.md) for the complete
design.
