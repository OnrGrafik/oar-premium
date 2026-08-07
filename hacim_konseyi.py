"""
hacim_konseyi.py — HACİM KONSEYİ (bağımsız analizör kurulu + konsensüs + günlük özet)
════════════════════════════════════════════════════════════════════════════════════
Kullanıcı isteği (kalıcı):
  • Birbirinden HABERSİZ hacim analizörleri → her biri YALNIZ kendi verisini görür,
    kendi kararını (yön + güç + sayısal kanıt) üretir; kimse diğerinin sonucunu bilmez.
  • Toplayıcı hepsini birleştirir (konsensüs) → lider bağlamına girer (lider hepsini görür).
  • GÜN SONU (03:00 UTC) tek seferlik TAM özet Telegram'a → kural-tabanlı (LLM YOK),
    KESİLMEZ (sayfalara bölünür, "yarım kalma" biter).
  • Veri HAFTALIK depolanır → hafta bitince repo-kökü `hacim_veriseti.json`'a arşivlenir
    (git-senkron → kullanıcı `git pull` ile PC'ye indirir) → site hafızası (DATA_DIR) temizlenir.
  • Lider analizörleri DENETLER: hangisi katkı veriyor / gereksiz / eklenmeli (denetim()).

ÜYELER (7 bağımsız lens — ANAYASA #3: her biri gerçek motorun DOĞRULANMIŞ çıktısını kullanır):
  1. footprint/CVD    → oar_canli_footprint.footprint_al  (gerçek aggressor delta)
  2. order-flow       → order_flow_agent.order_flow_analiz (CVD/OI/funding/CB-premium puanı)
  3. order-book baskı → oar_orderbook.snapshot            (imbalance / true_pressure)
  4. likidite         → liquidity_agent.liquidity_analiz  (sweep / SFP / EQH-EQL)
  5. hacim-profili POC→ footprint POC vs fiyat            (değer-alanı kabul/ret lensi)
  6. opsiyon          → options_engine.gex_ozet           (gamma rejimi / CW-PW-ZG / max-pain)
  7. makro            → macro_engine.makro_veri            (makro yön eğilimi)

DÜRÜST SINIR: bu KATMAN CANLI KARARA (şampiyon giriş/çıkış) DOKUNMAZ (ANAYASA #8/#9).
Analiz + keşif üretir; hiçbir hacim sinyali serap kapısı (DSR≥0.95) geçmeden canlıya bağlanmaz.
"""
import os
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── Depolama yolları ────────────────────────────────────────────────────────────
_DATA_DIR = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
                 or ("/var/data" if Path("/var/data").exists() else "data"))
_DATA_DIR.mkdir(parents=True, exist_ok=True)

SNAPSHOT_FILE = _DATA_DIR / "hacim_konsey.json"          # site hafızası (efemeral/volume) — haftalık silinir
DURUM_FILE    = _DATA_DIR / "hacim_konsey_durum.json"    # son özet günü + hafta imzası (redeploy-dayanıklı)
VERISET_FILE  = Path(__file__).resolve().parent / "hacim_veriseti.json"  # repo-kökü, git-senkron (PC'ye iner)

SEMBOLLER   = ("BTCUSDT", "ETHUSDT")
# Konsensüs YÖN eşiği — TEK KAYNAK (lider bağlamı da bunu yazar; eşik-altı "zayıf eğilim"
# olarak gösterilir ki lider "NOTR" ile anlık olaydaki "SHORT"u çelişki sanmasın).
_NET_ESIK   = 0.15
TOPLA_ARALIK_S = 300          # 5 dk'da bir snapshot
SNAPSHOT_CAP   = 3000         # ~1 hafta (2 sembol × 288/gün × 7 ≈ 4032; cap güvenli üst sınır)
OZET_SAAT_UTC  = 3            # gün sonu özet saati (UTC). Kullanıcı "gece 03:00" — değiştirilebilir.
TG_SAYFA_KARAKTER = 3800      # Telegram 4096 limiti altı; özet KESİLMEZ, sayfalara bölünür.


def _now():
    return datetime.now(timezone.utc)

def _hafta_no(dt=None):
    iso = (dt or _now()).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"

def _load(p, d):
    try:
        return json.loads(Path(p).read_text()) if Path(p).exists() else d
    except Exception:
        return d

def _save(p, d):
    try:
        Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  BAĞIMSIZ ANALİZÖRLER — her biri {ad, yon, guc(0..1), kanit, ham, aktif} döner.
#  fail-safe: motor patlarsa NOTR + aktif=False (sessiz düşer, konseyi bozmaz).
#  yon ∈ {LONG, SHORT, NOTR} · guc ∈ [0,1] · kanit = tek satır sayısal gerekçe.
# ═══════════════════════════════════════════════════════════════════════════════

def _bos(ad, sebep=""):
    return {"ad": ad, "yon": "NOTR", "guc": 0.0, "kanit": sebep or "veri yok", "ham": {}, "aktif": False}

def _clamp01(x):
    return max(0.0, min(1.0, float(x)))


