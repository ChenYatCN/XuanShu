import unittest
from src.interaction_prompts import is_dungeon_entry_prompt
from src.interaction_prompts import (
    TEXT_RECORDS, interaction_kind, matches_text, portal_kind,
    resolve_portal_destination, split_quest_location, collect_object_name, quest_has_action,
)


class DungeonPromptTests(unittest.TestCase):
    def test_english_and_chinese_instructions(self):
        for text in (
            "Press X to enter", "<center>Press <icon;X> to enter</center>",
            "点击 X 进入", "点击 <image;X> 进入", "按下 X 进入",
            "點擊 X 進入", "按下 X 進入", "点击 &lt;icon;X&gt; 进入",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_dungeon_entry_prompt(text))

    def test_other_interactions_do_not_start_dungeon_sync(self):
        for text in (None, "", "火占师之墓", "进入", "点击 X 对话", "按下 X 开始游戏", "Press X to talk"):
            with self.subTest(text=text):
                self.assertFalse(is_dungeon_entry_prompt(text))


class VerifiedLanguageTests(unittest.TestCase):
    def test_known_ids_have_verified_translations(self):
        self.assertEqual(TEXT_RECORDS['PetGames_Action_Go'], ['Go!', '开始重复！'])
        self.assertEqual(TEXT_RECORDS['GUI_NPCInteractNoClick'][1], '按下 &Icons_XKey& 交谈')

    def test_rendered_prompts_in_both_languages(self):
        cases = {
            'Press X to Talk': 'talk', '按下 X 交谈': 'talk',
            '按下 <icon;X> 或 <icon;LeftMouseClick> 交谈': 'talk',
            'Press X or <icon;mouse> to Collect': 'collect',
            '按X收集': 'collect', '按下 X 采集': 'collect',
            '点击X进入': 'enter', '按下 X 键进入': 'enter',
            '点击 X 打开': 'open', '按下 X 传送': 'teleport',
            '按下 X 使用魔法艇': 'ride', '按下 X 骑乘': 'ride',
            '按下 X 开始游戏': None, '准备交谈': None,
            'Failed to enter world': None, '': None,
        }
        for text, kind in cases.items():
            with self.subTest(text=text):
                self.assertEqual(interaction_kind(text), kind)

    def test_raw_ids_encoded_markup_and_mixed_language(self):
        self.assertEqual(interaction_kind('<center><string;GUI_NPCInteractText>'), 'talk')
        self.assertEqual(interaction_kind('&lt;string;GUI_EnterDoor&gt;'), 'enter')
        self.assertEqual(interaction_kind('gui_npcinteracttext'), 'talk')
        self.assertTrue(matches_text('<center>Go!', 'PetGames_Action_Go'))
        self.assertTrue(matches_text('开始重复！', 'PetGames_Action_Go'))
        self.assertFalse(matches_text('完成！', 'PetGames_Action_Go'))

    def test_quest_location_and_object_extraction(self):
        for text, location in (
            ('<center>Collect Cog in Triton Avenue (0 of 3)</center>', 'Triton Avenue'),
            ('收集 齿轮 地点：三叉大道 (0 of 3)', '三叉大道'),
            ('寻找 齿轮 地点: 三叉大道（0/3）', '三叉大道'),
            ('拜访 伊恩', ''),
        ):
            self.assertEqual(split_quest_location(text)[1], location)
        self.assertEqual(collect_object_name('收集 齿轮 地点：三叉大道 (0 of 3)'), '齿轮')
        self.assertEqual(collect_object_name('Collect Cog in Triton Avenue (0 of 3)'), 'Cog')
        self.assertEqual(collect_object_name('Collect Cog'), '')

    def test_quest_action_recognition(self):
        for text in ('Defeat Malorn in The Commons', '击败 马龙 地点：大基地', '击败并收集 齿轮'):
            self.assertTrue(quest_has_action(text, 'defeat'))
        for text in ('Press Z to Photomance a Photo of Tower', '按下 Z 来拍照', '取得这个目标的照片：高塔'):
            self.assertTrue(quest_has_action(text, 'photomance'))
        self.assertFalse(quest_has_action('Collect a photo', 'photomance'))

    def test_portal_titles_and_multiple_translations(self):
        self.assertEqual(portal_kind('世界之门'), 'world_gate')
        self.assertEqual(portal_kind('<center>导航器</center>'), 'nanavator')
        self.assertEqual(portal_kind('纳瓦托'), 'nanavator')
        self.assertEqual(portal_kind('流式传送门(Streamportal)'), 'streamportal')
        self.assertTrue(matches_text('魔法城', 'WorldNames_WizardCity'))
        self.assertTrue(matches_text('Dragonspyre', 'WorldNames_DragonSpire'))
        for text in ('焦糖城市', '卡拉梅尔市', 'Karamelle City'):
            self.assertEqual(resolve_portal_destination(text, ['karamelle city', 'sweetzburg']), 'karamelle city')

    def test_empty_partial_and_ambiguous_places_never_choose_first(self):
        choices = ['outer athanor', 'inner athanor']
        for text in ('', None, 'Athanor', 'Unknown place', 'Inner Athanor or Outer Athanor'):
            self.assertIsNone(resolve_portal_destination(text, choices))
        self.assertEqual(resolve_portal_destination('阿萨诺尔内地', choices), 'inner athanor')
