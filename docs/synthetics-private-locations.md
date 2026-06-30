# Synthetics Private Locations with datadog-sync-cli

This guide describes how to replicate Synthetics Private Locations (PLs) from one Datadog
organization to another using `datadog-sync-cli`, and how to configure your PL workers so
that test traffic continues flowing when the destination region takes over (failover).

## Table of contents

- [Overview](#overview)
- [Replicating PLs with `datadog-sync-cli`](#replicating-pls-with-datadog-sync-cli)
  - [What gets synced](#what-gets-synced)
- [How replicated PLs work after sync](#how-replicated-pls-work-after-sync)
- [Configuring `datadogHostOverride` on your PL workers](#configuring-datadoghostoverride-on-your-pl-workers)
  - [Option A: env var (Kubernetes / Helm chart)](#option-a-env-var-kubernetes--helm-chart)
  - [Option B: command-line argument (Docker)](#option-b-command-line-argument-docker)
  - [Option C: JSON config file](#option-c-json-config-file)
- [Verifying the setup](#verifying-the-setup)

## Overview

When you onboard Synthetics Private Locations to a disaster-recovery setup, the goal is to
have the *same* PL identity reachable from two regions:

- **Primary region** (your everyday org) where tests normally run.
- **Secondary region** (your standby org) that takes over during a failover event.

`datadog-sync-cli` replicates the PL definition (name, description, tags, encryption keys,
DDR metadata) into the secondary org, so the secondary region recognizes the same PL when
traffic gets switched over.

The **PL worker itself** runs in your infrastructure and is the same binary regardless of
which region is active. The only thing that changes between regions is the **intake
hostname** the worker reports to. That hostname is set through the `datadogHostOverride`
config option, which the customer points at a DNS CNAME that resolves to whichever region
is currently serving traffic. The CNAME flip is what triggers the failover.

## Replicating PLs with `datadog-sync-cli`

### What gets synced

When you run `import` then `sync` against your two orgs with `synthetics_private_locations`
in scope, the tool:

1. Reads each private location from the source org (`GET /api/v1/synthetics/private-locations/<id>?include_pl_info=true`).
2. Creates a matching private location in the destination org via the DDR-aware
   create endpoint. The destination PL is created with:
   - The same display name, description, tags, and restricted-role metadata.
   - A reference back to the source PL (stored as `ddr_metadata.disaster_recovery`).
   - The source PL's test-encryption public key (so the secondary region can decrypt
     tests that were originally encrypted by the primary region).
3. Persists the destination PL in your local state file so subsequent syncs are idempotent.

You do **not** need to manage encryption keys, auth tokens, or worker configuration by
hand. Those are handled by the tool and by Datadog out-of-band.

Example commands (running in your environment with the two orgs' credentials configured):

```bash
# Pull source PLs into local state
datadog-sync-cli import \
  --resources synthetics_private_locations

# Replicate them into the destination org
datadog-sync-cli sync \
  --resources synthetics_private_locations
```

## How replicated PLs work after sync

After replication, both orgs have a PL with the same internal identifier (the same
`pl:<slug>` name) and shared encryption keys. However, the PL worker can only report to
**one datacenter at a time** — whichever datacenter the `datadogHostOverride` CNAME
currently resolves to. Only that datacenter's org will show the PL as healthy and receive
test results.

The customer-side work to enable this is:

1. **Run `datadog-sync-cli` to replicate the PL.** (Covered above.)
2. **Set `datadogHostOverride` on your PL worker(s)** to a CNAME whose DNS target you
   control. The next section explains the three ways to do this.
3. **Control the CNAME's DNS target** so it points to your primary region in steady state
   and gets flipped to the secondary region during failover. The flip is transparent to
   the worker — the next polling cycle picks up the new target automatically, and from
   that point on tests are pulled from and results pushed to the secondary region only.

## Configuring `datadogHostOverride` on your PL workers

`datadogHostOverride` tells the PL worker which intake hostname to send results and poll
for tests.

There are three equivalent ways to set it. Pick whichever fits your deployment.

### Option A: env var (Kubernetes / Helm chart)

Recommended when you run PL workers via the official [synthetics-private-location Helm
chart](https://github.com/DataDog/helm-charts/tree/main/charts/synthetics-private-location).

Set the `DATADOG_HOST_OVERRIDE` environment variable through the chart's
`environmentVariableOverride` block (or your standard env var injection mechanism):

```yaml
# values.yaml
datadog:
  apiKey: <your api key>
  appKey: <your app key>

environmentVariableOverride:
  - name: DATADOG_HOST_OVERRIDE
    value: <your-failover-cname>.synthetics.datadoghq.com
```

This sets the value once at the deployment level, and every PL container that the chart
spawns inherits it.

> Requires synthetics-worker version that supports the `DATADOG_HOST_OVERRIDE` env var
> (see [synthetics-worker#10100](https://github.com/DataDog/synthetics-worker/pull/10100)).
> Use that worker version or newer.

### Option B: command-line argument (Docker)

If you run the PL worker directly with `docker run`, pass `--datadogHostOverride` as a
launch argument:

```bash
docker run --rm \
  -v $(pwd)/worker-config.json:/etc/datadog/synthetics-check-runner.json \
  datadog/synthetics-private-location-worker:latest \
  --datadogHostOverride=<your-failover-cname>.synthetics.datadoghq.com
```

### Option C: JSON config file

Add `datadogHostOverride` to your PL worker's JSON config file (the same file the worker
already reads for API/app keys and other settings):

```json
{
  "datadogHostOverride": "<your-failover-cname>.synthetics.datadoghq.com",
  "datadogApiKey": "...",
  "datadogAppKey": "..."
}
```

Then point the worker at the file:

```bash
docker run --rm \
  -v $(pwd)/worker-config.json:/etc/datadog/synthetics-check-runner.json \
  datadog/synthetics-private-location-worker:latest
```

### Precedence

If you set the value through more than one mechanism, the worker resolves in this order
(highest precedence first):

1. CLI argument (`--datadogHostOverride=...`)
2. Environment variable (`DATADOG_HOST_OVERRIDE`)
3. JSON config file (`datadogHostOverride` field)

In practice, pick one mechanism and stick with it for clarity.

## Verifying the setup

After running the sync and starting your worker with `datadogHostOverride` set:

1. **Confirm the PL appears in both orgs.** Check the Synthetics → Settings → Private
   Locations page in each org. The PL should be listed with the same name in both.
2. **Confirm the worker reports to the active region.** Only the org whose datacenter the
   `datadogHostOverride` CNAME currently resolves to will show the PL as healthy. The
   other org's PL will appear offline until you flip the CNAME to point to it.
3. **Run a test.** Create a test in the active org targeting the replicated PL, unpause
   it, and confirm results appear. Results are pulled from and pushed to only the
   datacenter the CNAME resolves to — flipping the CNAME switches where tests run and
   results land, without any test-config changes.