async def a_footprint(sembol):
    """1. Aggressor delta / CVD lensi — net alıcı/satıcı baskısı (gün, gerçek delta)."""
    ad = "footprint_cvd"
    try:
        from oar_canli_footprint import footprint_al
        fp = await footprint_al(sembol)
        if not fp:
            return _bos(ad, "footprint gelmedi (httpx/ağ)")
        delta_map = fp.get("delta_map") or {}
        net = float(sum(delta_map.values()))
        vol_ort = float(fp.get("vol_ort") or 0.0)
        bar = int(fp.get("bar_sayisi") or 0)
        toplam_vol = vol_ort * bar
        oran = net / toplam_vol if toplam_vol > 0 else 0.0   # CVD/hacim ∈ ~[-1,1]
        yon = "LONG" if oran > 0.02 else ("SHORT" if oran < -0.02 else "NOTR")
        guc = _clamp01(abs(oran) * 3.0)
        return {"ad": ad, "yon": yon, "guc": guc,
                "kanit": f"net delta {net:+.0f} / hacim → CVD-oran {oran:+.3f}",
                "ham": {"net_delta": net, "cvd_oran": round(oran, 4), "poc": fp.get("poc")},
                "aktif": True}
    except Exception as e:
        return _bos(ad, f"hata: {e}")


async def a_orderflow(sembol):
    """2. Order-flow lensi — CVD+OI+funding+CB-premium birleşik puanı (-100..+100)."""
    ad = "order_flow"
    try:
        from order_flow_agent import order_flow_analiz
        r = await order_flow_analiz(sembol, "5m")
        puan = float(r.get("puan") or 0.0)
        yon = "LONG" if puan >= 25 else ("SHORT" if puan <= -25 else "NOTR")
        guc = _clamp01(abs(puan) / 100.0)
        return {"ad": ad, "yon": yon, "guc": guc,
                "kanit": f"puan {puan:+.0f} · {r.get('karar','')}",
                "ham": {"puan": puan, "karar": r.get("karar"), "aciklama": r.get("aciklama")},
                "aktif": True}
    except Exception as e:
        return _bos(ad, f"hata: {e}")


async def a_orderbook(sembol):
    """3. Order-book baskı lensi — mesafe-ağırlıklı true_pressure (anlık L2)."""
    ad = "order_book"
    try:
        # PERF (denetim #6): order-book zaten topla_loop ile 60s'de toplanıyor → taze depth
        # çağrısı yerine son toplanan snapshot'ı (_SON) kullan; yoksa taze çek (fallback).
        from oar_orderbook import _SON, snapshot
        m = _SON.get(sembol)
        if not m:
            m = await snapshot(sembol)
        if not m:
            return _bos(ad, "L2 gelmedi (httpx/ağ)")
        tp = m.get("true_pressure")
        if tp is None:
            return _bos(ad, "true_pressure yok")
        tp = float(tp)
        yon = "LONG" if tp > 0.10 else ("SHORT" if tp < -0.10 else "NOTR")
        guc = _clamp01(abs(tp) * 2.0)
        return {"ad": ad, "yon": yon, "guc": guc,
                "kanit": f"true_pressure {tp:+.3f} · imbalance {m.get('imbalance')}",
                "ham": {"true_pressure": tp, "imbalance": m.get("imbalance"),
                        "bid_ask_ratio": m.get("bid_ask_ratio")},
                "aktif": True}
    except Exception as e:
        return _bos(ad, f"hata: {e}")


async def a_likidite(sembol):
    """4. Likidite lensi — sweep yönü + SFP (avlanan taraf ters yön eğilimi)."""
    ad = "likidite"
    try:
        from liquidity_agent import liquidity_analiz
        r = await liquidity_analiz(sembol, "15m")
        sweepler = r.get("sweep") or []
        # BULLISH_SWEEP (aşağı likidite avı) → LONG eğilim; BEARISH_SWEEP → SHORT.
        skor = 0
        for s in sweepler:
            t = s.get("tip", "")
            if "BULLISH" in t:
                skor += 1
            elif "BEARISH" in t:
                skor -= 1
        son_sfp = r.get("son_sfp") or {}
        if son_sfp:
            if "BULL" in str(son_sfp.get("tip", "")).upper():
                skor += 1
            elif "BEAR" in str(son_sfp.get("tip", "")).upper():
                skor -= 1
        yon = "LONG" if skor > 0 else ("SHORT" if skor < 0 else "NOTR")
        guc = _clamp01(abs(skor) * 0.3)
        return {"ad": ad, "yon": yon, "guc": guc,
                "kanit": f"sweep-skor {skor:+d} ({len(sweepler)} sweep) · {r.get('ozet','')[:80]}",
                "ham": {"sweep_skor": skor, "sweep_sayi": len(sweepler), "son_sfp": son_sfp},
                "aktif": True}
    except Exception as e:
        return _bos(ad, f"hata: {e}")


