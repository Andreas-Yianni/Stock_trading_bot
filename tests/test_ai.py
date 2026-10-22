"""AI opinion (Gemini): prompt, response parsing, cache/log, engine factor and report output.
The Gemini HTTP call is always mocked (sockets are blocked by conftest)."""
import json
import time

import pytest
import yaml
from conftest import make_ohlcv

from tradebot import ai, config, engine, indicators, market, report
from tradebot.engine import Analysis, Factor
from tradebot.news import NewsItem


def _news(n, market_news=False):
    src = "CNBC" if market_news else "Reuters"
    return [NewsItem(f"{'Macro' if market_news else 'Stock'} headline {i}", f"https://ex.com/{src}/{i}", src,
                     time.time() - 3600, 0.0) for i in range(1, n + 1)]


def _analysis(cfg, n_news=3, n_market=2) -> Analysis:
    df = indicators.add_all(make_ohlcv([100 + i * 0.5 for i in range(260)]))
    factors = [Factor("Τάση", 1.0, 3, "ανοδική"), Factor("RSI", 0.0, 1.5, "ουδέτερο")]
    score, bias = engine.combine(factors, cfg)
    asset = market.Asset("AAPL", "stock", None, "AAPL", "Apple")
    lv = {"support": [(220.0, 2)], "resistance": [(240.0, 3)]}
    plan = engine._build_plan(bias, df, lv, cfg) if bias != "NEUTRAL" else None
    return Analysis(asset, "1d", float(df.close.iloc[-1]), df, lv, factors, score, bias, plan,
                    _news(n_news), None, [], [], _news(n_market, market_news=True))


def _raw(score=0.5, news=None):
    return {"score": score, "confidence": "medium", "summary": "Θετικές ειδήσεις.", "action": "Συμφωνώ.",
            "agrees_with_rules": True, "risks": ["Earnings"],
            "news": news if news is not None else [{"n": 1, "effect": "+", "why": "καλά κέρδη"},
                                                   {"n": 4, "effect": "-", "why": "Fed"}]}


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _gemini_ok(raw):
    return FakeResponse(200, {"candidates": [{"content": {"parts": [
        {"text": "thinking...", "thought": True}, {"text": json.dumps(raw)}]}}]})


