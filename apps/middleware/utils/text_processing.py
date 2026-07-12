import re

# Examples: WX-7F2A91B3-123, ATT-ABC-456-T002, chat-demo:PL-Conv-001-1, MD-Rule-XYZ-2
COORD_KIND_PATTERN = r"(?:COORD|WX|ATT|PL-Conv|PL-Claim|PL-Taxon|EV|MD-Rule|MD-Run|MD-Reset)"
COORD_PATTERN = re.compile(
    r"(?:[\w-]+(?::[\w-]+)?:)?"
    r"(?:"
    + COORD_KIND_PATTERN +
    r"-[A-Za-z0-9]+-\d+(?:-(?:[A-Za-z0-9]+))*"
    r"(?:-(?:T|I|A|V|D|P)\d{3})?"
    r")"
)
LITE_COORD_PATTERN = re.compile(
    r"(?:[\w-]+(?::[\w-]+)?:)?"
    r"(?:"
    + COORD_KIND_PATTERN +
    r"-[A-Za-z0-9]+(?:-(?:[A-Za-z0-9]+))*"
    r"(?:-(?:T|I|A|V|D|P)\d{3})?"
    r")"
)


def extract_coords_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    coords: list[str] = []
    seen: set[str] = set()

    def _append(coord_value: str) -> None:
        if not coord_value:
            return
        if coord_value in seen:
            return
        seen.add(coord_value)
        coords.append(coord_value)

    for match in COORD_PATTERN.finditer(text):
        _append(match.group(0))
    for match in LITE_COORD_PATTERN.finditer(text):
        raw_coord = match.group(0)
        _append(raw_coord)
        coord = normalize_coord_token(raw_coord) or raw_coord
        if coord != raw_coord:
            _append(coord)
    return coords


def normalize_coord_token(token: str) -> str | None:
    if not token:
        return None
    token = token.strip()
    if not token:
        return None
    if COORD_PATTERN.fullmatch(token):
        return token
    # Try to upgrade lite form like ATT-ABC-123 or WX-FOO-999 without the required -<digits>.
    base_match = re.match(
        r"(?:([\w-]+(?::[\w-]+)?)?:)?"
        r"((?:COORD|WX|ATT|PL-Conv|PL-Claim|PL-Taxon|EV|MD-Rule|MD-Run|MD-Reset)"
        r"-[A-Za-z0-9]+(?:-(?:[A-Za-z0-9]+))*"
        r"(?:-(?:T|I|A|V|D|P)\d{3})?)$",
        token,
    )
    if not base_match:
        return token
    namespace = base_match.group(1)
    bare = base_match.group(2)
    upgraded = f"{bare}-0"
    if namespace:
        upgraded = f"{namespace}:{upgraded}"
    return upgraded


def truncate_text(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    if len(value) <= limit:
        return value
    return f"{value[: max(0, limit - 1)].rstrip()}…"