async def a_vp_poc(sembol):
    """5. Hacim-profili POC lensi — fiyatın gün POC'una göre kabul/ret (değer-alanı)."""
    ad = "vp_poc"
    try:
        from oar_canli_footprint import footprint_al
        fp = await footprint_al(sembol)
        if not fp:
            return _bos(ad, "profil gelmedi")
        poc = fp.get("poc")
        vol_map = fp.get("vol_map") or {}
        if not poc or not vol_map:
            return _bos(ad, "POC/profil yok")
        # Güncel fiyat ~ en yüksek zaman-anahtarı yerine son işlem seviyesi: profil ağırlık merkezi.
        # Fiyat referansı: order-book mid (bağımsız), gelmezse POC (yön=NOTR).
        fiyat = None
        try:
            from oar_orderbook import _SON, snapshot
            m = _SON.get(sembol) or await snapshot(sembol)   # PERF: toplanmış snapshot'ı yeniden kullan
            fiyat = (m or {}).get("mid")
        except Exception:
            fiyat = None
        if not fiyat:
            return _bos(ad, "fiyat referansı yok")
        sapma = (fiyat - poc) / poc      # POC üstü = değer-alanı ÜSTÜ kabul (bullish acceptance)
        yon = "LONG" if sapma > 0.003 else ("SHORT" if sapma < -0.003 else "NOTR")
        guc = _clamp01(abs(sapma) * 40.0)
        return {"ad": ad, "yon": yon, "guc": guc,
                "kanit": f"fiyat POC'a göre %{sapma*100:+.2f} (POC {poc})",
                "ham": {"poc": poc, "fiyat": fiyat, "sapma_pct": round(sapma * 100, 3)},
                "aktif": True}
    except Exception as e:
        return _bos(ad, f"hata: {e}")


async def a_opsiyon(sembol):
    """6. Opsiyon lensi — gamma rejimi + spot'un CW/PW/ZG'ye konumu (yalnız BTC/ETH)."""
    ad = "opsiyon"
    try:
        from options_engine import gex_ozet
        para = "ETH" if sembol.upper().startswith("ETH") else "BTC"
        g = await gex_ozet(para)
        if g.get("error"):
            return _bos(ad, f"opsiyon: {g.get('error')}")
        spot = g.get("spot"); zg = g.get("zero_gamma")
        cw = g.get("call_wall"); pw = g.get("put_wall")
        yon = "NOTR"; guc = 0.0
        # GAMMA REJİMİ ARTIK YÖNE GİRİYOR (kullanıcı: "diğeri negatif gamma bildirimi
        # atıyor, bu arkadaşlar bu işe bakmıyor mu?" — HAKLIYDI: rejim hesaplanıyor ama
        # yalnız açıklama metnine yazılıyordu, oy (CW+PW)/2 orta noktasından geliyordu).
        # Mekanizma (Hull böl.19): spot < ZG → dealer NEGATİF gamma → hareketi HIZLANDIRIR
        # (momentum/aşağı ivme) · spot > ZG → POZİTİF gamma → bastırır (duvarlar arası pinleme).
        if spot and zg:
            zg_sapma = (spot - zg) / spot            # + : ZG üstü (pozitif gamma)
            if zg_sapma < -0.001:                    # NEGATİF gamma → düşüş ivmelenir
                yon = "SHORT"
                guc = _clamp01(abs(zg_sapma) * 25.0 + 0.25)
            elif zg_sapma > 0.001:                   # POZİTİF gamma → mean-revert/pinleme
                # Pozitif gamma'da yön duvarlara göre: PW'ye yakın destekli (LONG),
                # CW'ye yakın dirençli (SHORT); duvar yoksa stabilize = NOTR.
                if pw and cw and cw > pw:
                    orta = (cw + pw) / 2
                    sapma = (orta - spot) / spot
                    yon = "LONG" if sapma > 0.005 else ("SHORT" if sapma < -0.005 else "NOTR")
                    guc = _clamp01(abs(sapma) * 20.0)
        return {"ad": ad, "yon": yon, "guc": guc,
                "kanit": f"{g.get('gamma_rejim','—')} · spot {spot} CW {cw} PW {pw} ZG {zg}",
                "ham": {"spot": spot, "call_wall": cw, "put_wall": pw, "zero_gamma": zg,
                        "gamma_rejim": g.get("gamma_rejim"), "max_pain": g.get("max_pain")},
                "aktif": True}
    except Exception as e:
        return _bos(ad, f"hata: {e}")


async def a_makro(sembol):
    """7. Makro lensi — makro göstergelerin BTC yön eğilimi (sembol-bağımsız, kaba)."""
    ad = "makro"
    try:
        # ÖNCE: btcYorum dict'i STRING'e çevrilip anahtar kelime sayılıyordu — metin
        # neredeyse hiç değişmediği için bu üye gün boyu TEK yön veriyordu (§5t
        # "sabit üye, bilgi katkısı ~yok" denetimi). ARTIK sayısal makro sentez skoru
        # kullanılıyor: bloklar (enflasyon/istihdam/politika/likidite) değiştikçe oy da değişir.
        from macro_engine import makro_veri, makro_sentez
        mv = await makro_veri()
        s = makro_sentez(mv)
        skor = s.get("skor") or 0
        yon = "LONG" if skor >= 15 else ("SHORT" if skor <= -15 else "NOTR")
        guc = _clamp01(abs(skor) / 60.0)
        bloklar = ", ".join(f"{b['ad'].split()[0]}:{b['durum']}" for b in s.get("bloklar", []))
        return {"ad": ad, "yon": yon, "guc": guc,
                "kanit": f"makro net skor {skor:+d}/100 ({s.get('rejim', '')}) · {mv.get('kaynak_ozet', '')}",
                "ham": {"skor": skor, "egilim": s.get("egilim"), "bloklar": bloklar[:200]},
                "aktif": True}
    except Exception as e:
        return _bos(ad, f"hata: {e}")


