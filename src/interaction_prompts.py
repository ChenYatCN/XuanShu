"""Match rendered UI text against verified language-table records.

The client usually returns display text, not an ID. IDs select the bilingual
rules here; we also accept unresolved <string;ID> references when present.
"""
import html
import re
from functools import lru_cache
from src.game_text_catalog import TEXT_RECORDS, PORTAL_RECORDS, WORLD_RECORDS
from src.game_text_snapshot import snapshot_for_client,text_values

TEXT_RECORDS = {**TEXT_RECORDS, **PORTAL_RECORDS, **WORLD_RECORDS}


def plain_text(value) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<(?:br|/p)\s*/?>", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", text)).strip()


def _normal(value) -> str:
    return re.sub(r"[\s!！。.,，:：]", "", plain_text(value)).casefold()


def _reference(value):
    reference = html.unescape(str(value or ""))
    return re.sub(r"</?center>", "", reference, flags=re.I).strip().casefold()


def _id_matches(value,text_ids):
    reference=_reference(value)
    return any(reference in (text_id.casefold(),f'<string;{text_id}>'.casefold()) for text_id in text_ids)


def matches_text(value, *text_ids, client=None) -> bool:
    if _id_matches(value,text_ids):
        return True
    for text_id in text_ids:
        for text in text_values(text_id,client):
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
        _match = re.search(r"\bto (Talk|Open|Collect|Enter|Ride|Teleport|Climb|Use Magic Raft)\b", _english)
        if _match:
            _kind = _match[1].lower().replace("use magic raft", "ride")
            _INTERACTION_IDS.setdefault(_kind, []).append(_id)

@lru_cache(maxsize=None)
def _interaction_forms(snapshot):
    return {kind:frozenset(_prompt_form(text) for text_id in ids for text in snapshot.values(text_id) if text)-{''}
            for kind,ids in _INTERACTION_IDS.items()}


def _interaction_candidates(value,client=None):
    explicit={kind for kind,ids in _INTERACTION_IDS.items() if _id_matches(value,ids)}
    if explicit:
        return explicit
    form=_prompt_form(value)
    forms=_interaction_forms(snapshot_for_client(client))
    return {kind for kind,ids in _INTERACTION_IDS.items()
            if matches_text(value,*ids,client=client) or form in forms[kind]}


def interaction_kind(value, *, client=None):
    if not plain_text(value):
        # A string reference has no plain text; allow it below, but not an empty value.
        if "<string;" not in html.unescape(str(value or "")):
            return None
    candidates=_interaction_candidates(value,client)
    return next(iter(candidates)) if len(candidates)==1 else None


def is_dungeon_entry_prompt(value, *, client=None) -> bool:
    return interaction_kind(value,client=client) == "enter"


_QUEST_ACTION_IDS=(('WizardQuestGoals_Kill','WizardQuestGoals_KillCollect'),
                  ('WizardQuestGoals_00000667',),
                  ('WizardQuestGoals_00000670','WizardQuestGoals_00000671',
                   'WizardQuestGoals_00000672','WizardQuestGoals_00000748'))


def _quest_labels(text_id,client):
    # Duplicate labels cannot choose between distinct existing action groups.
    # Resource IDs still select their original, fixed group unambiguously.
    group=next(ids for ids in _QUEST_ACTION_IDS if text_id in ids)
    other={_normal(label) for ids in _QUEST_ACTION_IDS if ids is not group
           for other_id in ids for label in text_values(other_id,client) if label}
    return tuple(label for label in text_values(text_id,client) if label and _normal(label) not in other)


def _quest_matches(value,ids,client):
    return (_id_matches(value,ids) or any(_normal(value)==_normal(label)
            for text_id in ids for label in _quest_labels(text_id,client)))


