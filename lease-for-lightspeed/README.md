# LightSpeed Lease Infrastructure

Infrastructure control layer for provisioning and managing LightSpeed challenge instances for BPCTF2.

This directory contains the LightSpeed-specific provisioning tooling used by the BPCTF2 Lease Manager.

## Purpose

The LightSpeed infrastructure is managed through the `lsctl` control utility.

The self-service HTTP backend does not directly manipulate the underlying containers. Instead, it invokes `lsctl`, which acts as the infrastructure control plane.

```text
Player
   │
   ▼
lease-selfserve / leased.py
   │
   │ lsctl show / lease
   ▼
lsctl
   │
   ├── Challenge images
   │
   └── Datastores
   │
   ▼
LightSpeed Instance
```

## Components

### `lsctl`

Command-line control utility for LightSpeed infrastructure.

The self-service service invokes the utility for lease operations, including:

```text
lsctl show "<team>"
lsctl lease "<team>"
```

The utility is also available to organizers for direct infrastructure management.

### `docker-compose.images.yml`

Compose configuration for the LightSpeed challenge images and related containerized services.

### `docker-compose.datastores.yml`

Compose configuration for the datastore layer required by LightSpeed.

The separation between image/service infrastructure and datastore infrastructure allows the deployment to manage these parts independently.

### `install.sh`

Installation/bootstrap script for preparing the LightSpeed environment on a VM.

## Lease Lifecycle

A self-service LightSpeed request follows this general flow:

```text
Team
 │
 ▼
Web Lease Page
 │
 ▼
leased.py
 │
 ▼
lsctl show TEAM
 │
 ├── Existing lease ───────► Return existing allocation
 │
 └── No existing lease
             │
             ▼
        lsctl lease TEAM
             │
             ▼
       Docker infrastructure
             │
             ▼
       LightSpeed instance
             │
             ▼
       Endpoint + token
```

## Existing Lease Handling

The lease backend checks for an existing allocation before attempting provisioning.

This is important because a player may submit the same team name multiple times.

An existing lease is returned with:

```json
{
  "address": "...",
  "token": "...",
  "status": "existing"
}
```

rather than provisioning another instance.

## LightSpeed Response Parsing

The self-service backend extracts LightSpeed connection information from the output of `lsctl`.

The expected output includes:

```text
gRPC: <address>
Token: <token>
```

The backend parses these values into the API response.

Example:

```json
{
  "address": "<gRPC endpoint>",
  "token": "<token>",
  "status": "created"
}
```

The token is specific to the LightSpeed provisioning workflow.

## Provisioning Time

Cold provisioning can take approximately 10–25 seconds because the underlying lease operation may need to start Docker Compose services.

The player-facing page therefore remains in a requesting state while provisioning is taking place.

A client-side timeout should not automatically be interpreted as provisioning failure.

If the provisioning operation completed after the client stopped waiting, another request using the same team name will resolve the existing lease.

## Infrastructure Separation

LightSpeed uses separate Compose definitions for different infrastructure concerns:

```text
docker-compose.images.yml
        │
        ├── Challenge images
        └── Application services

docker-compose.datastores.yml
        │
        ├── Datastores
        └── Supporting stateful services
```

`lsctl` provides the higher-level orchestration interface used by the lease service.

## Self-Service Configuration

`leased.py` selects the LightSpeed implementation through environment variables:

```text
LEASED_MODE=lightspeed
LEASED_CTL=<path-to-lsctl>
```

The backend then uses the LightSpeed-specific parser.

Conceptually:

```python
PARSERS = {
    "lightspeed": parse_lightspeed,
    "ironcold": parse_ironcold,
}
```

The LightSpeed parser extracts:

```text
gRPC
Token
```

from the control utility's output.

## Operational Commands

The primary infrastructure operations used by the self-service service are:

```bash
./lsctl show "<team>"
./lsctl lease "<team>"
```

Organizer-level operations are also performed through `lsctl`.

If an allocated instance needs to be quarantined during an event, the control utility provides the corresponding operational command.

## Security Considerations

The LightSpeed control utility operates against the underlying container infrastructure and therefore requires the privileges necessary to manage Docker.

The HTTP backend is not intended to be exposed directly.

The production request path is:

```text
Public Internet
      │
      ▼
    Nginx
      │
      ▼
127.0.0.1:8088
      │
      ▼
  leased.py
      │
      ▼
    lsctl
      │
      ▼
Docker / LightSpeed
```

## Relationship to `lease-selfserve`

This directory owns the LightSpeed-specific infrastructure.

`lease-selfserve` owns the generic HTTP-facing lease workflow.

That separation means the self-service service can operate in either:

```text
LEASED_MODE=ironcold
```

or:

```text
LEASED_MODE=lightspeed
```

while the infrastructure-specific implementation remains in its respective directory.

## Repository Role

```text
lease-for-lightspeed/
├── README.md
├── docker-compose.datastores.yml
├── docker-compose.images.yml
├── install.sh
└── lsctl
```

The root README describes the entire Lease Manager.

This README documents only the LightSpeed infrastructure layer.
