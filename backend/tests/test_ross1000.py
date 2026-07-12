"""
ROSS 1000 Lazio (ISTAT) endpoints tests — iteration 3.
Covers:
- GET /api/ross1000/settings (defaults + auth)
- POST /api/ross1000/settings (upsert idempotent)
- GET /api/ross1000/preview (stats math + 400 when not configured)
- GET /api/ross1000/export-xml (XML structure, channel/country mapping)
"""
import os
import re
import pytest
import requests
from datetime import date, timedelta

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bb-manager-pro.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@bnb.it"
ADMIN_PASSWORD = "admin123"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.text}"
    return s


@pytest.fixture(scope="module")
def fresh_user_session():
    """Register a brand-new user so settings default state can be tested."""
    s = requests.Session()
    email = f"ross1000_{date.today().isoformat()}_{os.urandom(3).hex()}@example.it"
    r = s.post(f"{BASE_URL}/api/auth/register",
               json={"email": email, "password": "Passw0rd!", "name": "Ross Tester"},
               timeout=30)
    assert r.status_code == 200, f"register failed: {r.text}"
    yield s, email


# -------------------- Auth --------------------
class TestRoss1000Auth:
    def test_all_endpoints_reject_without_cookie(self):
        anon = requests.Session()
        endpoints = [
            ("GET", "/api/ross1000/settings"),
            ("POST", "/api/ross1000/settings"),
            ("GET", "/api/ross1000/preview?year=2026&month=3"),
            ("GET", "/api/ross1000/export-xml?year=2026&month=3"),
        ]
        for method, path in endpoints:
            r = anon.request(method, f"{BASE_URL}{path}",
                             json={} if method == "POST" else None, timeout=30)
            assert r.status_code == 401, f"{method} {path} expected 401 got {r.status_code}"


