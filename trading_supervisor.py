"""
Trading Supervisor — OAR Premium
══════════════════════════════════════════════════════════════════
Tüm agent çıktılarını toplar ve tek bir TRADE / NO_TRADE kararı verir.

3 Kritik Soru (hepsi geçmeli):
  1. EDGE VAR MI?     — Trend + Flow + Yapı uyumu
  2. R:R UYGUN MU?   — Minimum 1:3 (scalper), 1:4 (swing)
  3. İNVALİDASYON NET Mİ? — Stop seviyesi tanımlanabilir mi?

Bunların yanında:
  • Seans filtresi     — Asia'da giriş yasak mı?
  • Makro filtresi     — FOMC, Witching, OpEx varsa küçül veya geç
  • Likidite onayı     — SFP veya Sweep var mı?

Karar:
  TRADE_LONG  / TRADE_SHORT  / NO_TRADE
  + Güven skoru (0-100)
  + Red gerekçeleri

Pipeline:
  market_structure_agent → order_flow_agent → liquidity_agent
  → session_agent → macro (time_context) → trading_supervisor
"""

import asyncio
from datetime import datetime, timezone


# ─── Pipeline import ──────────────────────────────────────────────

async def _toplam_analiz(sembol: str) -> dict:
    """Tüm agent'ları paralel çalıştırır."""
    from market_structure_agent import coklu_timeframe_analiz
    from order_flow_agent import order_flow_analiz
    from liquidity_agent import liquidity_analiz
    from session_agent import session_analiz
    from time_context import time_risk_skoru

    sonuclar = await asyncio.gather(
        coklu_timeframe_analiz(sembol),
        order_flow_analiz(sembol, "5m"),
        liquidity_analiz(sembol, "15m"),
        session_analiz(sembol),
        time_risk_skoru(),
        return_exceptions=True,
    )

    def _safe(s, varsayilan):
        return s if isinstance(s, dict) else varsayilan

    return {
        "yapi":    _safe(sonuclar[0], {"hizalama": "KARISIK", "1h": {"yapi": "UNKNOWN"}}),
        "flow":    _safe(sonuclar[1], {"karar": "NEUTRAL_FLOW", "puan": 0}),
        "likidite": _safe(sonuclar[2], {"son_sfp": None, "sweep": []}),
        "seans":   _safe(sonuclar[3], {"trade_yonlendirme": "BEKLE", "aktif_seans": "BILINMIYOR"}),
        "makro":   _safe(sonuclar[4], {"risk_skoru": 50, "seviye": "ORTA"}),
    }


# ─── 3 Kritik Soru ───────────────────────────────────────────────

def _soru1_edge(yapi: dict, flow: dict) -> dict:
    """
    Edge var mı?
    Koşul: 1h yapı + order flow aynı yönde
    """
    yapi_1h = yapi.get("1h", {}).get("yapi", "UNKNOWN")
    flow_karar = flow.get("karar", "NEUTRAL_FLOW")
    hizalama = yapi.get("hizalama", "KARISIK")

    bullish_yapi = yapi_1h in ("TREND_UP", "EXPANSION") and hizalama == "BULLISH_HIZALI"
    bearish_yapi = yapi_1h in ("TREND_DOWN", "EXPANSION") and hizalama == "BEARISH_HIZALI"
    bullish_flow = flow_karar == "BULLISH_FLOW"
    bearish_flow = flow_karar == "BEARISH_FLOW"

    long_edge = bullish_yapi and bullish_flow
    short_edge = bearish_yapi and bearish_flow
    # Tek taraflı edge (sadece yapı veya sadece flow) → zayıf edge
    zayif_long = (bullish_yapi or bullish_flow) and not (bullish_yapi and bullish_flow)
    zayif_short = (bearish_yapi or bearish_flow) and not (bearish_yapi and bearish_flow)

    if long_edge:
        return {"gecti": True, "yon": "LONG", "guc": "GUCLU",
                "neden": f"MTF hizalama {hizalama} + Flow {flow_karar}"}
    if short_edge:
        return {"gecti": True, "yon": "SHORT", "guc": "GUCLU",
                "neden": f"MTF hizalama {hizalama} + Flow {flow_karar}"}
    if zayif_long:
        return {"gecti": True, "yon": "LONG", "guc": "ZAYIF",
                "neden": f"Sadece kısmi uyum: yapı={yapi_1h}, flow={flow_karar}"}
    if zayif_short:
        return {"gecti": True, "yon": "SHORT", "guc": "ZAYIF",
                "neden": f"Sadece kısmi uyum: yapı={yapi_1h}, flow={flow_karar}"}

    return {"gecti": False, "yon": "YOK", "guc": "YOK",
            "neden": f"Edge yok: yapı={yapi_1h}, hizalama={hizalama}, flow={flow_karar}"}


