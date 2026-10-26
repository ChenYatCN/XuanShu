"""Curated bilingual hints, not replacements for chat text.

Rows: category, English, preferred Chinese, aliases, usage context, source.
Keep uncertain translations identical to English until reviewed. Add only
source-backed names; do not copy conflicting entries from whole language tables.
All src/... source references below are relative to XuanShu; the XuanYi copy
is self-contained and must never import XuanShu or read its runtime cache.

`snapshot:` IDs refer to the existing collect_cache language snapshot built from
Locale_en-US-root.wad(.d), not a live lookup. `bundled:` IDs are in
src/data/collect_names.json. These modules require no game files at runtime.
Abbreviation expansions are conditional hints, never unconditional rewrites.
Sync this file and chat_translation_rules.py with XuanYi/xuanyi when updating.
"""


GLOSSARY = (
    ('spell', 'Stormblade', '暴风之剑', (), 'the spell, not a player name', 'snapshot:Spells_Stormblade'),
    ('spell', 'Fireblade', '火焰之刃', (), 'the spell', 'snapshot:Spells_Fireblade'),
    ('spell', 'Iceblade', '冰霜之刃', (), 'the spell', 'snapshot:Spells_Iceblade'),
    ('spell', 'Deathblade', '亡灵之刃', (), 'the spell', 'snapshot:Spells_00000073'),
    ('spell', 'Lifeblade', '生命之刃', (), 'the spell', 'snapshot:Spells_00000143'),
    ('spell', 'Mythblade', '神话之刃', (), 'the spell', 'snapshot:Spells_00000081'),
    ('spell', 'Balanceblade', '平衡之刃', (), 'the spell', 'snapshot:Spells_00000138'),
    ('spell', 'Tower Shield', '塔盾', (), 'the spell', 'snapshot:Spells_00000060'),
    ('spell', 'Satyr', '半羊人', (), 'the healing spell, not an ordinary creature reference', 'snapshot:Spells_00000064'),
    ('spell', 'Scarecrow', '稻草人', (), 'the spell, not an ordinary object', 'snapshot:Spells_Scarecrow'),
    ('spell', 'Forest Lord', '森林领主', (), 'the spell', 'snapshot:Spells_00000766'),
    ('spell', 'Deer Knight', '鹿骑士', (), 'the spell', 'snapshot:Spells_00001150'),
    ('spell', 'Potent Trap', '强力陷阱', (), 'the enchantment spell', 'snapshot:Spells_00001181'),
    ('spell', 'Sharpened Blade', '尖刃', (), 'the enchantment spell', 'snapshot:Spells_00001182'),
    ('spell', 'Mass Feint', '集体佯攻', (), 'the spell; does not establish a translation for Feint', 'snapshot:Spells_00001600'),
    ('equipment', 'Woolen Hood', '羊毛风帽', (), 'the complete equipment name only', 'bundled:Items_00000000'),
    ('equipment', 'Fur Lined Boots', '毛皮衬里长靴', (), 'the complete equipment name only', 'bundled:Items_00000002'),
    ('dungeon', 'Waterworks', '自来水厂', (), 'the dungeon, not ordinary waterworks', 'snapshot:WizardZone_00000876;SigilText_00000096'),
    ('location', 'The Commons', '广场', (), 'the Wizard City location', 'src/ibao_locations.py:LOCATION_ALIASES;WizardZone_TheCommons'),
    ('location', 'Unicorn Way', '独角兽大道', (), 'the location', 'src/ibao_locations.py:LOCATION_ALIASES;WizardZone_UnicornWay'),
    ('location', 'Triton Avenue', '海神大街', (), 'the location', 'src/ibao_locations.py:LOCATION_ALIASES;WizardZone_TritonAvenue'),
    ('location', 'Ravenwood', '拉文霍德', (), 'the location', 'src/ibao_locations.py:LOCATION_ALIASES;WizardZone_Ravenwood'),
    ('school', 'Fire', '火焰系', (), 'only a magic school, not ordinary fire or part of another name', 'snapshot:CharCreation_CLASS_SCHOOL_0'),
    ('school', 'Ice', '冰霜系', (), 'only a magic school, not ordinary ice or part of another name', 'snapshot:CharCreation_CLASS_SCHOOL_1'),
    ('school', 'Storm', '风暴系', (), 'only a magic school, not weather or part of another name', 'snapshot:CharCreation_CLASS_SCHOOL_2'),
    ('school', 'Myth', '神圣系', (), 'only a magic school, not an ordinary myth', 'snapshot:CharCreation_CLASS_SCHOOL_3'),
    ('school', 'Life', '生命系', (), 'only a magic school, not ordinary life or part of another name', 'snapshot:CharCreation_CLASS_SCHOOL_4'),
    ('school', 'Death', '亡灵系', (), 'only a magic school, not ordinary death or part of another name', 'snapshot:CharCreation_CLASS_SCHOOL_5'),
    ('school', 'Balance', '平衡系', (), 'only a magic school, not ordinary balance', 'snapshot:CharCreation_CLASS_SCHOOL_6'),
    ('mechanic', 'Pip', '魔力点', ('Pips',), 'spell-cost resource; prefer the spell-description table over Help_Icons25', 'snapshot:Spells-MadLibs_00000094'),
    ('mechanic', 'Power Pip', '超级魔豆', ('Power Pips',), 'spell-cost resource, not the chance to gain it', 'snapshot:Spells-MadLibs_00000056'),
    ('mechanic', 'Blade', '刀刃', ('Blades',), 'combat damage charm only, not equipment or part of a longer name', 'snapshot:Help_00000109'),
    ('mechanic', 'Trap', '陷阱', ('Traps',), 'combat damage ward only, not an ordinary trap or part of a longer name', 'snapshot:Help_iconsSpellCardArtTrap'),
    ('abbreviation', 'Treasure Card', '宝藏卡', ('TC', 'TCs', 'Treasure Cards'), 'TC only when referring to treasure cards, not initials or a player name', 'snapshot:GUI_TreasureCard;src/deck_encoder.py:tc deck'),
    ('abbreviation', 'Damage Over Time', '持续性伤害', ('DoT',), 'DoT only when referring to damage over time, not initials or an ordinary dot', 'snapshot:Help_00000103;DoT is the English initialism'),
    ('spell', 'Feint', 'Feint', (), 'PENDING: conflicting spell translations; retain English', 'snapshot:Spells_00000157=伪装;Spells_00000158=盛宴'),
    ('spell', 'Tempest', 'Tempest', (), 'PENDING: conflicting spell translations; retain English for the spell', 'snapshot:Spells_00000011=风暴;Spells_00003184=暴风雨'),
    ('dungeon', 'Castle Darkmoor', 'Castle Darkmoor', (), 'PENDING: conflicting location translations; retain English', 'src/ibao_locations.py:暗夜沼泽城堡; snapshot:WizardZone_00001110=黑暗荒原城堡'),
    ('abbreviation', 'AoE', 'AoE', (), 'area-of-effect abbreviation; no verified Chinese label, retain original spelling', 'src/config_combat.py:any<damage&aoe>'),
)
