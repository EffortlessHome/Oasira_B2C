"""Constants for the Oasira AI Conversation integration."""

import re

DOMAIN = "oasira_b2c"
DEFAULT_NAME = "Oasira AI Conversation"
DEFAULT_CONVERSATION_NAME = "Oasira AI Conversation"
DEFAULT_AI_TASK_NAME = "Oasira AI Task"

CONF_BASE_URL = "base_url"
DEFAULT_CONF_BASE_URL = "http://localhost:11434"
CONF_MODEL = "model"
CONF_CHAT_MODEL = "chat_model"
DEFAULT_MODEL = "llama3.2"
DEFAULT_CHAT_MODEL = "llama3.2"

EVENT_AUTOMATION_REGISTERED = "automation_registered_via_oasira_b2c"
EVENT_CONVERSATION_FINISHED = "oasira_b2c.conversation.finished"

CONF_PROMPT = "prompt"
DEFAULT_PROMPT = """You are a helpful AI voice assistant of Home Assistant that controls a real home.
Your goal is to proactively improve the user's comfort.

## Environment State
- Current Time: {{now()}}
- Current Area: {{area_id(current_device_id)}}

## Workspace
Your workspace is at: {{oasira_ai.working_directory()}}

## Guidelines
- Answer in plain text only.
- No symbols or parentheses
- Ask for clarification when the request is ambiguous
- Use tools to help accomplish tasks
- Prefer one sentence

## Personality
- Helpful and friendly
- Concise and to the point
- Curious and eager to learn

## Behavior Policy
- If the user explicitly names a device and action, execute it directly.
- Otherwise, infer the user's goal and select the most likely target entity, preferring primary environmental controls. Use get_attributes to check adjustable state values alone is not sufficient.
- If the selected entity is already at its limit, evaluate the next most likely entity. Repeat until a viable adjustment is found or all candidates are exhausted.
- Ask user a minimum adjustment proposal about selected entity. If no entity can further improve the situation, inform the user that conditions are already optimal.

## Devices
Available Devices:
```csv
entity_id,name,state,area_id,aliases
{% for entity in oasira_ai.exposed_entities() -%}
{{ entity.entity_id }},{{ entity.name }},{{ entity.state }},{{area_id(entity.entity_id)}},{{entity.aliases | join('/')}}
{% endfor -%}
```

{%- if skills %}
## Skills
The following skills extend your capabilities. To use a skill, call load_skill with the skill name to read its instructions.
When a skill file references a relative path, resolve it against the skill's location directory (e.g., skill at `/a/b/SKILL.md` references `scripts/run.py` → use `/a/b/scripts/run.py`) and always use the resulting absolute path in bash commands, as relative paths will fail.

<available_skills>
{%- for skill in skills %}
  <skill>
    <name>{{ skill.name }}</name>
    <description>{{ skill.description }}</description>
    <location>{{skill.path}}</location>
  </skill>
 {%- endfor %}
</available_skills>
{% endif %}

{{user_input.extra_system_prompt | default('', true)}}
"""
CONF_MAX_TOKENS = "max_tokens"
DEFAULT_MAX_TOKENS = 2048
CONF_TOP_P = "top_p"
DEFAULT_TOP_P = 0.9
CONF_TEMPERATURE = "temperature"
DEFAULT_TEMPERATURE = 0.7
CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION = "max_function_calls_per_conversation"
DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION = 10
CONF_SHORTEN_TOOL_CALL_ID = "shorten_tool_call_id"
DEFAULT_SHORTEN_TOOL_CALL_ID = False
CONF_FUNCTION_TOOLS = "functions"
DEFAULT_CONF_FUNCTION_TOOLS = [
    {
        "spec": {
            "name": "execute_services",
            "description": "Execute service in Home Assistant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delay": {
                        "type": "object",
                        "description": "Time to wait before execution",
                        "properties": {
                            "hours": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "minutes": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "seconds": {
                                "type": "integer",
                                "minimum": 0,
                            },
                        },
                    },
                    "list": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "domain": {
                                    "type": "string",
                                    "description": "The domain of the service.",
                                },
                                "service": {
                                    "type": "string",
                                    "description": "The service to be called",
                                },
                                "service_data": {
                                    "type": "object",
                                    "description": "The service data object to indicate what to control.",
                                    "properties": {
                                        "entity_id": {
                                            "type": "array",
                                            "items": {
                                                "type": "string",
                                                "description": "The entity_id retrieved from available devices. It must start with domain, followed by dot character.",
                                            },
                                        },
                                        "area_id": {
                                            "type": "array",
                                            "items": {
                                                "type": "string",
                                                "description": "The id retrieved from areas. You can specify only area_id without entity_id to act on all entities in that area",
                                            },
                                        },
                                    },
                                },
                            },
                            "required": ["domain", "service", "service_data"],
                        },
                    },
                },
            },
        },
        "function": {"type": "native", "name": "execute_service"},
    },
    {
        "spec": {
            "name": "analyze_home_automation_patterns",
            "description": "Analyze home usage patterns and recommend automations based on entity state history, movement of residents, lighting, and other sensor data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_range_days": {
                        "type": "integer",
                        "description": "Number of days to analyze (1-90)",
                        "minimum": 1,
                        "maximum": 90,
                        "default": 7,
                    },
                    "entity_types": {
                        "type": "array",
                        "description": "Types of entities to analyze (e.g., light, switch, binary_sensor, sensor, person, device_tracker)",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "include_entities": {
                        "type": "array",
                        "description": "Specific entity IDs to include in analysis",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "exclude_entities": {
                        "type": "array",
                        "description": "Specific entity IDs to exclude from analysis",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "pattern_types": {
                        "type": "array",
                        "description": "Types of patterns to detect (e.g., light_schedule, motion_light, presence_automation, sensor_trigger)",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "min_confidence": {
                        "type": "number",
                        "description": "Minimum confidence threshold for pattern detection (0.0-1.0)",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "default": 0.7,
                    },
                    "time_window_minutes": {
                        "type": "integer",
                        "description": "Time window in minutes for grouping similar events",
                        "minimum": 5,
                        "maximum": 1440,
                        "default": 30,
                    },
                },
            },
        },
        "function": {"type": "automation_analysis"},
    },
    {
        "spec": {
            "name": "get_attributes",
            "description": "Get attributes of entity or multiple entities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "array",
                        "description": "entity_id of entity or multiple entities",
                        "items": {"type": "string"},
                    }
                },
                "required": ["entity_id"],
            },
        },
        "function": {
            "type": "template",
            "value_template": "```csv\nentity,attributes\n{%for entity in entity_id%}\n{{entity}},{{states[entity].attributes}}\n{%endfor%}\n```",
        },
    },
    {
        "spec": {
            "name": "load_skill",
            "description": "Load a file from a skill's directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name",
                    },
                    "file": {
                        "type": "string",
                        "description": "Relative file path within the skill directory",
                    },
                },
                "required": ["name", "file"],
            },
        },
        "function": {
            "type": "read_file",
            "path": "{{oasira_ai.skill_dir(name)}}/{{file}}",
        },
    },
    {
        "spec": {
            "name": "bash",
            "description": "Execute a bash command in workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Bash command to execute",
                    },
                },
                "required": ["command"],
            },
        },
        "function": {"type": "bash", "command": "{{command}}"},
    },
    {
        "spec": {
            "name": "analyze_image",
            "description": "Analyze an image and provide detailed description or answer questions about it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_url": {
                        "type": "string",
                        "description": "URL of the image to analyze",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Optional prompt to guide the analysis",
                        "default": "Please describe this image in detail.",
                    },
                    "model": {
                        "type": "string",
                        "description": "The vision model to use for analysis",
                        "default": "llava",
                    },
                },
                "required": ["image_url"],
            },
        },
        "function": {"type": "image_analysis"},
    },
]
CONF_CONTEXT_THRESHOLD = "context_threshold"
DEFAULT_CONTEXT_THRESHOLD = 40000
CONTEXT_TRUNCATE_STRATEGIES = [{"key": "clear", "label": "Clear All Messages"}]
CONF_CONTEXT_TRUNCATE_STRATEGY = "context_truncate_strategy"
DEFAULT_CONTEXT_TRUNCATE_STRATEGY = CONTEXT_TRUNCATE_STRATEGIES[0]["key"]

