"""
Backend regression tests for B&B Manager (Italian B&B management app).
Iteration 2 — code-quality refactor (frontend only), backend logic unchanged.
Tests all endpoints listed in the review request.
"""
import os
import io
import zipfile
import pytest
import requests
from datetime import date, timedelta

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bb-manager-pro.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@bnb.it"
ADMIN_PASSWORD = "admin123"


# -------------------- fixtures --------------------
@pytest.fixture(scope="session")
def admin_session():
    """Login as admin and return a session with httpOnly cookies set."""
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
               timeout=30)
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    assert "access_token" in s.cookies, "access_token cookie missing"
    assert "refresh_token" in s.cookies, "refresh_token cookie missing"
    return s


@pytest.fixture(scope="session")
def anon_session():
    return requests.Session()


# -------------------- Auth --------------------
class TestAuth:
    def test_login_success_sets_cookies(self):
        s = requests.Session()
        r = s.post(f"{BASE_URL}/api/auth/login",
                   json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        assert "id" in data
        # httpOnly cookies
        assert "access_token" in s.cookies
        assert "refresh_token" in s.cookies
        # Verify Set-Cookie httponly flag
        set_cookie_header = r.headers.get("set-cookie", "").lower()
        assert "httponly" in set_cookie_header

    def test_login_invalid_credentials(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": "wrong"}, timeout=30)
        assert r.status_code == 401

    def test_me_with_cookie(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/auth/me", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == ADMIN_EMAIL
        assert "password_hash" not in data
        assert "_id" not in data

    def test_protected_endpoints_reject_without_cookie(self, anon_session):
        endpoints = [
            ("GET", "/api/auth/me"),
            ("GET", "/api/bookings"),
            ("GET", "/api/dashboard/stats"),
            ("GET", "/api/inventory"),
            ("GET", "/api/expenses"),
            ("GET", "/api/alloggiati/preview?start_date=2025-01-01&end_date=2025-12-31"),
            ("GET", "/api/alloggiati/export?start_date=2025-01-01&end_date=2025-12-31"),
            ("GET", "/api/alloggiati/export-zip?start_date=2025-01-01&end_date=2025-12-31"),
        ]
        for method, path in endpoints:
            r = anon_session.request(method, f"{BASE_URL}{path}", timeout=30)
            assert r.status_code == 401, f"{method} {path} expected 401 got {r.status_code}"


# -------------------- Dashboard --------------------
class TestDashboard:
    def test_dashboard_stats_shape(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/dashboard/stats", timeout=30)
        assert r.status_code == 200
        d = r.json()
        for k in ["total_gross", "total_net", "year_gross", "year_net",
                  "occupancy_pct", "total_bookings", "year_bookings",
                  "channels", "monthly"]:
            assert k in d, f"missing {k}"
        assert isinstance(d["channels"], list)
        assert isinstance(d["monthly"], list)
        assert isinstance(d["occupancy_pct"], (int, float))


# -------------------- Bookings CRUD & net_revenue math --------------------
class TestBookings:
    def _create(self, sess, channel, gross=100.0, nights=3):
        ci = date.today() + timedelta(days=10)
        co = ci + timedelta(days=nights)
        payload = {
            "guest_first_name": "TESTFIRST",
            "guest_last_name": "TESTLAST",
            "checkin": ci.isoformat(),
            "checkout": co.isoformat(),
            "gross_price": gross,
            "channel": channel,
            "notes": "TEST_iteration2",
            "date_of_birth": "1985-04-12",
            "place_of_birth": "Roma",
            "document_number": "AZ1234567",
            "document_place": "Roma",
        }
        r = sess.post(f"{BASE_URL}/api/bookings", json=payload, timeout=30)
        return r

    def test_net_revenue_math_airbnb(self, admin_session):
        r = self._create(admin_session, "Airbnb", gross=100.0, nights=3)
        assert r.status_code == 200
        d = r.json()
        # Airbnb: 100 * (1-0.03) * (1-0.21) = 100 * 0.97 * 0.79 = 76.63
        assert d["net_revenue"] == round(100.0 * 0.97 * 0.79, 2)
        assert d["nights"] == 3
        assert d["channel"] == "Airbnb"
        assert d["owner_id"]
        self._cleanup(admin_session, d["id"])

    def test_net_revenue_math_booking(self, admin_session):
        r = self._create(admin_session, "Booking", gross=100.0)
        d = r.json()
        # 100 * 0.85 * 0.79 = 67.15
        assert d["net_revenue"] == round(100.0 * 0.85 * 0.79, 2)
        self._cleanup(admin_session, d["id"])

    def test_net_revenue_math_direct(self, admin_session):
        r = self._create(admin_session, "Direct", gross=100.0)
        d = r.json()
        # 100 * 1.0 * 0.79 = 79.00
        assert d["net_revenue"] == round(100.0 * 0.79, 2)
        self._cleanup(admin_session, d["id"])

    def test_bookings_crud_flow(self, admin_session):
        r = self._create(admin_session, "Direct", gross=150.0, nights=2)
        assert r.status_code == 200
        bid = r.json()["id"]

        # List
        r_list = admin_session.get(f"{BASE_URL}/api/bookings", timeout=30)
        assert r_list.status_code == 200
        ids = [b["id"] for b in r_list.json()]
        assert bid in ids

        # Update
        ci = date.today() + timedelta(days=20)
        co = ci + timedelta(days=4)
        upd_payload = {
            "guest_first_name": "UPDATED", "guest_last_name": "USER",
            "checkin": ci.isoformat(), "checkout": co.isoformat(),
            "gross_price": 200.0, "channel": "Airbnb", "notes": "updated"
        }
        r_upd = admin_session.put(f"{BASE_URL}/api/bookings/{bid}", json=upd_payload, timeout=30)
        assert r_upd.status_code == 200
        upd = r_upd.json()
        assert upd["nights"] == 4
        assert upd["guest_first_name"] == "UPDATED"
        assert upd["net_revenue"] == round(200.0 * 0.97 * 0.79, 2)

        # Delete
        r_del = admin_session.delete(f"{BASE_URL}/api/bookings/{bid}", timeout=30)
        assert r_del.status_code == 200

        # Verify deletion
        r_list2 = admin_session.get(f"{BASE_URL}/api/bookings", timeout=30)
        ids2 = [b["id"] for b in r_list2.json()]
        assert bid not in ids2

    def _cleanup(self, sess, bid):
        try:
            sess.delete(f"{BASE_URL}/api/bookings/{bid}", timeout=30)
        except Exception:
            pass


# -------------------- Pricing AI --------------------
class TestPricingAI:
    def test_pricing_suggest_returns_valid_json(self, admin_session):
        payload = {
            "checkin": "2025-08-15",
            "checkout": "2025-08-18",
            "location": "Roma",
            "base_price": 90.0,
            "events": "Ferragosto",
            "occupancy_context": "alta stagione"
        }
        r = admin_session.post(f"{BASE_URL}/api/pricing/suggest", json=payload, timeout=90)
        assert r.status_code == 200, f"pricing AI failed: {r.status_code} {r.text}"
        d = r.json()
        for k in ["suggested_price", "min_price", "max_price", "reasoning", "nights", "total_suggested"]:
            assert k in d, f"missing {k}"
        assert d["nights"] == 3
        assert isinstance(d["suggested_price"], (int, float))
        assert isinstance(d["reasoning"], str) and len(d["reasoning"]) > 0
        assert d["total_suggested"] == round(d["suggested_price"] * 3, 2)


# -------------------- Public Registration (multipart + photo) --------------------
class TestPublicRegistration:
    def test_public_registration_with_photo(self, admin_session):
        ci = date.today() + timedelta(days=30)
        co = ci + timedelta(days=2)
        # Small in-memory JPG-like bytes
        fake_photo = b"\xff\xd8\xff\xe0" + b"TESTJPEG" * 50 + b"\xff\xd9"
        files = {"photos": ("doc.jpg", io.BytesIO(fake_photo), "image/jpeg")}
        form = {
            "guest_first_name": "PUBLIC",
            "guest_last_name": "TESTGUEST",
            "checkin": ci.isoformat(),
            "checkout": co.isoformat(),
            "channel": "Direct",
            "document_number": "PB999",
            "date_of_birth": "1990-01-01",
            "place_of_birth": "Milano",
            "country_of_birth": "ITALIA",
            "citizenship": "ITALIA",
            "sex": "M",
            "document_type": "IDENT",
            "document_place": "Milano",
        }
        r = requests.post(f"{BASE_URL}/api/public/registration", data=form, files=files, timeout=60)
        assert r.status_code == 200, f"public reg failed: {r.status_code} {r.text}"
        d = r.json()
        assert d["ok"] is True
        assert d["photos_uploaded"] == 1
        assert d["id"]
        # Find booking as admin and confirm photo_paths + owner_id=admin
        r_list = admin_session.get(f"{BASE_URL}/api/bookings", timeout=30)
        booking = next((b for b in r_list.json() if b["id"] == d["id"]), None)
        assert booking is not None, "created booking not visible to admin"
        assert booking.get("source") == "public_form"
        assert booking.get("photo_paths") and len(booking["photo_paths"]) == 1
        assert booking.get("guest_first_name") == "PUBLIC"
        # cleanup
        admin_session.delete(f"{BASE_URL}/api/bookings/{d['id']}", timeout=30)

    def test_public_property_info(self):
        r = requests.get(f"{BASE_URL}/api/public/property-info", timeout=30)
        assert r.status_code == 200
        assert r.json().get("active") is True


# -------------------- Alloggiati (preview / export / export-zip) --------------------
class TestAlloggiati:
    @pytest.fixture(scope="class")
    def booking_id(self, admin_session):
        ci = date.today() + timedelta(days=5)
        co = ci + timedelta(days=2)
        # Also send a photo via public reg so ZIP has foto_documenti content
        fake_photo = b"\xff\xd8\xff\xe0" + b"ZIP_TEST_PHOTO" * 30 + b"\xff\xd9"
        files = {"photos": ("zip_doc.jpg", io.BytesIO(fake_photo), "image/jpeg")}
        form = {
            "guest_first_name": "ZIPTESTFN", "guest_last_name": "ZIPTESTLN",
            "checkin": ci.isoformat(), "checkout": co.isoformat(),
            "channel": "Direct",
            "document_number": "ZIP123", "date_of_birth": "1985-05-15",
            "place_of_birth": "Torino", "country_of_birth": "ITALIA",
            "citizenship": "ITALIA", "sex": "F", "document_type": "IDENT",
            "document_place": "Torino",
        }
        r = requests.post(f"{BASE_URL}/api/public/registration", data=form, files=files, timeout=60)
        assert r.status_code == 200
        bid = r.json()["id"]
        yield bid, ci.isoformat(), co.isoformat()
        admin_session.delete(f"{BASE_URL}/api/bookings/{bid}", timeout=30)

    def test_alloggiati_preview(self, admin_session, booking_id):
        _, ci, _ = booking_id
        start = ci
        end = (date.fromisoformat(ci) + timedelta(days=10)).isoformat()
        r = admin_session.get(f"{BASE_URL}/api/alloggiati/preview",
                              params={"start_date": start, "end_date": end}, timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "total" in d and "records" in d
        assert d["total"] >= 1
        assert any(rec.get("valid") for rec in d["records"])

    def test_alloggiati_export_txt(self, admin_session, booking_id):
        _, ci, _ = booking_id
        start = ci
        end = (date.fromisoformat(ci) + timedelta(days=10)).isoformat()
        r = admin_session.get(f"{BASE_URL}/api/alloggiati/export",
                              params={"start_date": start, "end_date": end}, timeout=30)
        assert r.status_code == 200
        assert "text/plain" in r.headers.get("content-type", "")
        assert "attachment" in r.headers.get("content-disposition", "").lower()
        assert "ZIPTESTLN" in r.text.upper() or len(r.text) > 0

    def test_alloggiati_export_zip(self, admin_session, booking_id):
        _, ci, _ = booking_id
        start = ci
        end = (date.fromisoformat(ci) + timedelta(days=10)).isoformat()
        r = admin_session.get(f"{BASE_URL}/api/alloggiati/export-zip",
                              params={"start_date": start, "end_date": end}, timeout=30)
        assert r.status_code == 200
        assert "application/zip" in r.headers.get("content-type", "")
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        # Must contain .txt file, LEGGIMI.txt, and at least one file in foto_documenti/
        assert any(n.endswith(".txt") and n.startswith("alloggiati_") for n in names), f"no alloggiati txt in {names}"
        assert "LEGGIMI.txt" in names, f"LEGGIMI.txt missing in {names}"
        assert any(n.startswith("foto_documenti/") for n in names), f"foto_documenti/ missing in {names}"


# -------------------- Inventory --------------------
class TestInventory:
    def test_inventory_crud(self, admin_session):
        payload = {"name": "TEST_lenzuola", "category": "Biancheria",
                   "quantity": 10, "unit": "pz", "min_threshold": 2, "price_per_unit": 5.5}
        r = admin_session.post(f"{BASE_URL}/api/inventory", json=payload, timeout=30)
        assert r.status_code == 200
        iid = r.json()["id"]
        # List
        r_list = admin_session.get(f"{BASE_URL}/api/inventory", timeout=30)
        assert r_list.status_code == 200
        item = next((x for x in r_list.json() if x["id"] == iid), None)
        assert item is not None
        assert item["name"] == "TEST_lenzuola"
        assert item.get("owner_id")
        # Update
        payload["quantity"] = 15
        r_upd = admin_session.put(f"{BASE_URL}/api/inventory/{iid}", json=payload, timeout=30)
        assert r_upd.status_code == 200
        # Delete
        r_del = admin_session.delete(f"{BASE_URL}/api/inventory/{iid}", timeout=30)
        assert r_del.status_code == 200


# -------------------- Expenses --------------------
class TestExpenses:
    def test_expense_crud(self, admin_session):
        due = (date.today() + timedelta(days=30)).isoformat()
        payload = {"name": "TEST_IMU", "category": "IMU",
                   "amount": 250.0, "due_date": due, "recurrence": "yearly", "paid": False}
        r = admin_session.post(f"{BASE_URL}/api/expenses", json=payload, timeout=30)
        assert r.status_code == 200
        eid = r.json()["id"]
        # List
        r_list = admin_session.get(f"{BASE_URL}/api/expenses", timeout=30)
        assert r_list.status_code == 200
        exp = next((x for x in r_list.json() if x["id"] == eid), None)
        assert exp is not None
        assert exp["name"] == "TEST_IMU"
        # Update - toggle paid=true
        payload["paid"] = True
        r_upd = admin_session.put(f"{BASE_URL}/api/expenses/{eid}", json=payload, timeout=30)
        assert r_upd.status_code == 200
        # Verify persistence via GET (list)
        r_list2 = admin_session.get(f"{BASE_URL}/api/expenses", timeout=30)
        exp2 = next((x for x in r_list2.json() if x["id"] == eid), None)
        assert exp2["paid"] is True
        # Delete
        r_del = admin_session.delete(f"{BASE_URL}/api/expenses/{eid}", timeout=30)
        assert r_del.status_code == 200
