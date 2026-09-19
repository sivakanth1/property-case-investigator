class ToolError(Exception):
    """A tool call was well-formed but cannot be satisfied; returned to the model as a structured error."""
