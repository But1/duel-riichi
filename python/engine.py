"""Two-player riichi variant. Shared by Flask and the browser's Python worker.

The authoritative state owns all concealed information. AI decisions receive
only the AI's hand and public observations. No external AI service is used.
"""
import json
import math
import random
from collections import Counter
from functools import lru_cache

from mahjong.hand_calculating.hand import HandCalculator
from mahjong.hand_calculating.hand_config import HandConfig, OptionalRules
from mahjong.meld import Meld
from mahjong.shanten import Shanten


NAMES = [str(n) + s for s in ("萬", "筒", "索") for n in range(1, 10)] + ["東", "南", "西", "北", "白", "發", "中"]
YAKU_NAMES = {
    "Menzen Tsumo": "門前清自摸和", "Riichi": "立直", "Ippatsu": "一發",
    "Pinfu": "平和", "Tanyao": "斷么九", "Iipeiko": "一盃口", "Iipeikou": "一盃口",
    "Rinshan Kaihou": "嶺上開花", "Chankan": "搶槓", "Haitei Raoyue": "海底摸月",
    "Houtei Raoyui": "河底撈魚", "Chiitoitsu": "七對子", "Dora": "寶牌",
    "Yakuhai (haku)": "役牌・白", "Yakuhai (hatsu)": "役牌・發", "Yakuhai (chun)": "役牌・中",
    "Yakuhai (seat wind east)": "自風・東", "Yakuhai (seat wind south)": "自風・南",
    "Yakuhai (round wind east)": "場風・東", "Sanshoku Doujun": "三色同順",
    "Yakuhai (wind of place)": "役牌・自風", "Yakuhai (wind of round)": "役牌・場風",
    "Yakuhai (east)": "役牌・東", "Yakuhai (south)": "役牌・南",
    "Yakuhai (west)": "役牌・西", "Yakuhai (north)": "役牌・北",
    "Ittsu": "一氣通貫", "Chantai": "混全帶么九", "Chanta": "混全帶么九",
    "Toitoi": "對對和", "San Ankou": "三暗刻", "San Kantsu": "三槓子",
    "Sanshoku Doukou": "三色同刻", "Honroutou": "混老頭", "Shou Sangen": "小三元",
    "Honitsu": "混一色", "Junchan": "純全帶么九", "Ryanpeikou": "二盃口",
    "Chinitsu": "清一色", "Kokushi Musou": "國士無雙", "Suu Ankou": "四暗刻",
    "Daisangen": "大三元", "Shousuushii": "小四喜", "Daisuushii": "大四喜",
    "Tsuuiisou": "字一色", "Chinroutou": "清老頭", "Ryuuiisou": "綠一色",
    "Chuuren Poutou": "九蓮寶燈", "Suukantsu": "四槓子",
    "Suu Kantsu": "四槓子", "Tsuu Iisou": "字一色", "Dai Suushii": "大四喜",
    "Kokushi Musou Juusanmen Matchi": "國士無雙十三面", "Suu Ankou Tanki": "四暗刻單騎",
    "Daburu Chuuren Poutou": "純正九蓮寶燈",
    "Double Riichi": "兩立直", "Tenhou": "天和", "Chiihou": "地和",
}


class RuleError(ValueError):
    pass


