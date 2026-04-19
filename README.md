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
- Timeline: capture_snapshot, record_video_clip, create_timeline_event, update_timeline_event
- AI: change_config, analyze_image, scan_home_automation_patterns

For full service schemas and fields, see custom_components/oasira_b2c/services.yaml.

## Requirements

- Home Assistant with config flow support
- Recorder integration enabled
- Network access to Oasira and any configured Ollama endpoint

Python dependencies are declared in custom_components/oasira_b2c/manifest.json and installed by Home Assistant.

## Support

- Documentation: https://www.oasira.ai/
- Issues: https://github.com/Oasira/Oasira/issues

