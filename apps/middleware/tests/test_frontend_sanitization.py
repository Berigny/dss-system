import re
import subprocess
from pathlib import Path


def _extract_sanitizer():
    """Pull the sanitizeResponseText function from the embedded script."""
    content = Path("routes/home.py").read_text()
    match = re.search(
        r"function sanitizeResponseText\([^)]*\)\s*{[\s\S]*?\n}", content
    )
    assert match, "sanitizeResponseText function not found in routes/home.py"
    return match.group(0)


def _run_node(script: str):
    completed = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Node execution failed: {completed.stderr or completed.stdout}"
        )
    return completed.stdout


def test_sanitize_response_text_removes_json_fence():
    sanitizer = _extract_sanitizer()
    input_text = 'Intro\\n```json\\n{"hidden":"yes"}\\n```\\nOutro'
    script = f"""{sanitizer}
const input = {input_text!r};
const output = sanitizeResponseText(input);
if (/```json/.test(output)) {{
    throw new Error('JSON fence was not removed');
}}
if (!output.includes('Intro') || !output.includes('Outro')) {{
    throw new Error('Expected visible text missing after sanitization');
}}
console.log(output);
"""
    stdout = _run_node(script)
    assert "hidden" not in stdout
