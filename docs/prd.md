# Heimdall - Product Requirements Document

| Field | Value |
|-------|-------|
| **Version** | 1.0 |
| **Author** | Team Heimdall |
| **Created** | 2025-12-23 |
| **Status** | Draft |

---

## 1. Overview

Heimdall is a browser automation agent that executes natural language tasks in a real browser, collecting comprehensive context data for automation script generation.

### Vision

Enable anyone to create robust browser automation scripts by describing what they want in plain language.

### Goals

1. Execute natural language tasks reliably in browsers
2. Collect complete context for each action (DOM, selectors, network)
3. Output language-agnostic data usable by any automation framework

---

## 2. Target Users

| User | Need |
|------|------|
| **QA Engineers** | Generate test scripts from manual test cases |
| **Developers** | Automate repetitive browser tasks |
| **Automation Engineers** | Bootstrap automation suites quickly |

---

## 3. Core Features

### 3.1 Natural Language Task Execution

- Accept plain text task descriptions
- LLM interprets tasks and executes via browser tools
- Handle multi-step flows autonomously

### 3.2 Browser Control via CDP

- Direct Chrome DevTools Protocol integration
- Full control: navigation, clicks, typing, scrolling
- Network request interception and capture

### 3.3 Context Collection

| Data | Description |
|------|-------------|
| **DOM State** | Full DOM snapshot per action |
| **Selectors** | CSS, XPath, data-testid, aria, text |
| **Screenshots** | Before/after each action |
| **Network** | All requests/responses during execution |
| **Element Metadata** | Attributes, bounding box, visibility |

### 3.4 Language-Agnostic Output

- JSON format containing all collected context
- Can be used to generate scripts in:
  - Playwright
  - Selenium
  - Cypress
  - Puppeteer
  - Any other framework

---

## 4. User Experience

### 4.1 Input

```
Natural language task description
```

Examples:
- "Log into example.com with user@test.com and password123"
- "Search for 'laptop' on amazon and add first result to cart"
- "Fill out the contact form with test data and submit"

### 4.2 Output

```
output/
├── context.json      # All collected data
├── screenshots/      # Visual state per action
├── network.har       # Network activity
└── dom_snapshots/    # DOM state per action
```

---

## 5. Technical Requirements

### 5.1 Platform

- Python 3.11+
- Chrome/Chromium browser
- macOS, Linux, Windows

### 5.2 Dependencies

| Package | Purpose |
|---------|---------|
| `cdp-use` | Type-safe CDP client |
| `openai` / `anthropic` | LLM integration |
| `pydantic` | Data validation |
| `typer` | CLI framework |

### 5.3 Performance

| Metric | Target |
|--------|--------|
| Action overhead | < 5s per step |
| DOM extraction | < 2s |
| Memory usage | < 500MB |

---

## 6. Non-Functional Requirements

### 6.1 Reliability

- Retry failed actions with exponential backoff
- Validate DOM state before actions
- Handle dynamic content with wait strategies

### 6.2 Robustness

- Multiple selector strategies per element
- Fallback to alternative selectors
- Error recovery and reporting

### 6.3 Extensibility

- Plugin system for custom actions
- Configurable LLM providers
- Custom output formatters

---

## 7. Success Metrics

| Metric | Criteria |
|--------|----------|
| Task completion | 90%+ success rate on standard flows |
| Selector reliability | 95%+ of generated selectors work |
| User adoption | Positive feedback from beta users |

---

## 8. Milestones

| Phase | Deliverable | Timeline |
|-------|-------------|----------|
| **Phase 1** | Browser session + basic CDP | Week 1 |
| **Phase 2** | DOM extraction + selectors | Week 2 |
| **Phase 3** | Tool system + actions | Week 3 |
| **Phase 4** | Agent loop + LLM | Week 4 |
| **Phase 5** | Context collection + export | Week 5 |
| **Phase 6** | CLI + polish | Week 6 |

---

## 9. Out of Scope (v1)

- Script generation (only context collection)
- Visual/screenshot-based element detection
- Multi-browser support (Chrome only)
- Parallel test execution
- Cloud execution

---

## 10. Open Questions

| Question | Options |
|----------|---------|
| Default LLM? | OpenAI GPT-4 / Anthropic Claude |
| Primary selector? | data-testid / CSS / XPath |
| Retry count? | 3 / 5 / configurable |
| Network capture? | All / same-origin only |

---

## 11. References

- [RFC-001: Heimdall Technical Design](./rfc-001-heimdall.md)
- [browser-use](https://github.com/browser-use/browser-use)
- [cdp-use](https://github.com/browser-use/cdp-use)
