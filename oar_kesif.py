"""
oar_kesif.py — Otomatik Strateji Keşif Motoru (YENİ — yalnız LOKAL araştırma)
═══════════════════════════════════════════════════════════════════════════════
AMAÇ: "Agent en iyi sistemi bulsun." Sinyal-blok kütüphanesindeki (oar_sinyaller)
yöntemlerin HANGİ KOMBİNASYONU, OUT-OF-SAMPLE + MALİYET SONRASI en iyi sonucu
veriyor? Bunu disiplinle (overfit'e karşı) arar.

DİSİPLİN (profesyonel — kendini kandırmamak için):
  1. Veri zamanca üçe bölünür: ARAMA (walk-forward IS/OOS) + dokunulmamış HOLDOUT.
  2. Tüm blok kombinasyonları ARAMA setinde walk_forward ile OOS skoruyla sıralanır.
  3. Yalnız en iyi N aday, HİÇ GÖRÜLMEMİŞ HOLDOUT'ta doğrulanır (çoklu-test koruması).
     Holdout'ta da ayakta kalan kombinasyon = gerçek aday sistem.
  4. Maliyet (fee+slippage) sinyal üretiminde zaten düşülmüştür (outcome net'tir).
  5. Az örneklemli "harika" sonuçlar min_trade eşiğiyle elenir.

Girdi: aday sinyaller listesi — her biri feature dict + net 'outcome'/'pct'/'ts'.
Bunları oar_local_backtest (OAR Asia Range) ya da başka bir üretici sağlar; keşif
motoru kaynaktan bağımsızdır (saf, test edilebilir).
"""
import itertools

from walk_forward import walk_forward, metrik
from oar_sinyaller import blok_uygula, AKTIF_BLOKLAR


def _filtre(sinyaller: list, bloklar: list):
    """
    Tüm bloklardan geçen sinyaller. Bir blok None (veri yok) dönerse bu
    kombinasyon UYGULANAMAZ → None döner (keşifte atlanır).
    """
    out = []
    for s in sinyaller:
        gecti = True
        for ad in bloklar:
            r = blok_uygula(s, ad)
            if r is None:
                return None        # veri yok → kombinasyon geçersiz
            if not r:
                gecti = False
                break
        if gecti:
            out.append(s)
    return out


def _holdout_ayir(sinyaller: list, holdout_orani: float):
    """Zamanca son holdout_orani dilimi = dokunulmamış HOLDOUT; öncesi ARAMA."""
    if not sinyaller:
        return [], []
    ts_sirali = sorted(s["ts"] for s in sinyaller)
    kesim = ts_sirali[int(len(ts_sirali) * (1 - holdout_orani))]
    arama = [s for s in sinyaller if s["ts"] < kesim]
    holdout = [s for s in sinyaller if s["ts"] >= kesim]
    return arama, holdout


def kesfet(sinyaller: list, blok_havuzu: list = None,
           min_k: int = 1, max_k: int = 3, fold: int = 4, is_oran: float = 0.7,
           holdout_orani: float = 0.2, min_trade: int = 20, ust_n: int = 5) -> dict:
    """
    Blok kombinasyonlarını ARAMA setinde OOS ile sıralar, en iyi ust_n adayı
    HOLDOUT'ta doğrular. Döner: sıralı aday sistemler (OOS + holdout metrikleri).
    """
    blok_havuzu = blok_havuzu or AKTIF_BLOKLAR
    arama, holdout = _holdout_ayir(sinyaller, holdout_orani)

    adaylar = []
    for k in range(min_k, max_k + 1):
        for kombo in itertools.combinations(blok_havuzu, k):
            f = _filtre(arama, list(kombo))
            if f is None or len(f) < min_trade:
                continue
            wf = walk_forward(lambda _p: f, ["x"], fold_sayisi=fold, is_oran=is_oran)
            oos = wf.get("toplu_oos_metrik", {})
            adaylar.append({
                "bloklar": list(kombo),
                "oos_puan": oos.get("puan", 0),
                "oos_wr": oos.get("win_rate", 0),
                "oos_sharpe": oos.get("sharpe", 0),
                "oos_trade": oos.get("toplam_sinyal", 0),
                "arama_sinyal": len(f),
            })

    adaylar.sort(key=lambda x: (x["oos_puan"], x["oos_trade"]), reverse=True)

    # En iyi ust_n adayı HOLDOUT'ta doğrula (arama sırasında hiç görülmedi)
    en_iyiler = adaylar[:ust_n]
    for a in en_iyiler:
        hf = _filtre(holdout, a["bloklar"]) or []
        hm = metrik(hf)
        a["holdout_puan"] = hm.get("puan", 0)
        a["holdout_wr"] = hm.get("win_rate", 0)
        a["holdout_sharpe"] = hm.get("sharpe", 0)
        a["holdout_trade"] = hm.get("toplam_sinyal", 0)
        # Sağlamlık: OOS'ta iyi + holdout'ta da ayakta mı?
        a["saglam"] = (a["oos_puan"] >= 50 and a["holdout_puan"] >= 50)

    return {
        "toplam_aday": len(adaylar),
        "arama_sinyal": len(arama),
        "holdout_sinyal": len(holdout),
        "holdout_orani": holdout_orani,
        "en_iyiler": en_iyiler,
        "aktif_blok_havuzu": blok_havuzu,
    }


def rapor(sonuc: dict) -> str:
    sat = [
        "═══ STRATEJİ KEŞİF RAPORU ═══",
        f"Aday kombinasyon: {sonuc['toplam_aday']} | "
        f"ARAMA sinyal: {sonuc['arama_sinyal']} | HOLDOUT sinyal: {sonuc['holdout_sinyal']}",
        f"Blok havuzu: {', '.join(sonuc['aktif_blok_havuzu'])}",
        "── En iyi adaylar (OOS → HOLDOUT doğrulama) ──",
    ]
    if not sonuc["en_iyiler"]:
        sat.append("  (yeterli sinyalli kombinasyon yok — min_trade'i düşür ya da veri artır)")
    for a in sonuc["en_iyiler"]:
        bayrak = "✅ SAĞLAM" if a.get("saglam") else "⚠ holdout zayıf"
        sat.append(
            f"  [{'+'.join(a['bloklar'])}] OOS:{a['oos_puan']}(WR%{a['oos_wr']},n{a['oos_trade']}) "
            f"→ HOLDOUT:{a.get('holdout_puan',0)}(WR%{a.get('holdout_wr',0)},n{a.get('holdout_trade',0)}) {bayrak}"
        )
    return "\n".join(sat)
