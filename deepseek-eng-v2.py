#!/usr/bin/env python3

import os
import sys
import json
from pathlib import Path
from textwrap import dedent
from typing import List, Dict, Any, Optional, Tuple
from openai import OpenAI
from pydantic import BaseModel
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.style import Style
import time

# Force UTF-8 encoding for Windows console
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Initialize Rich console
console = Console()

# Token limits and management
MAX_CONTEXT_TOKENS = 50000  # Leave buffer for responses (API limit is 65536)
MAX_FILE_TOKENS = 8000      # Max tokens per file
MAX_FILES_PER_ADD = 20      # Max files to add at once
CHARS_PER_TOKEN = 4         # Rough estimate

# Try to initialize prompt session with fallback
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.styles import Style as PromptStyle
    
    prompt_session = PromptSession(
        style=PromptStyle.from_dict({
            'prompt': '#0066ff bold',  # Bright blue prompt
            'completion-menu.completion': 'bg:#1e3a8a fg:#ffffff',
            'completion-menu.completion.current': 'bg:#3b82f6 fg:#ffffff bold',
        })
    )
    use_prompt_toolkit = True
except Exception as e:
    # Fallback for environments without proper console
    console.print("[yellow]Note: Running in fallback mode (no auto-completion)[/yellow]")
    prompt_session = None
    use_prompt_toolkit = False

# --------------------------------------------------------------------------------
# 1. Configure OpenAI client and load environment variables
# --------------------------------------------------------------------------------
load_dotenv()  # Load environment variables from .env file
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)  # Configure for DeepSeek API

# --------------------------------------------------------------------------------
# 2. Define our schema using Pydantic for type safety
# --------------------------------------------------------------------------------
class FileToCreate(BaseModel):
    path: str
    content: str

class FileToEdit(BaseModel):
    path: str
    original_snippet: str
    new_snippet: str

# Remove AssistantResponse as we're using function calling now

# --------------------------------------------------------------------------------
# 2.1. Define Function Calling Tools
# --------------------------------------------------------------------------------
tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the content of a single file from the filesystem",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the file to read (relative or absolute)",
                    }
                },
                "required": ["file_path"]
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_multiple_files",
            "description": "Read the content of multiple files from the filesystem",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of file paths to read (relative or absolute)",
                    }
                },
                "required": ["file_paths"]
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file or overwrite an existing file with the provided content",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path where the file should be created",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file",
                    }
                },
                "required": ["file_path", "content"]
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_multiple_files",
            "description": "Create multiple files at once",
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"}
                            },
                            "required": ["path", "content"]
                        },
                        "description": "Array of files to create with their paths and content",
                    }
                },
                "required": ["files"]
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit an existing file by replacing a specific snippet with new content",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the file to edit",
                    },
                    "original_snippet": {
                        "type": "string",
                        "description": "The exact text snippet to find and replace",
                    },
                    "new_snippet": {
                        "type": "string",
                        "description": "The new text to replace the original snippet with",
                    }
                },
                "required": ["file_path", "original_snippet", "new_snippet"]
            },
        }
    }
]

# --------------------------------------------------------------------------------
# 3. system prompt
# --------------------------------------------------------------------------------
system_PROMPT = dedent("""\
    You are an elite software engineer called DeepSeek Engineer with decades of experience across all programming domains.
    Your expertise spans system design, algorithms, testing, and best practices.
    You provide thoughtful, well-structured solutions while explaining your reasoning.

    Core capabilities:
    1. Code Analysis & Discussion
       - Analyze code with expert-level insight
       - Explain complex concepts clearly
       - Suggest optimizations and best practices
       - Debug issues with precision

    2. File Operations (via function calls):
       - read_file: Read a single file's content
       - read_multiple_files: Read multiple files at once
       - create_file: Create or overwrite a single file
       - create_multiple_files: Create multiple files at once
       - edit_file: Make precise edits to existing files using snippet replacement

    Guidelines:
    1. Provide natural, conversational responses explaining your reasoning
    2. Use function calls when you need to read or modify files
    3. For file operations:
       - Always read files first before editing them to understand the context
       - Use precise snippet matching for edits
       - Explain what changes you're making and why
       - Consider the impact of changes on the overall codebase
    4. Follow language-specific best practices
    5. Suggest tests or validation steps when appropriate
    6. Be thorough in your analysis and recommendations

    IMPORTANT: In your thinking process, if you realize that something requires a tool call, cut your thinking short and proceed directly to the tool call. Don't overthink - act efficiently when file operations are needed.

    Remember: You're a senior engineer - be thoughtful, precise, and explain your reasoning clearly.
""")