# Advanced Options
CONF_ADVANCED_OPTIONS = "advanced_options"
DEFAULT_ADVANCED_OPTIONS = False

# Ollama-specific settings
CONF_NUM_CTX = "num_ctx"
DEFAULT_NUM_CTX = 4096
CONF_NUM_KEEP = "num_keep"
DEFAULT_NUM_KEEP = 0
CONF_SEED = "seed"
DEFAULT_SEED = 0
CONF_NUM_GPU = "num_gpu"
DEFAULT_NUM_GPU = 0
CONF_MAIN_GPU = "main_gpu"
DEFAULT_MAIN_GPU = 0
CONF_NUM_THREAD = "num_thread"
DEFAULT_NUM_THREAD = 0
CONF_NUM_BATCH = "num_batch"
DEFAULT_NUM_BATCH = 512
CONF_REPEAT_LAST_N = "repeat_last_n"
DEFAULT_REPEAT_LAST_N = 64
CONF_REPEAT_PENALTY = "repeat_penalty"
DEFAULT_REPEAT_PENALTY = 1.1
CONF_TFS_Z = "tfs_z"
DEFAULT_TFS_Z = 1.0
CONF_TYPICAL_P = "typical_p"
DEFAULT_TYPICAL_P = 1.0
CONF_MIROSTAT = "mirostat"
DEFAULT_MIROSTAT = 0
CONF_MIROSTAT_TAU = "mirostat_tau"
DEFAULT_MIROSTAT_TAU = 5.0
CONF_MIROSTAT_ETA = "mirostat_eta"
DEFAULT_MIROSTAT_ETA = 0.1
CONF_PRESENCE_PENALTY = "presence_penalty"
DEFAULT_PRESENCE_PENALTY = 0.0
CONF_FREQUENCY_PENALTY = "frequency_penalty"
DEFAULT_FREQUENCY_PENALTY = 0.0
CONF_BOOST = "boost"
DEFAULT_BOOST = True
CONF_STOP = "stop"
DEFAULT_STOP = ""
CONF_BACKUP_MODEL = "backup_model"
DEFAULT_BACKUP_MODEL = ""

