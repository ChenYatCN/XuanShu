"""Bilingual collect-target matching, independent of language/instance ID numbering."""
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from thefuzz import fuzz

from src.interaction_prompts import plain_text, split_quest_location, quest_has_action


_COUNT = re.compile(r"[（(]\s*(\d+)\s*(?:of|/|／)\s*(\d+)\s*[)）]\s*$", re.I)
_ACTIONS = re.compile(
    r"^(?:collect|gather|find|search(?:\s+for)?|pick(?:\s+up)?|take|recover|retrieve|catch|steal|break|destroy|get|use|open)\s+"
    r"|^(?:收集|聚集|采集|採集|寻找|尋找|找到|搜寻|搜尋|搜索|拾取|捡起|撿起|拾起|获取|獲取|获得|獲得|取回|恢复|恢復|找回|检索|檢索|捕捉|捕获|捕獲|拿取|偷取|窃取|竊取|破坏|破壞|毁坏|毀壞|打破|得到|摧毁|摧毀|使用|打开|打開)\s*"
    r"|^(?:偷|取|抓)\s+",
    re.I,
)


def normalize_name(value):
    text = plain_text(value).casefold()
    text = re.sub(r"^(?:the|a|an)\s+", "", text)
    # English objectives often pluralize a singular display name.
    words = re.findall(r"[a-z]+|[0-9]+|[\u3400-\u9fff]+", text)
    words = [w[:-1] if len(w) > 3 and w.endswith('s') and not w.endswith(('ss', 'us', 'is')) else w for w in words]
    return ''.join(words)


@dataclass(frozen=True)
class CollectGoal:
    target: str
    location: str
    current: int | None
    total: int | None

    @property
    def key(self):
        return normalize_name(self.target), normalize_name(self.location)


def parse_collect_goal(text):
    text = plain_text(text)
    if not text or quest_has_action(text, 'defeat'):
        return None
    count = _COUNT.search(text)
    current, total = (int(count[1]), int(count[2])) if count else (None, None)
    if count:
        if total <= 0 or current > total:
            return None
        text = text[:count.start()].strip()
    objective, location = split_quest_location(text)
    action = _ACTIONS.match(objective)
    if not action:
        return None
    target = objective[action.end():].strip()
    return CollectGoal(target, location, current, total) if target else None


def count_increased(before, after):
    return bool(before and after and before.key == after.key
                and before.total is not None and before.total == after.total
                and before.current < after.current <= after.total)


class CollectNames:
    def __init__(self, rows):
        self.by_id = {}
        self.by_name = defaultdict(set)
        for code, english, chinese in rows:
            aliases = {normalize_name(english), normalize_name(chinese)} - {''}
            if code:
                self.by_id[code.casefold()] = aliases
            for name in aliases:
                self.by_name[name].update(aliases)

    def aliases(self, name):
        key = normalize_name(name)
        return (self.by_name.get(key, set()) | {key}) - {''}

    def score(self, target, display='', code='', internal=''):
        expected = self.aliases(target)
        actual = self.aliases(display) if display else set()
        # A language ID can assist a missing label, but must not override a
        # contradictory live display name after a game/translation update.
        coded = self.by_id.get(str(code).casefold(), set())
        if not actual or actual.intersection(coded):
            actual = actual | coded
        if expected.intersection(actual):
            return 100
        best = 0
        for left in expected:
            for right in actual:
                # Avoid weak fuzzy matches between unrelated short Chinese names.
                if min(len(left), len(right)) >= 5 and bool(re.search('[\u3400-\u9fff]', left)) == bool(re.search('[\u3400-\u9fff]', right)):
                    ratio = fuzz.ratio(left, right)
                    if ratio >= 85:
                        best = max(best, ratio)
        # Original internal-name fallback, compared with English aliases too.
        # Keep the readable name, remove only a world prefix / numbered suffix.
        cleaned = re.sub(r'^[A-Za-z]{2,3}[-_]', '', str(internal))
        cleaned = re.sub(r'[-_]?\d+$', '', cleaned)
        internal_key = normalize_name(cleaned)
        for name in expected:
            if len(name) >= 5 and name == internal_key:
                best = max(best, 95)
            elif len(name) >= 7 and len(internal_key) >= 7:
                ratio = fuzz.ratio(name, internal_key)
                if ratio >= 85:
                    best = max(best, min(ratio, 90))
        return best


@lru_cache(maxsize=1)
def collect_names():
    path = Path(__file__).with_name('data') / 'collect_names.json'
    return CollectNames(json.loads(path.read_text(encoding='utf-8'))['records'])
