"""
Marnie's Grimmsnarl ex — rule-based agent
Kaggle: The Pokémon Company - PTCG AI Battle Challenge Simulation

Strategy encoded here:
  - Bench Impidimp / Munkidori / Snorunt early, evolve toward Grimmsnarl ex ASAP
    because Punk Up (evolve-from-hand trigger) tutors up to 5 Basic {D} Energy.
  - Froslass's Freezing Shroud passively damages every Ability-holder each turn
    (both sides) -> Munkidori's Adrena-Brain then shoves that self-damage onto
    the opponent's board, softening things up for knockouts.
  - Grimmsnarl ex's Shadow Bullet (180 + 30 bench) is the primary attacker.
  - Boss's Orders / Iris's Fighting Spirit pull up weakened Pokémon for KOs.
  - Never crash: every branch falls back to a legal, in-range selection.
"""

import os
import random

from cg.api import (
    AreaType,
    CardType,
    OptionType,
    SelectContext,
    SelectType,
    all_attack,
    all_card_data,
    to_observation_class,
)

# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------

CARD_TABLE = {c.cardId: c for c in all_card_data()}
ATTACK_TABLE = {a.attackId: a for a in all_attack()}

# Key deck card IDs (see deck.csv / DECKLIST.md for the full list + sources)
MUNKIDORI = 112
IMPIDIMP = 646
MORGREM = 647
GRIMMSNARL_EX = 648
SNORUNT = 860
FROSLASS = 104
BUDEW = 235
TATSUGIRI = 122
YVELTAL = 689
DARK_ENERGY = 7

LILLIES_DETERMINATION = 1227
PETREL = 1219
BOSS_ORDERS = 1182
IRIS_FIGHTING_SPIRIT = 1208
POKE_PAD = 1152
BUDDY_BUDDY_POFFIN = 1086
NIGHT_STRETCHER = 1097
RARE_CANDY = 1079
ENERGY_SWITCH = 1116
JUDGE = 1213  # substitute for "Special Red Card" (not present in this card DB)
SECRET_BOX = 1092
AIR_BALLOON = 1174
SPIKEMUTH_GYM = 1259

# Search / keep priority when choosing among cards (higher = more wanted)
SEARCH_PRIORITY = {
    GRIMMSNARL_EX: 100,
    MORGREM: 90,
    IMPIDIMP: 85,
    MUNKIDORI: 80,
    FROSLASS: 75,
    SNORUNT: 70,
    DARK_ENERGY: 65,
    SECRET_BOX: 72,  # huge value engine via Petrel: fetches Item+Tool+Supporter+Stadium at once
    LILLIES_DETERMINATION: 66,  # a fresh hand beats almost any single other trainer card
    BOSS_ORDERS: 60,
    RARE_CANDY: 55,
    PETREL: 50,
    IRIS_FIGHTING_SPIRIT: 40,
    JUDGE: 40,  # solid disruption + refill when Lillie isn't available
    SPIKEMUTH_GYM: 35,
    POKE_PAD: 30,
    BUDDY_BUDDY_POFFIN: 28,
    NIGHT_STRETCHER: 25,
    ENERGY_SWITCH: 20,
    AIR_BALLOON: 18,
    BUDEW: 8,
    TATSUGIRI: 5,
    YVELTAL: 5,
}

EVOLUTION_PRIORITY = {GRIMMSNARL_EX: 3, FROSLASS: 2, MORGREM: 1}
FREE_RETREAT_POKEMON = {BUDEW, YVELTAL}  # 0 retreat cost -- safe to keep active for flexibility



def card_name(card_id):
    c = CARD_TABLE.get(card_id)
    return c.name if c else f"#{card_id}"


def search_score(card_id):
    return SEARCH_PRIORITY.get(card_id, 0)


def resolve_cid(opt, obs, select):
    """Resolve the actual card ID behind a CARD-type option.

    Option only carries area/index/playerIndex for plain CARD selects (cardId
    is only populated for SKILL-type options) so we have to look the card up
    in the relevant zone ourselves: the revealed deck list when searching, or
    the corresponding player's hand/active/bench/discard/prize otherwise.
    """
    if getattr(opt, "cardId", None) is not None:
        return opt.cardId
    area = opt.area
    idx = opt.index
    pidx = opt.playerIndex
    if area is None or idx is None:
        return None
    try:
        if area == AreaType.DECK and select is not None and select.deck is not None:
            if idx < len(select.deck):
                c = select.deck[idx]
                return c.id if c is not None else None
            return None
        if area == AreaType.STADIUM:
            stadium = obs.current.stadium
            if stadium is not None and idx < len(stadium) and stadium[idx] is not None:
                return stadium[idx].id
            return None
        if area == AreaType.LOOKING:
            looking = obs.current.looking
            if looking is not None and idx < len(looking) and looking[idx] is not None:
                return looking[idx].id
            return None
        if pidx is None:
            return None
        p = obs.current.players[pidx]
        if area == AreaType.HAND:
            if p.hand is not None and idx < len(p.hand):
                return p.hand[idx].id
        elif area == AreaType.ACTIVE:
            if idx < len(p.active) and p.active[idx] is not None:
                return p.active[idx].id
        elif area == AreaType.BENCH:
            if idx < len(p.bench):
                return p.bench[idx].id
        elif area == AreaType.DISCARD:
            if idx < len(p.discard):
                return p.discard[idx].id
        elif area == AreaType.PRIZE:
            if idx < len(p.prize) and p.prize[idx] is not None:
                return p.prize[idx].id
    except Exception:
        return None
    return None