def photomancy_action(value, *, require_z=False, client=None) -> bool:
    """Read the photo instruction prefix, preserving an explicitly rendered Z key."""
    ids = ("WizardQuestGoals_00000670", "WizardQuestGoals_00000672")
    if not require_z:
        ids += ("WizardQuestGoals_00000671", "WizardQuestGoals_00000748")
    if _quest_matches(value,ids,client):
        return True

    def form(raw):
        raw = html.unescape(str(raw or ''))
        raw = re.sub(r"<(?:icon|image);Z>", "Z", raw, flags=re.I)
        raw = re.sub(r"&Icons_ZKey&", "Z", raw, flags=re.I)
        text = plain_text(raw).casefold().replace('鍵', '键')
        # Chinese HUD markup often leaves no spaces around the key or target.
        return re.sub(r"(?<=[\u3400-\u9fff])\s+|\s+(?=[\u3400-\u9fff])", "", text)

    text = form(value)
    for text_id in ids:
        for label in _quest_labels(text_id,client):
            label = form(label)
            if label and re.match(re.escape(label) + (r"(?![a-z])" if label[-1].isascii() else ''), text):
                return True
    press = r"press\s+z(?:\s+key)?\s+to\s+"
    return bool(re.match(r"^(?:" + press + (r")" if require_z else r")?")
        + r"take\s+(?:a\s+)?(?:picture|photo)\b", text))


def quest_has_action(value, action, *, client=None) -> bool:
    if action == 'photomance':
        return photomancy_action(value,client=client)
    ids = {
        "defeat": ("WizardQuestGoals_Kill", "WizardQuestGoals_KillCollect"),
        "pet_mode": ("WizardQuestGoals_00000667",),
    }[action]
    text = plain_text(value).casefold()
    for text_id in ids:
        for label in _quest_labels(text_id,client):
            if not label:
                continue
            label = plain_text(label).casefold()
            if re.search(r"(?<![a-z])" + re.escape(label) + r"(?![a-z])", text):
                return True
    return _quest_matches(value,ids,client)


def split_quest_location(value):
    # Quest_*Chat records use 'in' / '地点：'. The same marker appears in
    # the user's quest-helper screenshot. Do not split arbitrary Chinese 在.
    text = plain_text(value)
    text = re.sub(r"\s*[（(]\s*\d+\s*(?:of|/|／)\s*\d+\s*[)）]\s*$", "", text, flags=re.I)
    parts = re.split(r"\s+in\s+|\s*(?:地点|地點)\s*[:：]\s*", text, maxsplit=1, flags=re.I)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (text, "")


def collect_object_name(value, *, client=None):
    objective, location = split_quest_location(value)
    if not location:
        return ""
    prefixes = ["Collect", "收集", "采集", "採集"]
    for text_id in ("WizardQuestGoals_00000018", "WizardQuestGoals_Gather", "WizardQuestGoals_Get"):
        prefixes.extend(t for t in text_values(text_id,client) if t)
    for prefix in sorted(prefixes, key=len, reverse=True):
        if objective.casefold().startswith(prefix.casefold()):
            rest = objective[len(prefix):]
            if prefix.isascii() and rest and not rest[0].isspace():
                continue
            return rest.strip()
    # Preserve the original English one-word action fallback.
    return objective.split(" ", 1)[1].strip() if re.match(r"^[A-Za-z]+\s", objective) else ""


def portal_kind(value, *, client=None):
    kinds={kind for kind,ids in (
        ('streamportal',('GUI2_00000398','GUI2_00000399')),
        ('nanavator',('GUI2_00001319','GUI2_00001320')),
        ('world_gate',('WizardGameObjects_00000070','ZoneLocName_00000255')))
        if matches_text(value,*ids,client=client)}
    return next(iter(kinds)) if len(kinds)==1 else None


def resolve_portal_destination(value, allowed, *, client=None):
    text = plain_text(value).casefold()
    if not text:
        return None
    matches = {}
    for destination in allowed:
        canonical = destination.casefold()
        aliases = {canonical}
        for text_id,(english,_) in PORTAL_RECORDS.items():
            if plain_text(english).casefold() == canonical:
                for translated in text_values(text_id,client):
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


def quest_interaction_matches(objective, title, *, client=None):
    """Match the visible interaction's name to the tracked objective, not its zone."""
    goal, _ = split_quest_location(objective)
    name = plain_text(title).casefold().strip()
    goal = plain_text(goal).casefold()
    if not name or quest_has_action(objective, "defeat",client=client):
        return False
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", goal))


