"""Match rendered UI text against verified language-table records.

The client usually returns display text, not an ID. IDs select the bilingual
rules here; we also accept unresolved <string;ID> references when present.
"""
import html
import re
from src.game_text_catalog import TEXT_RECORDS, PORTAL_RECORDS, WORLD_RECORDS

TEXT_RECORDS = {**TEXT_RECORDS, **PORTAL_RECORDS, **WORLD_RECORDS}


def plain_text(value) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<(?:br|/p)\s*/?>", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", text)).strip()


def _normal(value) -> str:
    return re.sub(r"[\s!！。.,，:：]", "", plain_text(value)).casefold()


def matches_text(value, *text_ids) -> bool:
    reference = html.unescape(str(value or ""))
    reference = re.sub(r"</?center>", "", reference, flags=re.I).strip()
    for text_id in text_ids:
        if reference.casefold() in (text_id.casefold(), f"<string;{text_id}>".casefold()):
            return True
        for text in TEXT_RECORDS[text_id]:
            if text and _normal(value) == _normal(text):
                return True
    return False


def _prompt_form(value) -> str:
    text = plain_text(value)
    # Key/mouse images can be returned as resource tokens, markup or a literal X.
    text = re.sub(r"&(?:InputBindings|Icons)_[^&]+&", "", text, flags=re.I)
    text = re.sub(r"^(按下|点击|點擊|按)\s*[Xx]\s*(?:键|鍵)?", r"\1", text)
    text = re.sub(r"\bX\b", "", text, flags=re.I)
    text = re.sub(r"(?:[Xx]键|[Xx]鍵)", "", text)
    text = re.sub(r"^(?:按下|点击|點擊|按)\s*", "Press ", text)
    # Existing traditional-Chinese compatibility, without broad substring tests.
    text = text.translate(str.maketrans("進開傳採騎複體談", "进开传采骑复体谈"))
    return _normal(text)


_INTERACTION_IDS = {}
for _id, (_english, _translation) in TEXT_RECORDS.items():
    if _english.startswith("Press &InputBindings_NPCInteract&"):
        _match = re.search(r"\bto (Talk|Open|Collect|Enter|Ride|Teleport|Use Magic Raft)\b", _english)
        if _match:
            _kind = _match[1].lower().replace("use magic raft", "ride")
            _INTERACTION_IDS.setdefault(_kind, []).append(_id)

_INTERACTION_FORMS = {
    kind: {_prompt_form(text) for text_id in ids for text in TEXT_RECORDS[text_id] if text}
    for kind, ids in _INTERACTION_IDS.items()
}


def interaction_kind(value):
    if not plain_text(value):
        # A string reference has no plain text; allow it below, but not an empty value.
        if "<string;" not in html.unescape(str(value or "")):
            return None
    form = _prompt_form(value)
    for kind, ids in _INTERACTION_IDS.items():
        if matches_text(value, *ids):
            return kind
        if form in _INTERACTION_FORMS[kind]:
            return kind
    return None


def is_dungeon_entry_prompt(value) -> bool:
    return interaction_kind(value) == "enter"


def quest_has_action(value, action) -> bool:
    ids = {
        "defeat": ("WizardQuestGoals_Kill", "WizardQuestGoals_KillCollect"),
        "photomance": ("WizardQuestGoals_00000670", "WizardQuestGoals_00000671",
                       "WizardQuestGoals_00000672", "WizardQuestGoals_00000748"),
    }[action]
    text = plain_text(value).casefold()
    for text_id in ids:
        for label in TEXT_RECORDS[text_id]:
            if not label:
                continue
            label = plain_text(label).casefold()
            if re.search(r"(?<![a-z])" + re.escape(label) + r"(?![a-z])", text):
                return True
    return matches_text(value, *ids)


def split_quest_location(value):
    # Quest_*Chat records use 'in' / '地点：'. The same marker appears in
    # the user's quest-helper screenshot. Do not split arbitrary Chinese 在.
    text = plain_text(value)
    text = re.sub(r"\s*[（(]\s*\d+\s*(?:of|/|／)\s*\d+\s*[)）]\s*$", "", text, flags=re.I)
    parts = re.split(r"\s+in\s+|\s*(?:地点|地點)\s*[:：]\s*", text, maxsplit=1, flags=re.I)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (text, "")


def collect_object_name(value):
    objective, location = split_quest_location(value)
    if not location:
        return ""
    prefixes = ["Collect", "收集", "采集", "採集"]
    for text_id in ("WizardQuestGoals_00000018", "WizardQuestGoals_Gather", "WizardQuestGoals_Get"):
        prefixes.extend(t for t in TEXT_RECORDS[text_id] if t)
    for prefix in sorted(prefixes, key=len, reverse=True):
        if objective.casefold().startswith(prefix.casefold()):
            rest = objective[len(prefix):]
            if prefix.isascii() and rest and not rest[0].isspace():
                continue
            return rest.strip()
    # Preserve the original English one-word action fallback.
    return objective.split(" ", 1)[1].strip() if re.match(r"^[A-Za-z]+\s", objective) else ""


def portal_kind(value):
    if matches_text(value, "GUI2_00000398", "GUI2_00000399"):
        return "streamportal"
    if matches_text(value, "GUI2_00001319", "GUI2_00001320"):
        return "nanavator"
    if matches_text(value, "WizardGameObjects_00000070", "ZoneLocName_00000255"):
        return "world_gate"
    return None


def resolve_portal_destination(value, allowed):
    text = plain_text(value).casefold()
    if not text:
        return None
    matches = {}
    for destination in allowed:
        canonical = destination.casefold()
        aliases = {canonical}
        for english, translated in PORTAL_RECORDS.values():
            if plain_text(english).casefold() == canonical:
                aliases.add(plain_text(translated).casefold())
                aliases.add(re.sub(r"[（(][^()（）]*[)）]", "", plain_text(translated)).strip().casefold())
        for alias in aliases:
            if not alias:
                continue
            pattern = re.escape(alias)
            if alias.isascii():
                pattern = r"(?<![a-z])" + pattern + r"(?![a-z])"
            if re.search(pattern, text):
                matches[destination] = max(matches.get(destination, 0), len(alias))
    if len(matches) != 1:
        return None  # Unknown or ambiguous: never guess the first option.
    return next(iter(matches))


def quest_interaction_matches(objective, title):
    """Match the visible interaction's name to the tracked objective, not its zone."""
    goal, _ = split_quest_location(objective)
    name = plain_text(title).casefold().strip()
    goal = plain_text(goal).casefold()
    if not name or quest_has_action(objective, "defeat"):
        return False
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", goal))
