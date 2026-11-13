"""Rule-based headline scorer `rules-v1` (ML review 2026-11-13, BACKLOG 231-234, ADR in docs/decisions).

The LLM scorer needs an API key the brain does not hold, so the collector stored every headline unscored and the
design's shock blackout ("a high-relevance unscheduled shock blocks entries for `shock_blackout_min`") could never
fire. This scorer is the free, deterministic fallback: a curated keyword and phrase lexicon gives every prefiltered
headline the same fixed schema the LLM fills (relevance, rates, risk, dollar, surprise, shock), plus a shock
confidence. The collector computes it for every prefiltered headline, stores it in the `rule_*` columns, and uses it
as the row's score (`scorer` = SCORER) whenever the LLM did not score that row; an LLM score takes precedence and
both are kept (goldbot/data/news_collector.py).

Shock, direction and surprise read the title only. Tuned once on the tune half of 150 hand-labelled real headlines
(tests/fixtures/news_rules/real_labelled.yaml, BACKLOG 234); the held-out half is reported, never tuned on.

Rules, in the order they apply:
* relevance: from the title (summaries add at most SUMMARY_CAP: on the real feeds they are multi-headline wraps;
  another economy's data or central bank counts FOREIGN_WEIGHT, a US release US_DATA_WEIGHT): the sum of the
  distinct gold drivers named (gold itself, Fed and rates, US data, the dollar,
  geopolitics, central-bank gold buying, ETF flows, tariffs, other central banks), capped at 1; a shock sets it to
  at least SHOCK_RELEVANCE. Times the source weight.
* direction (rates, dollar, risk) and surprise: votes of the phrases found; a tie is neutral. A negation within
  NEG_WINDOW words before a phrase ("rules out a rate cut", "denies missile attack") flips a direction and cancels a
  shock or a risk-off vote.
* shock: only NEW events (safety review 2026-11-13): ongoing-conflict and market-reaction framing ("continue",
  "amid", "kill 12", "shrug off", "steady") takes ONGOING_PENALTY off, and `mark_repeats` unflags a later report of
  an event already reported in the lookback by any feed. The engine caps rule-shock blackout minutes per UTC day
  (`news.shock_window`, `rule_cap_min`). Only phrases that name an unscheduled event (missiles fired, invasion, declared war, emergency central-bank
  action, bank collapse, sovereign default, coup). Confidence = the phrase's strength, less HEDGE_PENALTY when hedged
  ("could", "threatens", "fears of", "anniversary", a question) and times the source weight; the flag is set only at
  `min_confidence` and above, so a guessed shock never blocks entries. A scheduled release is never a shock: none of
  the shock phrases names one.

The blackout only tightens: a rule shock blocks entries for `shock_blackout_min`, exactly like an LLM shock, and
exits never read it (engine `_blackout_close` skips `news_shock`; RiskGate is entries only).
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from goldbot.data.news import HeadlineScore, Surprise

SCORER = "rules-v1"
LLM_SCORER = "llm"
DEFAULT_SOURCE_WEIGHT = 0.9            # a feed not in news.source_weights
SHOCK_RELEVANCE = 0.9
HEDGE_PENALTY = 0.35
NEG_WINDOW = 4                         # words before a phrase searched for a negation or a hedge

_NEG = re.compile(r"\b(?:no|not|never|denies|denied|deny|won't|wont|unlikely|without|averts?|averted|avoids?|avoided|"
                  r"rejects?|rejected|fails?|failed|halts?|halted|ends?|ended|calls? off|called off|rules? out|"
                  r"ruled out|dismiss(?:es|ed)?|false|fake|hoax)\b", re.I)
_HEDGE = re.compile(r"\b(?:could|may|might|would|threatens?|threatened|warns?|warned|fears?|feared|risk of|possible|"
                    r"potential|if|weighs?|considers?|mulls?|anniversary|years? after|analysis|explainer|what|why|how|"
                    r"scenario|preparing)\b", re.I)
_CLAUSE_END = re.compile(r"[,;:.!?—–|]| - ")

# --------------------------------------------------------------------------- relevance: distinct gold drivers
_DRIVERS: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"\b(?:gold|xau(?:usd)?|bullion|precious metals?)\b", re.I), 0.45),
    (re.compile(r"\b(?:fed|fomc|powell|federal reserve|rate (?:cut|hike)s?|interest rates?|treasury yields?|"
                r"yields?|dot plot)\b", re.I), 0.6),          # 0.4 -> 0.6, BACKLOG 234 tune half ("Fed holds rates")
    (re.compile(r"\b(?:cpi|ppi|pce|inflation|payrolls?|nfp|jobs report|unemployment|jobless|gdp|retail sales|ism)\b",
                re.I), 0.35),
    (re.compile(r"\b(?:dollar|dxy|greenback|usd)\b", re.I), 0.3),
    (re.compile(r"\b(?:war|missiles?|rockets?|attacks?|air ?strikes?|invasion|invades?|invaded|sanctions?|"
                r"geopolitic\w*|troops|military|nuclear|ceasefire|truce|conflict|escalat\w*|tensions?|coup)\b", re.I),
     0.35),
    (re.compile(r"\bcentral banks?\b.*\b(?:gold|bullion)\b|\b(?:gold|bullion)\b.*\bcentral banks?\b|\bpboc\b.*\bgold\b",
                re.I), 0.3),
    (re.compile(r"\b(?:gold etfs?|gld|spdr gold|etf (?:inflows?|outflows?|holdings))\b", re.I), 0.3),
    (re.compile(r"\b(?:tariffs?|trade war)\b", re.I), 0.3),
    (re.compile(r"\b(?:ecb|boj|boe|pboc|snb|bank of (?:england|japan))\b", re.I), 0.2),
)

# BACKLOG 234 tune half (real headlines, 2026-11-13): titles about another economy, US markers, summary weighting
_FOREIGN = re.compile(r"\b(?:uk|britain|british|boe|bank of england|eurozone|euro area|ecb|germany|german|france|french|"
                      r"spain|spanish|italy|italian|china|chinese|pboc|japan|japanese|boj|australia\w*|rba|new zealand|"
                      r"nz|rbnz|canada|canadian|boc|bank of canada|swiss|snb|norway|sweden|india|korea\w*)\b", re.I)
_US = re.compile(r"\b(?:us|u\.s\.|united states|american|fed|fomc|federal reserve|powell|treasury|treasuries|nfp|"
                 r"non-farm|payrolls|ism|pce|wall street)\b", re.I)
US_DATA_WEIGHT = 0.6
FOREIGN_WEIGHT = 0.1
SUMMARY_WEIGHT = 0.25
SUMMARY_CAP = 0.2

# --------------------------------------------------------------------------- shock: unscheduled events only
_SHOCK: tuple[tuple[re.Pattern[str], float, str], ...] = (   # (phrase, strength, event group)
    (re.compile(r"\b(?:fires?|fired|launch(?:es|ed)?)\s+(?:\w+\s+){0,3}(?:missiles?|rockets?|drones?)\b", re.I), 0.95, "conflict"),
    (re.compile(r"\b(?:missile|rocket|drone)s?\s+(?:strikes?|attacks?|hits?|barrage)\b", re.I), 0.95, "conflict"),
    (re.compile(r"\bair ?strikes?\b", re.I), 0.95, "conflict"),
    (re.compile(r"\bdeclar(?:es|ed|ing)\s+war\b|\bwar\s+(?:breaks out|erupts|erupted)\b", re.I), 0.95, "conflict"),
    (re.compile(r"\binvad(?:es|ed|ing)\b|\binvasion\s+of\b", re.I), 0.95, "conflict"),
    (re.compile(r"\bnuclear\s+(?:strike|attack|test|detonation|explosion)\b", re.I), 0.95, "conflict"),
    (re.compile(r"\bemergency\s+(?:rate\s+(?:cut|hike)|meeting|(?:fed|fomc|ecb|boe|boj)\b|liquidity|action)|"
                r"\bintermeeting\b|\bunscheduled\s+(?:meeting|rate)\b", re.I), 0.95, "financial"),
    (re.compile(r"\bterror(?:ist)?\s+attacks?\b|\bcoup\b|\bmartial law\b|\bassassinat\w+", re.I), 0.9, "conflict"),
    (re.compile(r"\bbanks?\s+(?:collapses?|collapsed|fails?|failed|failure)\b|\bbank run\b", re.I), 0.9, "financial"),
    (re.compile(r"\bdefaults?\s+on\s+(?:its\s+)?(?:\w+\s+)?(?:debt|bonds?)\b|\bsovereign default\b", re.I), 0.9, "financial"),
    (re.compile(r"\b(?:attacks?|attacked|strikes?|struck)\s+(?:on\s+)?(?:\w+\s+){0,2}"
                r"(?:oil|refiner\w*|tankers?|nuclear|embassy|bases?|shipping)\b", re.I), 0.85, "conflict"),
)

# safety review 2026-11-13 (HIGH): a running conflict's updates and market-reaction lines are not new events. Any of
# these anywhere in the headline takes ONGOING_PENALTY off the shock confidence ("Israeli airstrikes kill 12 in Gaza",
# "Markets shrug off airstrikes", "Gold steady as missile strikes continue")
_ONGOING = re.compile(r"\b(?:continu\w*|ongoing|steady|steadies|steadied|shrugs?\s+off|shrugged\s+off|kill(?:s|ed)?\s+"
                      r"(?:at least\s+)?\d+|death toll|casualt\w*|amid|rages?|raging|latest|resum\w*|renewed|"
                      r"(?:second|third|fourth|fifth|\d+(?:st|nd|rd|th))\s+(?:day|week|night)|day\s+\d+)\b", re.I)
ONGOING_PENALTY = 0.35
# words that name the kind of event, not the event itself: left out when two reports are compared (mark_repeats)
_EVENT_WORDS = frozenset(
    "a an the at in on of to for as and or by from with into over after before more new says say said report reports "
    "reported sources breaking update gold jumps jump rises rise falls fall spikes spike surges surge bid haven "
    "fires fire fired launches launch launched missile missiles rocket rockets drone drones ballistic strike strikes "
    "struck airstrike airstrikes air attack attacks attacked war declares declared invades invaded invasion nuclear "
    "emergency meeting rate cut hike bank banks collapse collapses default defaults debt bonds coup terror terrorist "
    "toward towards against near".split())
REPEAT_OVERLAP = 0.5        # share of the shorter report's event words both share: the same event

# --------------------------------------------------------------------------- direction phrases
_HAWKISH = re.compile(r"\b(?:rate hikes?|hikes?\s+(?:interest\s+)?rates?|rais(?:es|ed|ing)\s+(?:interest\s+)?rates?|"
                      r"hawkish|higher for longer|tighten\w*)\b", re.I)
_DOVISH = re.compile(r"\b(?:rate cuts?|cuts?\s+(?:interest\s+)?rates?|cutting\s+rates?|lower(?:s|ed|ing)?\s+"
                     r"(?:interest\s+)?rates?|dovish|easing|more cuts)\b", re.I)
_DATA = re.compile(r"\b(?:cpi|pce|inflation|payrolls?|nfp|jobs report|jobs data|gdp|retail sales|ism|pmi|"
                   r"unemployment|jobless claims)\b", re.I)
_BAD_IF_HIGH = re.compile(r"\b(?:unemployment|jobless)\b", re.I)    # higher than expected = weaker economy
_BEAT = re.compile(r"\b(?:beats?\s+(?:estimates|expectations|forecasts?|consensus)|above\s+(?:expectations|"
                   r"forecasts?|estimates|consensus)|(?:stronger|hotter|higher)[- ]than[- ](?:expected|forecast))\b",
                   re.I)
_MISS = re.compile(r"\b(?:miss(?:es|ed)?\s+(?:estimates|expectations|forecasts?|consensus)|below\s+(?:expectations|"
                   r"forecasts?|estimates|consensus)|(?:weaker|cooler|softer|lower)[- ]than[- ](?:expected|forecast))\b",
                   re.I)
_INLINE = re.compile(r"\b(?:in line with (?:expectations|forecasts?|estimates|consensus)|as expected)\b", re.I)
_USD = r"(?:dollar|greenback|dxy|usd)"
_USD_UP = re.compile(rf"\b{_USD}\s+(?:index\s+)?(?:rises?|rose|gains?|gained|jumps?|jumped|rall(?:y|ies|ied)|climbs?|"
                     rf"climbed|strengthens?|strengthened|firms?|firmed|surges?|surged|soars?|soared|hits?\s+\S+\s+high)"
                     rf"\b|\b(?:stronger|firmer)\s+{_USD}\b|\b{_USD}\s+strength\b", re.I)
_USD_DOWN = re.compile(rf"\b{_USD}\s+(?:index\s+)?(?:falls?|fell|drops?|dropped|slips?|slipped|slides?|slid|weakens?|"
                       rf"weakened|tumbles?|tumbled|sinks?|sank|slumps?|slumped|hits?\s+\S+\s+low)\b|"
                       rf"\b(?:weaker|softer)\s+{_USD}\b|\b{_USD}\s+weakness\b", re.I)
_RISK_OFF = re.compile(r"\b(?:war|missiles?|rockets?|air ?strikes?|invasion|invades?|invaded|sanctions?|attacks?|"
                       r"escalat\w*|safe[- ]haven|risk[- ]off|sell-?off|crisis|stocks\s+(?:plunge|tumble|crash|sink)\w*|"
                       r"tariffs?\s+(?:on|imposed)|imposes?\s+tariffs?|trade war|coup|default)\b", re.I)
_RISK_ON = re.compile(r"\b(?:ceasefire|truce|peace\s+(?:deal|talks|agreement|accord)|de-?escalat\w*|risk[- ]on|"
                      r"trade deal|stocks\s+(?:rally|surge|soar)\w*|record high for stocks)\b", re.I)


class RuleScore(HeadlineScore):
    confidence: float          # shock confidence 0-1; `shock` is set only at the collector's min_confidence and above


def _preceding(text: str, start: int) -> str:
    """The up to NEG_WINDOW words before `start`, cut at the nearest clause boundary."""
    before = text[:start]
    cut = [m.end() for m in _CLAUSE_END.finditer(before)]
    if cut:
        before = before[cut[-1]:]
    return " ".join(before.split()[-NEG_WINDOW:])


def _negated(text: str, m: re.Match[str]) -> bool:
    return bool(_NEG.search(_preceding(text, m.start())))


def _hedged(text: str, m: re.Match[str]) -> bool:
    """A hedge word just before the phrase, or the phrase's sentence is a question."""
    end = re.search(r"[.!?]", text[m.end():])
    return bool(_HEDGE.search(_preceding(text, m.start()))) or (end is not None and end.group() == "?")


