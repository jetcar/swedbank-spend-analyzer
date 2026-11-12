# ADR-006: Explicit dashboard state and feature views

## Status

Accepted — 2026-11-09

## Context

`app/static/app.js` mixed API requests, shared mutable arrays, loading races,
navigation, account drafts, category editing, split editing and every dashboard
template. A change in one screen could silently depend on another screen's globals.

## Decision

Keep ES module sources and the current HTML/CSS. The existing esbuild pipeline
produces fingerprinted production assets. `app.js` composes the dashboard and
binds navigation, language, synchronization and page lifecycle.

| Module | Responsibility |
| --- | --- |
| `api-client.js` | JSON transport and structured HTTP failures; injected logging callbacks |
| `dashboard-state.js` | Dashboard snapshot and versioned loading; no DOM dependencies |
| `accounts-view.js` | Account rendering, monitoring and balance drafts |
| `categories-view.js` | Category editing, creation and icon selection |
| `transactions-view.js` | Filters, lists, transaction details and assignment |
| `split-editor.js` | Split drafts, validation and persistence |
| `overview-view.js` | Totals, recent transactions and cashflow chart |
| `budget-status.js` | Budget markup shared by overview and category management |
| `dashboard-ui.js` | Shared formatting markup, status, logging and action error handling |
| `expense-domain.js`, `finance-domain.js` | DOM-independent calculations and queries |
| `live-refresh.js` | SSE invalidation, deferred refresh and connection shutdown |

Feature factories receive state and callbacks explicitly. They never import the
entry point or other feature views. Drafts and filter state stay with the feature
that owns them. The composition root supplies cross-feature callbacks, including
opening splits and refreshing totals after a balance edit.

A load publishes accounts, categories, transactions and sync status together only if it is
still the newest request. Stale successes and failures are ignored. A current
failure preserves the last loaded snapshot. This does not cancel the network
requests that have already started.

Mutations go through the JSON client and action wrapper. Category updates send
only the changed property. Failed category and monitoring edits restore the
displayed persisted value and re-enable the control. Balance drafts retain their
existing retry behavior.

An SSE `expired` event closes the connection and navigates to the demo entry
page. Page lifecycle handlers stop the connection on departure and reconnect
with a snapshot refresh on restoration from the back/forward cache.

## Consequences

No new framework or public API change is required. Direct Python development
can serve source modules; production serves the existing fingerprinted build
with uncached HTML entry points. Architecture tests enforce
acyclic imports, independent feature views and DOM-free domain/loading modules.
Browser regression covers layouts and interactions across the extracted modules.

Templates remain strings with escaped external text. Splitting rendering into
smaller components or adopting a framework is a separate decision, not a
prerequisite for explicit ownership and dependencies.
