"""
oar_yorum.py — KURAL-TABANLI Lider yorum motoru (LLM YOK, dış API YOK).

Gemini/Groq sunucu yoğunluğundan yorum gelmiyordu → tüm site/Telegram lider
yorumları buradaki deterministik şablonlarla üretilir. Sayısal veri zaten
toplanmış oluyor (endpoint'ler `veri` dict'i doldurur); bu modül yalnız o
veriyi OAR bağlamında Türkçe metne döker.

İLKELER (ANAYASA uyumlu):
- SAYIYA güven: her cümle bir seviye/oran/mekanizma taşır, jenerik laf yok.
- 6c: ziyaretçiye kaynak/altyapı/yöntem adı SIZDIRMAZ (yalnız seviye + yön).
- Veri yoksa o cümleyi ATLA (uydurma yok). Hiçbir zaman "servis yoğun" demez.
"""

def _num(x):
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None

def _f(x, ondalik=None):
    """Fiyat/sayı biçimle. ondalik=None → otomatik (büyük sayı 0-1 hane)."""
    v = _num(x)
    if v is None:
        return "—"
    if ondalik is None:
        ondalik = 0 if abs(v) >= 100 else (2 if abs(v) >= 1 else 4)
    return f"{v:,.{ondalik}f}"

def _yuzde(x, hane=2):
    v = _num(x)
    return "—" if v is None else f"%{v:.{hane}f}"

def _mesafe_pct(hedef, spot):
    """spot'a göre hedefin uzaklığı (%). +üstünde / -altında."""
    h, s = _num(hedef), _num(spot)
    if h is None or s is None or s == 0:
        return None
    return (h - s) / s * 100

def _en_yakin(spot, adaylar):
    """adaylar=[(ad, deger), ...] → spot'a en yakın (ad, deger, mesafe_pct)."""
    s = _num(spot)
    en = None
    for ad, d in adaylar:
        dd = _num(d)
        if dd is None or s is None:
            continue
        m = abs(dd - s) / s * 100 if s else None
        if m is not None and (en is None or m < en[2]):
            en = (ad, dd, m)
    return en


# ══════════ 1. GRAFİK ALTI YORUM (BTC/ETH — /api/grafik-yorum) ══════════

