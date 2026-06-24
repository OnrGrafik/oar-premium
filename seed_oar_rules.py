"""
OAR Master Strateji Tohumlama — Lider Agent Eğitimi
═══════════════════════════════════════════════════════════════════
Gemini ile konsolide edilen OAR trade stratejisini kalıcı kurallar
olarak `oar_rules` kural bankasına yazar. Lider Agent promptu bu
kuralları `oar_rules.agent_baglami()` üzerinden otomatik çeker.

İDEMPOTENT: Aynı başlıklı kural zaten varsa tekrar eklemez.
Çalıştırma: python seed_oar_rules.py
"""
import oar_rules

# (baslik, icerik, tip, etiketler, oncelik)
MASTER_KURALLAR = [
    (
        "Asia Range %1 Tetikleyici + Fib",
        "Asya seansının (03:00-07:00 TR) en yüksek/en düşük aralığını ölç. "
        "Aralık genliği %1 üzerindeyse OAR stratejisini AKTİF say. Aralığın 0 (low) "
        "ile 1 (high) noktaları arasına Fibonacci çek. İç seviyeler: 0, 0.5, 1. "
        "Dış uzantılar: 1.377, 1.618, 2.272, 2.618 (üst) ve -0.377, -0.618, -1.272, "
        "-1.618 (alt). Fiyat ekstrem fib'lere geldiğinde dönüş/devam kurulumu ara.",
        "SETUP", ["asia", "fib", "tetikleyici"], 5,
    ),
    (
        "Hacimli Asia Kırılımı (Volume-Backed Breakout)",
        "Asya seansı içinde yoğun hacim birikimi (konsolidasyon) olduktan sonra: "
        "Asia Low kaybedilirse fiyat ekstrem alt fib uzantılarına (-1.272 / -1.618) "
        "kadar SHORT'lanır; Asia High kazanılırsa ekstrem üst fib'lere (2.272 / 2.618) "
        "kadar LONG gider. Kırılım yönünde trend takip işlemi kurgula; geri test "
        "(retest) doğrulaması skoru artırır.",
        "SETUP", ["asia", "breakout", "hacim"], 5,
    ),
    (
        "Fixed Range SR Kutusu — Long/Short Mantığı",
        "Fixed Range hacim profilinden (24 bin / FRVP) en yüksek hacimli bölgeleri "
        "(POC, HVN) 'Kırmızı SR Kutusu' kabul et. Fiyat bu kutunun ÜZERİNDE tutundukça "
        "VWAP + RSI destekli LONG ara (hedef: PDH/PWH veya üst fib uzantıları). Fiyat "
        "kutunun ALTINA inip direnç olarak test ederse SHORT ara (hedef: PDL/PWL veya "
        "alt fib uzantıları).",
        "FILTRE", ["frvp", "sr", "vwap"], 4,
    ),
    (
        "OAR Key-Level Sözlüğü (Likidite Haritası)",
        "TWO: Pazartesi NY borsası açılış seviyesi. TWAO: Salı NY açılış. "
        "MD (Midnight): diğer günler (Çar/Per/Cum) NY açılış saati seviyesi. "
        "NVWAP(D): günlük çıplak VWAP kapanışı (TR 03:00'da kapanır, ertesi güne yatay "
        "uzar); NVWAP(W)/NVWAP(M): haftalık/aylık VWAP kapanışı. "
        "PDL/PDH: likiditesi henüz alınmamış önceki gün dip/tepe (mıknatıs hedef). "
        "PWL/PWH: likiditesi alınmamış önceki hafta dip/tepe. "
        "Makro açılışlar: WO (haftalık), MO (aylık), QO (çeyreklik), YO (yıllık). "
        "Bu seviyeleri destek/direnç ve likidite hedefi olarak kullan.",
        "MAKRO", ["keylevel", "likidite", "nvwap", "pdl"], 5,
    ),
    (
        "Tepe/Dip Dönüş Onayı (Order Flow Konfirmasyonu)",
        "Bir Key-Level'a (TWO, NVWAP, PDH/PDL) veya ekstrem fib uzantısına gelindiğinde "
        "körlemesine işlem alma; hacim/order-flow onayı ara. Footprint (FP): seviyede "
        "agresif alıcı/satıcı emiliyor mu (Absorption) veya güç tükeniyor mu (Exhaustion). "
        "CVD & OI: likidite süpürmesi (sweep) sonrası uyumsuzluk — fiyat yeni tepe yaparken "
        "CVD düşüyor ve OI azalıyorsa dönüş sinyali. Coinbase Premium: spot tarafı dönüşü "
        "destekliyor mu. Bu üç onaydan en az ikisi varsa dönüş (Reversal) işlemini onayla.",
        "SETUP", ["reversal", "footprint", "cvd", "oi"], 5,
    ),
]


def tohumla() -> dict:
    mevcut_basliklar = {k["baslik"] for k in oar_rules.kurallari_getir(aktif_only=False)}
    eklenen, atlanan = [], []
    for baslik, icerik, tip, etiketler, oncelik in MASTER_KURALLAR:
        if baslik in mevcut_basliklar:
            atlanan.append(baslik)
            continue
        oar_rules.kural_ekle(baslik, icerik, tip=tip, etiketler=etiketler, oncelik=oncelik)
        eklenen.append(baslik)
    return {"eklenen": eklenen, "atlanan": atlanan, "istatistik": oar_rules.istatistik()}


if __name__ == "__main__":
    import json
    sonuc = tohumla()
    print(json.dumps(sonuc, ensure_ascii=False, indent=2))
