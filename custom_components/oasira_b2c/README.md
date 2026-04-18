# Oasira Home Integration

This directory contains the Home Assistant custom integration for Oasira Home.

The integration combines cloud-backed Oasira system data with local Home Assistant automation services, conversation features, AI task features, timeline event workflows, and deployable UI resources.

## Functional Scope

### Security and Alarm Operations

- Multi-mode alarm workflows and alert management
- Pending alarm confirmation and alarm cancellation services
- Alarm status query services
- Event creation services tied to active alarms

### Area and Entity Management

- Entity-to-area update service
- Label assignment service for entity organization
- Integration startup label bootstrap for Favorite and NotForSecurityMonitoring

### Notifications and Mobile Support

- Firebase configuration retrieval service for mobile app integration
- Push token webhook flows and notification fanout support

### Timeline and Camera Event Services

- Capture camera snapshots and optionally persist timeline events
- Record short camera clips and attach timeline metadata
- Create person detection timeline events from supplied media
- Query timeline events by camera, area, date range, and type
- Update and delete timeline events

### AI Capabilities

- Conversation platform integration
- AI task platform integration
- Runtime connection to Ollama using configurable base URL and model
- AI services:
	- change_config
	- analyze_image
	- scan_home_automation_patterns
	- reload_skills
	- download_skill

### Automation Assets and UX

- Blueprint package under blueprints/automation with Oasira scenarios
- Theme package under themes/
- Frontend resources under www/oasira_b2c/
- Deployment service to copy packaged assets into Home Assistant config paths

## Home Assistant Platforms

The integration forwards setup to these platforms:

- switch
- binary_sensor
- sensor
- cover
- light
- alarm_control_panel
- button
- conversation
- ai_task

## Service Reference

Primary service definitions are documented in services.yaml in this folder.

Operational services include:

- clean_motion_files
- create_event
- cancel_alarm
- get_alarm_status
- confirm_pending_alarm
- create_alert
- update_entity
- deploy_latest_config
- get_firebase_config
- add_label_to_entity

Timeline services include:

- capture_snapshot
- record_video_clip
- create_person_event
- update_timeline_event
- create_timeline_event


AI services include:

- change_config
- analyze_image
- scan_home_automation_patterns
- reload_skills
- download_skill

## Installation

### HACS

1. Add this repository as a custom repository in HACS
2. Install Oasira Home
3. Restart Home Assistant
4. Add the integration from Settings > Devices and Services

### Manual

1. Copy this folder to custom_components/oasira_b2c
2. Restart Home Assistant
3. Add Oasira Home from Settings > Devices and Services

## Requirements

- Home Assistant with config flow support
- Recorder integration enabled
- Network access to Oasira cloud endpoints
- Network access to configured Ollama endpoint for AI features

Python dependencies are declared in manifest.json and installed automatically by Home Assistant.

## Support

- Issues: https://github.com/Oasira/Oasira/issues
- Website: https://www.oasira.ai/