def grafik(veri: dict, trade: dict | None = None) -> str:
    veri = veri or {}
    trade = trade or {}
    spot = _num(veri.get("fiyat"))
    ah, al = _num(veri.get("asia_high")), _num(veri.get("asia_low"))
    poc = _num(veri.get("asia_poc"))
    genlik = _num(veri.get("asia_range_pct"))
    parcalar = []

    # ── ① OAR: Asia konumu + kapı + rejim
    oar = []
    if spot is not None and ah is not None and al is not None:
        if spot > ah:
            oar.append(f"Fiyat {_f(spot)}, Asia yükseği {_f(ah)} **üzerinde** — üst ekstrem kırılımı")
        elif spot < al:
            oar.append(f"Fiyat {_f(spot)}, Asia düşüğü {_f(al)} **altında** — alt ekstrem kırılımı")
        else:
            konum = "POC üstü" if (poc is not None and spot >= poc) else "POC altı"
            oar.append(f"Fiyat {_f(spot)}, Asia bandı içinde ({_f(al)}–{_f(ah)}), {konum}")
    if genlik is not None:
        oar.append(f"Asia genliği {_yuzde(genlik)}")
    kapi = veri.get("market_kapisi")
    if kapi:
        oar.append(str(kapi))
    if oar:
        parcalar.append("OAR: " + "; ".join(oar) + ".")

    # ── ② OPSİYON: en yakın seviye + mekanizma
    ops = []
    cw, pw, zg, mp = (_num(veri.get("cw")), _num(veri.get("pw")),
                      _num(veri.get("zg")), _num(veri.get("max_pain")))
    if zg is not None and spot is not None:
        if spot >= zg:
            ops.append(f"Zero-gamma {_f(zg)} **üzerinde** — dealer hedge'i hareketi söndürür (sakin/menzil)")
        else:
            ops.append(f"Zero-gamma {_f(zg)} **altında** — dealer hedge'i hareketi büyütür (hızlanma riski)")
    en = _en_yakin(spot, [("Call-Wall (direnç)", cw), ("Put-Wall (destek)", pw)])
    if en:
        ad, d, m = en
        ops.append(f"en yakın duvar {ad} {_f(d)} ({_yuzde(m)} uzak)")
    if mp is not None:
        m = _mesafe_pct(mp, spot)
        if m is not None:
            ops.append(f"Max-Pain {_f(mp)} ({_yuzde(m)}) — vade yaklaşırken mıknatıs")
    if ops:
        parcalar.append("Opsiyon: " + "; ".join(ops) + ".")

    # ── ③ MAKRO: tek cümle
    mk = veri.get("makro") or {}
    if isinstance(mk, dict):
        mp2 = []
        if mk.get("fed_faiz") is not None:
            mp2.append(f"Fed {mk.get('fed_faiz')}")
        if mk.get("cpi") is not None:
            mp2.append(f"CPI {mk.get('cpi')}")
        if mk.get("issizlik") is not None:
            mp2.append(f"işsizlik {mk.get('issizlik')}")
        if mp2:
            parcalar.append("Makro: " + " · ".join(mp2) + " — risk iştahının yapısal zemini.")

    # ── İşlem fikri (varsa)
    su = trade.get("setuplar") or {}
    sc = su.get("scalp") or {}
    if sc.get("giris") is not None and sc.get("tp") is not None:
        parcalar.append(
            f"Kademe: {trade.get('yon','')} scalp giriş {_f(sc.get('giris'))} → "
            f"TP {_f(sc.get('tp'))} / SL {_f(sc.get('sl'))} (R:R {sc.get('rr','—')}).")

    return " ".join(parcalar) if parcalar else "Canlı veri toplanıyor — seviyeler birazdan güncellenecek."


# ══════════ 2. OPSİYON YORUMU (/api/opsiyon-yorum) ══════════

def opsiyon(veri: dict) -> str:
    veri = veri or {}
    spot = _num(veri.get("spot"))
    cw, pw = _num(veri.get("call_wall")), _num(veri.get("put_wall"))
    zg, mp = _num(veri.get("zero_gamma")), _num(veri.get("max_pain"))
    cvd, cvd_yon = _num(veri.get("opsiyon_cvd")), veri.get("cvd_yon")
    c = []

    if cw is not None and pw is not None:
        c.append(f"Dealer bandı **{_f(pw)} (Put-Wall/destek) – {_f(cw)} (Call-Wall/direnç)**; "
                 f"fiyat {_f(spot)} bu bantta kaldıkça duvarlar geri-çekim üretir")
    if zg is not None and spot is not None:
        if spot >= zg:
            c.append(f"spot Zero-Gamma {_f(zg)} **üstünde → pozitif gamma**: dealer ters işlemle oynaklığı bastırır, "
                     f"menzil davranışı beklenir")
        else:
            c.append(f"spot Zero-Gamma {_f(zg)} **altında → negatif gamma**: dealer trend yönünde hedge eder, "
                     f"hareketler sertleşir")
    if mp is not None:
        m = _mesafe_pct(mp, spot)
        c.append(f"Max-Pain {_f(mp)}" + (f" ({_yuzde(m)} uzak)" if m is not None else "") +
                 " — vadeye yaklaşırken fiyatı buraya çeken mıknatıs etkisi")
    if cvd is not None or cvd_yon:
        yon_txt = {"POZITIF": "alıcı", "NEGATIF": "satıcı", "BULLISH": "alıcı", "BEARISH": "satıcı"}.get(
            str(cvd_yon).upper(), cvd_yon or "nötr")
        c.append(f"opsiyon akışı (CVD) {yon_txt} tarafta" + (f" ({_f(cvd)})" if cvd is not None else ""))

    if not c:
        return "Opsiyon verisi toplanıyor."
    return ". ".join(s[0].upper() + s[1:] for s in c) + "."