UYELER = [a_footprint, a_orderflow, a_orderbook, a_likidite, a_vp_poc, a_opsiyon, a_makro]


# ═══════════════════════════════════════════════════════════════════════════════
#  KONSENSÜS — üyeleri (habersiz) topla, ağırlıklı yön oyu birleştir.
# ═══════════════════════════════════════════════════════════════════════════════

def _konsensus(uyeler):
    puan = 0.0; agirlik = 0.0
    long_g = short_g = 0.0
    aktif = [u for u in uyeler if u.get("aktif")]
    for u in aktif:
        isaret = 1.0 if u["yon"] == "LONG" else (-1.0 if u["yon"] == "SHORT" else 0.0)
        puan += isaret * u["guc"]
        agirlik += u["guc"]
        if isaret > 0:
            long_g += u["guc"]
        elif isaret < 0:
            short_g += u["guc"]
    net = puan / agirlik if agirlik > 0 else 0.0
    yon = "LONG" if net > _NET_ESIK else ("SHORT" if net < -_NET_ESIK else "NOTR")
    # mutabakat = baskın yöndeki üye sayısı / aktif üye
    yonler = [u["yon"] for u in aktif if u["yon"] != "NOTR"]
    if yonler:
        bask = max(set(yonler), key=yonler.count)
        mutabakat = yonler.count(bask) / len(aktif) if aktif else 0.0
    else:
        mutabakat = 0.0
    return {"yon": yon, "net": round(net, 3), "mutabakat": round(mutabakat, 2),
            "aktif_uye": len(aktif), "long_guc": round(long_g, 2), "short_guc": round(short_g, 2)}


async def konsey_topla(sembol):
    """Bir sembol için TÜM bağımsız üyeleri paralel çalıştır → konsensüs snapshot."""
    sonuclar = await asyncio.gather(*[u(sembol) for u in UYELER], return_exceptions=True)
    uyeler = []
    for r in sonuclar:
        if isinstance(r, dict):
            uyeler.append(r)
    return {"ts": _now().isoformat(), "sembol": sembol, "uyeler": uyeler,
            "konsensus": _konsensus(uyeler)}


# ═══════════════════════════════════════════════════════════════════════════════
#  TOPLAYICI DÖNGÜ — 5 dk'da bir snapshot → site hafızası (DATA_DIR, haftalık silinir).
# ═══════════════════════════════════════════════════════════════════════════════

def _snapshot_ekle(snap):
    db = _load(SNAPSHOT_FILE, {"snapshots": []})
    db["snapshots"].append(snap)
    db["snapshots"] = db["snapshots"][-SNAPSHOT_CAP:]
    _save(SNAPSHOT_FILE, db)


# ═══════════════════════════════════════════════════════════════════════════════
#  OLAY YAKALAMA (kullanıcı: "hacim değişince bildirim yok, gün sonunu bekliyor")
#  Konsey artık gün sonunu BEKLEMEDEN anlamlı DEĞİŞİMİ anında bildirir. Doğası gereği
#  düşük gürültü: yalnız DURUM DEĞİŞTİĞİNDE tetiklenir (§6b), tekrarlamaz.
# ═══════════════════════════════════════════════════════════════════════════════
OLAY_FILE = _DATA_DIR / "hacim_konsey_olay.json"
MUTABAKAT_OLAY_ESIK = 0.60      # bu mutabakatın üstündeki yön dönüşü bildirilir


def _olay_durum_yukle():
    return _load(OLAY_FILE, {})


def _olay_durum_yaz(d):
    _save(OLAY_FILE, d)


def _gamma_rejim(snap):
    """Snapshot'taki opsiyon üyesinden gamma rejimini çıkar (NEGATİF/POZİTİF/—)."""
    for u in snap.get("uyeler", []):
        if u.get("ad") == "opsiyon":
            r = str((u.get("ham") or {}).get("gamma_rejim") or "")
            if "NEGAT" in r.upper():
                return "NEGATİF"
            if "POZİT" in r.upper() or "POZIT" in r.upper():
                return "POZİTİF"
    return ""