def get_pokemon_at(obs, area, idx, pidx):
    """Return the Pokemon object at a given ACTIVE/BENCH slot, or None."""
    try:
        p = obs.current.players[pidx]
        zone = p.active if area == AreaType.ACTIVE else p.bench
        if idx < len(zone):
            return zone[idx]
    except Exception:
        pass
    return None


def going_second(obs):
    fp = obs.current.firstPlayer
    return fp != -1 and fp is not None and fp != obs.current.yourIndex


MARNIE_LINE = {IMPIDIMP, MORGREM, GRIMMSNARL_EX}


def marnie_basic_in_play(obs):
    """True if we already have Impidimp/Morgrem/Grimmsnarl ex on board."""
    me = my_state(obs)
    ids = []
    if me.active and me.active[0] is not None:
        ids.append(me.active[0].id)
    for p in me.bench:
        ids.append(p.id)
    return any(i in MARNIE_LINE for i in ids)


# Tracks whether Budew has already fired Itchy Pollen this game, so we don't
# keep trying to force it back into the active slot after it's done its job.
# Resets automatically whenever the turn counter drops (new game/battle).
_game_flags = {"budew_fired": False, "last_turn": -1}


def _maybe_reset_game_flags(turn):
    if turn is not None and turn < _game_flags["last_turn"] - 3:
        _game_flags["budew_fired"] = False
    _game_flags["last_turn"] = turn


def budew_needed(obs):
    """True if we should still be actively trying to find/use Budew."""
    if _game_flags["budew_fired"]:
        return False
    me = my_state(obs)
    if me.active and me.active[0] is not None and me.active[0].id == BUDEW:
        return False  # already have it, just need to attack with it
    if any(p.id == BUDEW for p in me.bench):
        return False  # already fetched, just needs to get into the active slot
    hand = me.hand or []
    if any(c.id == BUDEW for c in hand):
        return False  # already in hand, just needs to be played
    return True


def have_supporter_in_hand(obs):
    me = my_state(obs)
    for c in me.hand or []:
        card = CARD_TABLE.get(c.id)
        if card and card.cardType == CardType.SUPPORTER:
            return True
    return False


# ---------------------------------------------------------------------------
# Deck loading
# ---------------------------------------------------------------------------

def read_deck_csv() -> list:
    file_path = "deck.csv"
    if not os.path.exists(file_path):
        file_path = "/kaggle_simulations/agent/" + file_path
    with open(file_path, "r") as f:
        csv = f.read().split("\n")
    deck = []
    for i in range(60):
        deck.append(int(csv[i]))
    return deck


# ---------------------------------------------------------------------------
# Helpers for reading board state
# ---------------------------------------------------------------------------

def my_state(obs):
    return obs.current.players[obs.current.yourIndex]


def opp_state(obs):
    return obs.current.players[1 - obs.current.yourIndex]


def pokemon_hp_fraction(p):
    if p is None or p.maxHp == 0:
        return 1.0
    return p.hp / p.maxHp


def has_dark_energy(pokemon):
    return pokemon is not None and 7 in [int(e) for e in pokemon.energies]


def best_attack_for(card_id, pokemon):
    """Return the highest-damage attack this Pokémon can currently pay for."""
    c = CARD_TABLE.get(card_id)
    if c is None:
        return None
    energy_count = len(pokemon.energies) if pokemon else 0
    usable = []
    for aid in c.attacks:
        a = ATTACK_TABLE.get(aid)
        if a is None:
            continue
        if len(a.energies) <= energy_count:
            usable.append(a)
    if not usable:
        return None
    return max(usable, key=lambda a: a.damage)


def attacker_value(pokemon):
    """Rough 'how good is it to have this Pokemon active right now' score."""
    if pokemon is None:
        return -1
    energy_bonus = len(pokemon.energies) * 30
    return search_score(pokemon.id) + energy_bonus + pokemon.hp