# --------------------------------------------------------------------------------
# 4. Helper functions with token management
# --------------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string."""
    if not text:
        return 0
    # Rough estimate: ~4 characters per token
    # Add 10% buffer for safety
    return int(len(text) / CHARS_PER_TOKEN * 1.1)

def get_conversation_tokens() -> int:
    """Calculate total tokens in current conversation."""
    total = 0
    for msg in conversation_history:
        if msg.get("content"):
            total += estimate_tokens(msg["content"])
        # Tool calls also use tokens
        if msg.get("tool_calls"):
            total += estimate_tokens(json.dumps(msg["tool_calls"]))
    return total

def check_token_limit(additional_tokens: int) -> Tuple[bool, int]:
    """Check if adding tokens would exceed limit. Returns (can_add, current_total)."""
    current = get_conversation_tokens()
    if current + additional_tokens > MAX_CONTEXT_TOKENS:
        return False, current
    return True, current

def truncate_content(content: str, max_tokens: int) -> str:
    """Truncate content to fit within token limit."""
    estimated_tokens = estimate_tokens(content)
    if estimated_tokens <= max_tokens:
        return content
    
    # Calculate approximate character limit
    char_limit = max_tokens * CHARS_PER_TOKEN
    truncated = content[:char_limit]
    
    # Try to truncate at a line boundary
    last_newline = truncated.rfind('\n')
    if last_newline > char_limit * 0.8:  # If we found a newline in the last 20%
        truncated = truncated[:last_newline]
    
    return truncated + "\n\n... [Content truncated due to token limit]"

