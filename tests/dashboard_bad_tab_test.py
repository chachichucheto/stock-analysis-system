"""ダッシュボードの『悪い材料』タブと姿勢バナー(D52)。"""
from __future__ import annotations

from assoc.dashboard.build import _bad_tab, _stance_banner

STANCE = {"run_date": "2026-11-07", "stance": "投資を控える", "summary": "規制と資金繰りへの不安で急落",
          "causes": [{"cause": "民主党のAI規制", "status": "未解消", "detail": "方向が未定", "next_check": "11/12"}],
          "resume": {"verdict": "まだ", "reason": "理由が未解消", "conditions": ["規制の方向が出る"]}}
CALLS = [
    {"run_date": "2026-11-07", "_seq": 1, "target_type": "銘柄", "target": "2914 日本たばこ産業", "action": "撤退", "severity": "中",
     "scope": "個社", "urgency": "今日中", "reason": "ロシア事業の接収", "trigger": "11/5 接収",
     "codes": [{"code": "2914", "company_name": "日本たばこ産業"}],
     "alternatives": [{"code": "8058", "company_name": "三菱商事", "why": "ロシア比重が小さい"}],
     "reentry_conditions": "補償の道筋", "wrong_if": "影響が5%未満", "evidence_urls": ["https://example.com/a"]},
    {"run_date": "2026-11-07", "_seq": 2, "target_type": "銘柄", "target": "9984 ソフトバンクグループ", "action": "継続", "severity": "小",
     "scope": "個社", "reason": "影響軽微", "trigger": "x", "reentry_conditions": "-", "wrong_if": "-"}]


def _data(stance=STANCE, calls=CALLS, regime=None, watch=None):
    return {"stance": stance, "calls": calls, "latest_calls": calls, "regime": regime or {}, "watch": watch or {}}


def test_banner_shows_stance_and_resume_verdict():
    html = _stance_banner(_data())
    assert "投資を控える" in html and "まだ再開しない" in html and "stance-投資を控える" in html


def test_banner_falls_back_to_machine_regime():
    html = _stance_banner(_data(stance=None, calls=[], regime={"level": "慎重"}))
    assert "慎重" in html and "機械の判定のみ" in html


def test_tab_lists_causes_cards_alternatives_and_balance():
    html = _bad_tab(_data(), n_pick=0, n_caution=3)
    assert "下げた理由は解消されたか" in html and "未解消" in html
    assert "日本たばこ産業" in html and "三菱商事" in html and "戻ってよい条件" in html and "判断が外れる条件" in html
    assert "悪い材料:対応する" in html and "良い材料:本命" in html
    assert "株価が落ち着いただけでは" not in html          # 機械の判定が無いときは出さない


def test_tab_warns_that_calm_prices_are_not_a_reason_to_resume():
    regime = {"level": "投資を控える", "reasons": ["TOPIX が1日で -6.5%"],
              "stabilization": {"stabilized": True, "notes": ["✓ 安値から 3 営業日"]}}
    html = _bad_tab(_data(regime=regime), 0, 0)
    assert "株価が落ち着いただけでは、投資再開の理由になりません" in html


def test_tab_renders_before_any_night_judgment():
    html = _bad_tab(_data(stance=None, calls=[]), 0, 0)
    assert "まだ記録されていません" in html