def quest_interaction_correlates(objective, title, action, *, client=None, task_client=None):
    """Specific objective words may corroborate an ordinary current-target approach.

    This is weaker than a name match and never grants input on its own.
    Resource names, location labels and generic UI verbs are not task semantics.
    """
    kind = interaction_kind(action,client=client)
    if kind not in ('talk', 'open') and not re.fullmatch(
            r'press(?:or|或)?(?:to)?(?:use|使用)', _prompt_form(action)):
        return False
    if any(quest_has_action(objective, value,client=task_client or client) for value in ('defeat', 'photomance', 'pet_mode')):
        return False
    text = html.unescape(str(objective or ''))
    text = re.sub(r'<[^>]*>|&[^&]*\.dds&|\b[\w_]+\.dds\b', ' ', text, flags=re.I)
    goal, _ = split_quest_location(text)
    generic = {'使用', '点击', '點擊', '交谈', '交談', '对话', '對話', '进入', '進入',
               '按下', '打开', '打開', '地点', '地點', '任务', '目标',
               'use', 'click', 'talk', 'enter', 'press', 'open', 'to', 'in', 'at',
               'the', 'a', 'an', 'or', 'with', 'task', 'npc'}
    words = re.findall(r'[\u3400-\u9fff]+|[a-z]+', goal.casefold())
    name = _normal(title)
    for word in words:
        word = re.sub(r'^(?:使用|点击|點擊|交谈|交談|进入|進入|按下|在)', '', word)
        if word in generic or len(word) < (4 if word.isascii() else 2):
            continue
        pattern = re.escape(word)
        if word.isascii():
            pattern = r'(?<![a-z])' + pattern + r'(?![a-z])'
            # Preserve English word boundaries instead of removing spaces.
            if re.search(pattern, plain_text(title).casefold()):
                return True
        elif re.search(pattern, name):
            return True
    return False


def task_transition_prompt(title, action, *, client=None):
    """Classify transport instructions, without a map/door-name allowlist.

    Generic Use alone describes ordinary objects too. It additionally needs a
    destination-shaped title; Enter/Interact/transport instructions do not.
    """
    if len(_interaction_candidates(action,client))>1:
        return False
    if interaction_kind(action,client=client) in ('enter', 'ride', 'teleport', 'climb'):
        return True
    form = _prompt_form(action)
    if re.fullmatch(r'press(?:or|或)?(?:to)?(?:interact|互动|交互)', form):
        return True
    return bool(re.fullmatch(r'press(?:or|或)?(?:to)?(?:use|使用)', form)
        and re.match(r'^(?:去|前往|前去|传送至|傳送至)\s*\S|'
                     r'^(?:go\s+to|travel\s+to|teleport\s+to|to)\s+\S',
                     plain_text(title), flags=re.I))


_OBJECT_ACTION_IDS = tuple(text_id for text_id, (english, _) in TEXT_RECORDS.items()
    if re.match(r'^Press &InputBindings_NPCInteract&(?: or &Icons_LeftMouseClick&)? to '
                r'(?:Take|Use|Open|Pull|Read|Place(?!/))\b', english, flags=re.I))
@lru_cache(maxsize=None)
def _object_action_forms(snapshot):
    return frozenset(_prompt_form(text) for text_id in _OBJECT_ACTION_IDS
                     for text in snapshot.values(text_id) if text)-{''}


def task_object_prompt(title, action, *, client=None):
    """Known object instructions only; transport and NPC/collect remain separate."""
    if (len(_interaction_candidates(action,client))>1
            or task_transition_prompt(title, action,client=client) or portal_kind(title,client=client)
            or interaction_kind(action,client=client) in ('talk', 'collect', 'enter', 'ride', 'teleport')):
        return False
    return (matches_text(action, *_OBJECT_ACTION_IDS,client=client)
            or _prompt_form(action) in _object_action_forms(snapshot_for_client(client))
            or bool(re.fullmatch(r'press(?:or|或)?(?:to)?(?:take|use|open|pull|read|拿走|使用|打开|拖动|阅读)',
                                 _prompt_form(action))))
