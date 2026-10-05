# Iron Cold Lease Infrastructure

Infrastructure control layer for provisioning and managing Iron Cold challenge instances for BPCTF2.

This directory contains the Iron Cold-specific tooling used by the BPCTF2 Lease Manager. The `icctl` utility provides the control-plane interface used by organizers and by the self-service lease backend.

## Purpose

The Iron Cold lease layer is responsible for managing the lifecycle of challenge instances.

The self-service layer does not directly manipulate Docker Compose. Instead, it invokes `icctl`, allowing the infrastructure implementation to remain separated from the HTTP-facing lease service.

```text
Player
   │
   ▼
lease-selfserve / leased.py
   │
   │ icctl show / lease
   ▼
icctl
   │
   ▼
Docker Compose
   │
   ▼
Iron Cold Instance
```

## Components

### `icctl`

Command-line control utility for Iron Cold infrastructure.

The self-service API uses it primarily through:

```text
icctl show "<team>"
icctl lease "<team>"
```

The control utility is also used operationally by organizers for infrastructure management and remediation.

### `docker-compose.yml`

Defines the Docker-based Iron Cold challenge environment.

The Compose configuration is responsible for running the actual challenge infrastructure associated with a leased slot.

### `install.sh`

Installation/bootstrap script for preparing the Iron Cold environment on a VM.

It is intended to establish the required infrastructure and make the control utility available for use.

## Lease Lifecycle

A typical self-service request follows this sequence:

```text
Team submits lease request
        │
        ▼
leased.py
        │
        ▼
icctl show TEAM
        │
        ├── Existing lease
        │       │
        │       ▼
        │   Return existing instance
        │
        └── No existing lease
                │
                ▼
           icctl lease TEAM
                │
                ▼
          Docker Compose
                │
                ▼
          Iron Cold instance
                │
                ▼
          Lease information
```

The initial `show` operation is important because the HTTP service is intentionally idempotent for repeated requests.

## Existing Leases

If a team already has an active lease, `leased.py` retrieves the existing allocation instead of attempting to create another instance.

This prevents duplicate provisioning when:

- A player refreshes the page
- A request is repeated
- A client times out while the backend is still provisioning
- A player submits the same team name again

## Provisioning Time

Iron Cold provisioning may take approximately 10–25 seconds during a cold start because the underlying lease operation can require Docker Compose to start the challenge environment.

This is expected behavior.

A client-side timeout does not necessarily mean provisioning failed. A subsequent request using the same team name should resolve the existing lease if the original provisioning completed successfully.

## Operational Commands

The exact operational interface is provided by `icctl`.

Important operations used by the self-service layer include:

```bash
./icctl show "<team>"
./icctl lease "<team>"
```

The organizer tooling also supports operational remediation, including quarantining an abused slot with:

```bash
./icctl bad <slot>
```

The latter is an operational control rather than part of the player-facing API.

## Self-Service Integration

`leased.py` is configured to use this control utility through:

```text
LEASED_CTL
```

For an Iron Cold deployment:

```text
LEASED_MODE=ironcold
LEASED_CTL=<path-to-icctl>
```

The backend invokes the control utility and parses its output.

Iron Cold lease responses are expected to expose the target endpoint using:

```text
Target: ...
```

The self-service backend converts this into an API response containing:

```json
{
  "address": "...",
  "status": "created"
}
```

For an existing allocation:

```json
{
  "address": "...",
  "status": "existing"
}
```

## Security Considerations

The Iron Cold control utility runs with the privileges required to manage the underlying Docker infrastructure.

The self-service HTTP service is therefore deliberately kept behind nginx and binds only to localhost.

The HTTP service should not be exposed directly to the public network.

The production design used:

```text
Internet
   │
   ▼
Nginx / HTTPS
   │
   ▼
127.0.0.1:8088
   │
   ▼
leased.py
   │
   ▼
icctl
   │
   ▼
Docker
```

## Relationship to BPCTF2

This directory contains the challenge-specific infrastructure implementation used by BPCTF2.

It is intentionally separate from `lease-selfserve` so that the HTTP provisioning interface does not need to know how Iron Cold itself is implemented.

This separation also makes the self-service layer reusable for other challenge environments such as LightSpeed.

## Repository Role

```text
lease-for-ironcold/
├── README.md
├── docker-compose.yml
├── icctl
└── install.sh
```

The root repository documents the overall Lease Manager architecture.

This README documents only the Iron Cold infrastructure layer.
