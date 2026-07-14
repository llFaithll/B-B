from dotenv import load_dotenv
from pathlib import Path
from contextlib import asynccontextmanager

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import logging
import uuid
import bcrypt
import jwt
import secrets
import requests
import json
from datetime import datetime, timezone, timedelta, date
from typing import List, Optional, Literal, Annotated
from io import BytesIO

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, UploadFile, File, Form
from fastapi.responses import PlainTextResponse, StreamingResponse, FileResponse
import zipfile
import mimetypes
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from pydantic import BaseModel, Field, EmailStr, BeforeValidator, ConfigDict
from icalendar import Calendar
from ross1000 import build_movimenti_xml, compute_month_stats

# --------------------- Config ---------------------
JWT_ALGORITHM = "HS256"
# Prevenzione Crash Etsy: se il cliente dimentica il JWT_SECRET su Render, ne generiamo uno al volo sicuro
JWT_SECRET = os.environ.get("JWT_SECRET") or secrets.token_hex(32)
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")

# Prevenzione Crash Refuso Emergent: usiamo os.getenv per evitare il KeyError all'avvio
EMERGENT_LLM_KEY = os.getenv("EMERGENT_LLM_KEY", "default_placeholder")
UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client.get_default_database(default='gestionale')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --------------------- Lifespan (Startup & Shutdown) ---------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Avvio dell'applicazione: configurazione indici MongoDB...")
    await db.users.create_index("email", unique=True)
    await db.bookings.create_index([("owner_id", 1), ("checkin", -1)])
    await db.inventory.create_index([("owner_id", 1)])
    await db.expenses.create_index([("owner_id", 1), ("due_date", 1)])
    
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@bnb.it")
    admin_pw = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "email": admin_email, "password_hash": hash_password(admin_pw),
            "name": "Admin B&B", "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        logger.info(f"Seeded admin: {admin_email}")
    elif not verify_password(admin_pw, existing["password_hash"]):
        await db.users.update_one({"email": admin_email}, {"$set": {"password_hash": hash_password(admin_pw)}})
    
    yield
    logger.info("Chiusura dell'applicazione: disconnessione da MongoDB...")
    client.close()

app = FastAPI(title="B&B Manager", lifespan=lifespan)

# Abilitazione Globale dei CORS: Consente a qualsiasi URL del frontend (inclusi i domini dei clienti su Netlify) di comunicare con questo backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api")

# --------------------- Helpers ---------------------
def obj_to_str(v):
    if isinstance(v, ObjectId):
        return str(v)
    return v

PyObjectId = Annotated[str, BeforeValidator(obj_to_str)]

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def verify_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())