def _votes(text: str, pos: re.Pattern[str], neg: re.Pattern[str]) -> int:
    """Net direction: +1 per un-negated `pos` phrase and per negated `neg` phrase, -1 for the opposite."""
    v = 0
    for m in pos.finditer(text):
        v += -1 if _negated(text, m) else 1
    for m in neg.finditer(text):
        v += 1 if _negated(text, m) else -1
    return v


def shock_confidence(text: str, source_weight: float = 1.0) -> float:
    """0-1: the strongest un-negated shock phrase's strength, less HEDGE_PENALTY when hedged, times the source weight."""
    best = 0.0
    for pat, strength, _ in _SHOCK:
        for m in pat.finditer(text):
            if _negated(text, m):
                continue
            best = max(best, strength - (HEDGE_PENALTY if _hedged(text, m) else 0.0))
    if best and _ONGOING.search(text):
        best -= ONGOING_PENALTY
    return round(min(max(best * source_weight, 0.0), 1.0), 4)


def shock_group(text: str) -> str:
    """The event group of the first un-negated shock phrase ("conflict" or "financial"), "" without one."""
    for pat, _, group in _SHOCK:
        if any(not _negated(text, m) for m in pat.finditer(text)):
            return group
    return ""


def _event_words(text: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z][a-z']+", text.lower()) if len(w) > 2 and w not in _EVENT_WORDS)


def _same_event(a: str, b: str) -> bool:
    if shock_group(a) != shock_group(b):
        return False
    wa, wb = _event_words(a), _event_words(b)
    if not wa or not wb:
        return True                     # nothing but event words: indistinguishable reports, the same event
    return len(wa & wb) / min(len(wa), len(wb)) >= REPEAT_OVERLAP


def mark_repeats(items: pd.DataFrame, scores: Sequence[RuleScore], earlier: pd.DataFrame | None,
                 lookback: pd.Timedelta) -> list[tuple[RuleScore, str]]:
    """Safety review 2026-11-13: only the FIRST report of an event is a shock. Each rule shock in `items` (one score per
    row, same order) whose event (same group, REPEAT_OVERLAP of its event words) was already reported in the
    `lookback` before it, by any feed, is unflagged and names the first report's item_id. `earlier` = stored rows
    (rule_shock or rule_repeat_of set count as reports); reports in `items` count in received (then published) order.
    Returns (score, repeat_of) per row, "" = not a repeat."""
    def text(row: Any) -> str:
        return f"{row.get('title') or ''}. {row.get('summary') or ''}"
    reports: list[tuple[pd.Timestamp, str, str]] = []          # (received, text, first report id)
    if earlier is not None and not earlier.empty and "rule_shock" in earlier.columns:
        rep = earlier.get("rule_repeat_of", pd.Series("", index=earlier.index)).fillna("").astype(str)
        prior = earlier[earlier["rule_shock"].fillna(False).astype(bool) | rep.ne("")]
        for _, row in prior.iterrows():
            first = str(row.get("rule_repeat_of") or "") or str(row["item_id"])
            reports.append((pd.Timestamp(row["received_utc"]), text(row), first))
    rows = [r for _, r in items.iterrows()]
    order = sorted(range(len(rows)), key=lambda i: (pd.Timestamp(rows[i]["received_utc"]),
                                                    pd.Timestamp(rows[i].get("ts_utc", rows[i]["received_utc"]))))
    out: list[tuple[RuleScore, str]] = [(s, "") for s in scores]
    for i in order:
        s = scores[i]
        if not s.shock:
            continue
        at, t = pd.Timestamp(rows[i]["received_utc"]), text(rows[i])
        first = next((f for when, txt, f in reports if at - lookback <= when <= at and _same_event(t, txt)), "")
        if first:
            out[i] = (s.model_copy(update={"shock": False}), first)
        reports.append((at, t, first or str(rows[i]["item_id"])))
    return out


def _relevance(title: str, summary: str) -> float:
    """Relevance before the source weight, from the title (BACKLOG 234 tune half, 2026-11-13: the feeds' summaries
    are market wraps listing a dozen unrelated headlines, which put every wrap at 0.9). A driver named only in the
    summary adds SUMMARY_WEIGHT of its weight (at most SUMMARY_CAP in all). A title about another economy (a foreign
    country or central bank and no US marker) gets FOREIGN_WEIGHT for rates and data; a US data release gets
    US_DATA_WEIGHT, enough to clear 0.5 alone."""
    foreign = bool(_FOREIGN.search(title)) and not _US.search(title)
    raw, extra = 0.0, 0.0
    for k, (pat, weight) in enumerate(_DRIVERS):
        if k in (1, 2) and foreign:
            weight = FOREIGN_WEIGHT
        elif k == 2 and _US.search(title):
            weight = US_DATA_WEIGHT
        if pat.search(title):
            raw += weight
        elif summary and pat.search(summary):
            extra += SUMMARY_WEIGHT * weight
    return min(raw + min(extra, SUMMARY_CAP), 1.0)


def score_text(item: str, text: str, source_weight: float = 1.0, min_confidence: float = 0.8,
               summary: str = "") -> RuleScore:
    """One headline -> its rules-v1 score (see the module docstring). `text` is the title: shock, direction and
    surprise read it alone; `summary` only adds a little relevance (BACKLOG 234: summaries are multi-headline wraps)."""
    w = min(max(source_weight, 0.0), 1.0)
    conf = shock_confidence(text, w)
    shock = conf >= min_confidence
    raw = _relevance(text, summary)
    if shock:
        raw = max(raw, SHOCK_RELEVANCE)
    rates_v, usd_v = _votes(text, _HAWKISH, _DOVISH), _votes(text, _USD_UP, _USD_DOWN)
    surprise: Surprise = "none"
    if _DATA.search(text):
        beat, miss = bool(_BEAT.search(text)), bool(_MISS.search(text))
        if beat != miss:
            higher = beat
            strong = higher != bool(_BAD_IF_HIGH.search(text))   # a higher jobless number is a weaker economy
            surprise = "beat" if strong else "miss"
            rates_v += 1 if strong else -1
            usd_v += 1 if strong else -1
        elif _INLINE.search(text):
            surprise = "inline"
    risk_v = sum(0 if _negated(text, m) else -1 for m in _RISK_OFF.finditer(text)) \
        + sum(-1 if _negated(text, m) else 1 for m in _RISK_ON.finditer(text))
    if shock:
        risk_v = min(risk_v, -1)
    return RuleScore(id=item, relevance=round(raw * w, 4), confidence=conf, shock=shock,
                     rates="hawkish" if rates_v > 0 else "dovish" if rates_v < 0 else "neutral",
                     dollar="positive" if usd_v > 0 else "negative" if usd_v < 0 else "neutral",
                     risk="risk_off" if risk_v < 0 else "risk_on" if risk_v > 0 else "neutral",
                     surprise=surprise)


def score_rules(items: pd.DataFrame, source_weights: Mapping[str, float] | None = None,
                min_confidence: float = 0.8) -> list[RuleScore]:
    """rules-v1 scores for every row of `items` (item_id, source, title, summary): free and deterministic, so the
    collector calls it for every prefiltered headline whether or not the LLM scores it."""
    weights = dict(source_weights or {})
    out = []
    for iid, src, title, summ in zip(items["item_id"], items["source"], items["title"], items["summary"]):
        summary = "" if summ is None or summ != summ else str(summ)
        out.append(score_text(str(iid), str(title or ""), weights.get(str(src), DEFAULT_SOURCE_WEIGHT),
                              min_confidence, summary=summary))
    return out
