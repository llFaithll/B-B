_AG='/ross1000/settings'
_AF='Struttura non configurata'
_AE='photo_paths'
_AD='document_place'
_AC='document_type'
_AB='citizenship'
_AA='country_of_birth'
_A9='/expenses/{eid}'
_A8='Spesa non trovata'
_A7='/expenses'
_A6='/inventory/{iid}'
_A5='Prodotto non trovato'
_A4='/inventory'
_A3='/bookings/{bid}'
_A2='Prenotazione non trovata'
_A1='/bookings'
_A0='User not found'
_z='access'
_y='ADMIN_EMAIL'
_x='due_date'
_w='http://localhost:3000'
_v='.jpg'
_u='Content-Disposition'
_t='$gte'
_s='document_number'
_r='place_of_birth'
_q='date_of_birth'
_p='updated_at'
_o='checkout'
_n='user'
_m='Other'
_l='Booking'
_k='Invalid token'
_j='refresh_token'
_i='none'
_h='letti_disponibili'
_g='ross1000'
_f='kind'
_e='$lte'
_d='channel'
_c='$set'
_b='external_id'
_a='IDENT'
_Z='Airbnb'
_Y='access_token'
_X='type'
_W='sub'
_V='password_hash'
_U='camere_disponibili'
_T='guest_last_name'
_S='guest_first_name'
_R='gross_price'
_Q='net_revenue'
_P='created_at'
_O='role'
_N='codice_struttura'
_M='Direct'
_L='name'
_K='/'
_J='ITALIA'
_I='ok'
_H='nights'
_G='email'
_F=None
_E='checkin'
_D='_id'
_C=True
_B='owner_id'
_A='id'
from dotenv import load_dotenv
from pathlib import Path
from contextlib import asynccontextmanager
ROOT_DIR=Path(__file__).parent
load_dotenv(ROOT_DIR/'.env')
import os,logging,uuid,bcrypt,jwt,secrets,requests,json
from datetime import datetime,timezone,timedelta,date
from typing import List,Optional,Literal,Annotated
from io import BytesIO
from fastapi import FastAPI,APIRouter,HTTPException,Request,Response,Depends,UploadFile,File,Form
from fastapi.responses import PlainTextResponse,StreamingResponse,FileResponse
import zipfile,mimetypes
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from pydantic import BaseModel,Field,EmailStr,BeforeValidator,ConfigDict
from icalendar import Calendar
from ross1000 import build_movimenti_xml,compute_month_stats
JWT_ALGORITHM='HS256'
JWT_SECRET=os.environ['JWT_SECRET']
FRONTEND_URL=os.environ.get('FRONTEND_URL',_w).rstrip(_K)
EMERGENT_LLM_KEY=os.environ.get('EMERGENT_LLM_KEY','PROVA')
UPLOAD_DIR=ROOT_DIR/'uploads'
UPLOAD_DIR.mkdir(exist_ok=_C)
mongo_url=os.environ['MONGO_URL']
client=AsyncIOMotorClient(mongo_url)
db=client.get_default_database(default='gestionale')
logging.basicConfig(level=logging.INFO,format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger=logging.getLogger(__name__)
@asynccontextmanager
async def lifespan(app):
	A='admin';logger.info("Avvio dell'applicazione: configurazione indici MongoDB...");await db.users.create_index(_G,unique=_C);await db.bookings.create_index([(_B,1),(_E,-1)]);await db.inventory.create_index([(_B,1)]);await db.expenses.create_index([(_B,1),(_x,1)]);admin_email=os.environ.get(_y,'admin@bnb.it').lower();admin_pw=os.environ.get('ADMIN_PASSWORD','admin123');any_admin=await db.users.find_one({_O:A})
	if not any_admin:await db.users.insert_one({_G:admin_email,_V:hash_password(admin_pw),_L:'Admin B&B',_O:A,_P:datetime.now(timezone.utc).isoformat()});logger.info(f"Seeded primo admin del database: {admin_email}")
	else:logger.info('Un amministratore è già presente nel database. Salto il seeding iniziale.')
	yield;logger.info("Chiusura dell'applicazione: disconnessione da MongoDB...");client.close()
app=FastAPI(title='B&B Manager',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=['https://gestionale-bandb.netlify.app',_w],allow_credentials=_C,allow_methods=['*'],allow_headers=['*'])
api=APIRouter(prefix='/api')
def obj_to_str(v):
	if isinstance(v,ObjectId):return str(v)
	return v
PyObjectId=Annotated[str,BeforeValidator(obj_to_str)]
def hash_password(pw):return bcrypt.hashpw(pw.encode(),bcrypt.gensalt()).decode()
def verify_password(pw,hashed):return bcrypt.checkpw(pw.encode(),hashed.encode())
def create_access_token(user_id,email):payload={_W:user_id,_G:email,_X:_z,'exp':datetime.now(timezone.utc)+timedelta(minutes=1440)};return jwt.encode(payload,JWT_SECRET,algorithm=JWT_ALGORITHM)
def create_refresh_token(user_id):payload={_W:user_id,_X:'refresh','exp':datetime.now(timezone.utc)+timedelta(days=7)};return jwt.encode(payload,JWT_SECRET,algorithm=JWT_ALGORITHM)
def set_auth_cookies(response,access,refresh):response.set_cookie(_Y,access,httponly=_C,secure=_C,samesite=_i,max_age=86400,path=_K);response.set_cookie(_j,refresh,httponly=_C,secure=_C,samesite=_i,max_age=604800,path=_K)
async def get_current_user(request):
	token=request.cookies.get(_Y)
	if not token:
		auth_header=request.headers.get('Authorization','')
		if auth_header.startswith('Bearer '):token=auth_header[7:]
	if not token:raise HTTPException(status_code=401,detail='Not authenticated')
	try:
		payload=jwt.decode(token,JWT_SECRET,algorithms=[JWT_ALGORITHM])
		if payload.get(_X)!=_z:raise HTTPException(status_code=401,detail='Invalid token type')
		user=await db.users.find_one({_D:ObjectId(payload[_W])})
		if not user:raise HTTPException(status_code=401,detail=_A0)
		user[_A]=str(user[_D]);user.pop(_D,_F);user.pop(_V,_F);return user
	except jwt.ExpiredSignatureError:raise HTTPException(status_code=401,detail='Token expired')
	except jwt.InvalidTokenError:raise HTTPException(status_code=401,detail=_k)
class RegisterIn(BaseModel):email:EmailStr;password:str;name:str
class LoginIn(BaseModel):email:EmailStr;password:str
class BookingIn(BaseModel):guest_first_name:str;guest_last_name:str;checkin:str;checkout:str;gross_price:float;channel:Literal[_Z,_l,_M,_m]=_M;notes:Optional[str]='';date_of_birth:Optional[str]=_F;place_of_birth:Optional[str]=_F;country_of_birth:Optional[str]=_J;citizenship:Optional[str]=_J;sex:Optional[Literal['M','F']]='M';document_type:Optional[str]=_a;document_number:Optional[str]=_F;document_place:Optional[str]=_F;guest_type:Optional[str]='16'
class InventoryIn(BaseModel):name:str;category:Optional[str]='Generale';quantity:float;unit:Optional[str]='pz';min_threshold:float=0;price_per_unit:Optional[float]=0
class ExpenseIn(BaseModel):name:str;category:str;amount:float;due_date:str;recurrence:Literal['once','monthly','quarterly','yearly']='once';paid:bool=False;notes:Optional[str]=''
class ICalImportIn(BaseModel):url:str;channel:Literal[_Z,_l,_M,_m]=_Z;default_price:float=8e1
class PricingSuggestIn(BaseModel):checkin:str;checkout:str;location:str='Italia';base_price:float=8e1;events:Optional[str]='';occupancy_context:Optional[str]=''
CHANNEL_COMMISSION={_Z:.03,_l:.15,_M:.0,_m:.1}
CEDOLARE_SECCA_RATE=.21
def compute_net(gross,channel):commission=CHANNEL_COMMISSION.get(channel,.1);after_commission=gross*(1-commission);after_tax=after_commission*(1-CEDOLARE_SECCA_RATE);return round(after_tax,2)
def nights_between(checkin,checkout):ci=datetime.fromisoformat(checkin).date();co=datetime.fromisoformat(checkout).date();return max((co-ci).days,0)
def serialize_booking(doc):doc[_A]=str(doc.pop(_D));return doc
@api.post('/auth/register')
async def register(payload,response):
	email=payload.email.lower()
	if await db.users.find_one({_G:email}):raise HTTPException(status_code=400,detail='Email già registrata')
	doc={_G:email,_V:hash_password(payload.password),_L:payload.name,_O:_n,_P:datetime.now(timezone.utc).isoformat()};result=await db.users.insert_one(doc);uid=str(result.inserted_id);set_auth_cookies(response,create_access_token(uid,email),create_refresh_token(uid));return{_A:uid,_G:email,_L:payload.name,_O:_n}
@api.post('/auth/login')
async def login(payload,response):
	email=payload.email.lower();user=await db.users.find_one({_G:email})
	if not user or not verify_password(payload.password,user[_V]):raise HTTPException(status_code=401,detail='Credenziali non valide')
	uid=str(user[_D]);set_auth_cookies(response,create_access_token(uid,email),create_refresh_token(uid));return{_A:uid,_G:email,_L:user.get(_L),_O:user.get(_O,_n)}
@api.post('/auth/logout')
async def logout(response):response.delete_cookie(_Y,path=_K);response.delete_cookie(_j,path=_K);return{_I:_C}
@api.get('/auth/me')
async def me(user=Depends(get_current_user)):return user
@api.post('/auth/refresh')
async def refresh(request,response):
	token=request.cookies.get(_j)
	if not token:raise HTTPException(status_code=401,detail='No refresh token')
	try:
		payload=jwt.decode(token,JWT_SECRET,algorithms=[JWT_ALGORITHM])
		if payload.get(_X)!='refresh':raise HTTPException(status_code=401,detail=_k)
		user=await db.users.find_one({_D:ObjectId(payload[_W])})
		if not user:raise HTTPException(status_code=401,detail=_A0)
		access=create_access_token(str(user[_D]),user[_G]);response.set_cookie(_Y,access,httponly=_C,secure=_C,samesite=_i,max_age=86400,path=_K);return{_I:_C}
	except jwt.InvalidTokenError:raise HTTPException(status_code=401,detail=_k)
@api.post(_A1)
async def create_booking(payload,user=Depends(get_current_user)):nights=nights_between(payload.checkin,payload.checkout);net=compute_net(payload.gross_price,payload.channel);doc=payload.model_dump();doc.update({_H:nights,_Q:net,_B:user[_A],_b:_F,_P:datetime.now(timezone.utc).isoformat()});result=await db.bookings.insert_one(doc);doc[_A]=str(result.inserted_id);doc.pop(_D,_F);return doc
@api.get(_A1)
async def list_bookings(user=Depends(get_current_user)):docs=await db.bookings.find({_B:user[_A]}).sort(_E,-1).to_list(2000);return[serialize_booking(d)for d in docs]
@api.put(_A3)
async def update_booking(bid,payload,user=Depends(get_current_user)):
	nights=nights_between(payload.checkin,payload.checkout);net=compute_net(payload.gross_price,payload.channel);doc=payload.model_dump();doc.update({_H:nights,_Q:net});r=await db.bookings.update_one({_D:ObjectId(bid),_B:user[_A]},{_c:doc})
	if r.matched_count==0:raise HTTPException(404,_A2)
	updated=await db.bookings.find_one({_D:ObjectId(bid)});return serialize_booking(updated)
@api.delete(_A3)
async def delete_booking(bid,user=Depends(get_current_user)):
	r=await db.bookings.delete_one({_D:ObjectId(bid),_B:user[_A]})
	if r.deleted_count==0:raise HTTPException(404,_A2)
	return{_I:_C}
@api.post('/bookings/ical-import')
async def ical_import(payload,user=Depends(get_current_user)):
	A='isoformat'
	try:resp=requests.get(payload.url,timeout=15);resp.raise_for_status();cal=Calendar.from_ical(resp.content)
	except Exception as e:raise HTTPException(400,f"Impossibile leggere iCal: {e}")
	imported=0;skipped=0
	for comp in cal.walk():
		if comp.name!='VEVENT':continue
		uid=str(comp.get('UID',''))
		if not uid:continue
		if await db.bookings.find_one({_b:uid,_B:user[_A]}):skipped+=1;continue
		dtstart=comp.get('DTSTART').dt;dtend=comp.get('DTEND').dt;ci=dtstart.isoformat()if hasattr(dtstart,A)else str(dtstart);co=dtend.isoformat()if hasattr(dtend,A)else str(dtend);summary=str(comp.get('SUMMARY','Ospite iCal'));nights=nights_between(ci,co);gross=payload.default_price*max(nights,1);doc={_S:summary[:40],_T:'(iCal)',_E:ci,_o:co,_R:gross,_d:payload.channel,'notes':str(comp.get('DESCRIPTION','')),_H:nights,_Q:compute_net(gross,payload.channel),_B:user[_A],_b:uid,_P:datetime.now(timezone.utc).isoformat()};await db.bookings.insert_one(doc);imported+=1
	return{'imported':imported,'skipped':skipped}
@api.get('/dashboard/stats')
async def dashboard_stats(user=Depends(get_current_user)):
	A='revenue';bookings=await db.bookings.find({_B:user[_A]}).to_list(5000);now=datetime.now(timezone.utc).date();year_start=date(now.year,1,1);total_gross=sum(b.get(_R,0)for b in bookings);total_net=sum(b.get(_Q,0)for b in bookings);year_bookings=[b for b in bookings if b.get(_E,'')>=year_start.isoformat()];year_gross=sum(b.get(_R,0)for b in year_bookings);year_net=sum(b.get(_Q,0)for b in year_bookings);year_nights=sum(b.get(_H,0)for b in year_bookings);days_in_year=366 if now.year%4==0 else 365;occupancy=round(year_nights/days_in_year*100,1)if days_in_year else 0;channels={}
	for b in year_bookings:ch=b.get(_d,_M);channels[ch]=channels.get(ch,0)+b.get(_R,0)
	monthly={}
	for b in year_bookings:m=b.get(_E,'')[:7];monthly[m]=monthly.get(m,0)+b.get(_R,0)
	monthly_list=[{'month':k,A:round(v,2)}for(k,v)in sorted(monthly.items())];channel_list=[{_d:k,A:round(v,2)}for(k,v)in channels.items()];return{'total_gross':round(total_gross,2),'total_net':round(total_net,2),'year_gross':round(year_gross,2),'year_net':round(year_net,2),'occupancy_pct':occupancy,'total_bookings':len(bookings),'year_bookings':len(year_bookings),'channels':channel_list,'monthly':monthly_list}
@api.post(_A4)
async def create_inventory(payload,user=Depends(get_current_user)):doc=payload.model_dump();doc[_B]=user[_A];doc[_p]=datetime.now(timezone.utc).isoformat();r=await db.inventory.insert_one(doc);doc[_A]=str(r.inserted_id);doc.pop(_D,_F);return doc
@api.get(_A4)
async def list_inventory(user=Depends(get_current_user)):docs=await db.inventory.find({_B:user[_A]}).sort(_L,1).to_list(1000);return[serialize_booking(d)for d in docs]
@api.put(_A6)
async def update_inventory(iid,payload,user=Depends(get_current_user)):
	doc=payload.model_dump();doc[_p]=datetime.now(timezone.utc).isoformat();r=await db.inventory.update_one({_D:ObjectId(iid),_B:user[_A]},{_c:doc})
	if r.matched_count==0:raise HTTPException(404,_A5)
	return{_I:_C}
@api.delete(_A6)
async def delete_inventory(iid,user=Depends(get_current_user)):
	r=await db.inventory.delete_one({_D:ObjectId(iid),_B:user[_A]})
	if r.deleted_count==0:raise HTTPException(404,_A5)
	return{_I:_C}
@api.post(_A7)
async def create_expense(payload,user=Depends(get_current_user)):doc=payload.model_dump();doc[_B]=user[_A];doc[_P]=datetime.now(timezone.utc).isoformat();r=await db.expenses.insert_one(doc);doc[_A]=str(r.inserted_id);doc.pop(_D,_F);return doc
@api.get(_A7)
async def list_expenses(user=Depends(get_current_user)):docs=await db.expenses.find({_B:user[_A]}).sort(_x,1).to_list(1000);return[serialize_booking(d)for d in docs]
@api.put(_A9)
async def update_expense(eid,payload,user=Depends(get_current_user)):
	doc=payload.model_dump();r=await db.expenses.update_one({_D:ObjectId(eid),_B:user[_A]},{_c:doc})
	if r.matched_count==0:raise HTTPException(404,_A8)
	return{_I:_C}
@api.delete(_A9)
async def delete_expense(eid,user=Depends(get_current_user)):
	r=await db.expenses.delete_one({_D:ObjectId(eid),_B:user[_A]})
	if r.deleted_count==0:raise HTTPException(404,_A8)
	return{_I:_C}
@api.post('/pricing/suggest')
async def suggest_price(payload,user=Depends(get_current_user)):
	H='text';G='parts';F='application/json';E='total_suggested';D='reasoning';C='max_price';B='min_price';A='suggested_price';checkin=payload.checkin;checkout=payload.checkout;nights=nights_between(checkin,checkout);gemini_key=os.environ.get('GEMINI_API_KEY')
	if not gemini_key:logger.warning("GEMINI_API_KEY non trovata nelle variabili d'ambiente. Uso fallback statico.");suggested=payload.base_price;return{A:suggested,B:round(suggested*.8,2),C:round(suggested*1.4,2),_H:nights,E:round(suggested*nights,2),D:"Configura la variabile d'ambiente GEMINI_API_KEY su Render per sbloccare i consigli reali dell'IA."}
	prompt=f'''
    Sei un assistente virtuale esperto di Revenue Management for strutture ricettive, B&B e case vacanze situate in: {payload.location}.
    Analizza i seguenti parametri inseriti dall\'host e calcola una tariffa ottimale:
    - Data Check-in: {checkin}
    - Data Check-out: {checkout}
    - Notti totali: {nights}
    - Prezzo base dell\'host: {payload.base_price}€ a notte
    - Contesto occupazione / Richieste host: {payload.occupancy_context or"Nessuna specifica"}
    - Eventi segnalati o festività speciali: {payload.events or"Nessuno specificato"}

    Istruzioni di calcolo:
    1. Valuta la stagionalità naturale delle date indicate per la località \'{payload.location}\'.
    2. Applica un leggero incremento strategico se le date includono il weekend (venerdì e sabato).
    3. Incrementa il prezzo in presenza di alta stagione, festività nazionali (Natale, Pasqua, Ferragosto, ponti) o eventi in zona.
    4. Riduci leggermente o mantieni stabile la tariffa se il contesto occupazione indica bassa richiesta o stanze rimaste vuote sotto data.

    Rispondi escludendo tassativamente qualsiasi preambolo o formattazione markdown esterna. Devi restituire ESCLUSIVAMENTE un oggetto JSON valido con queste identiche chiavi:
    {{
      "suggested_price": un numero (la tariffa media a notte consigliata),
      "min_price": un numero (la tariffa minima limite per non andare in perdita),
      "max_price": un numero (la tariffa massima per ottimizzare il guadagno),
      "reasoning": "una spiegazione commerciale in italiano di massimo 3 frasi che descriva la strategia applicata (es. aumento dovuto al weekend e ad un evento concomitante)."
    }}
    '''
	try:url=f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={gemini_key}";headers={'Content-Type':F};data={'contents':[{G:[{H:prompt}]}],'generationConfig':{'responseMimeType':F}};response=requests.post(url,json=data,headers=headers,timeout=15);response.raise_for_status();result_json=response.json();ai_text=result_json['candidates'][0]['content'][G][0][H];parsed_data=json.loads(ai_text);suggested=float(parsed_data.get(A,payload.base_price));return{A:round(suggested,2),B:round(float(parsed_data.get(B,suggested*.8)),2),C:round(float(parsed_data.get(C,suggested*1.4)),2),D:parsed_data.get(D,"Prezzo calcolato in tempo reale dall'IA."),_H:nights,E:round(suggested*nights,2)}
	except Exception as e:logger.exception('Errore nella richiesta alle API di Gemini');suggested=payload.base_price;return{A:suggested,B:round(suggested*.8,2),C:round(suggested*1.4,2),_H:nights,E:round(suggested*nights,2),D:f"Servizio IA momentaneamente saturo. Applicata tariffa base di sicurezza. (Dettaglio: {str(e)[:45]})"}
def format_alloggiati_record(b):
	'Genera un record fixed-width per il portale Alloggiati Web.'
	try:
		ci=datetime.fromisoformat(b[_E]).date();nights=str(b.get(_H,1)).zfill(2);tipo=(b.get('guest_type')or'16').ljust(2);cognome=(b.get(_T)or'').upper().ljust(50)[:50];nome=(b.get(_S)or'').upper().ljust(30)[:30];sesso=(b.get('sex')or'M').ljust(1)[:1];dob=b.get(_q,'1980-01-01')
		try:dob_d=datetime.fromisoformat(dob).date();dob_str=f"{dob_d.day:02d}/{dob_d.month:02d}/{dob_d.year}"
		except Exception:dob_str='01/01/1980'
		comune_nascita=(b.get(_r)or'').upper().ljust(9)[:9];provincia='  ';stato_nascita=(b.get(_AA)or _J).upper().ljust(9)[:9];cittadinanza=(b.get(_AB)or _J).upper().ljust(9)[:9];doc_tipo=(b.get(_AC)or _a).ljust(5)[:5];doc_num=(b.get(_s)or'').upper().ljust(20)[:20];doc_luogo=(b.get(_AD)or'').upper().ljust(9)[:9];arrivo=f"{ci.day:02d}/{ci.month:02d}/{ci.year}";line=f"{tipo}{arrivo}{nights}{cognome}{nome}{sesso}{dob_str}{comune_nascita}{provincia}{stato_nascita}{cittadinanza}{doc_tipo}{doc_num}{doc_luogo}";return line
	except Exception as e:logger.warning(f"Skip record: {e}");return
@api.get('/alloggiati/export',response_class=PlainTextResponse)
async def alloggiati_export(start_date,end_date,user=Depends(get_current_user)):
	bookings=await db.bookings.find({_B:user[_A],_E:{_t:start_date,_e:end_date}}).to_list(1000);lines=[]
	for b in bookings:
		line=format_alloggiati_record(b)
		if line:lines.append(line)
	content='\r\n'.join(lines);return PlainTextResponse(content,headers={_u:f'attachment; filename="alloggiati_{start_date}_{end_date}.txt"'})
@api.get('/alloggiati/export-zip')
async def alloggiati_export_zip(start_date,end_date,user=Depends(get_current_user)):
	bookings=await db.bookings.find({_B:user[_A],_E:{_t:start_date,_e:end_date}}).to_list(1000);lines=[];photo_files=[]
	for b in bookings:
		line=format_alloggiati_record(b)
		if line:lines.append(line)
		safe_name=f"{(b.get(_T)or"X").upper()}_{(b.get(_S)or"X").upper()}".replace(' ','_')
		for(i,p)in enumerate(b.get(_AE,[])or[]):
			fpath=UPLOAD_DIR/p
			if fpath.exists():ext=fpath.suffix or _v;arc=f"foto_documenti/{safe_name}_{i+1}{ext}";photo_files.append((arc,str(fpath)))
	buf=BytesIO()
	with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED)as zf:
		zf.writestr(f"alloggiati_{start_date}_{end_date}.txt",'\r\n'.join(lines))
		for(arc,fpath)in photo_files:zf.write(fpath,arc)
		zf.writestr('LEGGIMI.txt','Carica il file .txt sul portale Alloggiati Web della Polizia di Stato.\r\nLa cartella foto_documenti contiene le foto dei documenti per il tuo archivio interno (non richieste dal portale ma da conservare per obblighi di legge).')
	buf.seek(0);return StreamingResponse(buf,media_type='application/zip',headers={_u:f'attachment; filename="alloggiati_{start_date}_{end_date}.zip"'})
