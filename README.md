# Oasira Home

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Version](https://img.shields.io/badge/version-1.0.1-blue.svg)](https://github.com/Oasira/Oasira)

Oasira Home is a Home Assistant integration focused on security orchestration, area-aware automation, timeline events, and built-in AI workflows.

## Core Capabilities

- Security and alarm coordination with services for pending confirmation, cancel, and status checks
- Area-based automation features including presence workflows, sleep mode support, and entity area updates
- Motion and monitoring automation support with entity grouping and routine orchestration
- Alert and event logging services for operational visibility
- Timeline event workflows for camera snapshots, video clips, person events, review/favorite updates, and cleanup
- AI features for Home Assistant conversation and AI task execution with configurable Ollama endpoint and model
- AI utility services including image analysis and automation pattern scanning
- Firebase configuration and push notification integration support
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

## Configuration Overview

Initial setup uses your Oasira account and system information, then provisions integration data and services.

AI configuration is managed through the integration config flow and supports:

- Ollama base URL
- Default model selection
- Conversation and AI task subentry configuration

## Service Coverage

The integration exposes service groups for:

- Alarm and operations: clean_motion_files, create_event, cancel_alarm, get_alarm_status, confirm_pending_alarm, create_alert, update_entity
- Labels and deployment: add_label_to_entity, deploy_latest_config, get_firebase_config
- Timeline: record_video_clip, create_timeline_event, summarize_timeline_period
- AI: change_config, analyze_image, scan_home_automation_patterns

For full service schemas and fields, see custom_components/oasira_b2c/services.yaml.

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
2. In Home Assistant, go to Settings > Devices & services > Add integration and search for "Oasira Home".
3. Enter the Oasira Home server URL (default: `https://oasira-api.jermie.workers.dev`), the one-time enrollment code, and a name for this hub.
4. The code is exchanged for a revocable hub key; there is no email/password login. If the key is later revoked, Home Assistant will ask you for a fresh code.

### Device stream

Once paired, the integration streams Home Assistant state to the Oasira Home backend using the hub key (no Firebase login):

- An initial snapshot of all tracked entities is sent on startup.
- State changes for `sensor`, `binary_sensor`, `switch`, `light`, `cover`, `climate`, `media_player`, `camera`, `lock`, `fan`, `humidifier` and `water_heater` entities are batched and posted to `POST /api/ingest/telemetry` every 30 seconds. The backend creates/updates devices by HA `entity_id` and records `hub_telemetry`.
- Safety/security binary sensors (smoke, gas, heat, water, motion, door/window, vibration, tamper) also produce idempotent alerts at `POST /api/ingest/alerts`; clearing a sensor resolves the same alert by its `external_id`.
- The hub key is sent as `Authorization: Bearer <key>`. If the backend rejects it with 401/403, streaming pauses and Home Assistant starts the re-pair flow for a fresh enrollment code.

Entities owned by this integration are excluded, so the stream never feeds its own output back to the server.

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

- **Oasira Home URL**: The Cloudflare Worker URL (without `/v1` or `/api` suffixes)
- **Enrollment code**: The one-time code created in the Oasira Home app
- **Hub name**: A friendly name for this Home Assistant instance

### Requirements

- Home Assistant **2024.1+**
- `recorder` component enabled
- Internet connection for cloud features

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