# ══════════ 3. PİYASA DURUMU — 3 başlık (/api/piyasa-durumu) ══════════

def piyasa(veri: dict) -> dict:
    veri = veri or {}
    spot = _num(veri.get("fiyat"))

    # ── TEKNİK: OAR + indikatör + Asia konumu
    tk = []
    genlik = _num(veri.get("asia_range_pct"))
    oar_rejim = veri.get("oar_rejim")
    if genlik is not None:
        if oar_rejim == "fade" or (genlik is not None and genlik >= 1):
            tk.append(f"Asia genliği **{_yuzde(genlik)}** (≥%1) → **range/fade günü**: ekstremlerden dönüş beklentisi")
        else:
            tk.append(f"Asia genliği **{_yuzde(genlik)}** (<%1) → **trend-devam** zemini")
    ah, al, poc = _num(veri.get("asia_high")), _num(veri.get("asia_low")), _num(veri.get("asia_poc"))
    if spot is not None and ah is not None and al is not None:
        if spot > ah:
            tk.append(f"fiyat {_f(spot)} Asia yükseği {_f(ah)} üstünde")
        elif spot < al:
            tk.append(f"fiyat {_f(spot)} Asia düşüğü {_f(al)} altında")
        elif poc is not None:
            tk.append(f"fiyat {_f(spot)} POC {_f(poc)} {'üstü' if spot>=poc else 'altı'}")
    if veri.get("indikator_skor") is not None:
        tk.append(f"indikatör skoru {veri.get('indikator_skor')} ({veri.get('indikator_yon','—')})")

    # ── TEMEL: opsiyon gamma + DVOL + makro
    tm = []
    gr = veri.get("gamma_rejim")
    cw, pw, zg = _num(veri.get("call_wall")), _num(veri.get("put_wall")), _num(veri.get("zero_gamma"))
    if gr:
        tm.append(f"gamma rejimi **{gr}**")
    if zg is not None and spot is not None:
        tm.append(("Zero-Gamma üstü → dealer oynaklığı bastırır"
                   if spot >= zg else "Zero-Gamma altı → dealer hareketi büyütür") + f" ({_f(zg)})")
    if cw is not None and pw is not None:
        tm.append(f"bant {_f(pw)}–{_f(cw)}")
    dv, dvd = _num(veri.get("dvol")), _num(veri.get("dvol_24s_degisim"))
    if dv is not None:
        yon = "artıyor" if (dvd or 0) > 0 else ("düşüyor" if (dvd or 0) < 0 else "yatay")
        tm.append(f"örtük oynaklık {_f(dv,1)} ({yon}, 24s Δ{_f(dvd,1)})")
    if veri.get("makro"):
        tm.append(f"makro: {veri.get('makro')}")

    # ── PSİKOLOJİ: funding + OI + CVD
    ps = []
    fnd = _num(veri.get("funding_pct"))
    if fnd is not None:
        taraf = "long'lar ödüyor (long kalabalık)" if fnd > 0 else ("short'lar ödüyor (short kalabalık)" if fnd < 0 else "dengeli")
        ps.append(f"funding {_yuzde(fnd,4)} → {taraf}")
    oi = _num(veri.get("oi_24s_pct"))
    if oi is not None:
        ps.append(f"açık pozisyon 24s {_yuzde(oi)} ({'yeni pozisyon' if oi>0 else 'kapanış/temizlik'})")
    cvd = _num(veri.get("cvd_3s_musd"))
    if cvd is not None:
        ps.append(f"kısa vadeli akış (CVD) {_f(cvd,1)}M$ {'alıcı' if cvd>0 else 'satıcı'} tarafta")
    if fnd is not None and cvd is not None and ((fnd > 0) != (cvd > 0)):
        ps.append("pozisyonlanma ile akış ZIT — kalabalığın yanlış tarafta olma riski")

    def _cumle(liste, bos):
        return ("; ".join(liste) + ".") if liste else bos
    return {
        "teknik": _cumle(tk, "Teknik veri toplanıyor."),
        "temel": _cumle(tm, "Opsiyon/makro verisi toplanıyor."),
        "psikoloji": _cumle(ps, "Pozisyonlanma verisi toplanıyor."),
    }