@api.get('/uploads/{filename}')
async def get_upload(filename,user=Depends(get_current_user)):
	fpath=UPLOAD_DIR/filename
	if not fpath.exists()or'..'in filename or _K in filename:raise HTTPException(404,'File non trovato')
	return FileResponse(str(fpath))
ADMIN_EMAIL_ENV=os.environ.get(_y,'admin@example.com')
@api.get('/public/property-info')
async def public_property_info():
	'Info pubblica per il form guest – verifica che ci sia almeno un admin.';admin=await db.users.find_one({_G:ADMIN_EMAIL_ENV})
	if not admin:raise HTTPException(404,_AF)
	return{_L:'Casa B&B','active':_C}
@api.post('/public/registration')
async def public_registration(guest_first_name=Form(...),guest_last_name=Form(...),checkin=Form(...),checkout=Form(...),channel=Form(_M),document_number=Form(...),date_of_birth=Form(...),place_of_birth=Form(...),country_of_birth=Form(_J),citizenship=Form(_J),sex=Form('M'),document_type=Form(_a),document_place=Form(''),photos=File(default=[])):
	proprietario=await db.users.find_one({_G:ADMIN_EMAIL_ENV})
	if not proprietario:raise HTTPException(400,_AF)
	owner_id=str(proprietario[_D]);photo_paths=[]
	for f in photos or[]:
		if not f.filename:continue
		ext=Path(f.filename).suffix.lower()or _v
		if ext not in[_v,'.jpeg','.png','.webp','.pdf','.heic']:continue
		safe=f"doc_{uuid.uuid4().hex}{ext}";content=await f.read()
		if len(content)>15728640:raise HTTPException(400,f"File {f.filename} troppo grande (max 15MB)")
		(UPLOAD_DIR/safe).write_bytes(content);photo_paths.append(safe)
	nights=nights_between(checkin,checkout);doc={_S:guest_first_name.strip(),_T:guest_last_name.strip(),_E:checkin,_o:checkout,_R:.0,_d:channel if channel in CHANNEL_COMMISSION else _M,'notes':'Registrazione ospite via form pubblico',_q:date_of_birth,_r:place_of_birth.strip(),_AA:country_of_birth.strip()or _J,_AB:citizenship.strip()or _J,'sex':sex if sex in('M','F')else'M',_AC:document_type.strip()or _a,_s:document_number.strip(),_AD:document_place.strip(),_H:nights,_Q:.0,_B:owner_id,_b:_F,_AE:photo_paths,'source':'public_form',_P:datetime.now(timezone.utc).isoformat()};result=await db.bookings.insert_one(doc);return{_I:_C,_A:str(result.inserted_id),'photos_uploaded':len(photo_paths)}