# Timeout configuration
CONF_TIMEOUT = "timeout"
DEFAULT_TIMEOUT = 120.0

# Default options for AI task
DEFAULT_AI_TASK_OPTIONS = {
    CONF_MODEL: DEFAULT_MODEL,
    CONF_BACKUP_MODEL: DEFAULT_BACKUP_MODEL,
    CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
    CONF_ADVANCED_OPTIONS: DEFAULT_ADVANCED_OPTIONS,
    CONF_TIMEOUT: DEFAULT_TIMEOUT,
}

# Skill System Constants
CONF_SKILLS = "skills"
DEFAULT_SKILLS_DIRECTORY = "skills"
SKILL_FILE_NAME = "SKILL.md"

# Skill Services
SERVICE_RELOAD_SKILLS = "reload_skills"
SERVICE_DOWNLOAD_SKILL = "download_skill"
SERVICE_DETECT_OBJECTS = "detect_objects"

# GitHub repository for downloadable skills
GITHUB_REPO_OWNER = "jekalmin"
GITHUB_REPO_NAME = "oasira_ai_conversation"
GITHUB_SKILLS_BRANCH = "develop"
GITHUB_SKILLS_PATH = "examples/skills"

# Working Directory
DEFAULT_WORKING_DIRECTORY = (
    "oasira_ai_conversation/"  # /config/oasira_ai_conversation/
)

# File system and shell security settings
SHELL_TIMEOUT = 300  # seconds
SHELL_OUTPUT_LIMIT = 10000  # characters
SHELL_DENY_PATTERNS = [
    r"\brm\s+-r",  # Recursive delete
    r"\brm\s+-rf",  # Force recursive delete
    r"\bdel\s+/[fqs]",  # Windows delete with flags
    r"\brmdir\s+/s",  # Windows recursive directory delete
    r"\bformat\b",  # Disk format
    r"\bmkfs\b",  # Make filesystem
    r"\bdiskpart\b",  # Windows disk partition
    r"\bdd\b",  # Disk duplicator
    r"\bshutdown\b",  # System shutdown
    r"\breboot\b",  # System reboot
    r"\bpoweroff\b",  # Power off
    r":\(\)\{.*:\|:.*\}",  # Fork bomb pattern
]

# File system limits
FILE_READ_SIZE_LIMIT = 1024 * 1024  # 1 MB

# Default allowed directories for file operations
DEFAULT_ALLOWED_DIRS = [
    DEFAULT_WORKING_DIRECTORY,  # /config/oasira_ai_conversation/
]

SERVICE_QUERY_IMAGE = "query_image"

CONF_PAYLOAD_TEMPLATE = "payload_template"


def get_model_config(model: str) -> dict[str, bool]:
    """Get model-specific configuration based on model name.
    
    Ollama models have different capabilities. This function provides
    a simplified config for Ollama compatibility.
    """
    return {
        "supports_top_p": True,
        "supports_temperature": True,
        "supports_max_tokens": False,
        "supports_num_ctx": True,
        "supports_json_mode": True,
    }