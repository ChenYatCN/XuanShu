import unittest
from unittest.mock import patch

from src import chat_translation_rules as rules


class Wizard101RulesTests(unittest.TestCase):
    def test_whole_words_phrases_case_and_chinese_chat(self):
        for message, expected in (
                ('STORMBLADE then tower   shield', ('Stormblade = 暴风之剑', 'Tower Shield = 塔盾')),
                ('请用暴风之剑和塔盾', ('Stormblade = 暴风之剑', 'Tower Shield = 塔盾')),
                ('Sharpened Blade then Power Pips', ('Sharpened Blade = 尖刃', 'Power Pip = 超级魔豆'))):
            with self.subTest(message=message):
                prompt = rules.build_translation_prompt(message)
                for term in expected:
                    self.assertIn(term, prompt)
        prompt = rules.build_translation_prompt('StormbladeX icecream wildlife trapping ATC DOTcom')
        for term in ('Stormblade =', 'Ice =', 'Life =', 'Trap =', 'Treasure Card =', 'Damage Over Time ='):
            self.assertNotIn(term, prompt)

    def test_longest_phrase_does_not_select_generic_shorter_term(self):
        prompt = rules.build_translation_prompt('Power Pip, Potent Trap, Sharpened Blade')
        for term in ('\n- Pip =', '\n- Trap =', '\n- Blade ='):
            self.assertNotIn(term, prompt)

    def test_links_and_formatting_do_not_trigger_terms(self):
        for message in (
                'https://example.com/Stormblade?q=TC',
                'www.example.com/Stormblade?q=TC',
                'ftp://example.com/Stormblade?q=TC',
                '<Stormblade> &Stormblade& $Stormblade$ {Stormblade} %Stormblade%'):
            with self.subTest(message=message):
                prompt = rules.build_translation_prompt(message)
                self.assertNotIn('Stormblade =', prompt)
                self.assertNotIn('Treasure Card =', prompt)

    def test_school_and_abbreviation_hints_are_context_conditional(self):
        prompt = rules.build_translation_prompt('Life and TC, DoT')
        self.assertIn('Life = 生命系', prompt)
        self.assertIn('school', prompt)
        self.assertIn('ordinary meaning', prompt)
        self.assertIn('Treasure Card = 宝藏卡', prompt)
        self.assertIn('Damage Over Time = 持续性伤害', prompt)
        self.assertIn('ambiguous', prompt)

    def test_fingerprint_changes_with_terms_and_prompt(self):
        old = rules.translation_rules_fingerprint()
        with patch.object(rules, 'GLOSSARY', rules.GLOSSARY + (
                ('test', 'New Spell', '测试法术', (), 'spell only', 'test fixture'),)):
            self.assertNotEqual(rules.translation_rules_fingerprint(), old)
        with patch.object(rules, 'BASE_RULES', rules.BASE_RULES + ' Updated rule.'):
            self.assertNotEqual(rules.translation_rules_fingerprint(), old)
        with patch.object(rules, 'DIRECTION_AUTO', rules.DIRECTION_AUTO + ' Updated direction.'):
            self.assertNotEqual(rules.translation_rules_fingerprint(), old)

    def test_curated_terms_have_traceable_sources_and_conflicts_are_preserved(self):
        categories = set()
        for category, en, zh, aliases, context, source in rules.GLOSSARY:
            self.assertTrue(en and zh and context and source)
            categories.add(category)
        self.assertTrue({'spell', 'equipment', 'dungeon', 'location', 'school', 'mechanic',
                         'abbreviation'} <= categories)
        prompt = rules.build_translation_prompt('Feint, Tempest, AoE, Castle Darkmoor')
        for term in ('Feint', 'Tempest', 'AoE', 'Castle Darkmoor'):
            self.assertIn(f'{term} = {term}', prompt)