@api.get('/alloggiati/preview')
async def alloggiati_preview(start_date,end_date,user=Depends(get_current_user)):
	bookings=await db.bookings.find({_B:user[_A],_E:{_t:start_date,_e:end_date}}).to_list(1000);records=[]
	for b in bookings:line=format_alloggiati_record(b);records.append({'guest':f"{b.get(_S,"")} {b.get(_T,"")}",_E:b.get(_E),_H:b.get(_H),'valid':line is not _F,'line_preview':line[:80]+'...'if line and len(line)>80 else line,'missing':[k for k in[_q,_r,_s]if not b.get(k)]})
	return{'total':len(bookings),'records':records}
class Ross1000Settings(BaseModel):codice_struttura:str;camere_disponibili:int;letti_disponibili:int
@api.get(_AG)
async def get_ross_settings(user=Depends(get_current_user)):
	doc=await db.settings.find_one({_B:user[_A],_f:_g})
	if not doc:return{_N:'',_U:1,_h:2}
	return{_N:doc.get(_N,''),_U:doc.get(_U,1),_h:doc.get(_h,2)}
@api.post(_AG)
async def save_ross_settings(payload,user=Depends(get_current_user)):await db.settings.update_one({_B:user[_A],_f:_g},{_c:{**payload.model_dump(),_B:user[_A],_f:_g,_p:datetime.now(timezone.utc).isoformat()}},upsert=_C);return{_I:_C}
async def _load_ross_settings(user_id):
	doc=await db.settings.find_one({_B:user_id,_f:_g})
	if not doc or not doc.get(_N):raise HTTPException(400,'Configura prima il codice struttura e le camere/letti disponibili in Impostazioni ROSS 1000.')
	return doc
async def _month_bookings(user_id,year,month):_,last_day=__import__('calendar').monthrange(year,month);month_start=date(year,month,1).isoformat();month_end=date(year,month,last_day).isoformat();return await db.bookings.find({_B:user_id,_E:{_e:month_end},_o:{'$gt':month_start}}).to_list(2000)
@api.get('/ross1000/preview')
async def ross_preview(year,month,user=Depends(get_current_user)):settings=await _load_ross_settings(user[_A]);bookings=await _month_bookings(user[_A],year,month);stats=compute_month_stats(year,month,bookings,settings[_U]);stats[_N]=settings[_N];return stats
@api.get('/ross1000/export-xml',response_class=PlainTextResponse)
async def ross_export_xml(year,month,user=Depends(get_current_user)):settings=await _load_ross_settings(user[_A]);bookings=await _month_bookings(user[_A],year,month);xml=build_movimenti_xml(codice_struttura=settings[_N],year=year,month=month,bookings=bookings,camere_disponibili=settings[_U],letti_disponibili=settings[_h]);fname=f"ross1000_{year}_{month:02d}.xml";return PlainTextResponse(xml,media_type='application/xml',headers={_u:f'attachment; filename="{fname}"'})
app.include_router(api)
globals()['app']=app
