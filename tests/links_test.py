from assoc.events import links as L
from assoc.events.companies import CompanyIndex
from assoc.master.names import CompanyMaster

NAMES = {"9449": "ＧＭＯインターネットグループ", "4784": "ＧＭＯインターネット", "3633": "ＧＭＯペパボ",
         "3769": "ＧＭＯペイメントゲートウェイ", "3788": "ＧＭＯグローバルサイン", "4493": "サイバーセキュリティクラウド",
         "4704": "トレンドマイクロ"}


def _index():
    return CompanyIndex.from_master(CompanyMaster(NAMES), {"GMO": "9449"})


def test_group_edges_do_not_crowd_out_other_paths(tmp_path):
    lm = L.LinkMap()
    L.add_groups(lm, _index(), NAMES)
    f = tmp_path / "links.yaml"
    f.write_text('- {a: "9449", b: "4493", kind: "受益", note: "実例"}\n', encoding="utf-8")
    L.add_curated(lm, f)
    rel = lm.related("9449", limit=6)
    assert rel[0]["code"] == "4493" and rel[0]["via"][0].startswith("受益")      # 手で確かめた関係が先頭
    assert sum(1 for r in rel if r["via"][0].startswith("同じグループ")) == L.PER_KIND


def test_scenario_and_theme_and_comention_edges():
    lm = L.LinkMap()
    L.add_scenarios(lm, [{"scenario_id": "SC1", "code": "4493"}, {"scenario_id": "SC1", "code": "4704"}])
    L.add_themes(lm, {"P06": ["4493", "9449"]}, {"P06": "サイバー"})
    L.add_co_mentions(lm, _index(), ["GMOとサイバーセキュリティクラウドが提携", "ＧＭＯインターネットグループ、サイバーセキュリティクラウドと協業"])
    via = {r["code"]: r["via"] for r in lm.related("4493")}
    assert "過去のシナリオSC1で並んだ" in via["4704"]
    assert any("同じテーマ(サイバー)" == v for v in via["9449"]) and any("2回一緒に出た" in v for v in via["9449"])