def counts(tiles):
    out = [0] * 34
    for tile in tiles:
        out[tile // 4] += 1
    return out


@lru_cache(maxsize=30000)
def shanten34(values, is_open=False):
    return Shanten().calculate_shanten(list(values), use_chiitoitsu=not is_open, use_kokushi=not is_open)


def shanten(hand, melds):
    return shanten34(tuple(counts(hand)), bool(melds))


def dora_type(indicator):
    t = indicator // 4
    if t < 27:
        return t // 9 * 9 + (t % 9 + 1) % 9
    if t < 31:
        return 27 + (t - 27 + 1) % 4
    return 31 + (t - 31 + 1) % 3


def ron_points(han, fu, dealer, yakuman=False):
    if yakuman:
        base = 8000 * max(1, han // 13)
        label = "役滿" if han < 26 else str(han // 13) + " 倍役滿"
    elif han >= 13:
        base, label = 8000, "累計役滿"
    elif han >= 11:
        base, label = 6000, "三倍滿"
    elif han >= 8:
        base, label = 4000, "倍滿"
    elif han >= 6:
        base, label = 3000, "跳滿"
    else:
        raw = fu * (2 ** (han + 2))
        base = min(2000, raw)
        label = "滿貫" if base == 2000 or han == 5 else ""
        if han == 5:
            base = 2000
    return int(math.ceil(base * (6 if dealer else 4) / 100) * 100), label


class MahjongAI:
    """Shanten/ukeire discard policy and a public-information guess heuristic."""

    @staticmethod
    def choose_discard(hand, melds, visible, dora):
        unique = {t // 4: t for t in hand}
        options = []
        for typ, tile in unique.items():
            rest = hand.copy()
            rest.remove(tile)
            options.append((shanten(rest, melds), typ, tile, rest))
        best = min(x[0] for x in options)
        ranked = []
        for value, typ, tile, rest in options:
            if value != best:
                continue
            c = counts(rest)
            ukeire = 0
            for t in range(34):
                remaining = max(0, 4 - visible[t])
                if not remaining or c[t] == 4:
                    continue
                c[t] += 1
                if shanten34(tuple(c), bool(melds)) < value:
                    ukeire += remaining
                c[t] -= 1
            # Keep dora and connected middle tiles on otherwise equal choices.
            keep = dora.count(typ) * 2 + (0 if typ >= 27 else 4 - abs(4 - typ % 9)) * .05
            ranked.append((ukeire - keep, typ, tile))
        return max(ranked)[2]

    @staticmethod
    def choose_guess(observation):
        # Intentionally accepts a sanitized observation, never a Game object.
        visible = observation["visible_counts"]
        river = observation["opponent_discards"]
        meld_types = observation["opponent_meld_types"]
        suits = Counter(t // 9 for t in meld_types if t < 27)
        recent = Counter(t // 9 for t in river[-6:] if t < 27)
        ranked = []
        for t in range(34):
            unseen = max(0, 4 - visible[t])
            score = unseen * (1.7 if t < 27 and 2 <= t % 9 <= 6 else 1.05)
            if t < 27:
                score += suits[t // 9] * .45 - recent[t // 9] * .12
                # Discarded neighboring tiles can indicate a changing shape.
                score += sum(.3 for x in river[-5:] if x < 27 and x // 9 == t // 9 and abs(x - t) in (1, 2))
            else:
                score *= .75
            score -= river.count(t) * .3
            ranked.append((score, -t, t))
        return [x[2] for x in sorted(ranked, reverse=True)[:2]]


class Game:
    VERSION = 3

    def __init__(self, seed=None, round_limit=10):
        if round_limit is not None and (type(round_limit) is not int or round_limit < 1):
            raise RuleError("局數必須是大於零的整數，或選擇無限制。")
        self.round_limit = round_limit
        self.rng = random.Random(seed)
        self.scores = [30000, 30000]
        self.pot = 0
        self.dealer = self.rng.randrange(2)
        self.round = 0
        self.events = []
        self.revision = 0
        self.match_over = False
        self._new_round()

    @classmethod
    def from_options(cls, payload=None):
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise RuleError("無效的對局設定。")
        return cls(round_limit=payload.get("round_limit", 10))

    def log(self, message):
        self.events.append({"id": len(self.events) + 1, "text": message})

    def _new_round(self):
        self.round += 1
        self.phase, self.step = "A", "drawn"
        self.turn = self.dealer
        self.hands = [[], []]
        self.melds = [[], []]
        self.rivers = [[], []]
        self.turns = [0, 0]
        self.riichi = [False, False]
        self.double_riichi = [False, False]
        self.drawn = [None, None]
        self.calls_made = False
        self.last_discard = None
        self.pending_kan = None
        self.declarer = None
        self.guesses = []
        self.guess_history = []
        self.b_cycle = 0
        self.b_left = 0
        self.b_draws = 0
        self.b_candidate = None
        self.b_score = None
        self.frozen_waits = []
        self.result = None
        self.kan_count = 0
        self._wait_cache = {}
        self.dice = [self.rng.randint(1, 6), self.rng.randint(1, 6)]
        tiles = list(range(136))
        self.rng.shuffle(tiles)
        cut = (sum(self.dice) * 2) % len(tiles)
        tiles = tiles[cut:] + tiles[:cut]
        self.dead = tiles[-14:]
        self.wall = tiles[:-14]
        for _ in range(3):
            for p in (self.dealer, 1 - self.dealer):
                self.hands[p].extend(self.wall[:4])
                del self.wall[:4]
        for p in (self.dealer, 1 - self.dealer):
            self.hands[p].append(self.wall.pop(0))
            self.hands[p].sort()
        self.log("第 %d 局開始，%s坐莊。骰子 %d・%d。" % (self.round, self.name(self.dealer), *self.dice))
        self._draw(self.dealer)

    @staticmethod
    def name(p):
        return "你" if p == 0 else "AI"

    def closed(self, p):
        return not any(m["opened"] for m in self.melds[p])

    def indicators(self):
        return [self.dead[4 + 2 * i] for i in range(self.kan_count + 1)]

    def _all_tiles(self, p, hand=None):
        return list(self.hands[p] if hand is None else hand) + [t for m in self.melds[p] for t in m["tiles"]]

    def _score(self, p, hand, win_tile, virtual=False, include_ura=False):
        # Also used for hypothetical B-stage waits. Scoring a shape does not
        # authorize a win; _win enforces the phase and declarer restrictions.
        if win_tile not in hand:
            return None
        melds = [Meld(meld_type=m["kind"], tiles=m["tiles"], opened=m["opened"], called_tile=m.get("called")) for m in self.melds[p]]
        indicators = self.indicators()
        if self.riichi[p] and include_ura:
            indicators += [self.dead[5 + 2 * i] for i in range(self.kan_count + 1)]
        config = HandConfig(
            is_tsumo=True, is_riichi=self.riichi[p],
            is_daburu_riichi=self.double_riichi[p],
            is_ippatsu=self.riichi[p] and self.phase == "B" and self.b_draws == 1 and not virtual,
            is_haitei=self.phase == "B" and not self.wall and not virtual,
            player_wind=27 if p == self.dealer else 28, round_wind=27,
            options=OptionalRules(has_open_tanyao=True, has_aka_dora=False),
        )
        response = HandCalculator().estimate_hand_value(self._all_tiles(p, hand), win_tile, melds=melds, dora_indicators=indicators, config=config)
        if response.error:
            return None
        amount, limit = ron_points(response.han, response.fu, p == self.dealer, any(y.is_yakuman for y in response.yaku))
        yaku = [{"name": YAKU_NAMES.get(y.name, y.name), "han": y.han_closed if self.closed(p) else y.han_open} for y in response.yaku]
        if include_ura and self.riichi[p] and not any(y.is_yakuman for y in response.yaku):
            ura_types = [dora_type(self.dead[5 + 2 * i]) for i in range(self.kan_count + 1)]
            ura_count = sum(ura_types.count(t // 4) for t in self._all_tiles(p, hand))
            if ura_count:
                for entry in yaku:
                    if entry["name"] == "寶牌":
                        entry["han"] -= ura_count
                yaku = [entry for entry in yaku if entry["han"]]
                yaku.append({"name": "裏寶牌", "han": ura_count})
        return {
            "han": response.han, "fu": response.fu, "points": amount, "limit": limit,
            "yaku": yaku,
            "fu_details": response.fu_details,
            "tsumo": True, "dealer": p == self.dealer,
        }

    def waits(self, p, valid=True):
        hand = self.hands[p]
        if len(hand) % 3 != 1:
            return []
        key = (p, tuple(hand), valid, self.riichi[p], self.kan_count, tuple((m["kind"], tuple(m["tiles"])) for m in self.melds[p]))
        if key in self._wait_cache:
            return self._wait_cache[key]
        if shanten(hand, self.melds[p]) != 0:
            self._wait_cache[key] = []
            return []
        owned = set(self._all_tiles(p))
        c = counts(hand)
        out = []
        for t in range(34):
            possible = next((x for x in range(t * 4, t * 4 + 4) if x not in owned), None)
            if possible is None:
                continue
            c[t] += 1
            complete = shanten34(tuple(c), bool(self.melds[p])) == -1
            c[t] -= 1
            if complete and (not valid or self._score(p, hand + [possible], possible, virtual=True)):
                out.append(t)
        self._wait_cache[key] = out
        return out

    def _draw(self, p, rinshan=False):
        if not self.wall:
            self._end_draw("牌山已盡，本局流局。")
            return
        if rinshan:
            tile = self.dead[self.kan_count]
            self.dead[self.kan_count] = self.wall.pop()
            self.kan_count += 1
        else:
            tile = self.wall.pop(0)
        self.hands[p].append(tile)
        self.hands[p].sort()
        self.drawn[p] = tile
        self.turn, self.step = p, "drawn"

    def _next(self, discarder):
        if all(n >= 18 for n in self.turns) or not self.wall:
            self._end_draw("18 巡結束，無人宣告聽牌。" if self.wall else "牌山已盡，本局流局。")
            return
        p = 1 - discarder
        if self.turns[p] >= 18:
            p = discarder
        self._draw(p)

    def call_options(self, p):
        if not self.last_discard or self.last_discard["player"] == p or self.turns[p] >= 18 or not self.wall:
            return []
        tile = self.last_discard["tile"]
        t = tile // 4
        hand = self.hands[p]
        same = [x for x in hand if x // 4 == t]
        out = []
        if len(same) >= 2:
            out.append({"kind": "pon", "consume": same[:2], "tiles": sorted(same[:2] + [tile]), "label": "碰"})
        if len(same) == 3 and self.kan_count < 4:
            out.append({"kind": "kan", "consume": same, "tiles": sorted(same + [tile]), "label": "大明槓"})
        if t < 27:
            for start in range(t - 2, t + 1):
                if start < 0 or start // 9 != t // 9 or (start + 2) // 9 != t // 9:
                    continue
                need = [x for x in range(start, start + 3) if x != t]
                if all(any(y // 4 == x for y in hand) for x in need):
                    consume = [next(y for y in hand if y // 4 == x) for x in need]
                    out.append({"kind": "chi", "consume": consume, "tiles": sorted(consume + [tile]), "label": "吃"})
        return out

    def kan_options(self, p):
        if self.kan_count >= 4 or not self.wall or self.phase != "A" or self.step != "drawn" or self.drawn[p] is None:
            return []
        out = []
        c = counts(self.hands[p])
        for t, n in enumerate(c):
            if n == 4:
                out.append({"kind": "ankan", "type": t, "label": "暗槓 " + NAMES[t]})
        for i, m in enumerate(self.melds[p]):
            t = m["tiles"][0] // 4
            if m["kind"] == "pon" and c[t]:
                out.append({"kind": "shouminkan", "type": t, "meld": i, "label": "加槓 " + NAMES[t]})
        return out

    def _discard(self, p, tile):
        if tile not in self.hands[p]:
            raise RuleError("請選擇自己手中的牌。")
        self.hands[p].remove(tile)
        self.drawn[p] = None
        self.turns[p] += 1
        self.rivers[p].append({"tile": tile, "face_down": False, "called": False, "phase": "A", "riichi": False})
        self.last_discard = {"player": p, "tile": tile}
        self.log(self.name(p) + "打出 " + NAMES[tile // 4] + "。")
        ready = self.waits(p)
        # A never allows a win, including on the declaration discard.
        if p == 1 and ready:
            self._declare(1, "riichi" if self.closed(1) and self.scores[1] >= 1000 else "tenpai")
            return
        if p == 0 and ready:
            self.step = "declare"
            return
        self._offer_response(p)

    def _offer_response(self, discarder):
        p = 1 - discarder
        if self.call_options(p):
            self.turn, self.step = p, "response"
        else:
            self._next(discarder)

    def _declare(self, p, mode):
        ready = self.waits(p)
        if not ready:
            raise RuleError("目前沒有具備役的自摸聽口，不能宣告。")
        if mode == "riichi":
            if not self.closed(p) or self.scores[p] < 1000:
                raise RuleError("立直需要門清且至少有 1,000 點。")
            self.riichi[p] = True
            self.double_riichi[p] = self.turns[p] == 1 and not self.calls_made
            self.scores[p] -= 1000
            self.pot += 1000
            self.rivers[p][-1]["riichi"] = True
        self.declarer = p
        self.frozen_waits = ready
        self.phase, self.step = "B", "guess"
        self.turn = 1 - p
        self.b_cycle = 1
        self.log(self.name(p) + ("宣告立直。" if mode == "riichi" else "宣告聽牌。") + "進入階段 B。")

    def _pass(self, p):
        self._next(self.last_discard["player"])

    def _call(self, p, idx):
        opts = self.call_options(p)
        if not isinstance(idx, int) or not 0 <= idx < len(opts) or self.pending_kan:
            raise RuleError("這個鳴牌選項已失效。")
        opt = opts[idx]
        tile = self.last_discard["tile"]
        other = 1 - p
        for t in opt["consume"]:
            self.hands[p].remove(t)
        self.rivers[other][-1]["called"] = True
        self.melds[p].append({"kind": opt["kind"], "tiles": opt["tiles"], "opened": True, "called": tile})
        self.calls_made = True
        self.turn, self.step = p, "drawn"
        self.drawn[p] = None
        self.log(self.name(p) + opt["label"] + " " + NAMES[tile // 4] + "。")
        if opt["kind"] == "kan":
            self._draw(p, rinshan=True)

    def _kan(self, p, idx):
        opts = self.kan_options(p)
        if not isinstance(idx, int) or not 0 <= idx < len(opts):
            raise RuleError("無法進行這個槓。")
        opt = opts[idx]
        tiles = [t for t in self.hands[p] if t // 4 == opt["type"]]
        # A permits kan and replacement draws, but never robbing a kan.
        self.pending_kan = {"player": p, "tile": tiles[0], "option": opt, "concealed": opt["kind"] == "ankan"}
        self._finish_added_kan()

    def _finish_added_kan(self):
        data = self.pending_kan
        p, opt = data["player"], data["option"]
        if data["concealed"]:
            tiles = [t for t in self.hands[p] if t // 4 == opt["type"]]
            for tile in tiles:
                self.hands[p].remove(tile)
            self.melds[p].append({"kind": "kan", "tiles": tiles, "opened": False})
        else:
            self.hands[p].remove(data["tile"])
            m = self.melds[p][opt["meld"]]
            m["kind"] = "shouminkan"
            m["tiles"].append(data["tile"])
        self.pending_kan = None
        self.calls_made = True
        self.log(self.name(p) + opt["label"] + "。翻開新寶牌指示牌。")
        self._draw(p, rinshan=True)

    def _guess(self, types):
        if not isinstance(types, list) or len(types) != 2 or len(set(types)) != 2 or any(type(t) is not int or not 0 <= t < 34 for t in types):
            raise RuleError("請選擇兩種不同的牌。")
        self.guesses = list(types)
        hit = bool(set(types).intersection(self.frozen_waits))
        self.guess_history.append({"cycle": self.b_cycle, "tiles": list(types), "hit": hit})
        self.log(self.name(1 - self.declarer) + "猜 " + "、".join(NAMES[t] for t in types) + ("，命中聽口！" if hit else "，未命中。"))
        if hit:
            self._end_draw("猜中和牌，成功阻止本局和牌。", kind="blocked")
            return
        if not self.wall:
            self._end_draw("牌山已盡，本局流局。")
            return
        self.b_left = min(5, len(self.wall))
        self.step, self.turn = "b_draw", self.declarer

    def _b_draw(self):
        if not self.wall or self.b_left <= 0:
            raise RuleError("目前不能自摸。")
        p = self.declarer
        tile = self.wall.pop(0)
        self.b_left -= 1
        self.b_draws += 1
        self.b_candidate = tile
        self.b_score = self._score(p, self.hands[p] + [tile], tile)
        self.step = "b_review"

    def _b_skip(self):
        p, tile = self.declarer, self.b_candidate
        winning = self.b_score is not None
        self.rivers[p].append({"tile": tile, "face_down": winning, "called": False, "phase": "B", "riichi": False})
        self.log(self.name(p) + ("蓋牌放過本次和牌。" if winning else "摸切 " + NAMES[tile // 4] + "。"))
        self.b_candidate, self.b_score = None, None
        if not self.wall:
            self._end_draw("已摸至海底，本局流局。")
        elif self.b_left == 0:
            self.b_cycle += 1
            self.step, self.turn = "guess", 1 - p
            self.log("五次自摸結束，進入第 %d 輪猜牌。" % self.b_cycle)
        else:
            self.step = "b_draw"

    def _win(self, p):
        if (self.phase != "B" or self.step != "b_review" or
                p != self.declarer or self.turn != p or
                self.b_candidate is None or self.b_score is None):
            raise RuleError("只有 B 階段的聽牌方可在自摸成功時和牌。")
        tile, score = self.b_candidate, self.b_score
        self.hands[p].append(tile)
        self.hands[p].sort()
        if self.riichi[p]:
            score = self._score(p, self.hands[p], tile, include_ura=True)
        payment = score["points"]
        self.scores[p] += payment + self.pot
        self.scores[1 - p] -= payment
        award = self.pot
        self.pot = 0
        previous_dealer = self.dealer
        self.dealer = p
        self.result = {"kind": "win", "winner": p, "tile": tile, "score": score, "pot": award,
                       "message": self.name(p) + "自摸和牌，獲得 %s 點。" % format(payment + award, ","),
                       "previous_dealer": previous_dealer, "waits": self.frozen_waits,
                       "ura_indicators": [self.dead[5 + 2 * i] for i in range(self.kan_count + 1)] if self.riichi[p] else []}
        self.phase, self.step = "result", "result"
        self.b_candidate, self.b_score = None, None
        self.log(self.result["message"])
        self._settle_match()

    def _end_draw(self, message, kind="draw"):
        self.result = {"kind": kind, "message": message, "waits": self.frozen_waits,
                       "winner": None}
        self.phase, self.step = "result", "result"
        self.log(message)
        self._settle_match()

    def _settle_match(self):
        # Every completed hand counts once, including blocked hands and draws.
        reason = "bankruptcy" if min(self.scores) <= 0 else (
            "round_limit" if self.round_limit is not None and self.round >= self.round_limit else None)
        self.match_over = reason is not None
        winner = None
        if self.match_over and self.scores[0] != self.scores[1]:
            winner = 0 if self.scores[0] > self.scores[1] else 1
        self.result.update(match_winner=winner, match_end_reason=reason)
        if self.match_over:
            outcome = "雙方同分，平手。" if winner is None else self.name(winner) + "贏得對局。"
            self.log(("已完成 %d 局，" % self.round if reason == "round_limit" else "一方點數歸零，") + outcome)

    def ai_observation(self, p=1):
        # Physical IDs are deduplicated because called river tiles also appear
        # in melds. Opponent concealed tiles and the wall never enter this data.
        visible = set(self.hands[p])
        visible.update(self.indicators())
        for q in range(2):
            visible.update(t for m in self.melds[q] for t in m["tiles"])
            visible.update(r["tile"] for r in self.rivers[q] if not r["face_down"] or q == p)
        return {
            "visible_counts": counts(visible),
            "opponent_discards": [r["tile"] // 4 for r in self.rivers[1 - p] if not r["face_down"]],
            "opponent_meld_types": [t // 4 for m in self.melds[1 - p] for t in m["tiles"]],
        }

    def _ai(self):
        p = 1
        if self.turn != p or self.phase == "result":
            raise RuleError("目前不是 AI 的回合。")
        if self.phase == "B":
            if self.step == "guess":
                self._guess(MahjongAI.choose_guess(self.ai_observation()))
            elif self.step == "b_draw":
                self._b_draw()
            elif self.step == "b_review":
                # AI takes a guaranteed legal win. Humans may press for value.
                if self.b_score:
                    self._win(1)
                else:
                    self._b_skip()
            return
        if self.step == "response":
            current = shanten(self.hands[p], self.melds[p])
            best = None
            for i, opt in enumerate(self.call_options(p)):
                if opt["kind"] == "kan":
                    continue
                rest = self.hands[p].copy()
                for t in opt["consume"]:
                    rest.remove(t)
                proposed = self.melds[p] + [{"tiles": opt["tiles"], "kind": opt["kind"], "opened": True}]
                after = min(shanten(rest[:k] + rest[k+1:], proposed) for k in range(len(rest)))
                all_types = [t // 4 for t in rest] + [t // 4 for m in proposed for t in m["tiles"]]
                yakuhai = any(m["kind"] != "chi" and m["tiles"][0] // 4 in (27, 28 if p != self.dealer else 27, 31, 32, 33) for m in proposed)
                tanyao = all(t < 27 and t % 9 not in (0, 8) for t in all_types)
                if after < current and (yakuhai or tanyao) and (best is None or after < best[0]):
                    best = (after, i)
            if best:
                self._call(p, best[1])
            else:
                self._pass(p)
            return
        if self.step == "drawn":
            opts = self.kan_options(p)
            if opts:
                self._kan(p, 0)
                return
            obs = self.ai_observation()
            tile = MahjongAI.choose_discard(self.hands[p], self.melds[p], obs["visible_counts"], [dora_type(t) for t in self.indicators()])
            self._discard(p, tile)

    def legal(self, p=0):
        if self.phase == "result":
            return {"new_match": True} if self.match_over else {"next_round": True}
        if self.turn != p:
            return {"ai": True} if p == 0 else {}
        if self.phase == "B":
            if self.step == "guess":
                return {"guess": True} if p != self.declarer else {}
            if p != self.declarer:
                return {}
            if self.step == "b_draw":
                return {"draw": True}
            if self.step == "b_review":
                return {"skip": True, "win": self.b_candidate is not None and bool(self.b_score)}
            return {}
        if self.step == "declare":
            return {"tenpai": bool(self.waits(p)), "riichi": self.closed(p) and self.scores[p] >= 1000, "continue": True}
        if self.step == "response":
            return {"pass": True, "calls": self.call_options(p)}
        return {"discard": True, "kans": self.kan_options(p)}

    def action(self, action, payload=None):
        payload = payload or {}
        if not isinstance(payload, dict):
            raise RuleError("無效的操作內容。")
        legal = self.legal()
        if action == "call":
            allowed = bool(legal.get("calls"))
        elif action == "kan":
            allowed = bool(legal.get("kans"))
        else:
            allowed = legal.get(action, False)
        if not allowed:
            raise RuleError("目前階段不能進行這個操作。")
        self._wait_cache = {}
        if action == "ai":
            self._ai()
        elif action == "discard":
            tile = payload.get("tile")
            if type(tile) is not int:
                raise RuleError("請選擇一張牌。")
            self._discard(0, tile)
        elif action in ("tenpai", "riichi"):
            self._declare(0, action)
        elif action == "continue":
            self._offer_response(0)
        elif action == "pass":
            self._pass(0)
        elif action == "call":
            self._call(0, payload.get("index"))
        elif action == "kan":
            self._kan(0, payload.get("index"))
        elif action == "guess":
            self._guess(payload.get("tiles"))
        elif action == "draw":
            self._b_draw()
        elif action == "skip":
            self._b_skip()
        elif action == "win":
            self._win(0)
        elif action == "next_round":
            self._new_round()
        elif action == "new_match":
            self.__init__(round_limit=payload.get("round_limit", 10))
        self.revision += 1
        return self.view()

    def view(self):
        reveal = self.phase == "result"
        players = []
        for p in range(2):
            rivers = []
            for r in self.rivers[p]:
                item = dict(r)
                if r["face_down"] and p != 0 and not reveal:
                    item["tile"] = None
                rivers.append(item)
            players.append({"name": self.name(p), "score": self.scores[p], "dealer": p == self.dealer,
                            "hand": list(self.hands[p]) if p == 0 or reveal else None,
                            "hand_count": len(self.hands[p]), "melds": self.melds[p], "river": rivers,
                            "turns": self.turns[p], "riichi": self.riichi[p], "closed": self.closed(p)})
        legal = self.legal()
        human_waits = self.waits(0) if len(self.hands[0]) % 3 == 1 and (self.phase != "B" or self.declarer == 0) else []
        current_score = self.b_score if self.phase == "B" and self.declarer == 0 else None
        return {
            "version": self.VERSION, "revision": self.revision, "round": self.round,
            "round_limit": self.round_limit, "phase": self.phase,
            "step": self.step, "turn": self.turn, "players": players, "dealer": self.dealer,
            "dice": self.dice, "wall_count": len(self.wall), "dead_count": 14, "pot": self.pot,
            "dora_indicators": self.indicators(), "dora_types": [dora_type(t) for t in self.indicators()],
            "drawn": self.drawn[0], "last_discard": self.last_discard, "legal": legal,
            "waits": human_waits, "shanten": shanten(self.hands[0], self.melds[0]),
            "declarer": self.declarer, "b_cycle": self.b_cycle, "b_left": self.b_left,
            "b_draws": self.b_draws, "candidate": self.b_candidate if self.declarer == 0 else None,
            "candidate_visible": self.b_candidate is not None, "candidate_score": current_score,
            "guesses": self.guesses, "guess_history": self.guess_history,
            "events": self.events[-35:], "result": self.result, "match_over": self.match_over,
        }


_browser_game = None


def dispatch_json(raw):
    global _browser_game
    try:
        data = json.loads(raw)
        if data.get("action") == "new_match":
            _browser_game = Game.from_options(data.get("payload"))
            view = _browser_game.view()
        elif _browser_game is None:
            raise RuleError("請先開始對局。")
        elif data.get("action") == "state":
            view = _browser_game.view()
        else:
            view = _browser_game.action(data.get("action"), data.get("payload"))
        return json.dumps({"ok": True, "state": view}, ensure_ascii=False)
    except (RuleError, ValueError, TypeError) as error:
        return json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)