def read_local_file(file_path: str) -> str:
    """Return the text content of a local file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

def create_file(path: str, content: str):
    """Create (or overwrite) a file at 'path' with the given 'content'."""
    file_path = Path(path)
    
    # Security checks
    if any(part.startswith('~') for part in file_path.parts):
        raise ValueError("Home directory references not allowed")
    normalized_path = normalize_path(str(file_path))
    
    # Validate reasonable file size for operations
    if len(content) > 5_000_000:  # 5MB limit
        raise ValueError("File content exceeds 5MB size limit")
    
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    console.print(f"[bold blue]✓[/bold blue] Created/updated file at '[bright_cyan]{file_path}[/bright_cyan]'")

def show_diff_table(files_to_edit: List[FileToEdit]) -> None:
    if not files_to_edit:
        return
    
    table = Table(title="📝 Proposed Edits", show_header=True, header_style="bold bright_blue", show_lines=True, border_style="blue")
    table.add_column("File Path", style="bright_cyan", no_wrap=True)
    table.add_column("Original", style="red dim")
    table.add_column("New", style="bright_green")

    for edit in files_to_edit:
        table.add_row(edit.path, edit.original_snippet, edit.new_snippet)
    
    console.print(table)

def apply_diff_edit(path: str, original_snippet: str, new_snippet: str):
    """Reads the file at 'path', replaces the first occurrence of 'original_snippet' with 'new_snippet', then overwrites."""
    try:
        content = read_local_file(path)
        
        # Verify we're replacing the exact intended occurrence
        occurrences = content.count(original_snippet)
        if occurrences == 0:
            raise ValueError("Original snippet not found")
        if occurrences > 1:
            console.print(f"[bold yellow]⚠ Multiple matches ({occurrences}) found - requiring line numbers for safety[/bold yellow]")
            console.print("[dim]Use format:\n--- original.py (lines X-Y)\n+++ modified.py[/dim]")
            raise ValueError(f"Ambiguous edit: {occurrences} matches")
        
        updated_content = content.replace(original_snippet, new_snippet, 1)
        create_file(path, updated_content)
        console.print(f"[bold blue]✓[/bold blue] Applied diff edit to '[bright_cyan]{path}[/bright_cyan]'")

    except FileNotFoundError:
        console.print(f"[bold red]✗[/bold red] File not found for diff editing: '[bright_cyan]{path}[/bright_cyan]'")
    except ValueError as e:
        console.print(f"[bold yellow]⚠[/bold yellow] {str(e)} in '[bright_cyan]{path}[/bright_cyan]'. No changes made.")
        console.print("\n[bold blue]Expected snippet:[/bold blue]")
        console.print(Panel(original_snippet, title="Expected", border_style="blue", title_align="left"))
        console.print("\n[bold blue]Actual file content:[/bold blue]")
        console.print(Panel(content, title="Actual", border_style="yellow", title_align="left"))

def try_handle_add_command(user_input: str) -> bool:
    prefix = "/add "
    if user_input.strip().lower().startswith(prefix):
        path_to_add = user_input[len(prefix):].strip()
        try:
            normalized_path = normalize_path(path_to_add)
            if os.path.isdir(normalized_path):
                # Handle entire directory with token limits
                add_directory_to_conversation(normalized_path)
            else:
                # Handle a single file
                content = read_local_file(normalized_path)
                file_tokens = estimate_tokens(content)
                
                can_add, current_tokens = check_token_limit(file_tokens)
                if not can_add:
                    console.print(f"[bold red]✗[/bold red] Cannot add file: would exceed token limit")
                    console.print(f"[yellow]Current tokens: {current_tokens:,} / {MAX_CONTEXT_TOKENS:,}[/yellow]")
                    console.print(f"[yellow]File tokens: {file_tokens:,}[/yellow]")
                    return True
                
                # Truncate if needed
                if file_tokens > MAX_FILE_TOKENS:
                    console.print(f"[yellow]⚠ File is large ({file_tokens:,} tokens). Truncating to {MAX_FILE_TOKENS:,} tokens.[/yellow]")
                    content = truncate_content(content, MAX_FILE_TOKENS)
                
                conversation_history.append({
                    "role": "system",
                    "content": f"Content of file '{normalized_path}':\n\n{content}"
                })
                console.print(f"[bold blue]✓[/bold blue] Added file '[bright_cyan]{normalized_path}[/bright_cyan]' to conversation.")
                console.print(f"[dim]Tokens used: {estimate_tokens(content):,} (Total: {get_conversation_tokens():,} / {MAX_CONTEXT_TOKENS:,})[/dim]\n")
        except OSError as e:
            console.print(f"[bold red]✗[/bold red] Could not add path '[bright_cyan]{path_to_add}[/bright_cyan]': {e}\n")
        return True
    return False

def add_directory_to_conversation(directory_path: str):
    """Add directory contents with improved token management."""
    with console.status("[bold bright_blue]🔍 Scanning directory...[/bold bright_blue]") as status:
        excluded_files = {
            # Python specific
            ".DS_Store", "Thumbs.db", ".gitignore", ".python-version",
            "uv.lock", ".uv", "uvenv", ".uvenv", ".venv", "venv",
            "__pycache__", ".pytest_cache", ".coverage", ".mypy_cache",
            # Node.js / Web specific
            "node_modules", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
            ".next", ".nuxt", "dist", "build", ".cache", ".parcel-cache",
            ".turbo", ".vercel", ".output", ".contentlayer",
            # Build outputs
            "out", "coverage", ".nyc_output", "storybook-static",
            # Environment and config
            ".env", ".env.local", ".env.development", ".env.production",
            # Misc
            ".git", ".svn", ".hg", "CVS"
        }
        excluded_extensions = {
            # Binary and media files
            ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".avif",
            ".mp4", ".webm", ".mov", ".mp3", ".wav", ".ogg",
            ".zip", ".tar", ".gz", ".7z", ".rar",
            ".exe", ".dll", ".so", ".dylib", ".bin",
            # Documents
            ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
            # Python specific
            ".pyc", ".pyo", ".pyd", ".egg", ".whl",
            # UV specific
            ".uv", ".uvenv",
            # Database and logs
            ".db", ".sqlite", ".sqlite3", ".log",
            # IDE specific
            ".idea", ".vscode",
            # Web specific
            ".map", ".chunk.js", ".chunk.css",
            ".min.js", ".min.css", ".bundle.js", ".bundle.css",
            # Cache and temp files
            ".cache", ".tmp", ".temp",
            # Font files
            ".ttf", ".otf", ".woff", ".woff2", ".eot"
        }
        
        # Collect eligible files first
        eligible_files = []
        skipped_files = []
        max_file_size = 500_000  # 500KB limit per file for directories
        
        for root, dirs, files in os.walk(directory_path):
            # Skip hidden directories and excluded directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in excluded_files]
            
            for file in files:
                if file.startswith('.') or file in excluded_files:
                    skipped_files.append(os.path.join(root, file))
                    continue
                
                _, ext = os.path.splitext(file)
                if ext.lower() in excluded_extensions:
                    skipped_files.append(os.path.join(root, file))
                    continue
                
                full_path = os.path.join(root, file)
                
                try:
                    # Check file size
                    file_size = os.path.getsize(full_path)
                    if file_size > max_file_size:
                        skipped_files.append(f"{full_path} (too large: {file_size:,} bytes)")
                        continue
                    
                    # Check if it's binary
                    if is_binary_file(full_path):
                        skipped_files.append(f"{full_path} (binary)")
                        continue
                    
                    eligible_files.append(full_path)
                    
                except OSError:
                    skipped_files.append(f"{full_path} (error reading)")
        
        # Now process eligible files with token limits
        added_files = []
        total_tokens_added = 0
        current_tokens = get_conversation_tokens()
        
        console.print(f"\n[bold yellow]⚠ Token Budget:[/bold yellow]")
        console.print(f"Current usage: {current_tokens:,} / {MAX_CONTEXT_TOKENS:,} tokens")
        console.print(f"Available: {MAX_CONTEXT_TOKENS - current_tokens:,} tokens")
        console.print(f"Found {len(eligible_files)} eligible files\n")
        
        # Limit files to add
        files_to_add = eligible_files[:MAX_FILES_PER_ADD]
        if len(eligible_files) > MAX_FILES_PER_ADD:
            console.print(f"[yellow]⚠ Limiting to first {MAX_FILES_PER_ADD} files (found {len(eligible_files)})[/yellow]\n")
        
        # Collect file contents
        files_content = []
        
        for file_path in files_to_add:
            try:
                normalized_path = normalize_path(file_path)
                content = read_local_file(normalized_path)
                file_tokens = estimate_tokens(content)
                
                # Check if we can add this file
                if current_tokens + total_tokens_added + file_tokens > MAX_CONTEXT_TOKENS:
                    console.print(f"[yellow]⏭ Skipping {file_path} (would exceed token limit)[/yellow]")
                    break
                
                # Truncate if individual file is too large
                if file_tokens > MAX_FILE_TOKENS // 2:  # Use half limit for directory adds
                    content = truncate_content(content, MAX_FILE_TOKENS // 2)
                    file_tokens = estimate_tokens(content)
                
                relative_path = os.path.relpath(normalized_path, directory_path)
                files_content.append(f"=== {relative_path} ===\n{content}")
                added_files.append(relative_path)
                total_tokens_added += file_tokens
                
            except Exception as e:
                console.print(f"[red]Error reading {file_path}: {e}[/red]")
        
        if files_content:
            # Add all files in a single consolidated message
            consolidated_content = f"Files from directory '{directory_path}':\n\n" + "\n\n".join(files_content)
            conversation_history.append({
                "role": "system",
                "content": consolidated_content
            })
            
            console.print(f"[bold blue]✓[/bold blue] Added {len(added_files)} files from '[bright_cyan]{directory_path}[/bright_cyan]'")
            console.print(f"[dim]Tokens added: {total_tokens_added:,} (Total: {get_conversation_tokens():,} / {MAX_CONTEXT_TOKENS:,})[/dim]\n")
            
            if added_files:
                console.print("[bold bright_blue]📁 Added files:[/bold bright_blue]")
                for f in added_files[:10]:  # Show first 10
                    console.print(f"  [bright_cyan]📄 {f}[/bright_cyan]")
                if len(added_files) > 10:
                    console.print(f"  [dim]... and {len(added_files) - 10} more[/dim]")
        else:
            console.print(f"[yellow]⚠ No files could be added from '{directory_path}' due to token limits[/yellow]")
        
        if skipped_files and len(skipped_files) < 20:
            console.print(f"\n[dim]Skipped {len(skipped_files)} files (binary/excluded/large)[/dim]")

def is_binary_file(file_path: str, peek_size: int = 1024) -> bool:
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(peek_size)
        # If there is a null byte in the sample, treat it as binary
        if b'\0' in chunk:
            return True
        return False
    except Exception:
        # If we fail to read, just treat it as binary to be safe
        return True

def ensure_file_in_context(file_path: str) -> bool:
    try:
        normalized_path = normalize_path(file_path)
        content = read_local_file(normalized_path)
        file_marker = f"Content of file '{normalized_path}'"
        if not any(file_marker in msg["content"] for msg in conversation_history):
            conversation_history.append({
                "role": "system",
                "content": f"{file_marker}:\n\n{content}"
            })
        return True
    except OSError:
        console.print(f"[bold red]✗[/bold red] Could not read file '[bright_cyan]{file_path}[/bright_cyan]' for editing context")
        return False

def normalize_path(path_str: str) -> str:
    """Return a canonical, absolute version of the path with security checks."""
    path = Path(path_str).resolve()
    
    # Prevent directory traversal attacks
    if ".." in path.parts:
        raise ValueError(f"Invalid path: {path_str} contains parent directory references")
    
    return str(path)

# Fallback input function for environments without prompt_toolkit
def get_user_input(prompt_text: str) -> str:
    """Get user input with fallback for environments without prompt_toolkit."""
    if use_prompt_toolkit and prompt_session:
        return prompt_session.prompt(prompt_text)
    else:
        # Fallback to standard input
        return input(prompt_text)

# --------------------------------------------------------------------------------
# 5. Conversation state
# --------------------------------------------------------------------------------
conversation_history = [
    {"role": "system", "content": system_PROMPT}
]

# --------------------------------------------------------------------------------
# 6. OpenAI API interaction with streaming
# --------------------------------------------------------------------------------

def execute_function_call_dict(tool_call_dict) -> str:
    """Execute a function call from a dictionary format and return the result as a string."""
    try:
        function_name = tool_call_dict["function"]["name"]
        arguments = json.loads(tool_call_dict["function"]["arguments"])
        
        if function_name == "read_file":
            file_path = arguments["file_path"]
            normalized_path = normalize_path(file_path)
            content = read_local_file(normalized_path)
            return f"Content of file '{normalized_path}':\n\n{content}"
            
        elif function_name == "read_multiple_files":
            file_paths = arguments["file_paths"]
            results = []
            for file_path in file_paths:
                try:
                    normalized_path = normalize_path(file_path)
                    content = read_local_file(normalized_path)
                    results.append(f"Content of file '{normalized_path}':\n\n{content}")
                except OSError as e:
                    results.append(f"Error reading '{file_path}': {e}")
            return "\n\n" + "="*50 + "\n\n".join(results)
            
        elif function_name == "create_file":
            file_path = arguments["file_path"]
            content = arguments["content"]
            create_file(file_path, content)
            return f"Successfully created file '{file_path}'"
            
        elif function_name == "create_multiple_files":
            files = arguments["files"]
            created_files = []
            for file_info in files:
                create_file(file_info["path"], file_info["content"])
                created_files.append(file_info["path"])
            return f"Successfully created {len(created_files)} files: {', '.join(created_files)}"
            
        elif function_name == "edit_file":
            file_path = arguments["file_path"]
            original_snippet = arguments["original_snippet"]
            new_snippet = arguments["new_snippet"]
            
            # Ensure file is in context first
            if not ensure_file_in_context(file_path):
                return f"Error: Could not read file '{file_path}' for editing"
            
            apply_diff_edit(file_path, original_snippet, new_snippet)
            return f"Successfully edited file '{file_path}'"
            
        else:
            return f"Unknown function: {function_name}"
            
    except Exception as e:
        return f"Error executing {function_name}: {str(e)}"

def execute_function_call(tool_call) -> str:
    """Execute a function call and return the result as a string."""
    try:
        function_name = tool_call.function.name
        arguments = json.loads(tool_call.function.arguments)
        
        if function_name == "read_file":
            file_path = arguments["file_path"]
            normalized_path = normalize_path(file_path)
            content = read_local_file(normalized_path)
            return f"Content of file '{normalized_path}':\n\n{content}"
            
        elif function_name == "read_multiple_files":
            file_paths = arguments["file_paths"]
            results = []
            for file_path in file_paths:
                try:
                    normalized_path = normalize_path(file_path)
                    content = read_local_file(normalized_path)
                    results.append(f"Content of file '{normalized_path}':\n\n{content}")
                except OSError as e:
                    results.append(f"Error reading '{file_path}': {e}")
            return "\n\n" + "="*50 + "\n\n".join(results)
            
        elif function_name == "create_file":
            file_path = arguments["file_path"]
            content = arguments["content"]
            create_file(file_path, content)
            return f"Successfully created file '{file_path}'"
            
        elif function_name == "create_multiple_files":
            files = arguments["files"]
            created_files = []
            for file_info in files:
                create_file(file_info["path"], file_info["content"])
                created_files.append(file_info["path"])
            return f"Successfully created {len(created_files)} files: {', '.join(created_files)}"
            
        elif function_name == "edit_file":
            file_path = arguments["file_path"]
            original_snippet = arguments["original_snippet"]
            new_snippet = arguments["new_snippet"]
            
            # Ensure file is in context first
            if not ensure_file_in_context(file_path):
                return f"Error: Could not read file '{file_path}' for editing"
            
            apply_diff_edit(file_path, original_snippet, new_snippet)
            return f"Successfully edited file '{file_path}'"
            
        else:
            return f"Unknown function: {function_name}"
            
    except Exception as e:
        return f"Error executing {function_name}: {str(e)}"

def trim_conversation_history():
    """Improved trimming that preserves important context."""
    # Keep more messages and be smarter about what to trim
    MAX_MESSAGES = 30  # Increased from 15
    
    if len(conversation_history) <= MAX_MESSAGES:
        return
    
    # Always keep the system prompt
    system_msgs = [msg for msg in conversation_history if msg["role"] == "system" and msg["content"] == system_PROMPT]
    other_msgs = [msg for msg in conversation_history if msg not in system_msgs]
    
    # If we have too many messages, intelligent trimming
    if len(other_msgs) > MAX_MESSAGES:
        # Keep recent messages
        recent_msgs = other_msgs[-(MAX_MESSAGES-5):]  # Keep last N-5 messages
        
        # Also keep the last few user messages for context
        user_msgs = [msg for msg in other_msgs if msg["role"] == "user"][-5:]
        
        # Combine, removing duplicates while preserving order
        kept_msgs = []
        seen = set()
        for msg in user_msgs + recent_msgs:
            msg_id = id(msg)  # Use object identity
            if msg_id not in seen:
                seen.add(msg_id)
                kept_msgs.append(msg)
        
        # Rebuild conversation history
        conversation_history.clear()
        conversation_history.extend(system_msgs + kept_msgs)
        
        console.print(f"[dim]Trimmed conversation history to {len(conversation_history)} messages[/dim]")

def stream_openai_response(user_message: str):
    # Check token limit before sending
    message_tokens = estimate_tokens(user_message)
    current_tokens = get_conversation_tokens()
    
    if current_tokens + message_tokens > MAX_CONTEXT_TOKENS:
        console.print(f"\n[bold red]⚠ Token limit approaching![/bold red]")
        console.print(f"Current: {current_tokens:,} tokens")
        console.print(f"Message: {message_tokens:,} tokens")
        console.print(f"Total would be: {current_tokens + message_tokens:,} / {MAX_CONTEXT_TOKENS:,}")
        console.print("\n[yellow]Trimming conversation history...[/yellow]")
        trim_conversation_history()
        current_tokens = get_conversation_tokens()
        console.print(f"[green]After trimming: {current_tokens:,} tokens[/green]\n")
    
    # Add the user message to conversation history
    conversation_history.append({"role": "user", "content": user_message})
    
    # Trim conversation history if it's getting too long
    trim_conversation_history()

    # Remove the old file guessing logic since we'll use function calls
    try:
        stream = client.chat.completions.create(
            model="deepseek-reasoner",
            messages=conversation_history,
            tools=tools,
            max_completion_tokens=64000,
            stream=True
        )

        console.print("\n[bold bright_blue]🐋 Seeking...[/bold bright_blue]")
        reasoning_started = False
        reasoning_content = ""
        final_content = ""
        tool_calls = []

        for chunk in stream:
            # Handle reasoning content if available
            if hasattr(chunk.choices[0].delta, 'reasoning_content') and chunk.choices[0].delta.reasoning_content:
                if not reasoning_started:
                    console.print("\n[bold blue]💭 Reasoning:[/bold blue]")
                    reasoning_started = True
                console.print(chunk.choices[0].delta.reasoning_content, end="")
                reasoning_content += chunk.choices[0].delta.reasoning_content
            elif chunk.choices[0].delta.content:
                if reasoning_started:
                    console.print("\n")  # Add spacing after reasoning
                    console.print("\n[bold bright_blue]🤖 Assistant>[/bold bright_blue] ", end="")
                    reasoning_started = False
                final_content += chunk.choices[0].delta.content
                console.print(chunk.choices[0].delta.content, end="")
            elif chunk.choices[0].delta.tool_calls:
                # Handle tool calls
                for tool_call_delta in chunk.choices[0].delta.tool_calls:
                    if tool_call_delta.index is not None:
                        # Ensure we have enough tool_calls
                        while len(tool_calls) <= tool_call_delta.index:
                            tool_calls.append({
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""}
                            })
                        
                        if tool_call_delta.id:
                            tool_calls[tool_call_delta.index]["id"] = tool_call_delta.id
                        if tool_call_delta.function:
                            if tool_call_delta.function.name:
                                tool_calls[tool_call_delta.index]["function"]["name"] += tool_call_delta.function.name
                            if tool_call_delta.function.arguments:
                                tool_calls[tool_call_delta.index]["function"]["arguments"] += tool_call_delta.function.arguments

        console.print()  # New line after streaming

        # Store the assistant's response in conversation history
        assistant_message = {
            "role": "assistant",
            "content": final_content if final_content else None
        }
        
        if tool_calls:
            # Convert our tool_calls format to the expected format
            formatted_tool_calls = []
            for i, tc in enumerate(tool_calls):
                if tc["function"]["name"]:  # Only add if we have a function name
                    # Ensure we have a valid tool call ID
                    tool_id = tc["id"] if tc["id"] else f"call_{i}_{int(time.time() * 1000)}"
                    
                    formatted_tool_calls.append({
                        "id": tool_id,
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"]
                        }
                    })
            
            if formatted_tool_calls:
                # Important: When there are tool calls, content should be None or empty
                if not final_content:
                    assistant_message["content"] = None
                    
                assistant_message["tool_calls"] = formatted_tool_calls
                conversation_history.append(assistant_message)
                
                # Execute tool calls and add results immediately
                console.print(f"\n[bold bright_cyan]⚡ Executing {len(formatted_tool_calls)} function call(s)...[/bold bright_cyan]")
                for tool_call in formatted_tool_calls:
                    console.print(f"[bright_blue]→ {tool_call['function']['name']}[/bright_blue]")
                    
                    try:
                        result = execute_function_call_dict(tool_call)
                        
                        # Add tool result to conversation immediately
                        tool_response = {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": result
                        }
                        conversation_history.append(tool_response)
                    except Exception as e:
                        console.print(f"[red]Error executing {tool_call['function']['name']}: {e}[/red]")
                        # Still need to add a tool response even on error
                        conversation_history.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": f"Error: {str(e)}"
                        })
                
                # Get follow-up response after tool execution
                console.print("\n[bold bright_blue]🔄 Processing results...[/bold bright_blue]")
                
                follow_up_stream = client.chat.completions.create(
                    model="deepseek-reasoner",
                    messages=conversation_history,
                    tools=tools,
                    max_completion_tokens=64000,
                    stream=True
                )
                
                follow_up_content = ""
                reasoning_started = False
                
                for chunk in follow_up_stream:
                    # Handle reasoning content if available
                    if hasattr(chunk.choices[0].delta, 'reasoning_content') and chunk.choices[0].delta.reasoning_content:
                        if not reasoning_started:
                            console.print("\n[bold blue]💭 Reasoning:[/bold blue]")
                            reasoning_started = True
                        console.print(chunk.choices[0].delta.reasoning_content, end="")
                    elif chunk.choices[0].delta.content:
                        if reasoning_started:
                            console.print("\n")
                            console.print("\n[bold bright_blue]🤖 Assistant>[/bold bright_blue] ", end="")
                            reasoning_started = False
                        follow_up_content += chunk.choices[0].delta.content
                        console.print(chunk.choices[0].delta.content, end="")
                
                console.print()
                
                # Store follow-up response
                conversation_history.append({
                    "role": "assistant",
                    "content": follow_up_content
                })
        else:
            # No tool calls, just store the regular response
            conversation_history.append(assistant_message)

        return {"success": True}

    except Exception as e:
        error_msg = f"DeepSeek API error: {str(e)}"
        console.print(f"\n[bold red]❌ {error_msg}[/bold red]")
        
        # If it's a token limit error, provide helpful info
        if "65536" in str(e) or "token" in str(e).lower():
            console.print(f"\n[yellow]Token usage: {get_conversation_tokens():,} / {MAX_CONTEXT_TOKENS:,}[/yellow]")
            console.print("[yellow]Try starting a new conversation or using fewer files.[/yellow]")
        
        return {"error": error_msg}

# --------------------------------------------------------------------------------
# 7. Main interactive loop
# --------------------------------------------------------------------------------

def main():
    # Create a beautiful gradient-style welcome panel
    welcome_text = """[bold bright_blue]🐋 DeepSeek Engineer[/bold bright_blue] [bright_cyan]with Function Calling[/bright_cyan]
