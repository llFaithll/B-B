"""Ross 1000 Lazio XML export module.

Genera il tracciato XML ufficiale del sistema ROSS 1000 (Regione Lazio) per la
comunicazione mensile ISTAT dei flussi turistici delle strutture ricettive.
Riferimento: https://www.ross1000.it/source/tracciato-xml.pdf
"""
from datetime import date, timedelta
from typing import Optional
from xml.sax.saxutils import escape
from calendar import monthrange

# Codici ISTAT paesi esteri (ROSS1000) - subset dei più comuni
# Formato: 1XXXXXXXX per esteri, 100000100 per Italia
COUNTRY_CODES = {
    "ITALIA": "100000100", "ITALY": "100000100", "IT": "100000100",
    "FRANCIA": "100000203", "FRANCE": "100000203", "FR": "100000203",
    "GERMANIA": "100000206", "GERMANY": "100000206", "DE": "100000206",
    "SPAGNA": "100000212", "SPAIN": "100000212", "ES": "100000212",
    "REGNO UNITO": "100000219", "UNITED KINGDOM": "100000219", "UK": "100000219", "GB": "100000219",
    "STATI UNITI": "100000400", "USA": "100000400", "US": "100000400",
    "SVIZZERA": "100000215", "SWITZERLAND": "100000215", "CH": "100000215",
    "PAESI BASSI": "100000210", "NETHERLANDS": "100000210", "NL": "100000210",
    "BELGIO": "100000202", "BELGIUM": "100000202", "BE": "100000202",
    "AUSTRIA": "100000201", "AT": "100000201",
    "PORTOGALLO": "100000211", "PORTUGAL": "100000211", "PT": "100000211",
    "GIAPPONE": "100000505", "JAPAN": "100000505", "JP": "100000505",
    "CINA": "100000502", "CHINA": "100000502", "CN": "100000502",
    "CANADA": "100000401", "CA": "100000401",
    "AUSTRALIA": "100000601", "AU": "100000601",
    "BRASILE": "100000402", "BRAZIL": "100000402", "BR": "100000402",
    "RUSSIA": "100000228", "RU": "100000228",
    "POLONIA": "100000213", "POLAND": "100000213", "PL": "100000213",
    "ROMANIA": "100000229", "RO": "100000229",
}

# Codici canale prenotazione ROSS1000
CHANNEL_CODES = {
    "Direct": "DIRETTA WEB",
    "Airbnb": "AIRBNB",
    "Booking": "BOOKING.COM",
    "Other": "ALTRO PORTALE",
}


def country_code(country: Optional[str]) -> str:
    """Restituisce il codice ISTAT paese. Default: Italia."""
    if not country:
        return "100000100"
    return COUNTRY_CODES.get(country.strip().upper(), "100000100")


def format_date(d: date) -> str:
    """YYYYMMDD."""
    return f"{d.year:04d}{d.month:02d}{d.day:02d}"


def parse_iso(s: str) -> date:
    return date.fromisoformat(s[:10])


def build_movimenti_xml(
    codice_struttura: str,
    year: int,
    month: int,
    bookings: list,
    camere_disponibili: int,
    letti_disponibili: int,
) -> str:
    """Genera l'XML <movimenti> per il mese richiesto.

    - Un elemento <movimento> per ogni giorno del mese
    - Per ogni giorno: struttura (occupancy calcolata) + arrivi + partenze
    - tipoalloggiato=16 (ospite singolo), default per B&B
    """
    _, last_day = monthrange(year, month)
    days = [date(year, month, d) for d in range(1, last_day + 1)]

    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<movimenti>",
           f"  <codice>{escape(codice_struttura)}</codice>",
           "  <prodotto>CASABNB</prodotto>"]

    for day in days:
        day_iso = day.isoformat()
        # Prenotazioni attive quel giorno: checkin <= day < checkout
        active = [b for b in bookings
                  if parse_iso(b["checkin"]) <= day < parse_iso(b["checkout"])]
        camere_occupate = len(active)
        arrivi = [b for b in bookings if parse_iso(b["checkin"]) == day]
        partenze = [b for b in bookings if parse_iso(b["checkout"]) == day]
        apertura = "SI" if camere_disponibili > 0 else "NO"

        out.append("  <movimento>")
        out.append(f"    <data>{format_date(day)}</data>")
        out.append("    <struttura>")
        out.append(f"      <apertura>{apertura}</apertura>")
        out.append(f"      <camereoccupate>{camere_occupate}</camereoccupate>")
        out.append(f"      <cameredisponibili>{camere_disponibili}</cameredisponibili>")
        out.append(f"      <lettidisponibili>{letti_disponibili}</lettidisponibili>")
        out.append("    </struttura>")

        if arrivi:
            out.append("    <arrivi>")
            for b in arrivi:
                out.extend(_arrivo_xml(b))
            out.append("    </arrivi>")
        if partenze:
            out.append("    <partenze>")
            for b in partenze:
                out.extend(_partenza_xml(b))
            out.append("    </partenze>")
        out.append("  </movimento>")

    out.append("</movimenti>")
    return "\n".join(out)