def retreat_cost_of(pokemon):
    c = CARD_TABLE.get(pokemon.id) if pokemon else None
    return c.retreatCost if c else 1


def prize_value(card_id):
    """How many Prize cards a KO on this Pokemon is worth."""
    c = CARD_TABLE.get(card_id)
    if c is None:
        return 1
    if getattr(c, "megaEx", False):
        return 3
    if getattr(c, "ex", False):
        return 2
    return 1


class AttackPlan:
    """The single best (attacker, attack, target) combo available to us this
    turn, computed once per decision instead of scored piecemeal. This keeps
    RETREAT / ATTACH / Boss's Orders / Iris's Fighting Spirit all pointed at
    the same coherent plan rather than making locally-reasonable but
    globally-inconsistent choices.
    """

    def __init__(self):
        self.attacker_area = None
        self.attacker_index = None
        self.attack_id = None
        self.target_area = None
        self.target_index = None
        self.needs_energy = False
        self.is_lethal = False
        self.score = -1


def compute_attack_plan(obs):
    me = my_state(obs)
    opp = opp_state(obs)
    plan = AttackPlan()

    my_slots = []
    if me.active and me.active[0] is not None:
        my_slots.append((AreaType.ACTIVE, 0, me.active[0]))
    for i, p in enumerate(me.bench):
        if p is not None:
            my_slots.append((AreaType.BENCH, i, p))

    opp_slots = []
    if opp.active and opp.active[0] is not None:
        opp_slots.append((AreaType.ACTIVE, 0, opp.active[0]))
    for i, p in enumerate(opp.bench):
        if p is not None:
            opp_slots.append((AreaType.BENCH, i, p))

    opp_prizes_remaining = len(opp.prize)

    for area, idx, pokemon in my_slots:
        card = CARD_TABLE.get(pokemon.id)
        if card is None:
            continue
        energy_count = len(pokemon.energies)
        for aid in card.attacks:
            atk = ATTACK_TABLE.get(aid)
            if atk is None:
                continue
            shortfall = len(atk.energies) - energy_count
            if shortfall > 1:
                continue  # more than one energy short -- not gettable this turn
            needs_energy = shortfall > 0
            for t_area, t_idx, opp_pokemon in opp_slots:
                dmg = atk.damage
                is_ko = dmg >= opp_pokemon.hp
                if is_ko:
                    prize = prize_value(opp_pokemon.id)
                    score = 1000 + prize * 500
                    if prize >= opp_prizes_remaining:
                        score = 1_000_000  # this KO wins the game outright
                else:
                    score = dmg  # partial credit for chip damage
                score += energy_count * 5
                if needs_energy:
                    score -= 25  # mild preference for attacks already payable
                if score > plan.score:
                    plan.score = score
                    plan.attacker_area, plan.attacker_index = area, idx
                    plan.attack_id = aid
                    plan.target_area, plan.target_index = t_area, t_idx
                    plan.needs_energy = needs_energy
                    plan.is_lethal = is_ko and prize_value(opp_pokemon.id) >= opp_prizes_remaining
    return plan


# ---------------------------------------------------------------------------
# Option scoring
# ---------------------------------------------------------------------------

