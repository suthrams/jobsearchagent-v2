# ADR-046: Hybrid Configuration Model (YAML + DB Overrides)

## Status

Accepted

## Context

The system originally used config.yaml for all configuration.

With UI integration, users need to adjust:

* job search criteria
* preferences
* limits

However, exposing full config to UI risks instability and unsafe behavior.

## Decision

Adopt hybrid configuration model:

```text
Effective Config = YAML Defaults + DB Overrides
```

* YAML stores system defaults
* DB stores user overrides
* ConfigService merges both

## Consequences

### Benefits

* flexible UI-driven configuration
* stable system defaults
* traceable configuration per run
* safe bounded behavior

### Trade-offs

* additional service layer
* config merge complexity
* need for validation

## Constraints

Users cannot modify:

* LLM models
* prompt definitions
* safety limits
* cost limits
