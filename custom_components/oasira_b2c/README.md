# Oasira Home Integration (`oasira_b2c`)

This directory contains the **Oasira B2C** Home Assistant custom integration. It connects a local Home Assistant instance to the **Oasira Home service** ([https://home.oasira.ai](https://home.oasira.ai)) so Home Assistant devices, state, and automations can power the Oasira Home experience across web, mobile, and TV apps.

## Overview and Purpose

The Oasira B2C integration is the Home Assistant-side component responsible for integrating with Oasira Home and enabling a connected, user-friendly smart home experience beyond Home Assistant’s native UI.

**Main functions**

- **Device and platform integration bridge:** Makes Home Assistant devices/entities available to Oasira Home workflows by streaming supported state and safety-related events to the Oasira Home ingest APIs.
- **Oasira Home service connectivity:** Maintains a hub-paired, authenticated connection to Oasira Home for cloud-backed experiences used by the Oasira Home website, mobile apps, and TV apps.
- **Notification and event delivery:** Provides `notify.oasira` and alert/state ingestion so Home Assistant automations and device activity can surface in Oasira Home experiences.
- **Local HA capabilities:** Exposes Home Assistant platforms and domain services that support pairing, entity organization, and local automation use cases.

**How it fits into the ecosystem**

- Home Assistant remains the local automation and device integration engine.
- Oasira B2C securely links that local system to Oasira Home using an enrollment-code-based hub pairing and revocable API key.
- Oasira Home services/apps consume synchronized data and provide an easier, more powerful, and more consistent smart home experience across supported clients (web, mobile, TV).

This integration pairs using a one-time enrollment code, verifies the hub key, streams tracked device telemetry and supported safety alerts to Oasira Home ingest endpoints, and registers `notify.oasira` for Home Assistant automations.

## Functional Scope

### Hub Pairing and Cloud Connectivity

- Config flow enrollment via `POST {api_url}/api/ingest/enroll` (one-time code → hub API key)
- Hub key verification on startup (`GET {api_url}/api/ingest/me`) with reauth when revoked/unauthorized
- Hub-paired operation (no Firebase/email-password login flow in this integration)

### Device and Alert Streaming (Oasira Home ingest)

- Streams eligible `binary_sensor` state to `POST {api_url}/api/ingest/telemetry` (batched, periodic flush, initial snapshot on start); registered entities must be enabled, visible, and uncategorized, and other domains are not streamed
- Converts configured safety/security binary sensor class activity to idempotent alerts via `POST {api_url}/api/ingest/alerts` and resolves matching alerts when cleared
- Uses `Authorization: Bearer <hub api_key>`; `401/403` triggers reauth
- Excludes entities owned by this integration from the outbound stream

### Notifications

- Registers `notify.oasira` (notify domain service) backed by the Oasira Home API
- Sends notifications to `POST {api_url}/api/ingest/notifications` with hub auth
- Accepts `message` (required), optional `title`, and optional `data` (including `data.kind`: `security|energy|automation|device`)

### Local Services and Automation Helpers

- Entity/organization services: `update_entity`, `add_label_to_entity`
- Virtual power profile services: `add_virtual_power_device`, `remove_virtual_power_device` (persisted in HA storage and reloaded into integration state)
- Deployment service: `deploy_latest_config` (copies packaged blueprints into the Home Assistant blueprint path, preserving existing user-edited files)
- Startup/bootstrap behavior includes label creation (`Favorite`, `NotForSecurityMonitoring`) and motion/security grouping setup

## Home Assistant Platforms

The integration forwards config entry setup to:

- `switch`
- `binary_sensor`
- `sensor`
- `alarm_control_panel`
- `button`

## Services

### `notify.oasira` (notify domain)

Sends a notification through the Oasira Home API.

- **Required:** `message`
- **Optional:** `title`, `data`
- **Example:**

```yaml
service: notify.oasira
data:
  title: "Front door"
  message: "Motion detected"
  data:
    kind: security
```

### `oasira_b2c.*` domain services

Defined in `services.yaml` in this folder:

- `update_entity`
- `add_label_to_entity`
- `add_virtual_power_device`
- `remove_virtual_power_device`
- `deploy_latest_config`

## Packaged Assets

- `blueprints/` automation and script blueprints
- `deploy_latest_config` copies missing blueprints into the Home Assistant blueprint directory without overwriting existing files

The package does not include frontend panels or static `www` assets, and the integration does not register a static frontend path.

## Requirements

- Home Assistant with config flow support
- Network access to the configured Oasira Home API endpoint
- Recorder enabled if required by dependent HA components/features in your installation

Dependencies are declared in `manifest.json` and installed by Home Assistant.

## Support

- Issues: https://github.com/EffortlessHome/Oasira_B2C/issues
- Website: https://www.oasira.ai/