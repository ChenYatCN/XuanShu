import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from wizwalker.memory import DuelPhase
from wizwalker.utils import Rectangle
from wizwalker.extensions.wizsprinter.combat_backends.combat_api import NamedSpell
from src.combat_targeting import (
    BattlefieldFallbackCombatCard, FusionSelectionIncomplete,
    TargetingSprintyCombat, UpstreamSprintyCombat,
)


def window(x, name='Card1'):
    return SimpleNamespace(name=AsyncMock(return_value=name),
        is_visible=AsyncMock(return_value=True), maybe_graphical_spell=AsyncMock(return_value=object()),
        scale_to_client=AsyncMock(return_value=Rectangle(x, 100, x + 64, 196)))


class FusionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.text = 'Select Fusion result'
        self.left, self.right, self.pet = window(100, 'Card2'), window(190), window(500, 'PetCard')
        self.prompt = SimpleNamespace(is_visible=AsyncMock(return_value=True),
            maybe_text=AsyncMock(side_effect=lambda: self.text))
        self.selection = SimpleNamespace(is_visible=AsyncMock(return_value=True))
        self.hand = SimpleNamespace(is_visible=AsyncMock(return_value=True),
            get_windows_with_type=AsyncMock(return_value=[self.right, self.left, self.pet]))
        self.client = SimpleNamespace(in_battle=AsyncMock(return_value=True),
            duel=SimpleNamespace(duel_phase=AsyncMock(return_value=DuelPhase.planning)),
            mouse_handler=SimpleNamespace(click_window=AsyncMock(side_effect=self.selected)))
        self.combat = object.__new__(TargetingSprintyCombat)
        self.combat.client = self.client
        self.combat._fusion_ui = (self.selection, self.prompt, self.hand)
        self.combat._spell_check_boxes = ['old hand']

    async def selected(self, selected):
        self.assertIs(selected, self.right)
        self.text = 'Choose a spell'

    async def test_right_choice_for_english_chinese_and_key(self):
        for text in ('Select Fusion result', '<center>选择融合结果</center>',
                     '<string;GUI3_SelectTieredFusionHelp>'):
            self.text = text
            self.client.mouse_handler.click_window.reset_mock()
            with patch('src.combat_targeting.asyncio.sleep', AsyncMock()):
                await self.combat._complete_fusion_result()
                await self.combat._complete_fusion_result()
            self.client.mouse_handler.click_window.assert_awaited_once_with(self.right)
            self.assertIsNone(self.combat._spell_check_boxes)

    async def test_material_selection_and_normal_hand_are_never_clicked(self):
        for text in ('Choose a spell', 'non-grey spell to fuse them.', '<string;GUI3_FusionSelectHelp>'):
            self.text = text
            await self.combat._complete_fusion_result()
        self.client.mouse_handler.click_window.assert_not_awaited()
        self.hand.get_windows_with_type.assert_not_awaited()

    async def test_unconfirmed_selection_clicks_once_then_stops(self):
        self.client.mouse_handler.click_window.side_effect = None
        with patch('src.combat_targeting.asyncio.sleep', AsyncMock()):
            with self.assertRaises(FusionSelectionIncomplete):
                await self.combat._complete_fusion_result()
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.right)

    async def test_ambiguous_choices_do_not_guess(self):
        self.hand.get_windows_with_type.return_value = [self.left]
        with patch('src.combat_targeting.asyncio.sleep', AsyncMock()):
            with self.assertRaises(FusionSelectionIncomplete):
                await self.combat._complete_fusion_result()
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_leaving_planning_prevents_click(self):
        self.client.duel.duel_phase.return_value = DuelPhase.execution
        with self.assertRaises(FusionSelectionIncomplete):
            await self.combat._complete_fusion_result()
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_waits_for_stable_cards_before_clicking(self):
        delay = AsyncMock()

        async def selected_after_wait(selected):
            self.assertEqual(delay.await_count, 4)
            await self.selected(selected)

        self.client.mouse_handler.click_window.side_effect = selected_after_wait
        with patch('src.combat_targeting.asyncio.sleep', delay):
            await self.combat._complete_fusion_result()
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.right)
        self.assertEqual(delay.await_args_list[:4], [unittest.mock.call(0.1)] * 4)

    async def test_selection_interval_tracks_combat_cast_time(self):
        self.combat.config = SimpleNamespace(cast_time=0.3)
        delay = AsyncMock()

        async def selected_after_configured_wait(selected):
            self.assertEqual(delay.await_count, 6)
            await self.selected(selected)

        self.client.mouse_handler.click_window.side_effect = selected_after_configured_wait
        with patch('src.combat_targeting.asyncio.sleep', delay):
            await self.combat._complete_fusion_result()
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.right)

    async def test_leaving_planning_during_wait_never_clicks(self):
        async def phase_changes(_):
            self.client.duel.duel_phase.return_value = DuelPhase.execution

        with patch('src.combat_targeting.asyncio.sleep', AsyncMock(side_effect=phase_changes)):
            with self.assertRaises(FusionSelectionIncomplete):
                await self.combat._complete_fusion_result()
        self.client.mouse_handler.click_window.assert_not_awaited()

    async def test_moving_cards_restart_stability_wait(self):
        self.left.scale_to_client.side_effect = [Rectangle(90, 100, 154, 196)] + [Rectangle(100, 100, 164, 196)] * 49
        delay = AsyncMock()

        async def selected_after_motion(selected):
            self.assertGreaterEqual(delay.await_count, 5)
            await self.selected(selected)

        self.client.mouse_handler.click_window.side_effect = selected_after_motion
        with patch('src.combat_targeting.asyncio.sleep', delay):
            await self.combat._complete_fusion_result()
        self.client.mouse_handler.click_window.assert_awaited_once_with(self.right)

    async def test_incomplete_fusion_does_not_replay_round(self):
        self.combat._handle_fresh_round = AsyncMock(side_effect=FusionSelectionIncomplete('pending'))
        await self.combat.handle_round()
        self.combat._handle_fresh_round.assert_awaited_once()

    async def test_get_cards_resolves_choice_before_returning_hand(self):
        card = BattlefieldFallbackCombatCard(self.combat, self.left)
        async def read_hand(_):
            self.assertEqual(self.text, 'Choose a spell')
            return [card]
        with patch.object(UpstreamSprintyCombat, 'get_cards', read_hand), \
                patch('src.combat_targeting.asyncio.sleep', AsyncMock()):
            self.assertEqual(await self.combat.get_cards(), [card])

    async def test_quoted_names_use_internal_name_not_resource_filename(self):
        self.text = 'Choose a spell'
        card = SimpleNamespace(name=AsyncMock(return_value='Glowbug_Squall'))
        self.combat.get_castable_cards = AsyncMock(return_value=[card])
        self.assertIs(await self.combat.try_get_spell(NamedSpell('Glowbug')), card)
        self.assertIs(await self.combat.try_get_spell(NamedSpell('Glowbug_Squall', True)), card)
        self.assertIsNone(await self.combat.try_get_spell(NamedSpell('Glowbug Squall - T01 - TEMP', True)))

    async def test_missing_material_or_epic_never_casts_fusion_base(self):
        from src.config_combat import StrCombatConfigProvider
        import wizwalker.extensions.wizsprinter.sprinty_combat as runtime
        self.text = 'Choose a spell'
        self.combat.config = StrCombatConfigProvider('Musicology[Shrike][epic] | pass')
        move = self.combat.config.config.infinite_rounds[0].priorities[0]
        for missing in ('material', 'epic', 'grayed_material'):
            with self.subTest(missing=missing):
                base, material, epic = [SimpleNamespace(
                    name=AsyncMock(return_value=name), is_castable=AsyncMock(return_value=True),
                    is_enchanted=AsyncMock(return_value=False), cast=AsyncMock()) for name in
                    ('Sound of Musicology - T1', 'Shadow Creature Shrike', 'Epic')]
                hand = [base, epic] if missing == 'material' else [base, material]
                if missing == 'grayed_material':
                    hand = [base, material, epic]
                    material.is_castable.return_value = False
                self.combat.get_cards = AsyncMock(return_value=hand)
                with patch.object(runtime, 'is_enchantable', AsyncMock(return_value=True)):
                    self.assertFalse(await self.combat.try_execute_config(move))
                for card in hand:
                    card.cast.assert_not_awaited()
                self.assertFalse(self.combat._fusion_cast_active)

    async def test_generic_damage_keeps_fusion_base_but_casts_another_card(self):
        from src.config_combat import StrCombatConfigProvider
        import wizwalker.extensions.wizsprinter.sprinty_combat as runtime
        self.text = 'Choose a spell'
        self.combat.config = StrCombatConfigProvider(
            'Musicology[Shrike][epic] | any<damage> | pass', cast_time=0)
        moves = self.combat.config.config.infinite_rounds[0].priorities
        base, other = [SimpleNamespace(name=AsyncMock(return_value=name),
            is_castable=AsyncMock(return_value=True), is_enchanted=AsyncMock(return_value=False),
            cast=AsyncMock()) for name in ('Sound of Musicology - T1', 'Tempest')]
        hand = [base, other]
        other.cast.side_effect = lambda *args, **kwargs: hand.remove(other)
        self.combat.get_cards = AsyncMock(side_effect=lambda: list(hand))
        self.combat.try_get_config_target = AsyncMock(return_value=None)
        with (patch.object(runtime, 'is_enchantable', AsyncMock(return_value=True)),
              patch.object(runtime, 'does_card_contain_reqs', AsyncMock(return_value=True)),
              patch.object(runtime, 'card_requires_target_selection', AsyncMock(return_value=False)),
              patch.object(runtime, 'card_is_multi_target', AsyncMock(return_value=False))):
            self.assertFalse(await self.combat.try_execute_config(moves[0]))
            self.assertTrue(await self.combat.try_execute_config(moves[1]))
        base.cast.assert_not_awaited()
        other.cast.assert_awaited_once()
        self.assertEqual(hand, [base])

    async def test_resource_filename_reserves_base_without_changing_name_matching(self):
        from src.config_combat import StrCombatConfigProvider
        import wizwalker.extensions.wizsprinter.sprinty_combat as runtime
        self.text = 'Choose a spell'
        self.combat.config = StrCombatConfigProvider(
            '"Sound of Musicology - T01 - Base"["Shadow Creature Shrike"][epic] | any<damage>')
        base = SimpleNamespace(name=AsyncMock(return_value='Sound of Musicology - T1'),
                               is_castable=AsyncMock(return_value=True))
        self.combat.get_cards = AsyncMock(return_value=[base])
        moves = self.combat.config.config.infinite_rounds[0].priorities
        self.assertFalse(await self.combat.try_execute_config(moves[0]))
        with patch.object(runtime, 'does_card_contain_reqs', AsyncMock(return_value=True)):
            self.assertIsNone(await self.combat.try_get_spell(moves[1].move.card))

    async def test_keep_applies_to_named_materials_chains_and_explicit_rounds(self):
        from src.config_combat import StrCombatConfigProvider
        self.text = 'Choose a spell'
        for config in ('Musicology[Shrike][epic] & pass | pass',
                       '{1} Musicology[Shrike][epic]\npass'):
            with self.subTest(config=config):
                self.combat.config = StrCombatConfigProvider(config)
                material = SimpleNamespace(name=AsyncMock(return_value='Shadow Creature Shrike'),
                                           is_castable=AsyncMock(return_value=True))
                self.combat.get_cards = AsyncMock(return_value=[material])
                self.assertIsNone(await self.combat.try_get_spell(NamedSpell('Shrike')))
                # Explicit discard/read operations remain available.
                self.assertIs(await self.combat.try_get_spell(NamedSpell('Shrike'), castable=False), material)

    async def test_single_enchant_keeps_existing_behavior(self):
        from src.config_combat import StrCombatConfigProvider
        self.combat.config = StrCombatConfigProvider('Musicology[epic] | any<damage>')
        self.assertEqual(self.combat._fusion_spells_to_keep(), [])
        self.combat.get_castable_cards = AsyncMock(return_value=[SimpleNamespace(
            name=AsyncMock(return_value='Sound of Musicology - T1'))])
        self.assertIsNotNone(await self.combat.try_get_spell(NamedSpell('Musicology')))

    async def test_generic_epic_damage_cannot_touch_fusion_base(self):
        from src.config_combat import StrCombatConfigProvider
        import wizwalker.extensions.wizsprinter.sprinty_combat as runtime
        self.combat.config = StrCombatConfigProvider('Musicology[Shrike][epic] | any<damage>[epic]')
        base, epic = [SimpleNamespace(name=AsyncMock(return_value=name),
            is_castable=AsyncMock(return_value=True), cast=AsyncMock()) for name in
            ('Sound of Musicology - T1', 'Epic')]
        self.combat.get_cards = AsyncMock(return_value=[base, epic])
        self.combat.try_get_config_target = AsyncMock(return_value=None)
        move = self.combat.config.config.infinite_rounds[0].priorities[1]
        with (patch.object(runtime, 'is_enchantable', AsyncMock(return_value=True)),
              patch.object(runtime, 'does_card_contain_reqs', AsyncMock(
                  side_effect=lambda card, template: card is base))):
            self.assertFalse(await self.combat.try_execute_config(move))
        base.cast.assert_not_awaited()
        epic.cast.assert_not_awaited()

    async def test_early_epic_skips_fusion_base_and_enchants_other_card(self):
        from src.config_combat import StrCombatConfigProvider
        import wizwalker.extensions.wizsprinter.sprinty_combat as runtime
        self.text = 'Choose a spell'
        self.combat.config = StrCombatConfigProvider(
            'Epic @ spell(any<damage>) | Musicology[Shrike][epic] | pass', cast_time=0)
        move = self.combat.config.config.infinite_rounds[0].priorities[0]
        for has_other in (False, True):
            with self.subTest(has_other=has_other):
                base, other, epic = [SimpleNamespace(name=AsyncMock(return_value=name),
                    is_castable=AsyncMock(return_value=name == 'Epic'),
                    is_enchanted=AsyncMock(return_value=False), cast=AsyncMock()) for name in
                    ('Sound of Musicology - T1', 'Tempest', 'Epic')]
                hand = [base, other, epic] if has_other else [base, epic]
                async def enchant(target, **kwargs):
                    self.assertIs(target, other)
                    hand.remove(epic)
                epic.cast.side_effect = enchant
                self.combat.get_cards = AsyncMock(side_effect=lambda: list(hand))
                self.combat.get_num_card_windows = AsyncMock(side_effect=lambda: len(hand))
                self.combat.cur_card_count = len(hand)
                with (patch.object(runtime, 'is_enchantable', AsyncMock(return_value=True)),
                      patch.object(runtime, 'does_card_contain_reqs', AsyncMock(
                          side_effect=lambda card, template: card is not epic)),
                      patch.object(runtime, 'card_requires_target_selection', AsyncMock(return_value=False)),
                      patch.object(runtime, 'card_is_multi_target', AsyncMock(return_value=False))):
                    self.assertEqual(await self.combat.try_execute_config(move), has_other)
                base.cast.assert_not_awaited()
                if has_other:
                    epic.cast.assert_awaited_once()
                else:
                    epic.cast.assert_not_awaited()
                self.assertFalse(self.combat._protect_spell_target)

    async def test_fusion_right_epic_cast_chain_in_runtime_and_packaged_executor(self):
        from src.config_combat import StrCombatConfigProvider
        import wizwalker.extensions.wizsprinter.sprinty_combat as runtime

        compat_path = Path(__file__).resolve().parents[1] / 'libs/wizsprinter/wizwalker/extensions/wizsprinter/sprinty_combat.py'
        spec = importlib.util.spec_from_file_location(
            'wizwalker.extensions.wizsprinter._fusion_test_compat', compat_path)
        compat = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(compat)
        move = StrCombatConfigProvider('Glowbug[Shrike][epic] | pass').config.infinite_rounds[0].priorities[0]

        for backend, side in ((backend, side) for backend in (runtime, compat) for side in ('left', 'right')):
            with self.subTest(executor=backend.__name__, side=side):
                events = []

                def card(name):
                    result = object.__new__(BattlefieldFallbackCombatCard)
                    result.name = AsyncMock(return_value=name)
                    result.is_castable = AsyncMock(return_value=True)
                    result.is_enchanted = AsyncMock(return_value=False)
                    return result

                base, material, epic, fused = [card(name) for name in
                    ('Glowbug_Squall', 'Shadow Creature Shrike', 'Epic', 'Fused_Glowbug')]
                hand = [base, material, epic]
                self.text = 'Choose a spell'
                self.combat.config = StrCombatConfigProvider('Glowbug[Shrike][epic] | pass', cast_time=0)
                self.combat.cur_card_count = 3
                self.client.fusion_result_side = side
                self.combat.try_get_config_target = AsyncMock(return_value=None)

                async def fuse(target, **kwargs):
                    self.assertIs(target, base)
                    events.append('fuse')
                    self.text = 'Select Fusion result'

                async def choose(selected):
                    self.assertIs(selected, self.left if side == 'left' else self.right)
                    events.append(side)
                    self.text = 'Choose a spell'
                    hand[:] = [fused, epic]

                async def enchant(target, **kwargs):
                    self.assertIs(target, fused)
                    events.append('epic')
                    fused.is_enchanted.return_value = True
                    hand[:] = [fused]

                async def cast(target, **kwargs):
                    self.assertTrue(await fused.is_enchanted())
                    events.append('cast')
                    hand.clear()

                async def read_hand(_):
                    return list(hand)

                material.cast = AsyncMock(side_effect=fuse)
                epic.cast = AsyncMock(side_effect=enchant)
                fused.cast = AsyncMock(side_effect=cast)
                self.client.mouse_handler.click_window.side_effect = choose
                with patch.object(UpstreamSprintyCombat, 'get_cards', read_hand), \
                        patch.object(runtime, 'is_enchantable', AsyncMock(return_value=True)), \
                        patch.object(backend, 'card_requires_target_selection', AsyncMock(return_value=False)), \
                        patch.object(backend, 'card_is_multi_target', AsyncMock(return_value=False)), \
                        patch('src.combat_targeting.asyncio.sleep', AsyncMock()), \
                        patch.object(UpstreamSprintyCombat, 'try_execute_config', backend.SprintyCombat.try_execute_config):
                    self.assertTrue(await self.combat.try_execute_config(move))
                self.assertEqual(events, ['fuse', side, 'epic', 'cast'])
                self.assertEqual(self.combat.cur_card_count, 1)

    def setup_early_fusion(self, config, *, grayed_result=False, missing_epic=False):
        from src.config_combat import StrCombatConfigProvider
        from wizwalker.extensions.wizsprinter.combat_backends.combat_api import TargetType
        import wizwalker.extensions.wizsprinter.sprinty_combat as runtime
        self.text = 'Choose a spell'
        self.events = []
        self.combat.config = StrCombatConfigProvider(config, cast_time=0)
        self.combat._fusion_preparation_pending = True
        self.combat._prepared_fusions = {}
        self.combat.try_get_config_target = AsyncMock(side_effect=lambda target:
            target.extra_data if target is not None and target.target_type is TargetType.type_spell else None)

        def card(name, castable=True):
            result = object.__new__(BattlefieldFallbackCombatCard)
            result.name = AsyncMock(return_value=name)
            result.is_castable = AsyncMock(return_value=castable)
            result.is_enchanted = AsyncMock(return_value=False)
            result.cast = AsyncMock()
            return result

        self.base, self.material, self.epic, self.fused, self.buff = [card(name) for name in
            ('Sound of Musicology - T1', 'Shadow Creature Shrike', 'Epic',
             'Fused_Musicology', 'Blade')]
        self.fused.is_castable.return_value = not grayed_result
        self.cards = [self.base, self.material, self.buff]
        if not missing_epic:
            self.cards.append(self.epic)
        self.combat.cur_card_count = len(self.cards)
        self.combat.get_num_card_windows = AsyncMock(side_effect=lambda: len(self.cards))

        async def fuse(target, **kwargs):
            self.assertIs(target, self.base)
            self.events.append('fuse')
            self.text = 'Select Fusion result'

        async def choose(selected):
            side = getattr(self.client, 'fusion_result_side', 'right')
            self.assertIs(selected, self.left if side == 'left' else self.right)
            self.events.append(side)
            self.text = 'Choose a spell'
            self.cards.remove(self.base)
            self.cards.remove(self.material)
            self.cards.append(self.fused)

        async def enchant(target, **kwargs):
            self.assertIs(target, self.fused)
            self.events.append('epic')
            self.fused.is_enchanted.return_value = True
            self.cards.remove(self.epic)

        async def cast_fused(target, **kwargs):
            self.events.append('cast')
            self.cards.remove(self.fused)

        async def cast_buff(target, **kwargs):
            self.events.append('buff')
            self.cards.remove(self.buff)

        self.material.cast.side_effect = fuse
        self.client.mouse_handler.click_window.side_effect = choose
        self.epic.cast.side_effect = enchant
        self.fused.cast.side_effect = cast_fused
        self.buff.cast.side_effect = cast_buff
        for patcher in (
            patch.object(UpstreamSprintyCombat, 'get_cards', AsyncMock(side_effect=lambda: list(self.cards))),
            patch.object(runtime, 'is_enchantable', AsyncMock(side_effect=lambda card: not card.is_enchanted.return_value)),
            patch.object(runtime, 'does_card_contain_reqs', AsyncMock(side_effect=lambda card, _: card is self.fused or card is self.base)),
            patch.object(runtime, 'card_requires_target_selection', AsyncMock(return_value=False)),
            patch.object(runtime, 'card_is_multi_target', AsyncMock(return_value=False)),
            patch('src.combat_targeting.asyncio.sleep', AsyncMock()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        return self.combat.config.config.infinite_rounds[0].priorities

    async def test_early_fusion_keeps_higher_buff_priority_and_config_unchanged(self):
        moves = self.setup_early_fusion('Blade | Musicology[Shrike][epic] | pass')
        original = repr(self.combat.config.config)
        self.assertTrue(await self.combat.try_execute_config(moves[0]))
        self.assertEqual(self.events, ['fuse', 'right', 'buff'])
        self.assertEqual(repr(self.combat.config.config), original)
        self.assertIn(self.fused, self.cards)
        self.epic.cast.assert_not_awaited()
        self.base.cast.assert_not_awaited()
        self.assertFalse(self.combat._fusion_preparation_pending)

    async def test_early_epic_on_grayed_fused_result_then_delayed_cast_no_refusion(self):
        moves = self.setup_early_fusion(
            'Epic @ spell(any<damage>) | Musicology[Shrike][epic] | pass', grayed_result=True)
        self.assertTrue(await self.combat.try_execute_config(moves[0]))
        self.assertEqual(self.events, ['fuse', 'right', 'epic'])
        self.assertFalse(await self.combat.try_execute_config(moves[1]))
        # Next planning round: result can now be cast, Epic/material are gone.
        self.combat._fusion_preparation_pending = True
        self.fused.is_castable.return_value = True
        self.assertFalse(await self.combat.try_execute_config(moves[0]))
        self.assertTrue(await self.combat.try_execute_config(moves[1]))
        self.assertEqual(self.events, ['fuse', 'right', 'epic', 'cast'])
        self.material.cast.assert_awaited_once()
        self.epic.cast.assert_awaited_once()
        self.assertFalse(self.combat._fusion_cast_active)
        self.assertFalse(self.combat._protect_spell_target)

    async def test_early_fusion_without_epic_waits_then_enchants_and_casts(self):
        moves = self.setup_early_fusion('Musicology[Shrike][epic] | pass', missing_epic=True)
        self.assertFalse(await self.combat.try_execute_config(moves[0]))
        self.assertEqual(self.events, ['fuse', 'right'])
        self.fused.cast.assert_not_awaited()
        self.cards.append(self.epic)
        self.combat._fusion_preparation_pending = True
        self.assertTrue(await self.combat.try_execute_config(moves[0]))
        self.assertEqual(self.events, ['fuse', 'right', 'epic', 'cast'])
        self.material.cast.assert_awaited_once()

    async def test_active_round_only_and_conditions_are_respected(self):
        moves = self.setup_early_fusion('Blade | ?(self.health < 25%) Musicology[Shrike][epic]\n{2} Musicology[Shrike][epic]')
        self.combat.evaluate_condition = AsyncMock(return_value=False)
        self.assertTrue(await self.combat.try_execute_config(moves[0]))
        self.assertEqual(self.events, ['buff'])
        self.material.cast.assert_not_awaited()
        self.combat.evaluate_condition.assert_awaited_once()

    async def test_fusion_result_shared_between_infinite_and_explicit_rounds(self):
        moves = self.setup_early_fusion('Blade | Musicology[Shrike][epic]\n{2} Musicology[Shrike][epic]', grayed_result=True)
        await self.combat.try_execute_config(moves[0])
        self.fused.is_castable.return_value = True
        self.combat._fusion_preparation_pending = True
        explicit = self.combat.config.config.specific_rounds[2].priorities[0]
        self.assertTrue(await self.combat.try_execute_config(explicit))
        self.assertEqual(self.events, ['fuse', 'right', 'buff', 'epic', 'cast'])
        self.material.cast.assert_awaited_once()

    async def test_grayed_original_fuses_then_early_epic_waits_for_castability(self):
        moves = self.setup_early_fusion(
            'Epic @ spell(any<damage>) | Musicology[Shrike][epic]', grayed_result=True)
        self.base.is_castable.return_value = False
        self.assertTrue(await self.combat.try_execute_config(moves[0]))
        self.assertFalse(await self.combat.try_execute_config(moves[1]))
        self.assertEqual(self.events, ['fuse', 'right', 'epic'])
        self.base.cast.assert_not_awaited()
        self.fused.cast.assert_not_awaited()
        self.fused.is_castable.return_value = True
        self.combat._fusion_preparation_pending = True
        self.assertTrue(await self.combat.try_execute_config(moves[1]))
        self.assertEqual(self.events, ['fuse', 'right', 'epic', 'cast'])
        self.material.cast.assert_awaited_once()
        self.epic.cast.assert_awaited_once()

    async def test_grayed_template_base_fuses_without_overtaking_buff(self):
        moves = self.setup_early_fusion('Blade | any<damage>[Shrike][epic]')
        self.base.is_castable.return_value = False
        self.assertTrue(await self.combat.try_execute_config(moves[0]))
        self.assertEqual(self.events, ['fuse', 'right', 'buff'])
        self.material.cast.assert_awaited_once()
        self.base.cast.assert_not_awaited()
        self.fused.cast.assert_not_awaited()

    async def test_both_grayed_cards_fuse_then_early_epic_and_delayed_cast(self):
        moves = self.setup_early_fusion(
            'Epic @ spell(any<damage>) | Musicology[Shrike][epic]', grayed_result=True)
        self.base.is_castable.return_value = False
        self.material.is_castable.return_value = False
        self.assertTrue(await self.combat.try_execute_config(moves[0]))
        self.assertEqual(self.events, ['fuse', 'right', 'epic'])
        self.assertFalse(await self.combat.try_execute_config(moves[1]))
        self.base.cast.assert_not_awaited()
        self.fused.cast.assert_not_awaited()
        self.fused.is_castable.return_value = True
        self.combat._fusion_preparation_pending = True
        self.assertTrue(await self.combat.try_execute_config(moves[1]))
        self.assertEqual(self.events, ['fuse', 'right', 'epic', 'cast'])
        self.material.cast.assert_awaited_once()
        self.epic.cast.assert_awaited_once()

    async def test_grayed_base_cannot_fuse_without_material(self):
        moves = self.setup_early_fusion('Epic @ spell(any<damage>) | Musicology[Shrike][epic]')
        self.base.is_castable.return_value = False
        self.cards.remove(self.material)
        self.assertFalse(await self.combat.try_execute_config(moves[0]))
        self.assertFalse(await self.combat.try_execute_config(moves[1]))
        self.material.cast.assert_not_awaited()
        self.epic.cast.assert_not_awaited()
        self.base.cast.assert_not_awaited()

    async def test_unconfirmed_early_fusion_sends_one_material_input_and_aborts(self):
        moves = self.setup_early_fusion('Blade | Musicology[Shrike][epic]')
        self.base.is_castable.return_value = False
        self.material.is_castable.return_value = False
        self.material.cast.side_effect = None
        with self.assertRaises(FusionSelectionIncomplete):
            await self.combat.try_execute_config(moves[0])
        self.material.cast.assert_awaited_once()
        self.buff.cast.assert_not_awaited()
        self.assertTrue(self.combat._round_input_started)
        self.assertEqual(self.combat._prepared_fusions, {})

    async def test_early_fusion_chains_and_left_selection(self):
        moves = self.setup_early_fusion('Blade | Musicology[Shrike][epic] & pass')
        self.base.is_castable.return_value = False
        self.material.is_castable.return_value = False
        self.client.fusion_result_side = 'left'
        self.assertTrue(await self.combat.try_execute_config(moves[0]))
        self.assertEqual(self.events, ['fuse', 'left', 'buff'])

    async def test_new_battle_clears_prepared_results(self):
        self.combat._prepared_fusions = {('old', 'material'): 'old fused'}
        with patch.object(UpstreamSprintyCombat, 'handle_combat', AsyncMock()):
            await self.combat.handle_combat()
        self.assertEqual(self.combat._prepared_fusions, {})

    async def test_consumed_fusion_mapping_is_removed(self):
        moves = self.setup_early_fusion('Blade | Musicology[Shrike][epic]')
        await self.combat.try_execute_config(moves[0])
        self.cards.remove(self.fused)
        self.assertIsNone(await self.combat._prepared_fusion_card(moves[1].move))
        self.assertEqual(self.combat._prepared_fusions, {})

    async def test_real_round_prepares_inside_mouse_and_ownership_before_priorities(self):
        from src.automation_ownership import get_client_automation_ownership
        self.setup_early_fusion('Blade | Musicology[Shrike][epic]')
        # handle_round resets its UI cache; supply the actual existing path.
        self.selection.get_child_by_name = AsyncMock(side_effect=lambda name:
            self.prompt if name == 'HelpText' else self.hand if name == 'Hand' else self.selection)
        self.client.root_window = self.selection
        mouse = MagicMock()
        mouse.click_window = self.client.mouse_handler.click_window
        self.client.mouse_handler = mouse
        original_cast = self.material.cast.side_effect

        async def owned_fusion(target, **kwargs):
            self.assertTrue(get_client_automation_ownership(self.client).locked)
            mouse.__aenter__.assert_awaited_once()
            await original_cast(target, **kwargs)

        self.material.cast.side_effect = owned_fusion
        self.combat.round_number = AsyncMock(return_value=1)
        self.combat._inspect_deck_once = AsyncMock()
        self.combat.get_card_counts = AsyncMock(return_value=(0, 0))
        self.combat.get_client_member = AsyncMock(return_value=SimpleNamespace(is_stunned=AsyncMock(return_value=False)))
        self.combat.turn_adjust = self.combat.rel_round_offset = 0
        self.combat.prev_card_count = 0
        self.combat.was_pass = self.combat.had_first_round = False
        await self.combat.handle_round()
        self.assertEqual(self.events, ['fuse', 'right', 'buff'])
        self.assertEqual(self.combat.prev_card_count, 3)
        self.assertFalse(get_client_automation_ownership(self.client).locked)
        mouse.__aexit__.assert_awaited_once()

    async def test_packaging_executor_casts_prepared_result_without_material(self):
        moves = self.setup_early_fusion('Blade | Musicology[Shrike][epic]')
        await self.combat.try_execute_config(moves[0])
        compat_path = Path(__file__).resolve().parents[1] / 'libs/wizsprinter/wizwalker/extensions/wizsprinter/sprinty_combat.py'
        spec = importlib.util.spec_from_file_location('wizwalker.extensions.wizsprinter._fusion_test_compat', compat_path)
        compat = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(compat)
        with (patch.object(UpstreamSprintyCombat, 'try_execute_config', compat.SprintyCombat.try_execute_config),
              patch.object(compat, 'card_requires_target_selection', AsyncMock(return_value=False)),
              patch.object(compat, 'card_is_multi_target', AsyncMock(return_value=False))):
            self.assertTrue(await self.combat.try_execute_config(moves[1]))
        self.assertEqual(self.events, ['fuse', 'right', 'buff', 'epic', 'cast'])
        self.material.cast.assert_awaited_once()

    async def test_generic_damage_cannot_consume_prepared_result_before_fusion_priority(self):
        moves = self.setup_early_fusion('any<damage>[epic] | Musicology[Shrike][epic]')
        self.assertFalse(await self.combat.try_execute_config(moves[0]))
        self.assertEqual(self.events, ['fuse', 'right'])
        self.fused.cast.assert_not_awaited()
        self.epic.cast.assert_not_awaited()
        self.assertTrue(await self.combat.try_execute_config(moves[1]))
        self.assertEqual(self.events, ['fuse', 'right', 'epic', 'cast'])

    async def test_early_material_click_error_aborts_without_normal_priority(self):
        moves = self.setup_early_fusion('Blade | Musicology[Shrike][epic]')
        self.material.cast.side_effect = ValueError('click uncertain')
        with self.assertRaises(FusionSelectionIncomplete):
            await self.combat.try_execute_config(moves[0])
        self.material.cast.assert_awaited_once()
        self.buff.cast.assert_not_awaited()
        self.assertTrue(self.combat._round_input_started)

    async def test_phase_end_after_early_fusion_never_casts_following_priority(self):
        moves = self.setup_early_fusion('Blade | Musicology[Shrike][epic]')
        async def phase_end(*args, **kwargs):
            self.client.duel.duel_phase.return_value = DuelPhase.execution
        self.material.cast.side_effect = phase_end
        with self.assertRaises(FusionSelectionIncomplete):
            await self.combat.try_execute_config(moves[0])
        self.material.cast.assert_awaited_once()
        self.buff.cast.assert_not_awaited()