[dim blue]Powered by DeepSeek-R1 with Chain-of-Thought Reasoning[/dim blue]
[yellow]Token-aware version with smart limits[/yellow]"""
    
    console.print(Panel.fit(
        welcome_text,
        border_style="bright_blue",
        padding=(1, 2),
        title="[bold bright_cyan]🤖 AI Code Assistant[/bold bright_cyan]",
        title_align="center"
    ))
    
    # Create an elegant instruction panel
    instructions = """[bold bright_blue]📁 File Operations:[/bold bright_blue]
  • [bright_cyan]/add path/to/file[/bright_cyan] - Include a single file in conversation
  • [bright_cyan]/add path/to/folder[/bright_cyan] - Include files from a folder (max 20 files)
  • [dim]The AI can automatically read and create files using function calls[/dim]

[bold bright_blue]🎯 Commands:[/bold bright_blue]
  • [bright_cyan]exit[/bright_cyan] or [bright_cyan]quit[/bright_cyan] - End the session
  • [bright_cyan]/tokens[/bright_cyan] - Show current token usage
  • Just ask naturally - the AI will handle file operations automatically!

[bold yellow]⚠ Token Limits:[/bold yellow]
  • Max context: [bright_yellow]50,000[/bright_yellow] tokens (~200,000 characters)
  • Max per file: [bright_yellow]8,000[/bright_yellow] tokens (~32,000 characters)
  • API limit: [bright_yellow]65,536[/bright_yellow] tokens total"""
    
    console.print(Panel(
        instructions,
        border_style="blue",
        padding=(1, 2),
        title="[bold blue]💡 How to Use[/bold blue]",
        title_align="left"
    ))
    
    if not use_prompt_toolkit:
        console.print("\n[yellow]⚠ Note: Running without prompt_toolkit auto-completion support.[/yellow]")
        console.print("[yellow]For best experience, run from Windows Terminal, Command Prompt, or Git Bash.[/yellow]\n")
    
    console.print()

    while True:
        try:
            user_input = get_user_input("🔵 You> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[bold yellow]👋 Exiting gracefully...[/bold yellow]")
            break

        if not user_input:
            continue

        if user_input.lower() in ["exit", "quit"]:
            console.print("[bold bright_blue]👋 Goodbye! Happy coding![/bold bright_blue]")
            break
            
        if user_input.lower() == "/tokens":
            current = get_conversation_tokens()
            console.print(f"\n[bold cyan]📊 Token Usage:[/bold cyan]")
            console.print(f"Current: {current:,} / {MAX_CONTEXT_TOKENS:,} ({current/MAX_CONTEXT_TOKENS*100:.1f}%)")
            console.print(f"Available: {MAX_CONTEXT_TOKENS - current:,} tokens")
            console.print(f"Messages in history: {len(conversation_history)}\n")
            continue

        if try_handle_add_command(user_input):
            continue

        response_data = stream_openai_response(user_input)
        
        if response_data.get("error"):
            console.print(f"[bold red]❌ Error: {response_data['error']}[/bold red]")

    console.print("[bold blue]✨ Session finished. Thank you for using DeepSeek Engineer![/bold blue]")

if __name__ == "__main__":
    main()
