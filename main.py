from __future__ import annotations

import os
from collections import defaultdict

from cg.api import (
    AreaType,
    Card,
    CardType,
    EnergyType,
    Observation,
    OptionType,
    Pokemon,
    SelectContext,
    all_card_data,
    to_observation_class,
)


class CardId:
    KYOGRE = 721
    SNOVER = 722
    MEGA_ABOMASNOW_EX = 723

    ABRA = 741
    KADABRA = 742
    ALAKAZAM = 743

    MAKUHITA = 673
    HARIYAMA = 674
    LUNATONE = 675
    SOLROCK = 676
    RIOLU = 677
    MEGA_LUCARIO_EX = 678
    DWEBBLE = 344
    CRUSTLE = 345

    BASIC_FIGHTING_ENERGY = 6
    DUSK_BALL = 1102
    SWITCH = 1123
    PREMIUM_POWER_PRO = 1141
    FIGHTING_GONG = 1142
    POKE_PAD = 1152
    HERO_CAPE = 1159
    BOSS_ORDERS = 1182
    CARMINE = 1192
    LILLIE_DETERMINATION = 1227
    GRAVITY_MOUNTAIN = 1252

    LUMIOSE_CITY = 1267
    LILLIES_PEARL = 1172
    LEGACY_ENERGY = 12


MEGA_BRAVE_ATTACK_ID = 983
LOW_DECK_THRESHOLD = 10

# Abra/Kadabra-kill priority: deny the Psychic/Alakazam line (Lucario is x2 weak to Psychic).
# Moderate value tuned by sweep — high values over-commit and skip better KOs. Gated to Psychic decks.
ABRA_KILL_BONUS = 400
KADABRA_KILL_BONUS = 400


SCRIPT_DIR = os.path.dirname(os.path.abspath(globals().get("__file__", "main.py")))
DECK_LIST_PATH = os.path.join(SCRIPT_DIR, "lucario_deck.csv")
if not os.path.exists(DECK_LIST_PATH):
    DECK_LIST_PATH = "/kaggle_simulations/agent/lucario_deck.csv"
with open(DECK_LIST_PATH, "r", encoding="utf-8") as deck_file:
    starting_deck_list = [int(line) for line in deck_file.read().splitlines() if line.strip()]


all_cards = all_card_data()
card_data_by_id = {card.cardId: card for card in all_cards}


class AttackPlan:
    def __init__(
        self,
        attacker_index: int = -1,
        target_index: int = -1,
        attack_index: int = -1,
        target_remaining_hp: int = -1,
        needs_energy_attach: bool = False,
    ):
        self.attacker_index = attacker_index
        self.target_index = target_index
        self.attack_index = attack_index
        self.target_remaining_hp = target_remaining_hp
        self.needs_energy_attach = needs_energy_attach


current_plan = AttackPlan()
last_seen_turn = -1
ability_used_this_turn = False


def get_card(obs: Observation, area: AreaType, index: int, player_index: int) -> Pokemon | Card | None:
    player = obs.current.players[player_index]
    match area:
        case AreaType.DECK:
            return obs.select.deck[index]
        case AreaType.HAND:
            return player.hand[index]
        case AreaType.DISCARD:
            return player.discard[index]
        case AreaType.ACTIVE:
            return player.active[index]
        case AreaType.BENCH:
            return player.bench[index]
        case AreaType.PRIZE:
            return player.prize[index]
        case AreaType.STADIUM:
            return obs.current.stadium[index]
        case AreaType.LOOKING:
            return obs.current.looking[index]
        case _:
            return None


def prize_value(pokemon: Pokemon) -> int:
    data = card_data_by_id[pokemon.id]
    prizes = 3 if data.megaEx else 2 if data.ex else 1
    for energy_card in pokemon.energyCards:
        if energy_card.id == CardId.LEGACY_ENERGY:
            prizes -= 1
    for tool_card in pokemon.tools:
        if tool_card.id == CardId.LILLIES_PEARL and "Lillie" in data.name:
            prizes -= 1
    return max(0, prizes)


