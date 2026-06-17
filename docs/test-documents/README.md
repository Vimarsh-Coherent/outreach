# Test documents for AI sequence RAG

Upload these on **Sequences → + AI sequence** to test document-grounded generation.

| File | Use case |
|------|----------|
| [mergequeue-devtools.md](mergequeue-devtools.md) | **Dev-tools** pitch to platform/DevEx teams & eng managers (fact-dense — best for high grounding scores) |
| [founderos-productivity.md](founderos-productivity.md) | **Startup-founder productivity** pitch (fact-dense — best for high grounding scores) |
| [coherent-analytics-pitch.md](coherent-analytics-pitch.md) | B2B SaaS outreach to engineering managers |
| [projectflow-saas.md](projectflow-saas.md) | Startup founder / product team outreach |
| [security-compliance-brief.txt](security-compliance-brief.txt) | CISO / compliance leader outreach |

> **The two new docs are richer** (named features, metrics, proof points, pricing,
> integrations). Because grounding now reuses the document's exact terminology and
> numbers, these dense docs produce noticeably higher cosine similarity in the
> grounding check than the older short briefs.

## Example prompts

> The prompt is for **strategy only** (audience, tone, steps, channels, cadence).
> All product facts come from the selected document(s). Don't put product claims in
> the prompt — put them in the doc.

**Dev-tools (select `mergequeue-devtools.md`):**
```
4-step mixed sequence (LinkedIn connect, then 3 emails) for platform engineering managers. Engineer-to-engineer tone, spaced over 2 weeks. Use {{first_name}} and {{company}}.
```

**Startup founder (select `founderos-productivity.md`):**
```
3-email sequence for pre-seed founders who are about to raise. Casual, founder-to-founder tone. Use {{first_name}}.
```

**Discrimination test (select BOTH dev-tools + founderos):**
```
Pick the ONE product that best fits a startup founder and write a 4-step mixed email + LinkedIn sequence for that audience. Casual tone.
```

## Checklist (read this if generation says "blocked" / "no documents")
- Qdrant running: `docker compose up qdrant`
- Doc status shows **indexed** with chunk count > 0
- **The document checkbox is selected before clicking Generate** — this is the #1
  cause of empty-excerpt output. With no document selected, the generator falls
  back to the prompt and grounding can't be measured.
- After generating, open the sequence → **Check document grounding** to see the
  cosine score per email.
