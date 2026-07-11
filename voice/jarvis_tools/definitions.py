"""Jarvis tools - definitions.py"""

from __future__ import annotations

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_active_window",
            "description": (
                "Get the title of the foreground (active) window. "
                "Use when the user asks what app or file they are looking at. "
                "Cursor window titles usually contain the open file name."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_screen",
            "description": (
                "Read the visible desktop/browser screen using the local vision model. "
                "Use this on authenticated/personalized pages like YouTube playlists, "
                "where fetch_url cannot see the user's logged-in page. Returns visible text and likely page elements."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "What to look for on screen, e.g. visible YouTube playlist names",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_jarvis_browser",
            "description": (
                "Open a URL in the dedicated Jarvis-controlled browser profile. "
                "Use for personalized/authenticated sites Jarvis needs to inspect later."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "HTTP or HTTPS URL to open"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_jarvis_browser",
            "description": (
                "Read real DOM text from the dedicated Jarvis-controlled browser tab. "
                "Use instead of screenshots for personalized pages like YouTube playlists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "What to extract from the current page"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_browser_context",
            "description": (
                "Read the latest DOM/text/link snapshot sent by the Jarvis Firefox extension. "
                "Use this for the user's real Firefox session and authenticated pages like YouTube playlists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "What to extract, e.g. YouTube playlist names"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_browser_context_link",
            "description": (
                "Open a link from the latest Firefox page context by matching link text. "
                "Use after read_browser_context when the user names a playlist/link to open."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Playlist/link name to match"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate_browser_context",
            "description": "Navigate the Firefox tab connected to the Jarvis extension to a URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "HTTP or HTTPS URL"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_browser_context",
            "description": (
                "Click a visible Firefox page link, button, or control by matching its text or aria label."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Visible text to click"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "act_on_context",
            "description": (
                "Observe currently available browser, window, app, file, and tool actions, then map the user's "
                "spoken command to the most likely action and execute it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The user's spoken command"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setup_firefox_bridge",
            "description": (
                "Open Firefox's temporary extension page and the Jarvis extension folder so the user can load "
                "the extension into their normal logged-in Firefox session."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_powershell",
            "description": "Run a PowerShell command on this Windows PC.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "PowerShell command"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_folder_in_cursor",
            "description": (
                "Open a folder in the Cursor code editor. "
                "Use when the user asks to open a project or folder in Cursor."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_path": {
                        "type": "string",
                        "description": (
                            "Folder path, e.g. C:/Users/marce/Documents/Programming/ai-assistant or ai-assistant"
                        ),
                    },
                },
                "required": ["folder_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Open a Windows application, file, or URL (not folders — use open_folder_in_cursor for those).",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "App name, path, or URL",
                    }
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web in the default browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query to open in the browser",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a URL in the default browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "HTTP or HTTPS URL to open",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_research",
            "description": (
                "Search the open web, fetch pages, extract verified facts, and store them "
                "under .agent/web for later retrieval. Use for research questions that need "
                "multiple sources, not just reading one known URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to research on the web",
                    },
                    "project_path": {
                        "type": "string",
                        "description": (
                            "Optional project folder for .agent/web storage "
                            "(default: ai-assistant repo)"
                        ),
                    },
                    "max_pages": {
                        "type": "integer",
                        "default": 5,
                        "description": "Maximum pages to fetch this run",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a public web page and return readable text for summarizing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "HTTP or HTTPS URL to read",
                    },
                    "max_chars": {"type": "integer", "default": 6000},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file under the user's Documents folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 8000},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a text file under Documents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files under Documents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": ""},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": (
                "Check git status for a project folder: branch, uncommitted changes, remote URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Project folder path (e.g. C:/Users/marce/Documents/Programming/ai-assistant)",
                    },
                },
                "required": ["project_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_command",
            "description": (
                "Run a safe git command in an allowed project folder. "
                "Use for status, diff, log, add, commit, pull, push, branch, and remote checks. "
                "Dangerous args like force push, hard reset, clean -fdx, and branch delete are blocked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Project folder path",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Git arguments without the leading 'git', e.g. ['status', '--short']",
                    },
                },
                "required": ["project_path", "args"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_publish_project",
            "description": (
                "Create a GitHub repo under magnify-dev (if it does not exist), "
                "commit all changes, and push to GitHub. Use when the user asks to "
                "put a project on GitHub or push code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "Local project folder to publish",
                    },
                    "repo_name": {
                        "type": "string",
                        "description": "GitHub repository name (e.g. ai-assistant)",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Commit message for any uncommitted changes",
                    },
                    "visibility": {
                        "type": "string",
                        "enum": ["public", "private"],
                        "description": "Repo visibility when creating (default public)",
                    },
                    "org": {
                        "type": "string",
                        "description": "GitHub organization (default magnify-dev)",
                    },
                },
                "required": ["project_path", "repo_name", "commit_message"],
            },
        },
    },
]

BLOCKED_COMMAND_FRAGMENTS = [
    "format ",
    "remove-item -recurse -force c:\\",
    "rm -rf /",
    "shutdown",
    "restart-computer",
    "stop-computer",
    "reg delete",
    "stop-process -name 'cursor'",
    'stop-process -name "cursor"',
    "stop-process cursor",
]

BLOCKED_GIT_ARGS = [
    "--force",
    "push --force",
    "reset --hard",
    "clean -fdx",
    "filter-branch",
    "branch -D",
]