def score_main_option(opt, obs):
    """Score a SelectType.MAIN option. Higher = more desirable."""
    state = obs.current
    me = my_state(obs)
    opp = opp_state(obs)
    my_active = me.active[0] if me.active else None
    opp_active = opp.active[0] if opp.active else None

    _maybe_reset_game_flags(state.turn)

    if opt.type == OptionType.ATTACK:
        a = ATTACK_TABLE.get(opt.attackId)
        if a is None or my_active is None:
            return 5
        score = 40 + a.damage / 4
        if opp_active is not None and a.damage >= opp_active.hp:
            score += 200  # lethal, always take it
        if my_active.id == BUDEW:
            # Itchy Pollen (item-lock) is worth prioritizing the FIRST time,
            # but only then -- once it's fired, Budew sitting active and
            # re-poking for 10 every turn is actively bad (it's a dead turn
            # and Budew is a free prize). After it's locked once, let
            # retreating to a real attacker win instead.
            if not _game_flags["budew_fired"]:
                score = max(score, 65)
            else:
                score = 3
        elif a.damage <= 30:
            # A tiny chip attack (Impidimp/Morgrem/Budew tier) ends our turn
            # for almost no board impact. Against anything with real HP it's
            # usually better to spend the turn developing an actual attacker.
            # Keep it above END (so we still do it when there's nothing
            # better) but below evolving / benching / charging up.
            score = min(score, 12)
        return score

    if opt.type == OptionType.EVOLVE:
        # opt.index/area = card in hand; give strong priority to Grimmsnarl ex line
        # Evolution is a permanent board improvement (HP, Punk Up's energy
        # burst, Shadow Bullet). MAIN allows one action per prompt, and the
        # engine re-prompts MAIN after evolving (verified directly), so
        # scoring EVOLVE above ABILITY costs nothing -- the ability still
        # fires on the very next prompt. The reverse ordering was measured
        # skipping 26-43% of available evolutions.
        return 110  # engine will present specific evolve targets via SelectContext.EVOLVE

    if opt.type == OptionType.ABILITY:
        # Attacking ends your turn's action sequence, so an ability we
        # haven't used yet gets forfeited for the turn if we attack first.
        # Munkidori's Adrena-Brain in particular needs to fire *before* we
        # attack: it clears self-inflicted damage from Froslass's Freezing
        # Shroud, and skipping it turn after turn lets our own bench quietly
        # die to our own passive damage. 100 comfortably outranks the
        # strongest non-lethal attack in the deck (Shadow Bullet: 40+45=85)
        # without touching the rest of the attack-priority balance.
        return 100


    if opt.type == OptionType.PLAY:
        card_in_hand = None
        hand = me.hand or []
        if opt.index is not None and opt.index < len(hand):
            card_in_hand = hand[opt.index].id
        base = 30
        if card_in_hand is not None:
            c = CARD_TABLE.get(card_in_hand)
            if c and c.cardType == CardType.SUPPORTER:
                if state.supporterPlayed:
                    return -1
                if card_in_hand == LILLIES_DETERMINATION:
                    # Lillie's Determination draws 8 cards instead of 6 as
                    # long as we still have all 6 Prize cards -- i.e. before
                    # we've taken a single knockout. That's a big enough
                    # swing that it should get played early rather than
                    # sitting in hand -- but it also shuffles our whole hand
                    # away, so it must never preempt this turn's energy
                    # attachment (that's a real card we'd whiff otherwise).
                    # Scored below ATTACH for exactly that reason.
                    if me.deckCount < 10:
                        # Drawing up to 8 could empty (or nearly empty) the
                        # deck outright -- decking ourselves out next turn
                        # is an instant loss, so back off here.
                        return 5
                    if len(me.prize) == 6:
                        return 43
                    return 41
                if card_in_hand == JUDGE:
                    # Judge refreshes our hand (draw 4) while also cutting the
                    # opponent's hand down -- when Lillie isn't available,
                    # this is generally better than Boss's Orders/Petrel/Iris,
                    # especially early before board state locks in.
                    if me.deckCount < 6:
                        return 5  # same deck-out caution, smaller draw so a lower bar
                    return 39
                if card_in_hand in (BOSS_ORDERS, IRIS_FIGHTING_SPIRIT):
                    # These only earn their keep if our best attack this turn
                    # actually wants a *currently benched* opponent Pokemon --
                    # if the opponent's active is already our best target,
                    # playing a gust effect does nothing useful.
                    plan = compute_attack_plan(obs)
                    if plan.target_area == AreaType.BENCH and plan.score >= 500:
                        return 47  # worth pulling the target up before we attack
                    return 20
                # Other supporters (Petrel): useful, but should lose to
                # Lillie's Determination and Judge when those are available.
                return 32 + search_score(card_in_hand) * 0.1
            if card_in_hand == RARE_CANDY:
                # If this is showing up as a legal option at all, we have a
                # Basic in play and a matching Stage 2 in hand -- taking the
                # Impidimp -> Grimmsnarl ex shortcut is almost always right.
                return 85
            if card_in_hand in (BUDDY_BUDDY_POFFIN, POKE_PAD):
                # Board development / digging for Budew -- get these down
                # every turn they're available instead of sitting on them.
                bonus = 20 if budew_needed(obs) else 0
                return 58 + bonus
            if c and c.cardType == CardType.STADIUM:
                # Play Spikemuth Gym essentially on sight -- it's free value
                # every turn (search a Marnie's Pokémon) once it's down.
                return 68
            if c and c.cardType == CardType.POKEMON and getattr(c, "basic", False):
                # Bench basics aggressively. Against fast one-prize-a-turn
                # decks (e.g. Mega Lucario ex) the loss we keep suffering is
                # "ran out of Pokemon to put active," so keeping the bench
                # populated is survival, not just development. The thinner
                # our board, the more urgent each new body is.
                total_bodies = (1 if (me.active and me.active[0] is not None) else 0) + len(me.bench)
                if total_bodies <= 2:
                    return 75  # critically thin -- get a body down now
                if total_bodies <= 4:
                    return 52
                return 40 + search_score(card_in_hand) * 0.1
            base += search_score(card_in_hand) * 0.3
        return base

    if opt.type == OptionType.ATTACH:
        if state.energyAttached:
            return -1
        # CRITICAL: MAIN-level ATTACH options each carry a specific target
        # (inPlayArea/inPlayIndex). Scoring them all identically meant the
        # tie always broke to the first option -- the Active slot -- so we
        # kept feeding energy to whatever the opponent was about to KO,
        # while the bench never developed. (Watched this lose a real ladder
        # game 0-6 to a lone Fezandipiti that one-shot our active every
        # turn while every benched Pokemon sat at 0 energy for 12 turns.)
        base = 45.0
        target = get_pokemon_at(obs, opt.inPlayArea, opt.inPlayIndex, obs.current.yourIndex)
        if target is not None:
            plan = compute_attack_plan(obs)
            if plan.needs_energy and plan.attacker_area == opt.inPlayArea and plan.attacker_index == opt.inPlayIndex:
                return base + 15  # completes this turn's planned attack
            e = len(target.energies)
            if target.id == MUNKIDORI and e == 0:
                return base + 12  # 1 {D} switches on Adrena-Brain every turn
            if target.id == GRIMMSNARL_EX and e < 2:
                return base + 10  # toward Shadow Bullet's {D}{D}
            if target.id in (MORGREM, IMPIDIMP) and opt.inPlayArea == AreaType.BENCH:
                return base + 6  # pre-charge the evolution line safely on the bench
            if target.id == YVELTAL and e < 3:
                return base + 7  # our only fast attacker (Dark Feather, 110)
            # Default: prefer the bench over the active. The active is the
            # Pokemon most likely to be KO'd before it uses the energy.
            if opt.inPlayArea == AreaType.BENCH:
                return base + 3
            # Active with a usable attack is still fine to top up.
            if best_attack_for(target.id, target) is not None:
                return base + 1
            return base
        return base

    if opt.type == OptionType.RETREAT:
        if my_active is None:
            return -1
        # Get Budew into the attacking seat -- but never at the cost of
        # giving up a real attack we could make this turn, and never before
        # we've attached energy or played Lillie/Judge this turn (both of
        # those need to happen first or we risk whiffing the turn's energy
        # attachment / shuffling away cards we needed). Scored below ATTACH
        # (45) and below Lillie/Judge (39-43) on purpose.
        if my_active.id != BUDEW and not _game_flags["budew_fired"]:
            if any(p.id == BUDEW for p in me.bench):
                current_attack = best_attack_for(my_active.id, my_active)
                if current_attack is None or current_attack.damage < 30:
                    return 33
                return 15  # still want Budew eventually, just not this turn
        # Tatsugiri's Attract Customers only works from the Active Spot --
        # swap it in to dig up a Supporter when our hand doesn't have one
        # ready for next turn. Lower priority than the Budew swap since it's
        # a "nice to have" rather than a free, un-missable play.
        if my_active.id != TATSUGIRI and not have_supporter_in_hand(obs):
            if any(p.id == TATSUGIRI for p in me.bench):
                return 31
        # If our best available attack this turn belongs to a bench Pokemon
        # (not the current active), get it into position -- especially
        # worth it if that attack would be a real knockout.
        plan = compute_attack_plan(obs)
        if plan.attacker_area == AreaType.BENCH and plan.score > 0:
            bench_pokemon = me.bench[plan.attacker_index] if plan.attacker_index < len(me.bench) else None
            if bench_pokemon is not None and bench_pokemon.id != my_active.id:
                if retreat_cost_of(my_active) == 0 or plan.score >= 1000:
                    return 42
        # Free retreat: if the active Pokémon costs nothing to retreat, there's
        # no reason to leave a worse attacker sitting there when something
        # more ready (more energy, healthier, higher-value) is on the bench.
        # This also covers "we just got a free promotion after a KO" in
        # spirit -- that case is a separate SWITCH/TO_ACTIVE selection, but
        # uses the same attacker_value comparison for consistency.
        if retreat_cost_of(my_active) == 0:
            current_val = attacker_value(my_active)
            if any(attacker_value(p) > current_val for p in me.bench):
                return 27
        frac = pokemon_hp_fraction(my_active)
        if frac < 0.35:
            return 25
        return 5

    if opt.type == OptionType.DISCARD:
        return 2

    if opt.type == OptionType.END:
        return 1  # always legal fallback, lowest priority

    return 10