async def olay_tara(snap: dict):
    """
    Snapshot'ı önceki durumla karşılaştır → anlamlı olayı ANINDA Telegram'a bildir.
    Yakalananlar: ① konsensüs YÖN DÖNÜŞÜ (güçlü mutabakatla) ② GAMMA REJİM dönüşü
    (diğer bildirim hattının konuştuğu olay — konsey artık ondan habersiz değil).
    """
    sembol = snap.get("sembol")
    kon = snap.get("konsensus") or {}
    yon = kon.get("yon"); mut = kon.get("mutabakat", 0.0)
    gam = _gamma_rejim(snap)
    durum = _olay_durum_yukle()
    onceki = durum.get(sembol) or {}
    olaylar = []

    # ① Konsensüs yön dönüşü (yalnız GÜÇLÜ mutabakatta — zayıf salınım gürültüdür)
    o_yon = onceki.get("yon")
    if (o_yon and yon and yon != "NOTR" and o_yon != "NOTR" and yon != o_yon
            and mut >= MUTABAKAT_OLAY_ESIK):
        olaylar.append(f"🔄 *{sembol}* hacim konsensüsü *{o_yon} → {yon}* "
                       f"(mutabakat %{mut*100:.0f}, net {kon.get('net',0):+.2f})")

    # ② Gamma rejim dönüşü (opsiyon üyesi artık bunu YÖNE de katıyor)
    o_gam = onceki.get("gamma")
    if o_gam and gam and gam != o_gam:
        olaylar.append(f"⚡ *{sembol}* gamma rejimi *{o_gam} → {gam}* "
                       f"({'hareket hızlanır' if gam=='NEGATİF' else 'hareket bastırılır/pinleme'})")

    # durum güncelle (ilk turda yalnız taban kurulur → sahte 'dönüş' bildirimi olmaz)
    durum[sembol] = {"yon": yon, "gamma": gam, "ts": _now().isoformat()}
    if olaylar and onceki:
        # BÜTÜNLEŞİKLİK: olayları sakla → lider gözlemi de bunları görsün (konsey_baglami)
        gecmis = durum.get("son_olaylar") or []
        for o in olaylar:
            gecmis.append({"ts": _now().isoformat(), "metin": o.replace("*", "")})
        durum["son_olaylar"] = gecmis[-20:]
    _olay_durum_yaz(durum)

    if olaylar and onceki:
        uyeler = ", ".join(f"{u['ad']}={u['yon']}" for u in snap.get("uyeler", []) if u.get("aktif"))
        metin = ("⚠️ *HACİM KONSEYİ — ANLIK OLAY*\n" + "\n".join(olaylar)
                 + f"\n_üyeler: {uyeler}_")
        await _uzun_gonder(metin)
        print(f"[hacim_konseyi] olay bildirildi: {sembol} {olaylar}", flush=True)


async def konsey_loop():
    """Sürekli toplayıcı. Başlatma: main.py startup Group 1 (order-book'un yanına)."""
    await asyncio.sleep(45)   # startup yoğunluğu geçsin
    while True:
        try:
            for s in SEMBOLLER:
                snap = await konsey_topla(s)
                _snapshot_ekle(snap)
                try:
                    await olay_tara(snap)      # gün sonunu bekleme — anlamlı değişimi anında bildir
                except Exception as e:
                    print(f"[hacim_konseyi] olay tarama hata: {e}", flush=True)
                await asyncio.sleep(2)
        except Exception as e:
            print(f"[hacim_konseyi] toplayıcı hata: {e}", flush=True)
        await asyncio.sleep(TOPLA_ARALIK_S)


# ═══════════════════════════════════════════════════════════════════════════════
#  GÜN SONU ÖZET (kural-tabanlı, TAM, sayfalı — KESİLMEZ) + HAFTALIK ROTASYON.
# ═══════════════════════════════════════════════════════════════════════════════

def _son_gun_snapshotlari():
    db = _load(SNAPSHOT_FILE, {"snapshots": []})
    sinir = (_now() - timedelta(hours=24)).isoformat()
    return [s for s in db.get("snapshots", []) if s.get("ts", "") >= sinir]


def _gun_ozeti_hesapla(snaps):
    """Snapshot'lardan sembol×üye günlük özet + konsensüs zaman-serisi (kural-tabanlı)."""
    ozet = {}
    for sembol in SEMBOLLER:
        ss = [s for s in snaps if s.get("sembol") == sembol]
        if not ss:
            continue
        uye_ist = {}
        for s in ss:
            for u in s.get("uyeler", []):
                d = uye_ist.setdefault(u["ad"], {"long": 0, "short": 0, "notr": 0,
                                                  "guc_top": 0.0, "n": 0, "aktif_n": 0,
                                                  "son_kanit": ""})
                d["n"] += 1
                if u.get("aktif"):
                    d["aktif_n"] += 1
                d[{"LONG": "long", "SHORT": "short", "NOTR": "notr"}[u["yon"]]] += 1
                d["guc_top"] += u.get("guc", 0.0)
                d["son_kanit"] = u.get("kanit", "")
        kon = [s.get("konsensus", {}) for s in ss]
        kon_long = sum(1 for k in kon if k.get("yon") == "LONG")
        kon_short = sum(1 for k in kon if k.get("yon") == "SHORT")
        kon_notr = sum(1 for k in kon if k.get("yon") == "NOTR")
        net_ort = sum(k.get("net", 0.0) for k in kon) / len(kon) if kon else 0.0
        mut_ort = sum(k.get("mutabakat", 0.0) for k in kon) / len(kon) if kon else 0.0
        ozet[sembol] = {
            "snapshot_sayi": len(ss),
            "uye_ist": uye_ist,
            "konsensus_dagilim": {"LONG": kon_long, "SHORT": kon_short, "NOTR": kon_notr},
            "net_ort": round(net_ort, 3),
            "mutabakat_ort": round(mut_ort, 2),
            "baskin_yon": ("LONG" if kon_long > kon_short and kon_long > kon_notr else
                           "SHORT" if kon_short > kon_long and kon_short > kon_notr else "NOTR"),
        }
    return ozet


