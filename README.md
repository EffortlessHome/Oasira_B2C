# Oasira Home (B2C)

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-1.0.1-blue.svg)](https://github.com/Oasira/Oasira)

The **Oasira B2C integration** is the Home Assistant custom integration that connects your local Home Assistant instance to the **Oasira Home service** ([https://home.oasira.ai](https://home.oasira.ai)).

## Overview

Oasira B2C acts as the bridge between your Home Assistant–managed devices and automations and the broader Oasira Home experience. Its primary role is to:

- **Expose and integrate Home Assistant devices** so they can participate in the Oasira Home ecosystem.
- **Communicate with Oasira Home services** (including the Oasira Home website, mobile apps, and TV apps) to enable a more unified, accessible, and powerful smart home experience across interfaces and locations.
- **Synchronize relevant home data and events** with Oasira Home (such as device state/telemetry, safety alerts, and notifications) using a secure, hub-paired connection.
- **Provide local Home Assistant entities and services** that support Oasira-driven workflows while keeping core automations and device control within Home Assistant.

In practice, this integration pairs your Home Assistant hub to Oasira Home with a revocable API key, streams supported device and alert data to Oasira Home’s ingest APIs, and enables features such as `notify.oasira` so automations running in Home Assistant can deliver notifications through the Oasira Home platform. This allows users to manage and interact with their smart home through Oasira’s apps and web experiences while leveraging Home Assistant’s local integrations and automation engine.

## Core Capabilities

- Hub-paired connection to Oasira Home (one-time enrollment code exchange for a revocable hub API key)
- Device state and safety/security alert streaming to the Oasira Home ingest API
- `notify.oasira` service for sending notifications through the Oasira Home API
- Entity, label, virtual power, and deployment services for local Home Assistant workflows
- Area/presence, sleep mode, motion grouping, and other automation-oriented entities and helpers
- Packaged automation and script blueprints

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Add this repository as a custom repository
3. Search for Oasira Home and install
4. Restart Home Assistant
5. Go to Settings > Devices and Services and add Oasira Home

### Manual Installation

1. Copy custom_components/oasira_b2c into your Home Assistant custom_components directory
2. Restart Home Assistant
3. Add Oasira Home from Settings > Devices and Services

## Service Coverage

Domain services (`oasira_b2c.*`) defined in this integration include:

- Entity and organization: `update_entity`, `add_label_to_entity`
- Virtual power estimation: `add_virtual_power_device`, `remove_virtual_power_device`
- Deployment: `deploy_latest_config`

Notification service:

- `notify.oasira` (notify domain): sends notifications to Oasira Home via `POST {api_url}/api/ingest/notifications` using the hub API key.
  - Required: `message`
  - Optional: `title`, `data` (for example `data.kind`: `security|energy|automation|device`)

For `oasira_b2c` service schemas and fields, see `custom_components/oasira_b2c/services.yaml`.

## Requirements

- Home Assistant with config flow support
- Recorder integration enabled
- Network access to the configured Oasira Home API endpoint

Python dependencies are declared in custom_components/oasira_b2c/manifest.json and installed by Home Assistant.

## Support

- Documentation: https://www.oasira.ai/
- Issues: https://github.com/EffortlessHome/Oasira_B2C/issues

## Configuration

### Initial Setup

1. In the Oasira Home app, open **Settings → Home Assistant hubs** and create an enrollment code.
2. In Home Assistant, go to **Settings > Devices & services > Add integration** and search for **Oasira Home**.
3. Enter the Oasira Home server URL (default: `https://homeapi.oasira.ai`), the one-time enrollment code, and a name for this hub.
4. The code is exchanged for a revocable hub API key (no email/password login). If the key is later revoked, Home Assistant starts reauth and asks for a fresh enrollment code.

### Device stream

Once paired, the integration streams Home Assistant state to the Oasira Home backend using the hub key:

- An initial snapshot of tracked entities is sent on startup.
- State changes for `binary_sensor` entities are batched and posted to `POST /api/ingest/telemetry` every 30 seconds. Other entity domains are not streamed.
- Configured safety/security binary sensor classes (such as smoke, gas, heat, water, doors/windows, vibration, and tamper) produce idempotent alerts at `POST /api/ingest/alerts`; clearing a sensor resolves the matching alert.
- The hub key is sent as `Authorization: Bearer <key>`. If the backend rejects it with 401/403, streaming pauses and Home Assistant starts re-pair (reauth).

Only registered entities that are enabled, visible, and not categorized are included; entities owned by this integration are excluded. Unregistered binary sensor states are also eligible.

### Notifications

The integration registers `notify.oasira`, which posts notifications to Oasira Home:

- Endpoint: `POST {api_url}/api/ingest/notifications`
- Auth: `Authorization: Bearer <hub api_key>`
- Payload: includes `title`, `message`/`body`, `kind`, `data`, and hub/home identifiers when available

Example:

```yaml
service: notify.oasira
data:
  title: "Laundry"
  message: "Washer cycle finished"
  data:
    kind: automation
```

No `configuration.yaml`, custom panel, or external-directory configuration is required.

The integration does not currently ship frontend assets. The `deploy_latest_config` service copies packaged automation and script blueprints into the Home Assistant blueprint directory without overwriting existing files.

### Required Information

- **Oasira Home URL**: API base URL (default: `https://homeapi.oasira.ai`; the integration normalizes `/v1`, `/api`, `/api/ingest`, or `/api/public` suffixes)
- **Enrollment code**: One-time code created in the Oasira Home app
- **Hub name**: Friendly name for this Home Assistant instance

### Requirements

- Home Assistant **2024.1+**
- `recorder` integration enabled (for integration dependencies/features that rely on it)
- Network access to the configured Oasira Home API endpoint

---

## 🏗️ Architecture: Local-First by Design

```
┌─────────────────────────────────────┐
│           Your Home Network         │
│                                     │
│  ┌──────────────────────────────┐   │
│  │       Home Assistant         │   │
│  │  local automations and state │   │
│  └──────────────┬───────────────┘   │
└─────────────────┼───────────────────┘
                  │ binary sensor telemetry
                  │ and safety alerts
                  ▼
        ┌────────────────────┐
        │  Oasira Home Cloud │
        └────────────────────┘
```

✅ Home Assistant automations run **locally**
⚠️ When paired, the integration sends state and metadata for enabled, non-hidden `binary_sensor` entities to the Oasira Home cloud. Relevant safety binary sensors also generate cloud alerts.