# ══════════ 4. ANLIK YORUM (Telegram — lider_anlik_yorum) ══════════

def anlik(veri: dict, tetikler: list | None = None) -> str:
    veri = veri or {}
    sup = veri.get("supervisor", {}) or {}
    flow = veri.get("flow", {}) or {}
    ops = veri.get("opsiyonlar", {}) or {}
    seans = veri.get("seans", {}) or {}
    c = []

    karar, yon = sup.get("karar"), sup.get("yon")
    if karar:
        c.append(f"Resmi karar **{karar}**" + (f" ({yon}, güven %{sup.get('guven',0)})" if yon else ""))
    fk = flow.get("karar")
    if fk:
        fk_txt = {"BULLISH_FLOW": "alıcı", "BEARISH_FLOW": "satıcı", "NEUTRAL_FLOW": "nötr"}.get(fk, fk)
        c.append(f"akış {fk_txt}" + (f" (CVD {flow.get('cvd_yon')})" if flow.get("cvd_yon") else ""))
    gr = ops.get("gamma_rejim")
    spot = _num(veri.get("fiyat"))
    zg = _num(ops.get("zero_gamma"))
    if gr and zg is not None and spot is not None:
        c.append(f"{gr}; Zero-Gamma {_f(zg)} {'üstü (sakin)' if spot>=zg else 'altı (hızlanma)'}")
    cw, pw = _num(ops.get("call_wall")), _num(ops.get("put_wall"))
    en = _en_yakin(spot, [("Call-Wall", cw), ("Put-Wall", pw)])
    if en:
        c.append(f"en yakın duvar {en[0]} {_f(en[1])} ({_yuzde(en[2])})")
    # hizalama/çelişki
    if karar in ("LONG", "SHORT") and fk in ("BULLISH_FLOW", "BEARISH_FLOW"):
        hizali = (karar == "LONG" and fk == "BULLISH_FLOW") or (karar == "SHORT" and fk == "BEARISH_FLOW")
        c.append("karar ve akış **hizalı**" if hizali else "karar ile akış **çelişiyor** — dikkat")

    if not c:
        return ""
    return ". ".join(s[0].upper() + s[1:] for s in c) + "."


# ══════════ 5. LİDER RAPOR ÖZETİ (/api/leader/report) ══════════

def lider_rapor(backtest: dict, research: dict, saglik: dict) -> str:
    backtest = backtest or {}
    research = research or {}
    saglik = saglik or {}
    c = []
    wr = backtest.get("genel_win_rate")
    if wr is not None:
        c.append(f"Genel WR %{wr} ({backtest.get('degerlendirilmis',0)} sinyal değerlendirildi)")
    bot_stats = backtest.get("bot_stats", {}) or {}
    if bot_stats:
        en = max(bot_stats, key=lambda b: bot_stats[b].get("win_rate", 0))
        c.append(f"en iyi bot {en} (WR %{bot_stats[en].get('win_rate',0)})")
    krit = next((b.get("bulgu") for b in research.get("bulgular", []) if b.get("etki") == "kritik"), None)
    if krit:
        c.append(f"kritik bulgu: {krit}")
    # arızalı servisler
    down = []
    if isinstance(saglik, dict):
        for k, v in saglik.items():
            if isinstance(v, dict) and v.get("durum") in ("hata", "down", "arizali"):
                down.append(k)
    if down:
        c.append("arızalı servis: " + ", ".join(down))
    if not c:
        return "Sistem izleniyor; raporlanacak kritik değişiklik yok."
    return ". ".join(s[0].upper() + s[1:] for s in c) + "."