def score_card_option(opt, obs, select):
    """Score a SelectType.CARD (or similar) option based on context."""
    ctx = select.context
    cid = resolve_cid(opt, obs, select)
    area = opt.area
    player_idx = opt.playerIndex
    me_idx = obs.current.yourIndex
    me = my_state(obs)

    is_mine = player_idx == me_idx
    is_opponent_pokemon = (player_idx is not None) and (player_idx != me_idx)

    # Setting up your opening Active Pokémon: Budew is a free lead. The
    # player going first can't attack on their own first turn regardless of
    # what's active, so there's no cost to leading Budew even then -- it'll
    # be ready to fire Itchy Pollen (0 energy, item-locks the opponent) the
    # very first turn we're legally allowed to attack.
    if ctx == SelectContext.SETUP_ACTIVE_POKEMON:
        if cid == BUDEW:
            return 200
        return search_score(cid) + 1

    # Spikemuth Gym's search ("search your deck for a Marnie's Pokémon") only
    # ever offers Impidimp/Morgrem/Grimmsnarl ex as options. Get a Basic down
    # on board first if we don't have one yet; once we do, prioritize the
    # Stage 2 pieces instead of grabbing redundant Impidimps.
    if ctx in (SelectContext.TO_HAND, SelectContext.LOOK) and cid in MARNIE_LINE:
        # Board survivability comes FIRST: if we're down to 2 or fewer total
        # bodies, a Stage 2 in hand does nothing -- it can't be benched as a
        # Pokemon, so a KO on our lone active just ends the game. Grab a
        # benchable Basic (Impidimp) whenever the board is thin, no matter
        # how tempting the Grimmsnarl ex is. (Lost a real ladder game to
        # exactly this: 1 Pokemon on board, Spikemuth fetched Grimmsnarl ex,
        # got donked the opponent's next turn.)
        me_now = my_state(obs)
        total_bodies = (1 if (me_now.active and me_now.active[0] is not None) else 0) + len(me_now.bench)
        if total_bodies <= 2:
            return 180 if cid == IMPIDIMP else 20
        if not marnie_basic_in_play(obs):
            return 150 if cid == IMPIDIMP else 70
        return search_score(cid) + 30

    # Distributing a burst of energy attachments (Punk Up searching up to 5
    # Basic {D} Energy and attaching them "in any way you like"). Cap any
    # single Grimmsnarl ex at 2 energy (enough for Shadow Bullet's {D}{D})
    # then route the rest onto other Marnie's Pokémon so they're charged up
    # for when they evolve. The board's energy counts update live between
    # each individual prompt in the burst, so we can read it straight off
    # the current observation without any extra bookkeeping.
    if ctx == SelectContext.ATTACH_FROM and is_mine:
        pokemon = get_pokemon_at(obs, area, opt.index, player_idx)
        total = len(pokemon.energies) if pokemon else 0
        plan = compute_attack_plan(obs)
        plan_bonus = 500 if (plan.needs_energy and plan.attacker_area == area and plan.attacker_index == opt.index) else 0
        if cid == GRIMMSNARL_EX:
            return (200 if total < 2 else 10) + plan_bonus  # hard cap at 2 (Shadow Bullet costs {D}{D})
        if cid in (MORGREM, IMPIDIMP):
            return 60 - total * 2 + plan_bonus
        return 40 - total * 2 + plan_bonus

    # Contexts where we are picking something FOR OURSELVES to keep/develop
    if ctx in (
        SelectContext.TO_HAND,
        SelectContext.TO_BENCH,
        SelectContext.TO_FIELD,
        SelectContext.SETUP_BENCH_POKEMON,
        SelectContext.LOOK,
        SelectContext.EVOLVES_FROM,
        SelectContext.EVOLVES_TO,
        SelectContext.ATTACH_TO,
    ):
        # Night Stretcher (and similar discard-recovery effects) offer a mix
        # of a Pokémon and a Basic Energy from the discard pile. Recover
        # whichever we actually need: a body if our board is thin (this is
        # exactly what prevents the "no Pokémon left to put active" losses),
        # otherwise energy if our active doesn't have any yet.
        if area == AreaType.DISCARD and is_mine:
            card = CARD_TABLE.get(cid)
            if card is not None:
                if card.cardType == CardType.POKEMON:
                    total_bodies = (1 if (me.active and me.active[0] is not None) else 0) + len(me.bench)
                    if total_bodies <= 2:
                        return 140  # board is thin -- get a body back urgently
                    return search_score(cid) + 15
                if card.cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY):
                    my_active_mon = me.active[0] if me.active else None
                    need_energy = my_active_mon is None or len(my_active_mon.energies) == 0
                    return 90 if need_energy else 45
        # Any Pokémon search (Buddy-Buddy Poffin, Poké Pad, etc.) should grab
        # Budew if we don't have one working yet -- it's cheap, free to use,
        # and locks the opponent out of Items the moment it can attack.
        # SURVIVAL OVERRIDE first: at 2 or fewer total bodies, any search
        # that can find a benchable BASIC Pokemon must take one -- an
        # evolution card can't be put down as a body, and one KO from an
        # empty board is an instant loss.
        _card = CARD_TABLE.get(cid)
        if _card is not None and _card.cardType == CardType.POKEMON:
            _total_bodies = (1 if (me.active and me.active[0] is not None) else 0) + len(me.bench)
            if _total_bodies <= 2:
                if getattr(_card, "basic", False):
                    return 170 + search_score(cid) * 0.1
                return 12  # evolutions can wait until we're not about to lose
        if cid == BUDEW and budew_needed(obs):
            return 130
        return search_score(cid) + 1

    # Contexts where we are choosing what to get rid of -> discard lowest value
    if ctx in (
        SelectContext.DISCARD,
        SelectContext.TO_DECK,
        SelectContext.TO_DECK_BOTTOM,
        SelectContext.NOT_MOVE,
    ):
        # Prefer to discard basic energy overflow and low priority cards
        return -search_score(cid)

    # Damage / targeting contexts -> prefer opponent's Pokémon, especially
    # ones we can knock out or that are dangerous
    if ctx in (
        SelectContext.DAMAGE,
        SelectContext.DAMAGE_COUNTER,
        SelectContext.DAMAGE_COUNTER_ANY,
        SelectContext.EFFECT_TARGET,
    ):
        if is_opponent_pokemon:
            return 100
        return 1

    if ctx in (SelectContext.HEAL, SelectContext.REMOVE_DAMAGE_COUNTER):
        if is_mine:
            return 100
        return 1

    if ctx == SelectContext.SWITCH or ctx == SelectContext.TO_ACTIVE:
        # Bring up our strongest available attacker -- but this has to agree
        # with *why* we decided to retreat in the first place. Getting
        # Budew active to fire Itchy Pollen the moment we can attack is the
        # single highest-value retreat play in this deck, full stop; digging
        # for a Supporter via Tatsugiri is worthwhile but secondary.
        # NOTE: this deliberately checks "hasn't fired yet" rather than
        # budew_needed() -- budew_needed() is about whether to go *fetch* a
        # copy, and already returns False once Budew is sitting on the
        # bench, which is exactly the case we need to match here.
        if is_mine:
            if cid == BUDEW and not _game_flags["budew_fired"]:
                return 300
            if cid == TATSUGIRI and not have_supporter_in_hand(obs):
                return 150
            plan = compute_attack_plan(obs)
            if plan.attacker_area == area and plan.attacker_index == opt.index:
                return 250  # this is the Pokemon our attack plan wants active
            # General case (including a forced promotion after our active
            # was knocked out): bring up whichever Pokémon is most ready to
            # fight -- energy already attached and healthy -- not just
            # whichever has the highest static card value.
            pokemon = get_pokemon_at(obs, area, opt.index, player_idx)
            if pokemon is not None:
                return attacker_value(pokemon) + 20
            return search_score(cid) + 20
        # Targeting the opponent's board (Boss's Orders / Iris's Fighting
        # Spirit): use the same attack plan so we pull up exactly the
        # Pokemon our best attack this turn is meant to hit, instead of
        # picking arbitrarily among their bench.
        plan = compute_attack_plan(obs)
        if plan.target_area == area and plan.target_index == opt.index:
            return 250
        pokemon = get_pokemon_at(obs, area, opt.index, player_idx)
        if pokemon is not None:
            # Fall back: favor high Prize value, low remaining HP targets --
            # easier and more rewarding knockouts.
            return prize_value(pokemon.id) * 60 - pokemon.hp
        return 100

    if ctx == SelectContext.DEVOLVE:
        if is_opponent_pokemon:
            return 100
        return 1

    # Default: mild preference for our own key cards
    return search_score(cid) + (5 if is_mine else 0)