def denetim(gun_ozeti=None):
    """
    LİDER DENETİMİ — hangi analizör katkı veriyor / gereksiz / dikkat.
    Kural-tabanlı: aktif-oranı düşükse 'gereksiz/arızalı', hep-NOTR ise 'katkı yok',
    yüksek güç+istikrarlı yön ise 'değerli'. Kullanıcı isteği: lider agentleri denetlesin.
    """
    if gun_ozeti is None:
        gun_ozeti = _gun_ozeti_hesapla(_son_gun_snapshotlari())
    notlar = []
    birlesik = {}
    for sembol, o in gun_ozeti.items():
        for ad, d in o.get("uye_ist", {}).items():
            b = birlesik.setdefault(ad, {"long": 0, "short": 0, "notr": 0, "guc_top": 0.0,
                                         "n": 0, "aktif_n": 0})
            for k in ("long", "short", "notr", "n", "aktif_n"):
                b[k] += d.get(k, 0)
            b["guc_top"] += d.get("guc_top", 0.0)
    for ad, b in birlesik.items():
        n = b["n"] or 1
        aktif_oran = b["aktif_n"] / n
        notr_oran = b["notr"] / n
        guc_ort = b["guc_top"] / n
        # SABİT ÜYE tespiti: gün boyu TEK yön (ör. makro 279/279 SHORT) → hiç ayrışma
        # üretmiyor, konsensüse sadece sabit bir kayma ekliyor = BİLGİ KATKISI YOK.
        tek_yon = max(b["long"], b["short"], b["notr"])
        if aktif_oran >= 0.3 and n >= 20 and tek_yon / n >= 0.98:
            hangi = "LONG" if b["long"] == tek_yon else ("SHORT" if b["short"] == tek_yon else "NOTR")
            notlar.append(f"🔁 {ad}: gün boyu SABİT {hangi} ({tek_yon}/{n}) — hiç ayrışma yok, "
                          f"konsensüse sabit kayma ekliyor (bilgi katkısı ~YOK).")
            continue
        if aktif_oran < 0.3:
            notlar.append(f"⚠️ {ad}: aktif-oran %{aktif_oran*100:.0f} — çoğu zaman veri gelmiyor (ARIZALI/gereksiz?).")
        elif notr_oran > 0.9:
            notlar.append(f"➖ {ad}: %{notr_oran*100:.0f} NOTR — bu dönemde sinyal katkısı yok.")
        elif guc_ort >= 0.4:
            notlar.append(f"✅ {ad}: ort güç {guc_ort:.2f}, aktif %{aktif_oran*100:.0f} — DEĞERLİ katkı.")
        else:
            notlar.append(f"• {ad}: ort güç {guc_ort:.2f}, aktif %{aktif_oran*100:.0f} — zayıf/nötr.")
    return {"notlar": notlar, "birlesik": birlesik}


def _gun_ozet_metni(gun_ozeti, den):
    """Kural-tabanlı TAM özet metni (LLM YOK). Sayfalayıcı bunu böler → KESİLMEZ."""
    tar = _now().strftime("%Y-%m-%d %H:%M UTC")
    L = [f"📊 *HACİM KONSEYİ — Gün Sonu Özeti* ({tar})", ""]
    if not gun_ozeti:
        L.append("Bugün snapshot toplanmadı (canlı veri gelmedi veya sistem yeni başladı).")
        return "\n".join(L)
    for sembol, o in gun_ozeti.items():
        kd = o["konsensus_dagilim"]
        L.append(f"━━━ *{sembol}* ({o['snapshot_sayi']} snapshot) ━━━")
        L.append(f"Baskın konsensüs: *{o['baskin_yon']}* · net eğilim {o['net_ort']:+.3f} · "
                 f"ort mutabakat %{o['mutabakat_ort']*100:.0f}")
        L.append(f"Konsensüs dağılımı: LONG {kd['LONG']} · SHORT {kd['SHORT']} · NOTR {kd['NOTR']}")
        L.append("Üye kırılımı (bağımsız analizörler):")
        for ad, d in o["uye_ist"].items():
            n = d["n"] or 1
            guc_ort = d["guc_top"] / n
            L.append(f"  • {ad}: LONG {d['long']} / SHORT {d['short']} / NOTR {d['notr']} "
                     f"· ort güç {guc_ort:.2f} · aktif %{d['aktif_n']/n*100:.0f}")
            if d.get("son_kanit"):
                L.append(f"      son: {d['son_kanit']}")
        L.append("")
    L.append("━━━ *LİDER DENETİMİ* (analizör sağlığı/katkısı) ━━━")
    L.extend(den.get("notlar", []) or ["(denetim notu yok)"])
    L.append("")
    L.append("_Not: bu bir ANALİZ katmanıdır; hiçbir hacim sinyali serap testinden (DSR≥0.95) "
             "geçmeden canlı karara bağlanmaz. Şampiyonlara dokunulmaz._")
    return "\n".join(L)


