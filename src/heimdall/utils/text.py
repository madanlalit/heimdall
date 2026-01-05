"""
Text utility functions.
"""


def extract_json_from_markdown(text: str) -> str:
    """
    Extract JSON content from markdown code blocks or raw text.

    Args:
        text: Input text that might contain markdown code blocks

    Returns:
        The extracted JSON string
    """
    text = text.strip()

    # Try to find JSON code block
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end != -1:
            return text[start:end].strip()

    # Try to find generic code block
    if "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end != -1:
            return text[start:end].strip()

    # Handle optional "json" prefix without backticks
    if text.startswith("json"):
        text = text[4:].strip()

    return text