@pytest.fixture
def gemini(monkeypatch):
    """Turns the AI on with a fake key and records every (mocked) Gemini call."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    calls = []
    state = {"response": _gemini_ok(_raw())}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "body": json, "headers": headers})
        r = state["response"]
        return r(url) if callable(r) else r

    monkeypatch.setattr(ai.requests, "post", fake_post)
    return calls, state


def test_not_configured_without_key(cfg):
    assert ai.configured() is False
    assert ai.opinion(_analysis(cfg)) is None


def test_disabled_in_config(cfg, gemini):
    c = yaml.safe_load((config.ROOT / "config.yaml").read_text(encoding="utf-8"))
    c["ai"] = {"enabled": False}
    (config.ROOT / "config.yaml").write_text(yaml.safe_dump(c), encoding="utf-8")
    assert ai.opinion(_analysis(cfg)) is None
    assert gemini[0] == []


def test_prompt_numbers_symbol_then_market_news(cfg):
    a = _analysis(cfg, n_news=2, n_market=2)
    seen = ai._seen_items(a)
    assert [(s.n, s.market) for s in seen] == [(1, False), (2, False), (3, True), (4, True)]
    p = ai.build_prompt(a, seen)
    assert "[1] Stock headline 1 (Reuters)" in p
    assert "[3] Macro headline 1 (CNBC)" in p
    assert "Σκορ κανόνων" in p and "AAPL" in p


def test_parse_clamps_score_and_ignores_unknown_news(cfg):
    seen = ai._seen_items(_analysis(cfg))
    op = ai.parse(_raw(score=3.0, news=[{"n": 99, "effect": "+", "why": "x"}, {"n": "2", "effect": "-", "why": "y"},
                                        {"n": 1, "effect": "?", "why": "z"}]), seen, "m")
    assert op.score == 1.0
    assert [(s.n, s.effect) for s in op.cited] == [(2, "-")]
    assert ai.parse({"score": "bad", "confidence": "huge"}, ai._seen_items(_analysis(cfg)), "m").score == 0.0


def test_opinion_calls_gemini_once_then_uses_cache(cfg, gemini):
    calls, _ = gemini
    a = _analysis(cfg)
    op = ai.opinion(a)
    assert op.score == 0.5 and op.stance == "θετική" and not op.cached
    assert [(s.n, s.effect) for s in op.cited] == [(1, "+"), (4, "-")]
    body = calls[0]["body"]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert calls[0]["headers"]["x-goog-api-key"] == "test-key"
    assert "test-key" not in calls[0]["url"]                       # key in a header, not in the URL
    assert ai.opinion(a).cached is True                             # same headlines -> cache
    assert len(calls) == 1
    log = [json.loads(x) for x in ai.LOG_FILE.read_text(encoding="utf-8").splitlines()]
    assert len(log) == 1 and log[0]["symbol"] == "AAPL" and log[0]["ai_score"] == 0.5


def test_new_headlines_mean_a_new_call(cfg, gemini):
    calls, _ = gemini
    ai.opinion(_analysis(cfg, n_news=3))
    ai.opinion(_analysis(cfg, n_news=4))
    assert len(calls) == 2


def test_rate_limit_becomes_warning_not_crash(cfg, gemini):
    _, state = gemini
    state["response"] = FakeResponse(429, {"error": {"message": "quota"}})
    a = _analysis(cfg)
    engine.add_ai(a, cfg)
    assert a.ai is None
    assert any("AI" in w and "429" in w for w in a.warnings)
    assert not any(f.name.startswith("AI") for f in a.factors)


def test_falls_back_to_next_model_when_overloaded(cfg, gemini):
    calls, state = gemini
    first, second = ai.models()
    state["response"] = lambda url: FakeResponse(503, {"error": {"message": "overloaded"}}) if first in url \
        else _gemini_ok(_raw())
    op = ai.opinion(_analysis(cfg))
    assert op.model == second
    assert [first in c["url"] for c in calls] == [True, False]


def test_timeout_also_falls_back(cfg, gemini):
    _, state = gemini
    first = ai.models()[0]

    def resp(url):
        if first in url:
            raise ai.requests.Timeout("slow")
        return _gemini_ok(_raw())
    state["response"] = resp
    assert ai.opinion(_analysis(cfg)).model != first


def test_weight_zero_is_observation_only(cfg, gemini):
    a = _analysis(cfg)
    before = (a.score, a.bias, a.plan)
    engine.add_ai(a, cfg)
    f = [f for f in a.factors if f.name.startswith("AI")][0]
    assert f.weight == 0 and "μόνο παρατήρηση" in f.note
    assert (a.score, a.bias, a.plan) == before


def test_positive_weight_changes_score(cfg, gemini):
    _, state = gemini
    state["response"] = _gemini_ok(_raw(score=-1.0))
    c = yaml.safe_load((config.ROOT / "config.yaml").read_text(encoding="utf-8"))
    c["ai"] = {"weight": 3}
    (config.ROOT / "config.yaml").write_text(yaml.safe_dump(c), encoding="utf-8")
    a = _analysis(cfg)
    before = a.score
    engine.add_ai(a, cfg)
    assert a.score < before
    assert a.score == pytest.approx(engine.combine(a.factors, cfg)[0])


def test_report_shows_ai_section_and_cited_news(cfg, gemini):
    a = _analysis(cfg)
    engine.add_ai(a, cfg)
    html = report.to_html(a)
    assert "🤖 Γνώμη AI (Gemini): θετική" in html
    assert '[1] <a href="https://ex.com/Reuters/1">Stock headline 1</a>' in html
    assert "/ai AAPL" in html and "μόνο παρατήρηση" in html
    assert html.count("Γνώμη θετική") == 0                         # AI factor not repeated in "Τι βλέπω"
    full = report.ai_html(a)
    assert "Διάβασε επίσης" in full and "[2] Stock headline 2" in full and "[5] Macro headline 2" in full


def test_ai_html_without_key_explains(cfg):
    assert "GEMINI_API_KEY" in report.ai_html(_analysis(cfg))


def test_messages_stay_under_telegram_limit(cfg, gemini):
    _, state = gemini
    long_news = [{"n": i, "effect": "+", "why": "λόγος " * 30} for i in range(1, 7)]
    state["response"] = _gemini_ok(_raw(news=long_news))
    a = _analysis(cfg, n_news=15, n_market=8)
    for n in a.news + a.market_news:
        n.title = "Very long headline " * 8
    engine.add_ai(a, cfg)
    assert len(report.to_html(a)) <= 4096
    parts = report.split_message(report.ai_html(a))
    assert len(parts) >= 2 and all(len(p) <= 4096 for p in parts)
    assert "\n".join(parts) == report.ai_html(a)                   # nothing lost, split only at line breaks
    for p in parts:
        assert p.count("<a ") == p.count("</a>") and p.count("<i>") == p.count("</i>")
