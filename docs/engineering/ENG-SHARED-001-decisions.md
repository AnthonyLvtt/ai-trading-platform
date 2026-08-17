# ENG-SHARED-001 — Shared Domain Primitives

## Status

Implementation record for CTO review.

## Scope

This change consolidates only technical primitives shared by future ATP vertical slices. It does not introduce Market Data models, trading logic, Strategy, Risk, OMS, Accounting, Exchange access, persistence infrastructure or a dependency-injection framework.

## Typed identifiers

Event, correlation and causation identifiers remain distinct Python types. Their values must be non-empty, trimmed and NFC-normalized. No universal public business identifier is introduced.

## Content identity and canonical bytes

`ContentIdentity` records the explicit `sha256` algorithm and the lowercase hexadecimal digest of the exact input bytes. Structured values use a deliberately small canonical JSON encoder with sorted string keys, compact separators, finite numbers and UTF-8 output. Unsupported values fail closed.

## Time

UTC timestamps are validated explicitly. `SystemUtcClock` contains access to current system time, while `FixedClock` provides deterministic injected time for tests and replayable behavior. `LogicalTime` is only a validated UTC timestamp primitive; it is not a distributed clock or Lamport clock.

## Environments

LOCAL, TEST, BACKTEST and SIMULATION remain active. DRY_RUN, TESTNET and LIVE remain recognized but non-activable. Unknown environments fail closed.

## Architecture impact

None. The implementation stays within the shared technical boundary and requires no ADR.
