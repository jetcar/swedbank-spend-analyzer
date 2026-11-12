# Implementation Plan: Modular frontend testing

## Task List

1. [x] Add Vitest and a browser-independent domain module.
2. [x] Cover daily indexing, split movement, and total updates with unit tests.
3. [x] Load the frontend domain module from the application and verify UI startup.
4. [ ] Extract remaining category and rendering state helpers incrementally.

## Checkpoint

- [x] Four unit tests pass.
- [x] Docker app health passes.
- [x] Browser loads the module frontend without console errors.

## Boundary

Playwright remains a later, small DOM smoke layer; it is not the primary frontend test suite.