async def _uzun_gonder(metin):
    """Telegram'a KESMEDEN gönder — satır sınırında sayfalara böl (yarım kalma biter)."""
    try:
        from main import _telegram_gonder
        from ajan_merkez import AJAN_THREAD, AJAN_CHAT
    except Exception as e:
        print(f"[hacim_konseyi] telegram import hata: {e}", flush=True)
        return False
    satirlar = metin.split("\n")
    sayfalar, cur = [], ""
    for s in satirlar:
        if len(cur) + len(s) + 1 > TG_SAYFA_KARAKTER:
            if cur:
                sayfalar.append(cur)
            # tek satır limitten uzunsa parçala
            while len(s) > TG_SAYFA_KARAKTER:
                sayfalar.append(s[:TG_SAYFA_KARAKTER])
                s = s[TG_SAYFA_KARAKTER:]
            cur = s
        else:
            cur = f"{cur}\n{s}" if cur else s
    if cur:
        sayfalar.append(cur)
    n = len(sayfalar)
    ok = True
    for i, p in enumerate(sayfalar, 1):
        bas = f"({i}/{n})\n" if n > 1 else ""
        r = await _telegram_gonder(bas + p, thread_id=AJAN_THREAD, chat_id=AJAN_CHAT)
        ok = ok and bool(r)
        await asyncio.sleep(0.6)
    return ok


# ── Haftalık rotasyon: hafta bitince repo-kökü veri setine arşivle + site hafızasını temizle ──
def _haftalik_rotasyon(bugun_gun_ozeti):
    """
    Gün özetini haftanın kovasına yaz. Hafta DEĞİŞTİYSE önceki haftayı repo-kökü
    hacim_veriseti.json'a taşı (git-senkron → PC'ye iner) + DATA_DIR snapshot'larını temizle.
    """
    durum = _load(DURUM_FILE, {})
    bu_hafta = _hafta_no()
    vs = _load(VERISET_FILE, {"haftalar": [], "aciklama":
               "Hacim konseyi haftalık veri seti (git-senkron). Her hafta gün özetleri; "
               "hafta bitince site hafızasından buraya taşınır, son 12 hafta tutulur."})

    # Aktif hafta kaydını bul/oluştur
    aktif = None
    for h in vs["haftalar"]:
        if h.get("hafta") == bu_hafta:
            aktif = h
            break
    if aktif is None:
        aktif = {"hafta": bu_hafta, "gun_ozetleri": [], "baslangic": _now().isoformat()}
        vs["haftalar"].append(aktif)
    aktif.setdefault("gun_ozetleri", []).append(
        {"tarih": _now().strftime("%Y-%m-%d"), "ozet": bugun_gun_ozeti})
    aktif["bitis"] = _now().isoformat()

    onceki_hafta = durum.get("hafta")
    hafta_degisti = onceki_hafta and onceki_hafta != bu_hafta

    vs["haftalar"] = vs["haftalar"][-12:]     # son 12 hafta
    _save(VERISET_FILE, vs)

    if hafta_degisti:
        # Önceki haftanın snapshot'ları arşive taşındı → site hafızasını temizle (kullanıcı isteği).
        db = _load(SNAPSHOT_FILE, {"snapshots": []})
        kalan = [s for s in db.get("snapshots", []) if _hafta_no(_parse_ts(s.get("ts"))) == bu_hafta]
        _save(SNAPSHOT_FILE, {"snapshots": kalan})

    durum["hafta"] = bu_hafta
    durum["son_ozet_gun"] = _now().strftime("%Y-%m-%d")
    _save(DURUM_FILE, durum)
    return {"hafta": bu_hafta, "hafta_degisti": bool(hafta_degisti),
            "veriset_hafta_sayi": len(vs["haftalar"])}


def _parse_ts(ts):
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return _now()


async def gunluk_ozeti_yayinla():
    """Gün sonu: özet hesapla → Telegram (sayfalı) → haftalık rotasyon. Tek çağrı."""
    snaps = _son_gun_snapshotlari()
    gun_ozeti = _gun_ozeti_hesapla(snaps)
    den = denetim(gun_ozeti)
    metin = _gun_ozet_metni(gun_ozeti, den)
    await _uzun_gonder(metin)
    # Anlamlı+kalıcı gün konsensüsü → worker'a ADAY backtest görevi (PC'de test edilir).
    try:
        from hacim_gorev import konsey_gorev_uret
        uretilen = konsey_gorev_uret(gun_ozeti)
        if uretilen:
            print(f"[hacim_konseyi] {uretilen} yeni görev üretildi", flush=True)
    except Exception as e:
        print(f"[hacim_konseyi] görev üretim hata: {e}", flush=True)
    rot = _haftalik_rotasyon(gun_ozeti)
    print(f"[hacim_konseyi] gün özeti yayınlandı · {rot}", flush=True)
    return rot


def _sonraki_ozet_saniye():
    now = _now()
    hedef = now.replace(hour=OZET_SAAT_UTC, minute=0, second=0, microsecond=0)
    if hedef <= now:
        hedef += timedelta(days=1)
    return (hedef - now).total_seconds()


async def gunluk_ozet_loop():
    """Her gün OZET_SAAT_UTC'de gün sonu özetini yayınlar. Başlatma: main.py startup."""
    while True:
        try:
            await asyncio.sleep(_sonraki_ozet_saniye())
            # Aynı gün iki kez çalışmasını engelle (redeploy dayanıklı)
            durum = _load(DURUM_FILE, {})
            if durum.get("son_ozet_gun") == _now().strftime("%Y-%m-%d"):
                await asyncio.sleep(3600)
                continue
            await gunluk_ozeti_yayinla()
        except Exception as e:
            print(f"[hacim_konseyi] gün özeti hata: {e}", flush=True)
            await asyncio.sleep(3600)


