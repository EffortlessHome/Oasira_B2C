# Oasira Home (B2C)

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-1.0.1-blue.svg)](https://github.com/Oasira/Oasira)

The **Oasira B2C integration** is the Home Assistant custom integration that connects your local Home Assistant instance to the **Oasira Home service** ([https://home.oasira.ai](https://home.oasira.ai)).

## Overview

Oasira B2C acts as the bridge between your Home Assistant–managed devices and automations and the broader Oasira Home experience. Its primary role is to:

- **Expose and integrate Home Assistant devices** so they can participate in the Oasira Home ecosystem.
- **Communicate with Oasira Home services** (including the Oasira Home website, mobile apps, and TV apps) to enable a more unified, accessible, and powerful smart home experience across interfaces and locations.
- **Synchronize relevant home data and events** with Oasira Home (such as device state/telemetry, safety alerts, and notifications) using a secure, hub-paired connection.
- **Provide local Home Assistant entities, services, and webhooks** that support Oasira-driven workflows while keeping core automations and device control within Home Assistant.

In practice, this integration pairs your Home Assistant hub to Oasira Home with a revocable API key, streams supported device and alert data to Oasira Home’s ingest APIs, and enables features such as `notify.oasira` so automations running in Home Assistant can deliver notifications through the Oasira Home platform. This allows users to manage and interact with their smart home through Oasira’s apps and web experiences while leveraging Home Assistant’s local integrations and automation engine.

## Core Capabilities

- Hub-paired connection to Oasira Home (one-time enrollment code exchange for a revocable hub API key)
- Device state and safety/security alert streaming to the Oasira Home ingest API
- `notify.oasira` service for sending notifications through the Oasira Home API
- Alarm, entity, label, virtual power, and deployment services for local Home Assistant workflows
- Area/presence, sleep mode, motion grouping, and other automation-oriented entities and helpers
- Location update and security alarm webhook handling
- Deployable frontend assets and blueprints included in the integration package

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
- Network access to Oasira and any configured Ollama endpoint

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
- State changes for `sensor`, `binary_sensor`, `switch`, `light`, `cover`, `climate`, `media_player`, `camera`, `lock`, `fan`, `humidifier` and `water_heater` entities are batched and posted to `POST /api/ingest/telemetry` every 30 seconds.
- Safety/security binary sensors (smoke, gas, heat, water, motion, door/window, vibration, tamper, etc.) produce idempotent alerts at `POST /api/ingest/alerts`; clearing a sensor resolves the matching alert.
- The hub key is sent as `Authorization: Bearer <key>`. If the backend rejects it with 401/403, streaming pauses and Home Assistant starts re-pair (reauth).

Entities owned by this integration are excluded from the stream.

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

### Configuration.yaml changes

Add the following configuration to your Home Assistant configuration.yaml file:

```yaml
homeassistant:
  allowlist_external_dirs:
    - /media
    - /config/www/oasira_b2c

panel_custom:
  - name: Oasira-config-panel
    sidebar_title: Oasira Config
    sidebar_icon: mdi:alpha-e-box-outline
    url_path: oasira-config-panel
    module_url: /local/oasira_b2c/config-panel.js

  - name: Oasira-area-panel
    url_path: oasira-area-panel
    module_url: /local/oasira_b2c/area-panel.js

  - name: Oasira-label-panel
    url_path: oasira-label-panel
    module_url: /local/oasira_b2c/label-panel.js
```

After adding the configuration, restart Home Assistant for changes to take effect.

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
│  ┌──────────────┐  ┌────────────┐  │
│  │Home Assistant │──│Oasira│ │
│  │   (local)     │  │ (local)    │  │
│  └──────┬───────┘  └─────┬──────┘  │
│         │                │         │
│    Local Automations    Local UI    │
│    Local Security       Local Data  │
└─────────┬────────────────┬─────────┘
          │   Encrypted    │
          └───── Cloud ────┘
        (remote access only)
```

✅ All automations run **locally** — no cloud dependency
✅ Your data **stays on your hardware**
✅ Cloud used **only** for secure remote access & optional monitoring


