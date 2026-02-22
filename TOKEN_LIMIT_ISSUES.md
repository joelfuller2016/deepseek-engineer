# DeepSeek Engineer - Token Limit Issues

## 🚨 Current Issues

### 1. **Token Limit Exceeded (65,536 tokens)**
**Problem:** DeepSeek API has a maximum context window of 65,536 tokens, but the application has no token counting or limiting mechanism.

**Symptoms:**
- Error: "The length of your prompt exceeds the model's max input limit 65536"
- Happens when using `/add .` on a project directory
- Can occur when loading multiple large files

**Root Causes:**
- No token counting before adding files
- Can load up to 1000 files without size checks
- Each file creates a separate system message
- No cumulative token tracking

---

### 2. **Inefficient Token Usage**
**Problem:** Files are loaded as separate system messages, wasting tokens on repeated prefixes.

**Current behavior:**
```
{"role": "system", "content": "Content of file 'file1.py':\n\n..."}
{"role": "system", "content": "Content of file 'file2.py':\n\n..."}
{"role": "system", "content": "Content of file 'file3.py':\n\n..."}
```

**Issues:**
- Repeated "Content of file" prefix wastes tokens
- Multiple system messages fragment the context
- No consolidation of related files

---

### 3. **Aggressive History Trimming**
**Problem:** `trim_conversation_history()` only keeps the last 15 messages, which might delete important context.

**Current code:**
```python
if len(other_msgs) > 15:
    other_msgs = other_msgs[-15:]
```

**Issues:**
- Tool calls and responses count as separate messages
- A single file operation creates 3+ messages
- Important context can be lost quickly

---

### 4. **No Smart File Selection**
**Problem:** When adding a directory, all eligible files are loaded without prioritization.

**Current behavior:**
- Loads files alphabetically until limit reached
- No consideration of file importance
- No filtering based on user's query
- Binary/large files excluded, but still counts text files

---

### 5. **Missing Token Estimation**
**Problem:** No mechanism to estimate tokens before adding content.

**Needed:**
- Token counting function (rough estimate: ~4 chars = 1 token)
- Running total of tokens in conversation
- Warning when approaching limits
- Smart truncation of large files

---

## 🔧 Recommended Fixes

### 1. **Implement Token Counting**
```python
def estimate_tokens(text: str) -> int:
    # Rough estimate: 4 characters ≈ 1 token
    return len(text) // 4

def get_conversation_tokens() -> int:
    total = 0
    for msg in conversation_history:
        if msg.get("content"):
            total += estimate_tokens(msg["content"])
    return total
```

### 2. **Add Token Limits to File Loading**
```python
MAX_TOKENS = 50000  # Leave buffer for response
MAX_FILE_TOKENS = 10000  # Per file limit

def add_file_with_limit(path: str, content: str) -> bool:
    tokens = estimate_tokens(content)
    current_total = get_conversation_tokens()
    
    if current_total + tokens > MAX_TOKENS:
        console.print(f"[yellow]⚠ Cannot add {path}: would exceed token limit[/yellow]")
        return False
    
    if tokens > MAX_FILE_TOKENS:
        # Truncate large files
        content = content[:MAX_FILE_TOKENS * 4] + "\n... [truncated]"
    
    # Add to conversation
    return True
```

### 3. **Consolidate File Loading**
Instead of multiple system messages, consolidate files:
```python
files_content = []
for file in files_to_add:
    files_content.append(f"=== {file} ===\n{content}\n")

conversation_history.append({
    "role": "system",
    "content": "Project files:\n\n" + "\n".join(files_content)
})
```

### 4. **Smart File Selection**
- Prioritize files mentioned in user's query
- Limit directory additions to most relevant files
- Show token count before confirming large additions
- Allow selective file loading

### 5. **Better History Management**
- Keep important messages (tool results, user queries)
- Consolidate old file contents
- Track message importance
- Implement sliding window with context preservation

---

## 🚀 Quick Workarounds

Until fixes are implemented:

1. **Don't use `/add .` on large projects**
   - Be selective about which files to add
   - Add specific directories or files

2. **Add files one at a time**
   ```
   /add src/main.py
   /add src/utils.py
   ```

3. **Use the AI's automatic file reading**
   - Let the AI read files as needed via function calls
   - Don't preload entire projects

4. **Clear conversation when needed**
   - Exit and restart to clear context
   - Start fresh for new tasks

5. **Use specific subdirectories**
   ```
   /add src/components/
   ```
   Instead of adding the entire project

---

## 📊 Token Usage Guidelines

| Content Type | Approximate Tokens |
|--------------|-------------------|
| 1 line of code | ~10-20 tokens |
| 100 lines of code | ~1,000-2,000 tokens |
| Average Python file | ~5,000-10,000 tokens |
| System prompt | ~500 tokens |
| User message | ~50-200 tokens |
| **Total limit** | **65,536 tokens** |

**Safe operating range:** Keep total context under 50,000 tokens to leave room for responses.