# ═══════════════════════════════════════════════════════════════════════════════
#  LİDER BAĞLAMI — lider her soruda konseyin son durumunu görür (_site_baglami'ya girer).
# ═══════════════════════════════════════════════════════════════════════════════

def konsey_baglami() -> str:
    """
    Son snapshot(lar)dan lider bağlamı metni (site bağlamına eklenir).
    BÜTÜNLEŞİKLİK (kullanıcı sitemi: "biri NOTR derken diğeri SHORT veriyor"):
      ① snapshot ZAMANI + YAŞI yazılır → lider bayat veriyi taze sanmaz (konsey 5dk'da
         güncellenir + site bağlamı 120s cache → lidere ~7dk bayat düşebiliyordu).
      ② NOTR ise EĞİLİM açıkça yazılır (net −0.13 eşiğin kılpayı üstünde "NOTR" görünüp
         anlık olaydaki SHORT ile ÇELİŞKİ sanılıyordu — aslında ikisi de aynı yöne eğilimli).
      ③ SON OLAYLAR eklenir → lider, anlık bildirim hattının ne dediğini de görür.
    """
    db = _load(SNAPSHOT_FILE, {"snapshots": []})
    snaps = db.get("snapshots", [])
    if not snaps:
        return "HACİM KONSEYİ: henüz snapshot yok."
    L = [f"HACİM KONSEYİ (bağımsız hacim analizörleri · yön eşiği ±{_NET_ESIK}):"]
    for sembol in SEMBOLLER:
        son = None
        for s in reversed(snaps):
            if s.get("sembol") == sembol:
                son = s
                break
        if not son:
            continue
        k = son.get("konsensus", {})
        net = k.get("net", 0.0)
        yon = k.get("yon", "?")
        # ② eşik-altı eğilimi göster (sert eşik sürekli değeri ikili gösteriyordu)
        if yon == "NOTR" and abs(net) > 0.03:
            yon_txt = f"NOTR (zayıf {'SHORT' if net < 0 else 'LONG'} eğilimi)"
        else:
            yon_txt = yon
        # ① tazelik
        yas = ""
        try:
            dk = (_now() - _parse_ts(son.get("ts"))).total_seconds() / 60.0
            yas = f", {dk:.0f} dk önce" if dk >= 1 else ", şimdi"
        except Exception:
            pass
        aktif = [u for u in son.get("uyeler", []) if u.get("aktif")]
        uye_txt = ", ".join(f"{u['ad']}={u['yon']}({u['guc']:.1f})" for u in aktif) or "aktif üye yok"
        L.append(f"  {sembol}: konsensüs {yon_txt} (net {net:+.2f}, "
                 f"mutabakat %{k.get('mutabakat',0)*100:.0f}{yas}) · {uye_txt}")
    # ③ anlık olay hattıyla bütünleşme
    try:
        olaylar = (_load(OLAY_FILE, {}) or {}).get("son_olaylar", [])[-3:]
        if olaylar:
            L.append("  Son anlık olaylar (aynı konseyin canlı bildirimleri):")
            for o in olaylar:
                L.append(f"    · {o.get('ts','')[11:16]} — {o.get('metin','')}")
    except Exception:
        pass
    return "\n".join(L)


def durum() -> dict:
    """Endpoint/teşhis için özet durum."""
    db = _load(SNAPSHOT_FILE, {"snapshots": []})
    vs = _load(VERISET_FILE, {"haftalar": []})
    return {"snapshot_sayi": len(db.get("snapshots", [])),
            "veriset_hafta": len(vs.get("haftalar", [])),
            "son_ozet_gun": _load(DURUM_FILE, {}).get("son_ozet_gun"),
            "uye_sayi": len(UYELER), "ozet_saat_utc": OZET_SAAT_UTC,
            "baglam": konsey_baglami()}


def veriset_indirme() -> dict:
    """
    PC'ye HAFTALIK OTOMATİK KAYIT için tam veri paketi (indirme endpoint'i döner):
    haftalık özet arşivi (son 12 hafta) + O ANKİ ham snapshot'lar (site hafızası, haftalık
    silinmeden önce PC'ye insin diye). İndirici (hacim_indir.py) bunu bilgisayara yazar.
    """
    vs = _load(VERISET_FILE, {"haftalar": []})
    db = _load(SNAPSHOT_FILE, {"snapshots": []})
    return {"indirme_zamani": _now().isoformat(),
            "bu_hafta": _hafta_no(),
            "veriseti": vs,                                  # haftalık özet (son 12 hafta)
            "guncel_snapshotlar": db.get("snapshots", []),   # o anki ham veri (silinmeden)
            "snapshot_sayi": len(db.get("snapshots", [])),
            "hafta_sayi": len(vs.get("haftalar", []))}


if __name__ == "__main__":
    async def _t():
        for s in SEMBOLLER:
            snap = await konsey_topla(s)
            print(json.dumps(snap, ensure_ascii=False, indent=2))
    asyncio.run(_t())
