# Natural Chat Improvements

## Overview

The chat workflow has been simplified to provide more natural, flowing conversations by reducing over-aggressive sanitization and simplifying system prompt rules.

## What Changed

### 1. Configurable Sanitization

**Previous Behavior:**
- Always removed ALL code blocks and inline code from responses
- Made technical discussions impossible
- Could remove legitimate content that looked like code

**New Behavior:**
- **Minimal Sanitization (Default)**: Only removes obvious artifacts
  - ✅ Removes: Speaker tags (User:, Assistant:), meta notes ([Note: ...]), actions (*smiles*)
  - ✅ Preserves: Code blocks, inline code, technical terms
  - Best for: Natural conversations, technical discussions, mixed contexts

- **Aggressive Sanitization (Optional)**: Legacy behavior
  - Removes everything including code blocks and inline code
  - Use when: Code output is never desired (pure social chat)

### 2. Simplified System Prompt

**Previous Rules (Too Restrictive):**
```
- NEVER output code blocks
- NEVER show raw code
- NEVER state you're an AI
- NEVER ask if user wants you to do something
- Keep responses casual and natural like a real friend
- Be concise and to the point
- Don't make up facts
- Only extract facts when asked
- No meta-commentary or speaker labels
- Do not include actions
- NEVER state you don't have real-time info
- NEVER say you're just an AI
```

**New Rules (Balanced & Natural):**
```
- Be natural, conversational, and helpful like talking to a friend
- Be concise but complete - answer questions fully without being overwhelming
- If you don't know something, just say so naturally
- Avoid meta-commentary like 'As an AI...' - just respond directly
- Don't include action descriptions like *nods*
- Use code examples when helpful for technical discussions (configurable)
```

### 3. Code Output Behavior

**Previous:** Code was banned by default (`disallow_code=True`)

**New:** Code is allowed by default (`disallow_code=False`)
- Bot can naturally discuss technical topics with code examples
- Can be configured per-persona to disable if needed
- When disabled, explains concepts clearly without code examples

## Configuration

### Environment Variable

Add to your `.env` file:

```bash
# Use minimal sanitization for natural chat (default: true)
# Set to false for aggressive sanitization that removes all code
MINIMAL_SANITIZATION=true
```

### Per-Persona Configuration

In your persona JSON file:

```json
{
  "name": "MyBot",
  "system_prompt": "You are a helpful assistant...",
  "disallow_code": false  // false = allows code (default), true = no code examples
}
```

### Programmatic Control

```python
from agent.chat_workflow import ChatWorkflow

# Natural chat (default)
workflow = ChatWorkflow(
    persona=my_persona,
    minimal_sanitization=True  # Preserves code, technical terms
)

# Pure social chat (no code)
workflow = ChatWorkflow(
    persona=my_persona,
    minimal_sanitization=False  # Removes all code
)
```

## Examples

### Example 1: Technical Discussion

**User:** "How do I reverse a string in Python?"

**Before (Over-sanitized):**
```
You can reverse a string by using slicing with a negative step...
```
*(Code was removed, making the answer incomplete)*

**After (Natural):**
```
You can reverse a string using slicing with a negative step. Here's how:

`my_string[::-1]`

For example:
```python
text = "hello"
reversed_text = text[::-1]
print(reversed_text)  # Output: "olleh"
```

This works because `[::-1]` means start at the end and step backwards.
```

### Example 2: Casual Chat

**User:** "How's your day going?"

**Before (Over-engineered):**
```
I'm functioning optimally and ready to assist with any queries or topics you'd like to discuss. Would you like to explore something specific?
```
*(Too formal, asking permission)*

**After (Natural):**
```
Going well! Just here chatting and helping out. What's up with you?
```

### Example 3: Mixed Context

**User:** "Can you explain what APIs are?"

**Before (Inconsistent):**
```
APIs are Application Programming Interfaces that allow different software to communicate...
```
*(No code examples even though they'd be helpful)*

**After (Context-Aware):**
```
APIs (Application Programming Interfaces) let different programs talk to each other. Think of it like a waiter in a restaurant - you (the app) tell the waiter (the API) what you want, and they bring it back from the kitchen (the server).

For example, a weather app uses an API like this:
```python
response = requests.get('https://api.weather.com/forecast')
weather_data = response.json()
```

The API handles all the complex stuff and just gives you the data you need.
```

## Benefits

✅ **More Natural**: Less artificial restrictions, more flowing conversation  
✅ **Smarter Context**: Code examples when helpful, plain language when not  
✅ **Less Over-Processing**: Preserves legitimate content instead of aggressive removal  
✅ **Configurable**: Choose the right sanitization level for your use case  
✅ **Better Technical Support**: Can actually help with coding questions  
✅ **Fewer Awkward Gaps**: No more "I can't show you code" in technical discussions  

## Migration Guide

If you prefer the old aggressive behavior:

1. Set in `.env`:
   ```bash
   MINIMAL_SANITIZATION=false
   ```

2. Or in persona JSON:
   ```json
   {
     "disallow_code": true
   }
   ```

No code changes needed - it's fully backward compatible.

## Technical Details

### Sanitization Patterns

**Always Removed (Both Modes):**
- `SPEAKER_TAG_PATTERN`: `User:`, `Assistant:`, `System:` etc.
- `META_NOTE_PATTERN`: `[Note: ...]`, `[Meta: ...]`, `[System: ...]`
- `ACTION_PATTERN`: `*smiles*`, `*nods*`, `*gestures*`

**Conditionally Removed (Aggressive Mode Only):**
- `CODE_BLOCK_PATTERN`: ` ```...``` ` (complete or incomplete blocks)
- `INLINE_CODE_PATTERN`: `` `code` `` (inline backticks)

### Whitespace Handling

Both modes now preserve intentional formatting:
- Multiple spaces → single space
- Multiple blank lines → double newlines (preserves paragraph breaks)
- Does NOT collapse all whitespace to one line

## Testing

Run the test suite to verify:

```bash
# Check syntax
python -m py_compile agent/chat_workflow.py

# Run existing tests
pytest tests/test_chat_workflow.py -v
```

## Feedback

If you notice any issues with the new natural chat behavior:
1. Try adjusting `MINIMAL_SANITIZATION` setting
2. Adjust persona's `disallow_code` flag
3. Report specific cases where the bot behaves unnaturally