def _arrivo_xml(b: dict) -> list:
    """Genera <arrivo>...</arrivo> per una prenotazione."""
    bid = str(b.get("_id") or b.get("id", ""))
    dob = b.get("date_of_birth", "1980-01-01")
    try:
        dob_d = parse_iso(dob)
    except Exception:
        dob_d = date(1980, 1, 1)
    cittadinanza = country_code(b.get("citizenship") or "ITALIA")
    statoresidenza = cittadinanza  # per B&B semplice: stessa cittadinanza
    luogoresidenza = escape((b.get("place_of_birth") or "").upper()[:30]) if cittadinanza == "100000100" else "XX"
    statonascita = country_code(b.get("country_of_birth") or "ITALIA")
    canale = CHANNEL_CODES.get(b.get("channel", "Direct"), "ALTRO PORTALE")
    lines = [
        "      <arrivo>",
        f"        <idswh>{escape(bid)}</idswh>",
        "        <tipoalloggiato>16</tipoalloggiato>",
        "        <idcapo></idcapo>",
        f"        <cognome>{escape((b.get('guest_last_name') or '').upper()[:50])}</cognome>",
        f"        <nome>{escape((b.get('guest_first_name') or '').upper()[:30])}</nome>",
        f"        <sesso>{b.get('sex', 'M')}</sesso>",
        f"        <cittadinanza>{cittadinanza}</cittadinanza>",
        f"        <statoresidenza>{statoresidenza}</statoresidenza>",
        f"        <luogoresidenza>{luogoresidenza}</luogoresidenza>",
        f"        <datanascita>{format_date(dob_d)}</datanascita>",
        f"        <statonascita>{statonascita}</statonascita>",
        "        <tipoturismo>LEISURE</tipoturismo>",
        "        <mezzotrasporto>AUTO</mezzotrasporto>",
        f"        <canaleprenotazione>{canale}</canaleprenotazione>",
        "      </arrivo>",
    ]
    return lines


def _partenza_xml(b: dict) -> list:
    bid = str(b.get("_id") or b.get("id", ""))
    ci = parse_iso(b["checkin"])
    return [
        "      <partenza>",
        f"        <idswh>{escape(bid)}</idswh>",
        "        <tipoalloggiato>16</tipoalloggiato>",
        f"        <arrivo>{format_date(ci)}</arrivo>",
        "      </partenza>",
    ]


def compute_month_stats(year: int, month: int, bookings: list, camere_disponibili: int) -> dict:
    """Statistiche di riepilogo del mese (per l'anteprima UI)."""
    _, last_day = monthrange(year, month)
    days = [date(year, month, d) for d in range(1, last_day + 1)]

    total_arrivi = 0
    total_partenze = 0
    total_presenze = 0  # totale notti spese nel mese
    by_country = {}

    for day in days:
        active = [b for b in bookings
                  if parse_iso(b["checkin"]) <= day < parse_iso(b["checkout"])]
        total_presenze += len(active)
        for b in bookings:
            if parse_iso(b["checkin"]) == day:
                total_arrivi += 1
                c = (b.get("citizenship") or "ITALIA").upper()
                by_country[c] = by_country.get(c, 0) + 1
            if parse_iso(b["checkout"]) == day:
                total_partenze += 1

    total_room_nights_available = camere_disponibili * len(days)
    occupancy = round((total_presenze / total_room_nights_available) * 100, 1) if total_room_nights_available else 0

    return {
        "year": year, "month": month,
        "days_in_month": len(days),
        "total_arrivi": total_arrivi,
        "total_partenze": total_partenze,
        "total_presenze": total_presenze,
        "occupancy_pct": occupancy,
        "by_country": [{"country": k, "arrivi": v} for k, v in sorted(by_country.items(), key=lambda x: -x[1])],
    }