def target_priority_score(pokemon: Pokemon) -> int:
    data = card_data_by_id[pokemon.id]
    score = prize_value(pokemon) * 1000
    score += len(pokemon.energies) * 150
    score += len(pokemon.tools) * 100
    if data.stage2:
        score += 250
    elif data.stage1:
        score += 130

    if pokemon.id in {144, 322, 323, 337}:  # low-value support Pokemon
        score -= 200
    if pokemon.id == CardId.SNOVER:
        score += 950   # KO Snover before it evolves into Mega Abomasnow (Fighting wall); ported from 1084
    elif pokemon.id == CardId.MEGA_ABOMASNOW_EX:
        score += 250
    if pokemon.id == CardId.ABRA:
        score += ABRA_KILL_BONUS    # deny the Alakazam (Psychic) line before it OHKOs our Lucario
    elif pokemon.id == CardId.KADABRA:
        score += KADABRA_KILL_BONUS
    if pokemon.id == CardId.RIOLU:
        score += 800   # deny opponent's Lucario line by KOing Riolu (mirror edge, ported from 1084)
    elif pokemon.id == CardId.MEGA_LUCARIO_EX:
        score += 100
    if pokemon.id == 112 and len(pokemon.energies) >= 1:  # Munkidori
        score += 300
    score += pokemon.hp
    return score