def score_energy_option(opt, obs, ctx):
    if ctx in (SelectContext.DISCARD_ENERGY, SelectContext.TO_DECK_ENERGY):
        return -1  # discard/return least valuable energy first (they're mostly identical)
    return 1


def score_generic(opt, obs, select):
    """Fallback scorer for option types we don't specifically model."""
    if opt.type == OptionType.YES:
        return 5
    if opt.type == OptionType.NO:
        return 1
    if opt.type == OptionType.NUMBER:
        return opt.number if opt.number is not None else 0
    return 1


# ---------------------------------------------------------------------------
# Main decision function
# ---------------------------------------------------------------------------

def choose(select, obs):
    options = select.option
    n = len(options)
    scores = [0.0] * n

    for i, opt in enumerate(options):
        if select.type == SelectType.MAIN:
            scores[i] = score_main_option(opt, obs)
        elif select.type in (SelectType.CARD, SelectType.CARD_OR_ATTACHED_CARD):
            scores[i] = score_card_option(opt, obs, select)
        elif select.type == SelectType.ATTACHED_CARD:
            scores[i] = score_card_option(opt, obs, select)
        elif select.type == SelectType.ENERGY:
            scores[i] = score_energy_option(opt, obs, select.context)
        elif select.type == SelectType.ATTACK:
            a = ATTACK_TABLE.get(opt.attackId)
            scores[i] = (a.damage if a else 0)
        elif select.type == SelectType.EVOLVE:
            # opt.area/index = evolution card location (in hand); prefer
            # evolving toward Grimmsnarl ex / Froslass over other options.
            # When there's a choice of *which* board Pokémon to evolve (e.g.
            # two Impidimp in play), prefer the healthier one -- committing
            # a valuable evolution to an already-damaged body risks losing
            # it for free before it gets to do anything.
            hand_cid = resolve_cid(opt, obs, select) if opt.area == AreaType.HAND else None
            base = EVOLUTION_PRIORITY.get(hand_cid, 0) * 10 + search_score(hand_cid)
            target = get_pokemon_at(obs, opt.inPlayArea, opt.inPlayIndex, obs.current.yourIndex)
            hp_bonus = pokemon_hp_fraction(target) * 10 if target is not None else 0
            scores[i] = base + hp_bonus
        elif select.type == SelectType.SKILL:
            scores[i] = 1
        elif select.type == SelectType.COUNT:
            # Maximize helpful counts (draw more, move more damage, heal more)
            scores[i] = opt.number if opt.number is not None else i
        elif select.type == SelectType.YES_NO:
            if select.context == SelectContext.MULLIGAN:
                # never voluntarily mulligan away a working hand
                scores[i] = 1 if opt.type == OptionType.NO else 0
            elif select.context == SelectContext.IS_FIRST:
                # Prefer going second: lets Budew fire Itchy Pollen turn 1
                # (the first player can't attack on their first turn) and
                # gives us the extra draw on our first turn.
                scores[i] = 1 if opt.type == OptionType.NO else 0
            else:
                scores[i] = 1 if opt.type == OptionType.YES else 0
        elif select.type == SelectType.SPECIAL_CONDITION:
            scores[i] = 1
        else:
            scores[i] = score_generic(opt, obs, select)

    ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)

    k = select.maxCount
    # For pure cost-paying discards, only take the minimum required.
    if select.type in (SelectType.CARD, SelectType.CARD_OR_ATTACHED_CARD) and select.context in (
        SelectContext.DISCARD,
        SelectContext.DISCARD_CARD_OR_ATTACHED_CARD,
    ):
        k = select.minCount
    if select.type == SelectType.ENERGY and select.context in (
        SelectContext.DISCARD_ENERGY,
        SelectContext.TO_DECK_ENERGY,
    ):
        k = select.minCount
    if select.type == SelectType.MAIN:
        k = min(select.maxCount, 1) or select.minCount

    k = max(select.minCount, min(k, select.maxCount, n))
    chosen = ranked[:k]
    if len(chosen) < select.minCount:
        # pad with any remaining legal, unused indices
        for i in range(n):
            if i not in chosen:
                chosen.append(i)
            if len(chosen) >= select.minCount:
                break

    if select.type == SelectType.MAIN:
        me = my_state(obs)
        my_active = me.active[0] if me.active else None
        if my_active is not None and my_active.id == BUDEW:
            for i in chosen:
                if options[i].type == OptionType.ATTACK:
                    _game_flags["budew_fired"] = True
                    break

    return chosen


def agent(obs_dict: dict) -> list:
    """Implement Your Pokémon Trading Card Game Agent."""
    obs = to_observation_class(obs_dict)
    if obs.select is None:
        return read_deck_csv()

    try:
        result = choose(obs.select, obs)
        # Safety: validate before returning
        n = len(obs.select.option)
        result = [i for i in result if 0 <= i < n]
        result = list(dict.fromkeys(result))  # de-dupe, preserve order
        if len(result) < obs.select.minCount:
            for i in range(n):
                if i not in result:
                    result.append(i)
                if len(result) >= obs.select.minCount:
                    break
        result = result[: obs.select.maxCount]
        if len(result) < obs.select.minCount:
            # extremely defensive fallback
            result = list(range(min(obs.select.minCount, n)))
        return result
    except Exception:
        # Absolute fallback: never crash the match, always return something legal.
        n = len(obs.select.option)
        k = max(obs.select.minCount, min(obs.select.maxCount, n))
        return random.sample(range(n), k) if k > 0 else []
