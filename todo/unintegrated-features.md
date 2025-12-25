# Heimdall TODO

This directory tracks unintegrated features and pending improvements.

---

## 1. Watchdog Integration

**Status:** Implemented but not wired to Agent Loop

**Location:** `src/heimdall/watchdogs/`

### What exists:
- `NavigationWatchdog` - Detects URL changes
- `NetworkWatchdog` - Tracks XHR/fetch requests
- `DOMWatchdog` - Monitors DOM mutations
- `ErrorWatchdog` - Catches browser errors/crashes

### What's needed:
1. Start watchdogs in `Agent.__init__()` or `Agent.run()`
2. Subscribe the agent loop to watchdog events
3. Use navigation completion for smarter waiting
4. Use network idle for stability detection

### Integration point:
```python
# In agent/loop.py
from heimdall.watchdogs import NavigationWatchdog, NetworkWatchdog

async def run(self, task: str):
    # Start watchdogs
    nav_watchdog = NavigationWatchdog(self._session, self._bus)
    net_watchdog = NetworkWatchdog(self._session, self._bus)
    
    await nav_watchdog.start()
    await net_watchdog.start()
    
    try:
        # ... agent loop ...
    finally:
        await nav_watchdog.stop()
        await net_watchdog.stop()
```

---

## 2. Collector/Export Integration

**Status:** Has TODO comment in CLI, not wired up

**Location:** `src/heimdall/collector/`

### What exists:
- `Collector` - Captures step context (screenshots, selectors)
- `StepContext` - Data model for each step
- `Exporter` - JSON export functionality

### What's needed:
1. Create Collector instance in `_run_agent()`
2. Hook into agent loop to capture before/after each step
3. Call `Exporter.export_result()` at the end

### The TODO in cli.py:
```python
# Line 148:
# Note: In real usage, we'd integrate Collector during execution
```

### Integration pattern:
```python
from heimdall.collector import Collector, Exporter

# In agent loop, wrap step execution:
async def _execute_step(self, task: str):
    collector = self._collector
    step_ctx = collector.begin_step()
    
    # Capture before state
    step_ctx.screenshot_before = await self._session.screenshot()
    
    # Execute action...
    
    # Capture after state
    step_ctx.screenshot_after = await self._session.screenshot()
    collector.end_step(step_ctx)
```

---

## 3. Demo Mode

**Status:** Flag accepted but not functional

**Location:** `src/heimdall/browser/demo.py`

### What exists:
- `DemoMode` class with:
  - `highlight_element()` - Box around element
  - `show_tooltip()` - Floating tooltip

### What's needed:
1. Pass `demo_mode` flag through `_run_agent()` to Agent
2. Create `DemoMode` instance when enabled
3. Call `highlight_element()` before each action
4. Call `show_tooltip()` to show action description

### CLI flag exists but unused:
```python
demo: bool = typer.Option(False, "--demo", help="Enable demo mode")
# ... but demo_mode is never passed anywhere
```

---

## 4. State Persistence (Task Resumption)

**Status:** Module complete but not integrated

**Location:** `src/heimdall/persistence/`

### What exists:
- `StateManager` - File-based state storage
- `PersistedState` - Serializable agent state
- `TaskProgress` - Track completed/pending items
- Auto-generates `todo.md` and `results.md`

### What's needed:
1. Add `--resume` flag to CLI
2. Check for existing state at agent start
3. Save state after each successful step
4. Clear state on task completion

### Integration pattern:
```python
from heimdall.persistence import StateManager, PersistedState

# In CLI
state_mgr = StateManager(output_dir)

if resume and state_mgr.has_saved_state:
    previous_state = await state_mgr.load_state()
    agent.restore(previous_state)

# After each step
await state_mgr.save_state(agent.get_state())

# On completion
await state_mgr.clear_state()
```

---

## Priority Order

1. **Collector/Export** - Most valuable for the primary use case
2. **Demo Mode** - Great for debugging and demos
3. **Watchdogs** - Improves reliability
4. **State Persistence** - Nice-to-have for long tasks