def _soru2_rr(likidite: dict, yapi: dict, mod: str = "scalper") -> dict:
    """
    R:R uygun mu?
    Stop = SFP seviyesinin ötesi veya son swing low/high
    Hedef = Sonraki EQH/EQL
    Scalper: min 1:3 / Swing: min 1:4
    """
    min_rr = 3.0 if mod == "scalper" else 4.0

    son_sfp = likidite.get("son_sfp")
    eqh = likidite.get("eqh", [])
    eql = likidite.get("eql", [])

    # Stop hesapla
    stop_seviye = None
    hedef_seviye = None

    if son_sfp:
        tip = son_sfp.get("tip", "")
        if tip == "BULLISH_SFP":
            stop_seviye = son_sfp.get("mum_low", 0)
            hedef_seviye = eqh[0]["seviye"] if eqh else None
        elif tip == "BEARISH_SFP":
            stop_seviye = son_sfp.get("mum_high", 0)
            hedef_seviye = eql[0]["seviye"] if eql else None

    if stop_seviye and hedef_seviye and stop_seviye != 0:
        fiyat_ref = yapi.get("1h", {}).get("fiyat", 0)
        if fiyat_ref == 0:
            return {"gecti": False, "rr": 0.0, "neden": "Fiyat bilgisi yok", "stop": None, "hedef": None}

        risk = abs(fiyat_ref - stop_seviye)
        kazanc = abs(hedef_seviye - fiyat_ref)
        rr = kazanc / risk if risk > 0 else 0.0

        gecti = rr >= min_rr
        return {
            "gecti": gecti,
            "rr": round(rr, 2),
            "stop": stop_seviye,
            "hedef": hedef_seviye,
            "neden": (f"R:R = 1:{rr:.1f} (min 1:{min_rr})" +
                      (" ✓" if gecti else " ✗ — yetersiz")),
        }

    # SFP yoksa stop tahmini yap (swing high/low)
    swing_h = yapi.get("1h", {}).get("swing_highs", [])
    swing_l = yapi.get("1h", {}).get("swing_lows", [])
    if swing_h and swing_l:
        return {
            "gecti": False,
            "rr": 0.0,
            "stop": None,
            "hedef": None,
            "neden": "SFP yok — R:R hesaplanamıyor, stop manuel belirle",
        }

    return {"gecti": False, "rr": 0.0, "stop": None, "hedef": None,
            "neden": "Yeterli likidite verisi yok — R:R doğrulanamıyor"}


def _soru3_invalidasyon(likidite: dict, yapi: dict, edge_yon: str) -> dict:
    """
    İnvalidasyon net mi?
    Bir stop seviyesi açıkça tanımlanabiliyor mu?
    """
    son_sfp = likidite.get("son_sfp")

    if son_sfp:
        sfp_tip = son_sfp.get("tip", "")
        if (sfp_tip == "BULLISH_SFP" and edge_yon == "LONG"):
            stop = son_sfp.get("mum_low")
            return {"gecti": True, "stop": stop,
                    "neden": f"Bullish SFP low'u altı: {stop}"}
        if (sfp_tip == "BEARISH_SFP" and edge_yon == "SHORT"):
            stop = son_sfp.get("mum_high")
            return {"gecti": True, "stop": stop,
                    "neden": f"Bearish SFP high'ı üstü: {stop}"}

    # Swing high/low'dan invalidasyon
    swing_h = yapi.get("1h", {}).get("swing_highs", [])
    swing_l = yapi.get("1h", {}).get("swing_lows", [])

    if edge_yon == "LONG" and swing_l:
        stop = swing_l[-1]["fiyat"]
        return {"gecti": True, "stop": stop,
                "neden": f"Son swing low altı: {stop:.2f}"}
    if edge_yon == "SHORT" and swing_h:
        stop = swing_h[-1]["fiyat"]
        return {"gecti": True, "stop": stop,
                "neden": f"Son swing high üstü: {stop:.2f}"}

    return {"gecti": False, "stop": None,
            "neden": "İnvalidasyon seviyesi belirlenemiyor — trade yok"}


# ─── Filtreler ───────────────────────────────────────────────────

def _seans_filtresi(seans: dict) -> dict:
    """Asia seansında giriş yapma (opsiyonel kural)."""
    aktif = seans.get("aktif_seans", "BILINMIYOR")
    yonlendirme = seans.get("trade_yonlendirme", "BEKLE")

    if aktif == "ASIA":
        return {"gecti": False, "neden": "Asia seansı — trade yok (düşük likidite)"}
    if yonlendirme == "BEKLE":
        return {"gecti": False, "neden": f"Seans yönlendirmesi: BEKLE ({aktif})"}
    return {"gecti": True, "neden": f"Aktif seans: {aktif}, yönlendirme: {yonlendirme}"}


