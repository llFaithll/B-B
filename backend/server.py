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
# Se JWT_SECRET non è configurato su Render, evitiamo il crash generandone uno sicuro al volo
JWT_SECRET = os.environ.get("JWT_SECRET") or secrets.token_hex(32)
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")
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

# Configurazione CORS dinamica e robusta per la vendita su Etsy
origins = [
    "http://localhost:3000",
    "https://gestionale-bandb.netlify.app",
]
if os.environ.get("FRONTEND_URL"):
    origins.append(os.environ.get("FRONTEND_URL").rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    # Questa regex risolve istantaneamente per te e per tutti i clienti Etsy consentendo
    # la comunicazione da qualunque dominio o sotto-dominio ospitato su Netlify
    allow_origin_regex="https://.*\.netlify\.app",
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

# --------------------- GENERAZIONE RICEVUTA PDF (VERSIONE BLINDATA ED INDISTRUTTIBILE) ---------------------
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

@api.get("/bookings/{bid}/receipt-pdf")
async def generate_receipt_pdf(bid: str, guests_count: int = 1, kids_count: int = 0, user: dict = Depends(get_current_user)):
    try:
        booking = await db.bookings.find_one({"_id": ObjectId(bid), "owner_id": user["id"]})
    except Exception as e:
        logger.error(f"Errore MongoDB nel recupero booking: {e}")
        raise HTTPException(status_code=500, detail="Errore nel recupero della prenotazione dal database")

    if not booking:
        raise HTTPException(status_code=404, detail="Prenotazione non trovata")
        
    # 1. Recupero ultra-sicuro delle impostazioni con conversioni esplicite dei tipi di dato
    try:
        tax_settings = await db.settings.find_one({"owner_id": user["id"], "kind": "tourist_tax"})
        if tax_settings:
            fee = float(tax_settings.get("fee_per_night", 3.50))
            max_n = int(tax_settings.get("max_nights", 10))
        else:
            fee = 3.50
            max_n = 10
    except Exception as e:
        logger.warning(f"Errore recupero settings tassa, uso default: {e}")
        fee = 3.50
        max_n = 10
    
    # 2. Conversione forzata di "nights" per evitare crash se salvato come stringa o nullo in MongoDB
    try:
        raw_nights = booking.get("nights")
        booking_nights = int(raw_nights) if raw_nights is not None else 1
    except (ValueError, TypeError):
        booking_nights = 1
        
    if booking_nights <= 0:
        booking_nights = 1
        
    # Calcolo Notti Tassabili
    nights = min(booking_nights, max_n)
    
    # 3. Conversione forzata degli ospiti passati dal frontend
    try:
        g_count = max(int(guests_count), 1)
        k_count = max(int(kids_count), 0)
    except (ValueError, TypeError):
        g_count = 1
        k_count = 0
        
    adults_count = max(g_count - k_count, 1)
    total_tax = round(fee * nights * adults_count, 2)
    
    # 4. Conversione forzata del prezzo lordo
    try:
        gross_stay = float(booking.get("gross_price", 0.0))
    except (ValueError, TypeError):
        gross_stay = 0.0
        
    grand_total = round(gross_stay + total_tax, 2)
    stamp_duty = 2.0 if gross_stay > 77.47 else 0.0

    # --- Generazione del PDF in memoria tramite ReportLab ---
    try:
        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
        styles = getSampleStyleSheet()
        
        # Stili personalizzati per rendere la ricevuta elegante ed ordinata
        title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontSize=22, textColor=colors.HexColor("#1b4332"), spaceAfter=15)
        normal_style = ParagraphStyle('NormalStyle', parent=styles['Normal'], fontSize=10, leading=14)
        bold_style = ParagraphStyle('BoldStyle', parent=styles['Normal'], fontSize=10, fontName="Helvetica-Bold")
        
        story = []
        
        # Intestazione Struttura Ricettiva