def create_access_token(user_id: str, email: str) -> str:
    payload = {"sub": user_id, "email": email, "type": "access",
               "exp": datetime.now(timezone.utc) + timedelta(minutes=60 * 24)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def create_refresh_token(user_id: str) -> str:
    payload = {"sub": user_id, "type": "refresh",
               "exp": datetime.now(timezone.utc) + timedelta(days=7)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def set_auth_cookies(response: Response, access: str, refresh: str):
    response.set_cookie("access_token", access, httponly=True, secure=True, samesite="none", max_age=86400, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, secure=True, samesite="none", max_age=604800, path="/")

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user["id"] = str(user["_id"])
        user.pop("_id", None)
        user.pop("password_hash", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# --------------------- Models ---------------------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    name: str

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class BookingIn(BaseModel):
    guest_first_name: str
    guest_last_name: str
    checkin: str
    checkout: str
    gross_price: float
    channel: Literal["Airbnb", "Booking", "Direct", "Other"] = "Direct"
    notes: Optional[str] = ""
    date_of_birth: Optional[str] = None
    place_of_birth: Optional[str] = None
    country_of_birth: Optional[str] = "ITALIA"
    citizenship: Optional[str] = "ITALIA"
    sex: Optional[Literal["M", "F"]] = "M"
    document_type: Optional[str] = "IDENT"
    document_number: Optional[str] = None
    document_place: Optional[str] = None
    guest_type: Optional[str] = "16"

class InventoryIn(BaseModel):
    name: str
    category: Optional[str] = "Generale"
    quantity: float
    unit: Optional[str] = "pz"
    min_threshold: float = 0
    price_per_unit: Optional[float] = 0

class ExpenseIn(BaseModel):
    name: str
    category: str
    amount: float
    due_date: str
    recurrence: Literal["once", "monthly", "quarterly", "yearly"] = "once"
    paid: bool = False
    notes: Optional[str] = ""

class ICalImportIn(BaseModel):
    url: str
    channel: Literal["Airbnb", "Booking", "Direct", "Other"] = "Airbnb"
    default_price: float = 80.0

class PricingSuggestIn(BaseModel):
    checkin: str
    checkout: str
    location: str = "Italia"
    base_price: float = 80.0
    events: Optional[str] = ""
    occupancy_context: Optional[str] = ""

# --------------------- Utilities ---------------------
CHANNEL_COMMISSION = {"Airbnb": 0.03, "Booking": 0.15, "Direct": 0.0, "Other": 0.10}
CEDOLARE_SECCA_RATE = 0.21

def compute_net(gross: float, channel: str) -> float:
    commission = CHANNEL_COMMISSION.get(channel, 0.10)
    after_commission = gross * (1 - commission)
    after_tax = after_commission * (1 - CEDOLARE_SECCA_RATE)
    return round(after_tax, 2)

def nights_between(checkin: str, checkout: str) -> int:
    ci = datetime.fromisoformat(checkin).date()
    co = datetime.fromisoformat(checkout).date()
    return max((co - ci).days, 0)

def serialize_booking(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id"))
    return doc

# --------------------- Auth ---------------------
@api.post("/auth/register")
async def register(payload: RegisterIn, response: Response):
    email = payload.email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email già registrata")
    doc = {"email": email, "password_hash": hash_password(payload.password),
           "name": payload.name, "role": "user",
           "created_at": datetime.now(timezone.utc).isoformat()}
    result = await db.users.insert_one(doc)
    uid = str(result.inserted_id)
    set_auth_cookies(response, create_access_token(uid, email), create_refresh_token(uid))
    return {"id": uid, "email": email, "name": payload.name, "role": "user"}

@api.post("/auth/login")
async def login(payload: LoginIn, response: Response):
    email = payload.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenziali non valide")
    uid = str(user["_id"])
    set_auth_cookies(response, create_access_token(uid, email), create_refresh_token(uid))
    return {"id": uid, "email": email, "name": user.get("name"), "role": user.get("role", "user")}

@api.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}

@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user

@api.post("/auth/refresh")
async def refresh(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        access = create_access_token(str(user["_id"]), user["email"])
        response.set_cookie("access_token", access, httponly=True, secure=True, samesite="none", max_age=86400, path="/")
        return {"ok": True}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# --------------------- Bookings ---------------------
@api.post("/bookings")
async def create_booking(payload: BookingIn, user: dict = Depends(get_current_user)):
    nights = nights_between(payload.checkin, payload.checkout)
    net = compute_net(payload.gross_price, payload.channel)
    doc = payload.model_dump()
    doc.update({
        "nights": nights,
        "net_revenue": net,
        "owner_id": user["id"],
        "external_id": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    result = await db.bookings.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc

@api.get("/bookings")
async def list_bookings(user: dict = Depends(get_current_user)):
    docs = await db.bookings.find({"owner_id": user["id"]}).sort("checkin", -1).to_list(2000)
    return [serialize_booking(d) for d in docs]

@api.put("/bookings/{bid}")
async def update_booking(bid: str, payload: BookingIn, user: dict = Depends(get_current_user)):
    nights = nights_between(payload.checkin, payload.checkout)
    net = compute_net(payload.gross_price, payload.channel)
    doc = payload.model_dump()
    doc.update({"nights": nights, "net_revenue": net})
    r = await db.bookings.update_one({"_id": ObjectId(bid), "owner_id": user["id"]}, {"$set": doc})
    if r.matched_count == 0:
        raise HTTPException(404, "Prenotazione non trovata")
    updated = await db.bookings.find_one({"_id": ObjectId(bid)})
    return serialize_booking(updated)

@api.delete("/bookings/{bid}")
async def delete_booking(bid: str, user: dict = Depends(get_current_user)):
    r = await db.bookings.delete_one({"_id": ObjectId(bid), "owner_id": user["id"]})
    if r.deleted_count == 0:
        raise HTTPException(404, "Prenotazione non trovata")
    return {"ok": True}

@api.post("/bookings/ical-import")
async def ical_import(payload: ICalImportIn, user: dict = Depends(get_current_user)):
    try:
        resp = requests.get(payload.url, timeout=15)
        resp.raise_for_status()
        cal = Calendar.from_ical(resp.content)
    except Exception as e:
        raise HTTPException(400, f"Impossibile leggere iCal: {e}")
    imported = 0
    skipped = 0
    for comp in cal.walk():
        if comp.name != "VEVENT":
            continue
        uid = str(comp.get("UID", ""))
        if not uid:
            continue
        if await db.bookings.find_one({"external_id": uid, "owner_id": user["id"]}):
            skipped += 1
            continue
        dtstart = comp.get("DTSTART").dt
        dtend = comp.get("DTEND").dt
        ci = dtstart.isoformat() if hasattr(dtstart, 'isoformat') else str(dtstart)
        co = dtend.isoformat() if hasattr(dtend, 'isoformat') else str(dtend)
        summary = str(comp.get("SUMMARY", "Ospite iCal"))
        nights = nights_between(ci, co)
        gross = payload.default_price * max(nights, 1)
        doc = {
            "guest_first_name": summary[:40], "guest_last_name": "(iCal)",
            "checkin": ci, "checkout": co, "gross_price": gross,
            "channel": payload.channel, "notes": str(comp.get("DESCRIPTION", "")),
            "nights": nights, "net_revenue": compute_net(gross, payload.channel),
            "owner_id": user["id"], "external_id": uid,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.bookings.insert_one(doc)
        imported += 1
    return {"imported": imported, "skipped": skipped}

# --------------------- Dashboard ---------------------
@api.get("/dashboard/stats")
async def dashboard_stats(user: dict = Depends(get_current_user)):
    bookings = await db.bookings.find({"owner_id": user["id"]}).to_list(5000)
    now = datetime.now(timezone.utc).date()
    year_start = date(now.year, 1, 1)
    total_gross = sum(b.get("gross_price", 0) for b in bookings)
    total_net = sum(b.get("net_revenue", 0) for b in bookings)
    year_bookings = [b for b in bookings if b.get("checkin", "") >= year_start.isoformat()]
    year_gross = sum(b.get("gross_price", 0) for b in year_bookings)
    year_net = sum(b.get("net_revenue", 0) for b in year_bookings)
    year_nights = sum(b.get("nights", 0) for b in year_bookings)
    days_in_year = 366 if now.year % 4 == 0 else 365
    occupancy = round((year_nights / days_in_year) * 100, 1) if days_in_year else 0
    channels = {}
    for b in year_bookings:
        ch = b.get("channel", "Direct")
        channels[ch] = channels.get(ch, 0) + b.get("gross_price", 0)
    monthly = {}
    for b in year_bookings:
        m = b.get("checkin", "")[:7]
        monthly[m] = monthly.get(m, 0) + b.get("gross_price", 0)
    monthly_list = [{"month": k, "revenue": round(v, 2)} for k, v in sorted(monthly.items())]
    channel_list = [{"channel": k, "revenue": round(v, 2)} for k, v in channels.items()]
    return {
        "total_gross": round(total_gross, 2),
        "total_net": round(total_net, 2),
        "year_gross": round(year_gross, 2),
        "year_net": round(year_net, 2),
        "occupancy_pct": occupancy,
        "total_bookings": len(bookings),
        "year_bookings": len(year_bookings),
        "channels": channel_list,
        "monthly": monthly_list,
    }

# --------------------- Inventory ---------------------
@api.post("/inventory")
async def create_inventory(payload: InventoryIn, user: dict = Depends(get_current_user)):
    doc = payload.model_dump()
    doc["owner_id"] = user["id"]
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    r = await db.inventory.insert_one(doc)
    doc["id"] = str(r.inserted_id)
    doc.pop("_id", None)
    return doc

@api.get("/inventory")
async def list_inventory(user: dict = Depends(get_current_user)):
    docs = await db.inventory.find({"owner_id": user["id"]}).sort("name", 1).to_list(1000)
    return [serialize_booking(d) for d in docs]

@api.put("/inventory/{iid}")
async def update_inventory(iid: str, payload: InventoryIn, user: dict = Depends(get_current_user)):
    doc = payload.model_dump()
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    r = await db.inventory.update_one({"_id": ObjectId(iid), "owner_id": user["id"]}, {"$set": doc})
    if r.matched_count == 0:
        raise HTTPException(404, "Prodotto non trovato")
    return {"ok": True}

@api.delete("/inventory/{iid}")
async def delete_inventory(iid: str, user: dict = Depends(get_current_user)):
    r = await db.inventory.delete_one({"_id": ObjectId(iid), "owner_id": user["id"]})
    if r.deleted_count == 0:
        raise HTTPException(404, "Prodotto non trovato")
    return {"ok": True}

# --------------------- Expenses ---------------------
@api.post("/expenses")
async def create_expense(payload: ExpenseIn, user: dict = Depends(get_current_user)):
    doc = payload.model_dump()
    doc["owner_id"] = user["id"]
    doc["created_at"] = datetime.now(timezone.utc).isoformat()
    r = await db.expenses.insert_one(doc)
    doc["id"] = str(r.inserted_id)
    doc.pop("_id", None)
    return doc

@api.get("/expenses")
async def list_expenses(user: dict = Depends(get_current_user)):
    docs = await db.expenses.find({"owner_id": user["id"]}).sort("due_date", 1).to_list(1000)
    return [serialize_booking(d) for d in docs]

@api.put("/expenses/{eid}")
async def update_expense(eid: str, payload: ExpenseIn, user: dict = Depends(get_current_user)):
    doc = payload.model_dump()
    r = await db.expenses.update_one({"_id": ObjectId(eid), "owner_id": user["id"]}, {"$set": doc})
    if r.matched_count == 0:
        raise HTTPException(404, "Spesa non trovata")
    return {"ok": True}

@api.delete("/expenses/{eid}")
async def delete_expense(eid: str, user: dict = Depends(get_current_user)):
    r = await db.expenses.delete_one({"_id": ObjectId(eid), "owner_id": user["id"]})
    if r.deleted_count == 0:
        raise HTTPException(404, "Spesa non trovata")
    return {"ok": True}

# --------------------- Pricing AI (Groq Completions Corretto) ---------------------
@api.post("/pricing/suggest")
async def suggest_price(payload: PricingSuggestIn, user: dict = Depends(get_current_user)):
    checkin = payload.checkin
    checkout = payload.checkout
    nights = nights_between(checkin, checkout)
    
    groq_key = os.environ.get("GEMINI_API_KEY", "").strip()
    for c in ["[", "]", "(", ")", "'", '"', " "]:
        groq_key = groq_key.replace(c, "")
        
    if not groq_key:
        suggested = payload.base_price
        return {
            "suggested_price": suggested, "min_price": round(suggested * 0.8, 2),
            "max_price": round(suggested * 1.4, 2), "nights": nights,
            "total_suggested": round(suggested * nights, 2),
            "reasoning": "Configura la chiave di Groq nella variabile GEMINI_API_KEY su Render."
        }

    prompt = f"""
    Sei un assistente virtuale esperto di Revenue Management per strutture ricettive situato in: {payload.location}.
    Calcola la tariffa ottimale basandoti su:
    - Prezzo base dell'host: {payload.base_price}€ a notte
    - Notti: {nights}
    - Contesto occupazione: {payload.occupancy_context or 'Nessuna specifica'}
    - Eventi: {payload.events or 'Nessuno'}
    
    Restituisci la risposta esclusivamente in formato JSON valido, senza blocchi di codice markdown (no ```json). Il JSON deve contenere queste esatte chiavi:
    {{
      "suggested_price": numero,
      "min_price": numero,
      "max_price": numero,
      "reasoning": "spiegazione commerciale in italiano di massimo 3 frasi"
    }}
    """
    try:
        dominio = "api.groq.com"
        url = f"https://{dominio}/openai/v1/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "llama-3.1-8b-instant",
            "messages": [
                {"role": "system", "content": "Sei un analista economico che risponde solo in JSON puro senza markdown."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2
        }
        
        response = requests.post(url, json=data, headers=headers, timeout=15)
        response.raise_for_status()
        
        res_json = response.json()
        ai_text = res_json["choices"][0]["message"]["content"].strip()
        
        if ai_text.startswith("```"):
            ai_text = ai_text.split("```")[1]
            if ai_text.startswith("json"):
                ai_text = ai_text[4:]
        ai_text = ai_text.strip()
        
        parsed = json.loads(ai_text)
        suggested = float(parsed.get("suggested_price", payload.base_price))
        
        return {
            "suggested_price": round(suggested, 2),
            "min_price": round(float(parsed.get("min_price", suggested * 0.8)), 2),
            "max_price": round(float(parsed.get("max_price", suggested * 1.4)), 2),
            "reasoning": parsed.get("reasoning", "Calcolato dall'IA di bordo."),
            "nights": nights, 
            "total_suggested": round(suggested * nights, 2)
        }
    except Exception as e:
        logger.error(f"Errore chiamata Groq: {e}")
        suggested = payload.base_price
        return {
            "suggested_price": suggested, 
            "min_price": round(suggested * 0.8, 2),
            "max_price": round(suggested * 1.4, 2),
            "nights": nights, 
            "total_suggested": round(suggested * nights, 2),
            "reasoning": f"Servizio IA momentaneamente non disponibile. (Dettaglio: {str(e)[:40]})"
        }

# --------------------- MODELS TASSA E RICEVUTA ---------------------
class TaxSettingsIn(BaseModel):
    fee_per_night: float = 3.50  # Es. 3.50€ a Roma
    max_nights: int = 10         # Massimo n notti consecutive esentate oltre
    kids_under_age: int = 10     # Esenzione bambini sotto i X anni

# --------------------- IMPOSTAZIONI TASSA DI SOGGIORNO ---------------------
@api.get("/settings/tourist-tax")
async def get_tax_settings(user: dict = Depends(get_current_user)):
    doc = await db.settings.find_one({"owner_id": user["id"], "kind": "tourist_tax"})
    if not doc:
        return {"fee_per_night": 3.50, "max_nights": 10, "kids_under_age": 10}
    return {
        "fee_per_night": doc.get("fee_per_night", 3.50),
        "max_nights": doc.get("max_nights", 10),
        "kids_under_age": doc.get("kids_under_age", 10)
    }

@api.post("/settings/tourist-tax")
async def save_tax_settings(payload: TaxSettingsIn, user: dict = Depends(get_current_user)):
    await db.settings.update_one(
        {"owner_id": user["id"], "kind": "tourist_tax"},
        {"$set": {**payload.model_dump(), "owner_id": user["id"], "kind": "tourist_tax", "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )
    return {"ok": True}

# --------------------- GENERAZIONE RICEVUTA PDF ---------------------
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

@api.get("/bookings/{bid}/receipt-pdf")
async def generate_receipt_pdf(bid: str, guests_count: int = 1, kids_count: int = 0, user: dict = Depends(get_current_user)):
    booking = await db.bookings.find_one({"_id": ObjectId(bid), "owner_id": user["id"]})
    if not booking:
        raise HTTPException(status_code=404, detail="Prenotazione non trovata")
        
    tax_settings = await db.settings.find_one({"owner_id": user["id"], "kind": "tourist_tax"})
    fee = tax_settings.get("fee_per_night", 3.50) if tax_settings else 3.50
    max_n = tax_settings.get("max_nights", 10) if tax_settings else 10
    
    # Calcolo Tassa di Soggiorno applicando i limiti delle notti tassabili
    nights = min(booking.get("nights", 1), max_n)
    adults_count = max(guests_count - kids_count, 1)
    total_tax = round(fee * nights * adults_count, 2)
    
    gross_stay = booking.get("gross_price", 0.0)
    grand_total = round(gross_stay + total_tax, 2)
    
    # Controllo Marca da Bollo italiana (2€ se superi i 77.47€)
    stamp_duty = 2.0 if gross_stay > 77.47 else 0.0

    # Creazione del PDF in memoria tramite ReportLab
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    
    # Stili personalizzati per rendere la ricevuta elegante ed ordinata
    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontSize=22, textColor=colors.HexColor("#1b4332"), spaceAfter=15)
    normal_style = ParagraphStyle('NormalStyle', parent=styles['Normal'], fontSize=10, leading=14)
    bold_style = ParagraphStyle('BoldStyle', parent=styles['Normal'], fontSize=10, fontName="Helvetica-Bold")
    
    story = []
    
    # Intestazione Struttura Ricettiva
    story.append(Paragraph("RICEVUTA NON FISCALE", title_style))
    story.append(Paragraph(f"<b>Struttura:</b> Casa B&B<br/><b>Gestore:</b> {user.get('name')}<br/><b>Email:</b> {user.get('email')}", normal_style))
    story.append(Spacer(1, 20))
    
    # Info Ospite e Soggiorno
    guest_name = f"{booking.get('guest_first_name', '')} {booking.get('guest_last_name', '')}".upper()
    story.append(Paragraph(f"<b>Ricevuta n°:</b> {booking.get('_id')[:8].upper()} / {datetime.now().year}", normal_style))
    story.append(Paragraph(f"<b>Data emissione:</b> {date.today().strftime('%d/%m/%Y')}", normal_style))
    story.append(Paragraph(f"<b>Ospite principale:</b> {guest_name}", normal_style))
    story.append(Paragraph(f"<b>Periodo:</b> dal {booking.get('checkin')} al {booking.get('checkout')} ({booking.get('nights')} notti)", normal_style))
    story.append(Spacer(1, 20))
    
    # Tabella dei Costi
    data = [
        [Paragraph("<b>Descrizione Servizio</b>", normal_style), Paragraph("<b>Importo</b>", normal_style)],
        [Paragraph(f"Pernottamento breve ({booking.get('nights')} notti, {guests_count} ospiti)", normal_style), Paragraph(f"{gross_stay:.2f} €", normal_style)],
        [Paragraph(f"Imposta di Soggiorno Comunale ({nights} notti tassate per {adults_count} adulti)", normal_style), Paragraph(f"{total_tax:.2f} €", normal_style)]
    ]
    
    if stamp_duty > 0:
        data.append([Paragraph("Imposta di bollo assolta sull'originale", normal_style), Paragraph(f"{stamp_duty:.2f} €", normal_style)])
        grand_total = round(grand_total + stamp_duty, 2)
        
    data.append([Paragraph("<b>TOTALE COMPLESSIVO</b>", bold_style), Paragraph(f"<b>{grand_total:.2f} €</b>", bold_style)])
    
    t = Table(data, colWidths=[400, 100])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (1, 0), colors.HexColor("#e9ecef")),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor("#212529")),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, colors.lightgrey),
        ('LINEABOVE', (0, -1), (1, -1), 1, colors.HexColor("#1b4332")),
    ]))
    
    story.append(t)
    story.append(Spacer(1, 30))
    
    # Dicitura esenzione IVA (Obbligatoria per le locazioni turistiche / B&B non imprenditoriali)
    disclaimer = "Operazione effettuata da privato fuori dal campo di applicazione dell'IVA ai sensi dell'art. 4 del D.P.R. 633/1972 e successive modificazioni."
    story.append(Paragraph(f"<font size=8 color='gray'>{disclaimer}</font>", normal_style))
    
    doc.build(story)
    buf.seek(0)
    
    filename = f"ricevuta_{booking.get('guest_last_name', 'ospite')}.pdf"
    return StreamingResponse(buf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

# --------------------- Alloggiati Web Export ---------------------
def format_alloggiati_record(b: dict) -> Optional[str]:
    try:
        ci = datetime.fromisoformat(b["checkin"]).date()
        nights = str(b.get("nights", 1)).zfill(2)
        tipo = (b.get("guest_type") or "16").ljust(2)
        cognome = (b.get("guest_last_name") or "").upper().ljust(50)[:50]
        nome = (b.get("guest_first_name") or "").upper().ljust(30)[:30]
        sesso = (b.get("sex") or "M").ljust(1)[:1]
        dob = b.get("date_of_birth", "1980-01-01")
        try:
            dob_d = datetime.fromisoformat(dob).date()
            dob_str = f"{dob_d.day:02d}/{dob_d.month:02d}/{dob_d.year}"
        except Exception:
            dob_str = "01/01/1980"
        comune_nascita = (b.get("place_of_birth") or "").upper().ljust(9)[:9]
        provincia = "  "
        stato_nascita = (b.get("country_of_birth") or "ITALIA").upper().ljust(9)[:9]
        cittadinanza = (b.get("citizenship") or "ITALIA").upper().ljust(9)[:9]
        doc_tipo = (b.get("document_type") or "IDENT").ljust(5)[:5]
        doc_num = (b.get("document_number") or "").upper().ljust(20)[:20]
        doc_luogo = (b.get("document_place") or "").upper().ljust(9)[:9]
        arrivo = f"{ci.day:02d}/{ci.month:02d}/{ci.year}"
        return f"{tipo}{arrivo}{nights}{cognome}{nome}{sesso}{dob_str}{comune_nascita}{provincia}{stato_nascita}{cittadinanza}{doc_tipo}{doc_num}{doc_luogo}"
    except Exception as e:
        logger.warning(f"Skip record: {e}")
        return None

@api.get("/alloggiati/export", response_class=PlainTextResponse)
async def alloggiati_export(start_date: str, end_date: str, user: dict = Depends(get_current_user)):
    bookings = await db.bookings.find({
        "owner_id": user["id"],
        "checkin": {"$gte": start_date, "$lte": end_date}
    }).to_list(1000)
    lines = [l for b in bookings if (l := format_alloggiati_record(b))]
    return PlainTextResponse("\r\n".join(lines), headers={"Content-Disposition": f'attachment; filename="alloggiati_{start_date}_{end_date}.txt"'})

@api.get("/alloggiati/export-zip")
async def alloggiati_export_zip(start_date: str, end_date: str, user: dict = Depends(get_current_user)):
    bookings = await db.bookings.find({
        "owner_id": user["id"],
        "checkin": {"$gte": start_date, "$lte": end_date}
    }).to_list(1000)
    lines = [l for b in bookings if (l := format_alloggiati_record(b))]
    photo_files = []
    for b in bookings:
        safe_name = f"{(b.get('guest_last_name') or 'X').upper()}_{(b.get('guest_first_name') or 'X').upper()}".replace(" ", "_")
        for i, p in enumerate(b.get("photo_paths", []) or []):
            fpath = UPLOAD_DIR / p
            if fpath.exists():
                ext = fpath.suffix or ".jpg"
                arc = f"foto_documenti/{safe_name}_{i + 1}{ext}"
                photo_files.append((arc, str(fpath)))
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"alloggiati_{start_date}_{end_date}.txt", "\r\n".join(lines))
        for arc, fpath in photo_files:
            zf.write(fpath, arc)
        zf.writestr("LEGGIMI.txt",
                    "Carica il file .txt sul portale Alloggiati Web della Polizia di Stato.\r\n"
                    "La cartella foto_documenti contiene le foto dei documenti per il tuo archivio interno.")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="alloggiati_{start_date}_{end_date}.zip"'})

@api.get("/uploads/{filename}")
async def get_upload(filename: str, user: dict = Depends(get_current_user)):
    fpath = UPLOAD_DIR / filename
    if not fpath.exists() or ".." in filename or "/" in filename:
        raise HTTPException(404, "File non trovato")
    return FileResponse(str(fpath))

# --------------------- Public Guest Registration ---------------------
@api.get("/public/property-info")
async def public_property_info():
    admin = await db.users.find_one({"email": "giuseppesica01@gmail.com"})
    if not admin:
        raise HTTPException(404, "Struttura non configurata")
    return {"name": "Casa B&B", "active": True}

@api.post("/public/registration")
async def public_registration(
    guest_first_name: str = Form(...), guest_last_name: str = Form(...),
    checkin: str = Form(...), checkout: str = Form(...), channel: str = Form("Direct"),
    document_