def _makro_filtresi(makro: dict) -> dict:
    """Yüksek makro riskinde pozisyon küçült / geç."""
    risk = makro.get("risk_skoru", 0)
    seviye = makro.get("seviye", "DÜŞÜK")
    etkinlikler = makro.get("aktif_etkinlikler", [])
    etkinlik_str = ", ".join(e.get("tip", "") for e in etkinlikler) if etkinlikler else "yok"

    if risk > 65:
        return {"gecti": False, "uyari": "KRİTİK",
                "neden": f"Makro risk KRİTİK ({risk}/100): {etkinlik_str} — trade yok"}
    if risk > 40:
        return {"gecti": True, "uyari": "YUKSEK",
                "neden": f"Makro risk YÜKSEK ({risk}/100): {etkinlik_str} — pozisyon küçük tut"}
    return {"gecti": True, "uyari": "NORMAL",
            "neden": f"Makro risk {seviye} ({risk}/100)"}


def _likidite_filtresi(likidite: dict) -> dict:
    """SFP veya Sweep var mı? Onay verir."""
    son_sfp = likidite.get("son_sfp")
    sweeplar = likidite.get("sweep", [])

    if son_sfp:
        return {"gecti": True, "onay": "SFP",
                "neden": f"SFP tespit: {son_sfp.get('tip')} @ {son_sfp.get('sfp_seviye', '?')}"}
    if sweeplar:
        return {"gecti": True, "onay": "SWEEP",
                "neden": f"Sweep tespit: {sweeplar[0]['tip']}"}
    return {"gecti": False, "onay": "YOK",
            "neden": "SFP veya Sweep yok — likidite teyidi eksik"}


# ─── Ana Karar Motoru ─────────────────────────────────────────────

def _oar_kapi_uygula(karar: str, yon: str, oar_yon):
    """
    OAR OTORİTE KAPISI (saf) — Faz 5: her şey OAR'a göre.
    Trade yalnız OAR sistemi AYNI yönü onaylarsa geçer; aksi halde NO_TRADE.
    Döner: (karar, yon, mesaj, onay_mi).
    """
    if karar == "NO_TRADE":
        return karar, yon, None, False
    if oar_yon not in ("LONG", "SHORT"):
        return "NO_TRADE", "YOK", "OAR onayı yok → NO_TRADE", False
    if oar_yon != yon:
        return "NO_TRADE", "YOK", f"OAR {oar_yon} ≠ sistem {yon} → OAR'a karşı işlem yok", False
    return karar, yon, f"OAR {oar_yon} yön teyitli", True


async def _oar_otorite(sembol: str):
    """OAR sisteminin yönü (OAR-CORE confluence öncelikli, yoksa OAR agent yönü)."""
    try:
        from oar_session_agent import oar_analiz
        a = await oar_analiz(sembol)
    except Exception as e:
        return None, f"OAR analiz alınamadı: {str(e)[:50]}"
    core = [s for s in (a.get("setup_listesi") or []) if "OAR-CORE" in s]
    if core:
        yon = "LONG" if "LONG" in core[0] else "SHORT" if "SHORT" in core[0] else a.get("yon")
        return (yon if yon in ("LONG", "SHORT") else None), "OAR-CORE confluence"
    yon = a.get("yon")
    if yon in ("LONG", "SHORT"):
        return yon, f"OAR agent {yon} (skor {a.get('skor', 0)})"
    return None, "OAR NEUTRAL/sinyal yok"