class LucarioPolicy:
    def __init__(self, obs: Observation):
        self.obs = obs
        self.state = obs.current
        self.select = obs.select
        self.context = self.select.context
        self.my_player_index = self.state.yourIndex
        self.opponent_player_index = 1 - self.my_player_index
        self.me = self.state.players[self.my_player_index]
        self.opponent = self.state.players[self.opponent_player_index]
        self.my_prizes_remaining = len(self.me.prize)

        self.my_field_counts = defaultdict(int)
        self.my_hand_counts = defaultdict(int)
        self.my_discard_counts = defaultdict(int)
        self.has_ready_lucario_line = False
        self.has_ready_hariyama_line = False
        self.can_switch = False
        self.can_gust = False
        self.can_attack = False
        self.can_use_mega_brave = False
        self.active_stadium_id = self.state.stadium[0].id if self.state.stadium else 0

        self._count_cards()
        self._scan_main_phase_options()

    def choose(self) -> list[int]:
        if not self.select.option or self.select.maxCount == 0:
            return []

        if self.context == SelectContext.MAIN:
            self._plan_attack()

        option_scores = [self._score_option(option) for option in self.select.option]
        ranked_indices = [
            index
            for index, _ in sorted(enumerate(option_scores), key=lambda item: item[1], reverse=True)
        ]
        self._track_lunatone_ability_use(ranked_indices)
        return ranked_indices[: self.select.maxCount]

    def _count_cards(self) -> None:
        for pokemon in self.me.active + self.me.bench:
            if pokemon is None:
                continue
            self.my_field_counts[pokemon.id] += 1
            if pokemon.id in {CardId.MAKUHITA, CardId.HARIYAMA} and len(pokemon.energies) >= 3:
                self.has_ready_hariyama_line = True
            if pokemon.id in {CardId.RIOLU, CardId.MEGA_LUCARIO_EX} and len(pokemon.energies) >= 2:
                self.has_ready_lucario_line = True

        for card in self.me.hand:
            self.my_hand_counts[card.id] += 1
        for card in self.me.discard:
            self.my_discard_counts[card.id] += 1

    def _scan_main_phase_options(self) -> None:
        if self.context != SelectContext.MAIN:
            return
        for option in self.select.option:
            if option.type == OptionType.PLAY:
                card = get_card(self.obs, AreaType.HAND, option.index, self.my_player_index)
                if card.id == CardId.SWITCH:
                    self.can_switch = True
                elif card.id == CardId.BOSS_ORDERS:
                    self.can_gust = True
            elif option.type == OptionType.EVOLVE:
                card = get_card(self.obs, AreaType.HAND, option.index, self.my_player_index)
                if card.id == CardId.HARIYAMA:
                    self.can_gust = True
            elif option.type == OptionType.RETREAT:
                self.can_switch = True
            elif option.type == OptionType.ATTACK:
                self.can_attack = True
                if option.attackId == MEGA_BRAVE_ATTACK_ID:
                    self.can_use_mega_brave = True

    def _my_board(self) -> list[Pokemon | None]:
        return self.me.active + self.me.bench

    def _my_total_bodies(self) -> int:
        return sum(1 for pokemon in self._my_board() if pokemon is not None)

    def _opponent_board(self) -> list[Pokemon | None]:
        return self.opponent.active + self.opponent.bench

    def _opponent_has_crustle_axis(self) -> bool:
        return any(
            pokemon is not None and pokemon.id in {CardId.DWEBBLE, CardId.CRUSTLE}
            for pokemon in self._opponent_board()
        )

    def _opponent_is_water_deck(self) -> bool:
        # Abomasnow / water archetype: walls Fighting, so bulk up the Lucario line. Ported from 1084.
        return any(
            pokemon is not None and pokemon.id in {CardId.KYOGRE, CardId.SNOVER, CardId.MEGA_ABOMASNOW_EX}
            for pokemon in self._opponent_board()
        )

    def _should_preserve_hariyama(self) -> bool:
        return (
            self._opponent_has_crustle_axis()
            and self.my_hand_counts[CardId.HARIYAMA] >= 1
            and any(pokemon is not None and pokemon.id == CardId.MAKUHITA for pokemon in self._my_board())
        )

    def _can_evolve_board_index(self, board_index: int) -> bool:
        for option in self.select.option:
            if option.type != OptionType.EVOLVE:
                continue
            target_index = option.inPlayIndex
            if option.inPlayArea == AreaType.BENCH:
                target_index += 1
            if target_index == board_index:
                return True
        return False

    def _base_attack(self, pokemon: Pokemon, attack_index: int) -> tuple[int, int, int] | None:
        energy_required = 0
        base_damage = 0
        bonus_score = 0

        if pokemon.id == CardId.MEGA_LUCARIO_EX:
            if attack_index == 0:
                energy_required = 1
                base_damage = 130
                bonus_score += 60 * min(3, self.my_discard_counts[CardId.BASIC_FIGHTING_ENERGY])
            else:
                energy_required = 2
                base_damage = 270
            if self.my_prizes_remaining in {2, 3}:
                bonus_score -= 500
        elif attack_index == 1:
            return None
        elif pokemon.id == CardId.HARIYAMA:
            energy_required = 3
            base_damage = 210
        elif pokemon.id == CardId.MAKUHITA:
            return None
        elif pokemon.id == CardId.SOLROCK and self.my_field_counts[CardId.LUNATONE] >= 1:
            energy_required = 1
            base_damage = 70

        if base_damage <= 0:
            return None
        return energy_required, base_damage, bonus_score

    def _base_attack_after_evolution(self, pokemon: Pokemon, board_index: int, attack_index: int):
        if pokemon.id == CardId.MAKUHITA and attack_index == 0 and self._can_evolve_board_index(board_index):
            return 3, 210, -100
        return self._base_attack(pokemon, attack_index)

    def _plan_attack(self) -> None:
        global current_plan
        best_score = -1
        current_plan = AttackPlan()

        if self.state.turn < 2:
            return

        for attacker_index, my_pokemon in enumerate(self._my_board()):
            if my_pokemon is None:
                continue
            if attacker_index != 0 and not self.can_switch:
                break

            for attack_index in range(2):
                attack = self._base_attack_after_evolution(my_pokemon, attacker_index, attack_index)
                if attack is None:
                    continue
                energy_required, base_damage, bonus_score = attack

                available_energy = len(my_pokemon.energies)
                if attack_index == 1 and attacker_index == 0 and available_energy >= 2 and not self.can_use_mega_brave:
                    break

                needs_energy_attach = False
                if available_energy < energy_required:
                    if self.my_hand_counts[CardId.BASIC_FIGHTING_ENERGY] >= 1 and not self.state.energyAttached:
                        available_energy += 1
                        needs_energy_attach = available_energy >= energy_required
                    if not needs_energy_attach:
                        continue

                for target_index, op_pokemon in enumerate(self._opponent_board()):
                    if op_pokemon is None:
                        continue
                    if target_index != 0 and not self.can_gust:
                        break

                    damage = base_damage
                    if my_pokemon.id == CardId.MEGA_LUCARIO_EX and op_pokemon.id == CardId.CRUSTLE:
                        damage = 0
                    else:
                        op_data = card_data_by_id[op_pokemon.id]
                        if op_data.weakness == EnergyType.FIGHTING:
                            damage *= 2
                        elif op_data.resistance == EnergyType.FIGHTING:
                            damage -= 30

                    score = target_priority_score(op_pokemon)
                    prizes_taken = prize_value(op_pokemon) if op_pokemon.hp <= damage else 0
                    if prizes_taken == 0:
                        score *= damage / op_pokemon.hp
                    if len(self.opponent.prize) <= prizes_taken:
                        score = 50000

                    score += bonus_score
                    score += 220 if attacker_index == 0 else 0
                    score += 300 if target_index == 0 else 0
                    score += available_energy

                    if score > best_score:
                        best_score = score
                        current_plan = AttackPlan(
                            attacker_index=attacker_index,
                            target_index=target_index,
                            attack_index=attack_index,
                            target_remaining_hp=op_pokemon.hp - damage,
                            needs_energy_attach=needs_energy_attach,
                        )

    def _energy_target_score(self, pokemon: Pokemon, is_active: bool) -> int:
        energy_count = len(pokemon.energies)
        score = 8000 + (10 if is_active else 0)

        # Bench insurance: the losses we're actually seeing are a single KO
        # on the active with no charged-up replacement ready -- if we're
        # down to 2 or fewer bodies, an un-energized bench Pokemon that could
        # be the next active is worth more than a marginal top-up on the
        # Pokemon that's currently winning anyway.
        if not is_active and self._my_total_bodies() <= 2 and energy_count == 0:
            score += 150

        if pokemon.id in {CardId.MAKUHITA, CardId.HARIYAMA}:
            score += 1 if pokemon.id == CardId.HARIYAMA else 0
            score += 100 if energy_count < 3 else 0
            score -= 50 if self.has_ready_hariyama_line else 0
        elif pokemon.id == CardId.LUNATONE:
            score -= 100
        elif pokemon.id == CardId.SOLROCK:
            score += 20 if energy_count < 1 else -100
        elif pokemon.id in {CardId.RIOLU, CardId.MEGA_LUCARIO_EX}:
            score += 1 if pokemon.id == CardId.MEGA_LUCARIO_EX else 0
            score += 100 if energy_count < 2 else 0
            score -= 50 if self.has_ready_lucario_line else 0
        return score

    def _score_option(self, option) -> float:
        if option.type == OptionType.NUMBER:
            return option.number
        if option.type == OptionType.YES:
            return 100 if self.context == SelectContext.IS_FIRST else 1
        if option.type == OptionType.NO:
            return 0
        if option.type == OptionType.CARD:
            return self._score_card_choice(option)
        if option.type == OptionType.PLAY:
            return self._score_play(option)
        if option.type == OptionType.ATTACH:
            return self._score_attach(option)
        if option.type == OptionType.EVOLVE:
            return self._score_evolve(option)
        if option.type == OptionType.ABILITY:
            return self._score_ability(option)
        if option.type == OptionType.RETREAT:
            return 2000 if current_plan.attacker_index >= 1 else -1
        if option.type == OptionType.ATTACK:
            return 1100 if (option.attackId == MEGA_BRAVE_ATTACK_ID) == (current_plan.attack_index == 1) else 1000
        return 0

    def _score_card_choice(self, option) -> float:
        card = get_card(self.obs, option.area, option.index, option.playerIndex)
        if card is None:
            return 0

        if self.context in {SelectContext.SWITCH, SelectContext.TO_ACTIVE}:
            return self._score_active_choice(option, card)
        if self.context == SelectContext.SETUP_ACTIVE_POKEMON:
            return self._score_setup_active(card)
        if self.context == SelectContext.TO_HAND:
            return self._score_to_hand(card)
        if self.context == SelectContext.ATTACH_FROM and isinstance(card, Pokemon):
            return self._energy_target_score(card, option.area == AreaType.ACTIVE)
        return 0

    def _score_active_choice(self, option, card: Pokemon | Card) -> float:
        if not isinstance(card, Pokemon):
            return 0

        if option.playerIndex != self.my_player_index:
            return 100 if option.index == current_plan.target_index - 1 else 0

        score = len(card.energies) * 2
        if option.index == current_plan.attacker_index - 1:
            score += 100
        if card.id == CardId.MEGA_LUCARIO_EX:
            score += 8 if self.my_prizes_remaining in {2, 3} else 20
        elif card.id == CardId.HARIYAMA and len(card.energies) >= 2:
            score += 15
        elif card.id == CardId.MAKUHITA and len(card.energies) >= 2:
            score += 10
        elif card.id == CardId.SOLROCK:
            score += 5
        elif card.id == CardId.RIOLU:
            score += 4
        return score

    def _score_setup_active(self, card: Pokemon | Card) -> int:
        if card.id == CardId.SOLROCK:
            return 2 if self.state.firstPlayer == self.my_player_index else 4
        if card.id == CardId.RIOLU:
            return 3
        if card.id == CardId.MAKUHITA:
            return 1
        return 0

    def _score_to_hand(self, card: Pokemon | Card) -> float:
        score = 200 - self.my_hand_counts[card.id] * 100
        # Survival override: at 2 or fewer bodies, a search that can find a
        # Basic Pokemon should take it over anything else in this bucket --
        # losing to a single well-timed KO with no replacement ready is the
        # main way these games are actually being lost, not slow attrition.
        if self._my_total_bodies() <= 2 and card_data_by_id[card.id].basic:
            score += 300
        if card.id == CardId.MAKUHITA:
            score += -10 if self.my_field_counts[card.id] >= 1 else 10
        elif card.id == CardId.HARIYAMA:
            score += 20 if self.my_field_counts[CardId.MAKUHITA] >= 1 else -20
        elif card.id == CardId.LUNATONE:
            score += -250 if self.my_field_counts[card.id] >= 1 else 60
        elif card.id == CardId.SOLROCK:
            score += -250 if self.my_field_counts[card.id] >= 1 else 50
        elif card.id == CardId.RIOLU:
            lucario_line_count = self.my_field_counts[CardId.RIOLU] + self.my_field_counts[CardId.MEGA_LUCARIO_EX]
            score += -150 if lucario_line_count >= 2 else -3 if lucario_line_count >= 1 else 40
        elif card.id == CardId.MEGA_LUCARIO_EX:
            score += 40 if self.my_field_counts[CardId.RIOLU] >= 1 else -15
        elif card.id == CardId.BASIC_FIGHTING_ENERGY:
            score += 30 if not ability_used_this_turn or not self.state.energyAttached else -1
        return score

    def _score_play(self, option) -> float:
        card = get_card(self.obs, AreaType.HAND, option.index, self.my_player_index)
        data = card_data_by_id[card.id]
        if data.cardType == CardType.POKEMON:
            return self._score_play_pokemon(card)
        return self._score_play_trainer(card)

    def _score_play_pokemon(self, card: Card) -> float:
        score = 20000
        if card.id in {CardId.LUNATONE, CardId.SOLROCK} and self.my_field_counts[card.id] >= 1:
            return -1
        if card.id == CardId.RIOLU and self.my_field_counts[CardId.RIOLU] + self.my_field_counts[CardId.MEGA_LUCARIO_EX] >= 2:
            return -1
        return score

    def _score_play_trainer(self, card: Card) -> float:
        if card.id == CardId.SWITCH:
            return 6000 if current_plan.attacker_index > 0 else -1
        if card.id == CardId.PREMIUM_POWER_PRO:
            if self.state.supporterPlayed and current_plan.target_remaining_hp <= 0:
                return -1
            if not self.can_attack:
                can_bridge_draw = (
                    not self.state.supporterPlayed
                    and self.my_hand_counts[CardId.CARMINE] > 0
                    and self.my_hand_counts[CardId.LILLIE_DETERMINATION] == 0
                    and not self._is_deck_low()
                )
                return 3050 if can_bridge_draw else -1
            return 5000
        if card.id == CardId.BOSS_ORDERS:
            return 3200 if current_plan.target_index >= 1 else -1
        if card.id == CardId.CARMINE:
            if self._should_preserve_hariyama():
                return -1
            return -1 if self._is_deck_low() else 3000
        if card.id == CardId.LILLIE_DETERMINATION:
            return -1 if self._is_deck_low() else 3100
        if card.id == CardId.GRAVITY_MOUNTAIN:
            return self._score_gravity_mountain()
        return 10000

    def _score_gravity_mountain(self) -> float:
        opponent_has_stage2 = any(
            pokemon is not None and card_data_by_id[pokemon.id].stage2 for pokemon in self._opponent_board()
        )
        if opponent_has_stage2:
            return 3500
        return 1200 if self.active_stadium_id else -1

    def _is_deck_low(self) -> bool:
        return self.me.deckCount <= LOW_DECK_THRESHOLD

    def _score_attach(self, option) -> float:
        card = get_card(self.obs, AreaType.HAND, option.index, self.my_player_index)
        pokemon = get_card(self.obs, option.inPlayArea, option.inPlayIndex, self.my_player_index)
        if not isinstance(pokemon, Pokemon):
            return 0

        if card.id == CardId.HERO_CAPE:
            score = 7000
            if self._opponent_is_water_deck():
                if pokemon.id == CardId.RIOLU:
                    return 12200
                if pokemon.id == CardId.MEGA_LUCARIO_EX:
                    return 12800
            if pokemon.id == CardId.RIOLU:
                score += 100
            elif pokemon.id == CardId.MEGA_LUCARIO_EX:
                score += 200
            return score

        score = self._energy_target_score(pokemon, option.inPlayArea == AreaType.ACTIVE)
        board_index = option.inPlayIndex if option.inPlayArea == AreaType.ACTIVE else option.inPlayIndex + 1
        if board_index == current_plan.attacker_index and current_plan.needs_energy_attach:
            score += 200
        return score

    def _score_evolve(self, option) -> float:
        pokemon = get_card(self.obs, option.inPlayArea, option.inPlayIndex, self.my_player_index)
        if not isinstance(pokemon, Pokemon):
            return 0
        evolved_card = get_card(self.obs, option.area, option.index, self.my_player_index)
        board_index = option.inPlayIndex if option.inPlayArea == AreaType.ACTIVE else option.inPlayIndex + 1
        if pokemon.id == CardId.MAKUHITA and current_plan.target_index == 0 and not (
            evolved_card is not None and evolved_card.id == CardId.HARIYAMA and board_index == current_plan.attacker_index
        ):
            return -1
        return 9000 + len(pokemon.energies)

    def _score_ability(self, option) -> float:
        card = get_card(self.obs, option.area, option.index, self.my_player_index)
        if card.id == CardId.LUMIOSE_CITY:
            return 1
        if card.id == CardId.LUNATONE and self._is_deck_low():
            return -1
        return 30000

    def _track_lunatone_ability_use(self, ranked_indices: list[int]) -> None:
        global ability_used_this_turn
        if self.context != SelectContext.MAIN or not ranked_indices:
            return
        option = self.select.option[ranked_indices[0]]
        if option.type != OptionType.ABILITY:
            return
        card = get_card(self.obs, option.area, option.index, self.my_player_index)
        if card is not None and card.id == CardId.LUNATONE:
            ability_used_this_turn = True


def agent(obs_dict: dict) -> list[int]:
    global last_seen_turn
    global ability_used_this_turn
    global current_plan

    if obs_dict.get("select") is None and "current" not in obs_dict:
        last_seen_turn = -1
        ability_used_this_turn = False
        current_plan = AttackPlan()
        return starting_deck_list

    obs = to_observation_class(obs_dict)
    if obs.select is None:
        last_seen_turn = -1
        ability_used_this_turn = False
        current_plan = AttackPlan()
        return starting_deck_list

    if last_seen_turn != obs.current.turn:
        last_seen_turn = obs.current.turn
        ability_used_this_turn = False
        current_plan = AttackPlan()

    return LucarioPolicy(obs).choose()
