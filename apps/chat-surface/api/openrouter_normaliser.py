from typing import Any, Dict


def normalise_openrouter_response(raw: Dict[str, Any], model: str) -> Dict[str, Any]:
    """
    Convert *any* OpenRouter response into the one shape the front-end expects:
    {
      "text": str,               # assistant content (markdown or plain)
      "model": str,              # canonical model id
      "tokens": {                # optional, best-effort
          "prompt": int,
          "completion": int,
          "total":    int
      },
      "cost":   float,           # optional, best-effort
      "raw":    Dict[str, Any]   # original payload (for debugging)
    }
    If the response is unusable we return {"text": "", "model": model, "error": reason}
    """
    out: Dict[str, Any] = {"text": "", "model": model, "raw": raw}

    try:
        # 1. Find the message block -------------------------------------------------
        choice = raw["choices"][0]  # OR/and candidates: ["choices"][0]["candidate"]
        msg = choice.get("message") or choice.get("delta") or {}
        content = msg.get("content") or ""

        # 2. Some models (e.g. moonshot-v1) wrap content inside a list --------------
        if isinstance(content, list):
            # OpenAI-style multimodal: [{"type":"text","text":"..."}, {"type":"image_url",...}]
            text_parts = [c["text"] for c in content if c.get("type") == "text"]
            content = "".join(text_parts)

        out["text"] = content.strip()

        # 3. Token usage -------------------------------------------------------------
        usage = raw.get("usage") or {}
        out["tokens"] = {
            "prompt": usage.get("prompt_tokens", 0),
            "completion": usage.get("completion_tokens", 0),
            "total": usage.get("total_tokens", 0),
        }

        # 4. Cost (OpenRouter puts it in $.usage or top-level) ----------------------
        if "total_cost" in raw:
            out["cost"] = float(raw["total_cost"])
        elif usage and "total_cost" in usage:
            out["cost"] = float(usage["total_cost"])
        else:
            out["cost"] = 0.0

    except Exception as exc:
        # Never explode; let caller decide what to do
        out["error"] = str(exc)

    return out
