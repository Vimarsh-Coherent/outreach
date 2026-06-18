# MergeQueue — Developer Velocity Platform (Sales Brief)

## What MergeQueue is
MergeQueue is a merge automation and CI-orchestration platform for engineering
teams that ship to a shared main branch. It batches, tests, and merges pull
requests in a safe serialized queue so the main branch is never broken, even when
dozens of PRs land per day.

## Who it's for
- Platform / DevEx teams and engineering managers at high-velocity SaaS companies
- Teams of 30–800 engineers on a monorepo or a few large repos
- GitHub or GitLab shops where "broken main" and flaky CI block releases
- Pain today: merge conflicts at scale, red main branch, 45-minute CI queues,
  engineers babysitting "rebase and re-run" loops

## Core features
1. **Speculative merge queue** — tests each PR against the predicted post-merge
   state, so a green PR is guaranteed not to break main when it lands.
2. **Batch testing** — groups up to 10 PRs into a single CI run and bisects only
   when the batch fails, cutting CI minutes by up to 70%.
3. **Flaky-test detection** — automatically quarantines tests that fail
   non-deterministically and reports their flake rate over the last 30 days.
4. **Affected-target builds** — builds and tests only the targets touched by a
   change using the dependency graph, instead of the whole monorepo.
5. **Merge insights** — dashboards for time-to-merge, queue wait time, CI cost per
   PR, and the top 10 slowest test suites.

## Quantified value
- Cuts median time-to-merge from 6 hours to 38 minutes.
- Reduces CI compute spend by 40–70% via batching and affected-target builds.
- Keeps main green: customers report 95%+ reduction in "broken main" incidents.
- Setup is a GitHub App install plus one `mergequeue.yaml` file — live in a day,
  no CI migration required.

## Proof points
- A 400-engineer fintech went from 30+ broken-main incidents per month to fewer
  than 2, and cut their CI bill by $48,000/year.
- A Series C dev-tools company merged 1,200 PRs/week through MergeQueue with a
  99.9% green-main rate.
- "We deleted our internal 'merge bot' and three flaky-test Slack channels."
  — VP Engineering, 250-person SaaS.

## Integrations
GitHub, GitLab, CircleCI, GitHub Actions, Buildkite, Datadog CI Visibility,
Slack (queue + flake alerts).

## Pricing
- Team: $40/engineer/month (up to 100 engineers)
- Scale: $30/engineer/month (100–500 engineers, SSO, audit log)
- Enterprise: custom (self-hosted runner support, SLA)

## Call to action
Offer a 30-minute technical walkthrough, or a 14-day pilot on one repo with a
before/after CI-cost report.

## Outreach tone
Engineer-to-engineer, concrete, no hype. Lead with broken-main pain and CI cost,
not buzzwords. Reference cycle time and queue wait, not "synergy."