async def supervisor_karar(
    sembol: str = "BTCUSDT",
    mod: str = "scalper",
    seans_filtresi_aktif: bool = True,
    likidite_zorunlu: bool = False,
    oar_otorite: bool = True,
) -> dict:
    """
    Tüm agent'ları çalıştırır ve final TRADE/NO_TRADE kararı verir.

    Args:
        sembol:                 İşlem sembolü
        mod:                    "scalper" (1:3 RR) veya "swing" (1:4 RR)
        seans_filtresi_aktif:   Asia seansında engelle
        likidite_zorunlu:       SFP/Sweep olmadan trade yapma

    Returns:
        {
            "karar":      TRADE_LONG|TRADE_SHORT|NO_TRADE
            "guven":      0-100
            "yon":        LONG|SHORT|YOK
            "mod":        str
            "stop":       float|None
            "hedef":      float|None
            "rr":         float
            "red_listesi": list[str]   — reddedilme sebepleri
            "onay_listesi": list[str]  — geçen kontroller
            "edge":       dict
            "rr_analiz":  dict
            "invalidasyon": dict
            "seans":      dict
            "makro":      dict
            "likidite":   dict
            "ozet":       str
            "sembol":     str
            "timestamp":  str
        }
    """
    veri = await _toplam_analiz(sembol)

    yapi    = veri["yapi"]
    flow    = veri["flow"]
    likidite = veri["likidite"]
    seans   = veri["seans"]
    makro   = veri["makro"]

    red_listesi = []
    onay_listesi = []
    guven = 0

    # ── Soru 1: Edge ────────────────────────────────────────────
    edge = _soru1_edge(yapi, flow)
    if not edge["gecti"]:
        red_listesi.append(f"[EDGE] {edge['neden']}")
    else:
        onay_listesi.append(f"[EDGE] {edge['neden']}")
        guven += 35 if edge["guc"] == "GUCLU" else 15

    # ── Soru 2: R:R ─────────────────────────────────────────────
    rr_analiz = _soru2_rr(likidite, yapi, mod)
    if not rr_analiz["gecti"]:
        red_listesi.append(f"[R:R] {rr_analiz['neden']}")
    else:
        onay_listesi.append(f"[R:R] {rr_analiz['neden']}")
        guven += 25

    # ── Soru 3: İnvalidasyon ────────────────────────────────────
    inv = _soru3_invalidasyon(likidite, yapi, edge.get("yon", "YOK"))
    if not inv["gecti"]:
        red_listesi.append(f"[İNV] {inv['neden']}")
    else:
        onay_listesi.append(f"[İNV] {inv['neden']}")
        guven += 20

    # ── Filtreler ────────────────────────────────────────────────
    makro_f = _makro_filtresi(makro)
    if not makro_f["gecti"]:
        red_listesi.append(f"[MAKRO] {makro_f['neden']}")
    else:
        onay_listesi.append(f"[MAKRO] {makro_f['neden']}")
        guven += 10

    if seans_filtresi_aktif:
        seans_f = _seans_filtresi(seans)
        if not seans_f["gecti"]:
            red_listesi.append(f"[SEANS] {seans_f['neden']}")
        else:
            onay_listesi.append(f"[SEANS] {seans_f['neden']}")
            guven += 5

    liq_f = _likidite_filtresi(likidite)
    if likidite_zorunlu and not liq_f["gecti"]:
        red_listesi.append(f"[LİKİDİTE] {liq_f['neden']}")
    elif liq_f["gecti"]:
        onay_listesi.append(f"[LİKİDİTE] {liq_f['neden']}")
        guven += 5

    # ── Zorunlu 3 soru geçmediyse NO_TRADE ──────────────────────
    uc_soru_gecti = edge["gecti"] and rr_analiz["gecti"] and inv["gecti"]
    makro_gecti = makro_f["gecti"]

    guven = min(100, guven)

    if not uc_soru_gecti or not makro_gecti:
        karar = "NO_TRADE"
        yon = "YOK"
    else:
        yon = edge.get("yon", "YOK")
        karar = f"TRADE_{yon}" if yon in ("LONG", "SHORT") else "NO_TRADE"

    # ── OAR OTORİTESİ (Faz 5): trade yalnız OAR onaylarsa açılır ────────
    # Diğer kurallar bağlam/stop/RR sağlar ama OAR'a karşı işlem açtırmaz.
    # Opsiyon ve makro filtrelerine DOKUNULMAZ (kullanıcı talebi).
    if oar_otorite:
        oar_yon, oar_neden = await _oar_otorite(sembol)
        karar, yon, oar_mesaj, oar_onay = _oar_kapi_uygula(karar, yon, oar_yon)
        if oar_mesaj:
            (onay_listesi if oar_onay else red_listesi).append(f"[OAR] {oar_neden} — {oar_mesaj}")
        if oar_onay:
            guven = min(100, guven + 10)

    ozet_parcalar = [
        f"{'✅' if karar != 'NO_TRADE' else '❌'} {karar} | Güven: %{guven} | Sembol: {sembol} | Mod: {mod}",
        f"Yön: {yon} | R:R: 1:{rr_analiz['rr']:.1f} | Stop: {rr_analiz['stop']} | Hedef: {rr_analiz['hedef']}",
    ]
    if red_listesi:
        ozet_parcalar.append("Redler: " + " / ".join(r.split("] ")[1] if "] " in r else r for r in red_listesi))

    return {
        "karar":        karar,
        "guven":        guven,
        "yon":          yon,
        "mod":          mod,
        "stop":         rr_analiz.get("stop") or inv.get("stop"),
        "hedef":        rr_analiz.get("hedef"),
        "rr":           rr_analiz.get("rr", 0.0),
        "red_listesi":  red_listesi,
        "onay_listesi": onay_listesi,
        "edge":         edge,
        "rr_analiz":    rr_analiz,
        "invalidasyon": inv,
        "seans":        seans.get("ozet", ""),
        "makro":        makro_f,
        "likidite":     liq_f,
        "ozet":         "\n".join(ozet_parcalar),
        "sembol":       sembol,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import json
    r = asyncio.run(supervisor_karar("BTCUSDT", mod="scalper"))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print("\n" + r["ozet"])