# -------------------- Settings --------------------
class TestRoss1000Settings:
    def test_defaults_for_new_user(self, fresh_user_session):
        s, _ = fresh_user_session
        r = s.get(f"{BASE_URL}/api/ross1000/settings", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["codice_struttura"] == ""
        assert d["camere_disponibili"] == 1
        assert d["letti_disponibili"] == 2

    def test_post_and_persist_upsert(self, admin_session):
        payload = {"codice_struttura": "058091AABBCC", "camere_disponibili": 3, "letti_disponibili": 6}
        r = admin_session.post(f"{BASE_URL}/api/ross1000/settings", json=payload, timeout=30)
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        # GET to verify persistence
        r_get = admin_session.get(f"{BASE_URL}/api/ross1000/settings", timeout=30)
        d = r_get.json()
        assert d["codice_struttura"] == "058091AABBCC"
        assert d["camere_disponibili"] == 3
        assert d["letti_disponibili"] == 6

        # Idempotent upsert with new values
        payload2 = {"codice_struttura": "058091XYZ999", "camere_disponibili": 2, "letti_disponibili": 4}
        r2 = admin_session.post(f"{BASE_URL}/api/ross1000/settings", json=payload2, timeout=30)
        assert r2.status_code == 200
        r_get2 = admin_session.get(f"{BASE_URL}/api/ross1000/settings", timeout=30)
        d2 = r_get2.json()
        assert d2["codice_struttura"] == "058091XYZ999"
        assert d2["camere_disponibili"] == 2
        assert d2["letti_disponibili"] == 4


# -------------------- Preview & Export flow --------------------
class TestRoss1000PreviewAndExport:
    """
    Create a fresh user, verify:
      - preview returns 400 when settings not configured
      - after configure, preview returns math
      - export-xml returns valid XML w/ mandatory tags, channel/country mapping,
        and camereoccupate math is correct.
    """
    CODICE = "058091TESTXX"
    YEAR = 2026
    MONTH = 3  # March 2026

    @pytest.fixture(scope="class")
    def isolated_session(self):
        s = requests.Session()
        email = f"ross_iso_{os.urandom(4).hex()}@example.it"
        r = s.post(f"{BASE_URL}/api/auth/register",
                   json={"email": email, "password": "Passw0rd!", "name": "Iso User"}, timeout=30)
        assert r.status_code == 200
        yield s
        # cleanup bookings created below
        try:
            r_list = s.get(f"{BASE_URL}/api/bookings", timeout=30)
            for b in r_list.json():
                s.delete(f"{BASE_URL}/api/bookings/{b['id']}", timeout=30)
        except Exception:
            pass

    def test_preview_400_when_not_configured(self, isolated_session):
        r = isolated_session.get(f"{BASE_URL}/api/ross1000/preview",
                                 params={"year": self.YEAR, "month": self.MONTH}, timeout=30)
        assert r.status_code == 400
        assert "codice struttura" in r.text.lower() or "configura" in r.text.lower()

    def test_export_xml_400_when_not_configured(self, isolated_session):
        r = isolated_session.get(f"{BASE_URL}/api/ross1000/export-xml",
                                 params={"year": self.YEAR, "month": self.MONTH}, timeout=30)
        assert r.status_code == 400

    def test_configure_settings(self, isolated_session):
        payload = {"codice_struttura": self.CODICE, "camere_disponibili": 2, "letti_disponibili": 4}
        r = isolated_session.post(f"{BASE_URL}/api/ross1000/settings", json=payload, timeout=30)
        assert r.status_code == 200

    def test_create_two_bookings_and_verify_stats(self, isolated_session):
        # Booking 1: 2026-03-05 -> 2026-03-08 (3 nights: 5,6,7), Airbnb, ITALIA
        b1 = {
            "guest_first_name": "MARIO", "guest_last_name": "ROSSI",
            "checkin": "2026-03-05", "checkout": "2026-03-08",
            "gross_price": 300.0, "channel": "Airbnb",
            "date_of_birth": "1985-04-12", "place_of_birth": "Roma",
            "country_of_birth": "ITALIA", "citizenship": "ITALIA",
            "sex": "M", "document_type": "IDENT", "document_number": "AA123",
            "document_place": "Roma",
        }
        r1 = isolated_session.post(f"{BASE_URL}/api/bookings", json=b1, timeout=30)
        assert r1.status_code == 200
        # Booking 2: 2026-03-07 -> 2026-03-10 (3 nights: 7,8,9), Booking, FRANCIA
        b2 = {
            "guest_first_name": "JEAN", "guest_last_name": "DUPONT",
            "checkin": "2026-03-07", "checkout": "2026-03-10",
            "gross_price": 300.0, "channel": "Booking",
            "date_of_birth": "1990-06-15", "place_of_birth": "Parigi",
            "country_of_birth": "FRANCIA", "citizenship": "FRANCIA",
            "sex": "M", "document_type": "IDENT", "document_number": "FR456",
            "document_place": "Parigi",
        }
        r2 = isolated_session.post(f"{BASE_URL}/api/bookings", json=b2, timeout=30)
        assert r2.status_code == 200

        # Preview
        r = isolated_session.get(f"{BASE_URL}/api/ross1000/preview",
                                 params={"year": self.YEAR, "month": self.MONTH}, timeout=30)
        assert r.status_code == 200
        stats = r.json()
        # total_arrivi: 2 arrivals in March (5th and 7th)
        assert stats["total_arrivi"] == 2, f"expected 2 arrivi, got {stats['total_arrivi']}"
        # total_partenze: 2 departures in March (8th and 10th)
        assert stats["total_partenze"] == 2, f"expected 2 partenze, got {stats['total_partenze']}"
        # total_presenze = nights sum: 3 (b1) + 3 (b2) = 6
        assert stats["total_presenze"] == 6, f"expected 6 presenze, got {stats['total_presenze']}"
        assert stats["days_in_month"] == 31
        assert stats["codice_struttura"] == self.CODICE
        # by_country: ITALIA=1 arrival, FRANCIA=1 arrival
        countries = {c["country"]: c["arrivi"] for c in stats["by_country"]}
        assert countries.get("ITALIA") == 1
        assert countries.get("FRANCIA") == 1

    def test_export_xml_structure_and_mapping(self, isolated_session):
        r = isolated_session.get(f"{BASE_URL}/api/ross1000/export-xml",
                                 params={"year": self.YEAR, "month": self.MONTH}, timeout=30)
        assert r.status_code == 200
        # headers
        assert "application/xml" in r.headers.get("content-type", "")
        cd = r.headers.get("content-disposition", "").lower()
        assert "attachment" in cd
        assert "ross1000_2026_03.xml" in cd

        xml = r.text
        # XML declaration
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        # Root & mandatory tags
        assert "<movimenti>" in xml and "</movimenti>" in xml
        assert f"<codice>{self.CODICE}</codice>" in xml
        assert "<prodotto>CASABNB</prodotto>" in xml

        # One <movimento> per day of month (31 for March)
        movimenti = re.findall(r"<movimento>", xml)
        assert len(movimenti) == 31, f"expected 31 movimenti, got {len(movimenti)}"

        # <data> in YYYYMMDD format
        dates_found = re.findall(r"<data>(\d{8})</data>", xml)
        assert len(dates_found) == 31
        assert "20260301" in dates_found
        assert "20260331" in dates_found

        # struttura fields on every day
        assert xml.count("<apertura>SI</apertura>") == 31  # camere_disponibili=2 > 0
        assert xml.count("<cameredisponibili>2</cameredisponibili>") == 31
        assert xml.count("<lettidisponibili>4</lettidisponibili>") == 31

        # Camereoccupate math — extract per-day occupancy
        # Bookings: b1 5->8 active on 5,6,7. b2 7->10 active on 7,8,9.
        # So occupancy per day of March: day5=1, day6=1, day7=2, day8=1, day9=1, others 0
        movimenti_blocks = re.findall(r"<movimento>.*?</movimento>", xml, re.DOTALL)
        assert len(movimenti_blocks) == 31
        occ_by_day = {}
        for block in movimenti_blocks:
            m_data = re.search(r"<data>(\d{8})</data>", block)
            m_occ = re.search(r"<camereoccupate>(\d+)</camereoccupate>", block)
            assert m_data and m_occ
            occ_by_day[m_data.group(1)] = int(m_occ.group(1))
        assert occ_by_day["20260305"] == 1
        assert occ_by_day["20260306"] == 1
        assert occ_by_day["20260307"] == 2
        assert occ_by_day["20260308"] == 1
        assert occ_by_day["20260309"] == 1
        assert occ_by_day["20260304"] == 0
        assert occ_by_day["20260310"] == 0

        # <arrivi>/<arrivo> for checkins
        assert xml.count("<arrivo>") >= 2  # 2 arrivi + partenza <arrivo> reference tags
        assert "<tipoalloggiato>16</tipoalloggiato>" in xml
        assert "<cognome>ROSSI</cognome>" in xml
        assert "<nome>MARIO</nome>" in xml
        assert "<cognome>DUPONT</cognome>" in xml
        assert "<nome>JEAN</nome>" in xml
        assert "<sesso>M</sesso>" in xml

        # Country mapping: ITALIA -> 100000100, FRANCIA -> 100000203
        assert "<cittadinanza>100000100</cittadinanza>" in xml
        assert "<cittadinanza>100000203</cittadinanza>" in xml

        # Channel mapping: Airbnb -> AIRBNB, Booking -> BOOKING.COM
        assert "<canaleprenotazione>AIRBNB</canaleprenotazione>" in xml
        assert "<canaleprenotazione>BOOKING.COM</canaleprenotazione>" in xml

        # datanascita YYYYMMDD
        assert "<datanascita>19850412</datanascita>" in xml
        assert "<datanascita>19900615</datanascita>" in xml

        # partenze block present (2 checkouts in March: 03-08 and 03-10)
        partenze_blocks = re.findall(r"<partenze>.*?</partenze>", xml, re.DOTALL)
        assert len(partenze_blocks) == 2

    def test_channel_mapping_direct_and_other(self, isolated_session):
        # Create a Direct + Other booking to verify remaining channel mappings
        b_direct = {
            "guest_first_name": "GIULIA", "guest_last_name": "VERDI",
            "checkin": "2026-03-15", "checkout": "2026-03-16",
            "gross_price": 100.0, "channel": "Direct",
            "date_of_birth": "1975-11-01", "place_of_birth": "Napoli",
            "citizenship": "ITALIA", "sex": "F",
        }
        b_other = {
            "guest_first_name": "TEST", "guest_last_name": "OTHER",
            "checkin": "2026-03-20", "checkout": "2026-03-21",
            "gross_price": 100.0, "channel": "Other",
            "date_of_birth": "1980-01-01", "place_of_birth": "Roma",
            "citizenship": "ITALIA", "sex": "M",
        }
        assert isolated_session.post(f"{BASE_URL}/api/bookings", json=b_direct, timeout=30).status_code == 200
        assert isolated_session.post(f"{BASE_URL}/api/bookings", json=b_other, timeout=30).status_code == 200

        r = isolated_session.get(f"{BASE_URL}/api/ross1000/export-xml",
                                 params={"year": self.YEAR, "month": self.MONTH}, timeout=30)
        assert r.status_code == 200
        xml = r.text
        assert "<canaleprenotazione>DIRETTA WEB</canaleprenotazione>" in xml
        assert "<canaleprenotazione>ALTRO PORTALE</canaleprenotazione>" in xml
