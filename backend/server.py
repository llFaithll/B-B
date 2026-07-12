_AE='/ross1000/settings'
_AD='document_place'
_AC='document_number'
_AB='document_type'
_AA='citizenship'
_A9='country_of_birth'
_A8='place_of_birth'
_A7='date_of_birth'
_A6='/expenses/{eid}'
_A5='Spesa non trovata'
_A4='/expenses'
_A3='/inventory/{iid}'
_A2='Prodotto non trovato'
_A1='/inventory'
_A0='updated_at'
_z='/bookings/{bid}'
_y='Prenotazione non trovata'
_x='/bookings'
_w='monthly'
_v='User not found'
_u='refresh'
_t='access'
_s='ADMIN_EMAIL'
_r='due_date'
_q='FRONTEND_URL'
_p='$lte'
_o='$gte'
_n='external_id'
_m='user'
_l='IDENT'
_k='Other'
_j='Booking'
_i='Invalid token'
_h='refresh_token'
_g='none'
_f='http://localhost:3000'
_e='letti_disponibili'
_d='codice_struttura'
_c='channel'
_b='guest_last_name'
_a='guest_first_name'
_Z='$set'
_Y='Airbnb'
_X='access_token'
_W='type'
_V='sub'
_U='password_hash'
_T='camere_disponibili'
_S='ross1000'
_R='kind'
_Q='gross_price'
_P='net_revenue'
_O='ITALIA'
_N='Direct'
_M='created_at'
_L='role'
_K='name'
_J='/'
_I='nights'
_H='ok'
_G='checkin'
_F='email'
_E=None
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
FRONTEND_URL=os.environ.get(_q,_f).rstrip(_J)
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
	A='admin';logger.info("Avvio dell'applicazione: configurazione indici MongoDB...");await db.users.create_index(_F,unique=_C);await db.bookings.create_index([(_B,1),(_G,-1)]);await db.inventory.create_index([(_B,1)]);await db.expenses.create_index([(_B,1),(_r,1)]);admin_email=os.environ.get(_s,'admin@bnb.it').lower();admin_pw=os.environ.get('ADMIN_PASSWORD','admin123');any_admin=await db.users.find_one({_L:A})
	if not any_admin:await db.users.insert_one({_F:admin_email,_U:hash_password(admin_pw),_K:'Admin B&B',_L:A,_M:datetime.now(timezone.utc).isoformat()});logger.info(f"Seeded primo admin del database: {admin_email}")
	else:logger.info('Un amministratore è già presente nel database. Salto il seeding iniziale.')
	yield;logger.info("Chiusura dell'applicazione: disconnessione da MongoDB...");client.close()
app=FastAPI(title='B&B Manager',lifespan=lifespan)
frontend_url_env=os.environ.get(_q,_f).rstrip(_J)
allowed_origins=[frontend_url_env,_f]
app.add_middleware(CORSMiddleware,allow_origins=allowed_origins,allow_credentials=_C,allow_methods=['*'],allow_headers=['*'])
api=APIRouter(prefix='/api')
def obj_to_str(v):
	if isinstance(v,ObjectId):return str(v)
	return v
PyObjectId=Annotated[str,BeforeValidator(obj_to_str)]
def hash_password(pw):return bcrypt.hashpw(pw.encode(),bcrypt.gensalt()).decode()
def verify_password(pw,hashed):return bcrypt.checkpw(pw.encode(),hashed.encode())
def create_access_token(user_id,email):payload={_V:user_id,_F:email,_W:_t,'exp':datetime.now(timezone.utc)+timedelta(minutes=1440)};return jwt.encode(payload,JWT_SECRET,algorithm=JWT_ALGORITHM)
def create_refresh_token(user_id):payload={_V:user_id,_W:_u,'exp':datetime.now(timezone.utc)+timedelta(days=7)};return jwt.encode(payload,JWT_SECRET,algorithm=JWT_ALGORITHM)
def set_auth_cookies(response,access,refresh):response.set_cookie(_X,access,httponly=_C,secure=_C,samesite=_g,max_age=86400,path=_J);response.set_cookie(_h,refresh,httponly=_C,secure=_C,samesite=_g,max_age=604800,path=_J)
async def get_current_user_raw(request):
	'Estrattore utente raw immune agli errori di minificazione dei parametri.';token=request.cookies.get(_X)
	if not token:
		auth_header=request.headers.get('Authorization','')
		if auth_header.startswith('Bearer '):token=auth_header[7:]
	if not token:raise HTTPException(status_code=401,detail='Not authenticated')
	try:
		payload=jwt.decode(token,JWT_SECRET,algorithms=[JWT_ALGORITHM])
		if payload.get(_W)!=_t:raise HTTPException(status_code=401,detail='Invalid token type')
		user=await db.users.find_one({_D:ObjectId(payload[_V])})
		if not user:raise HTTPException(status_code=401,detail=_v)
		user[_A]=str(user[_D]);user.pop(_D,_E);user.pop(_U,_E);return user
	except jwt.ExpiredSignatureError:raise HTTPException(status_code=401,detail='Token expired')
	except jwt.InvalidTokenError:raise HTTPException(status_code=401,detail=_i)
class RegisterIn(BaseModel):email:EmailStr;password:str;name:str
class BookingIn(BaseModel):guest_first_name:str;guest_last_name:str;checkin:str;checkout:str;gross_price:float;channel:Literal[_Y,_j,_N,_k]=_N;notes:Optional[str]='';date_of_birth:Optional[str]=_E;place_of_birth:Optional[str]=_E;country_of_birth:Optional[str]=_O;citizenship:Optional[str]=_O;sex:Optional[Literal['M','F']]='M';document_type:Optional[str]=_l;document_number:Optional[str]=_E;document_place:Optional[str]=_E;guest_type:Optional[str]='16'
class InventoryIn(BaseModel):name:str;category:Optional[str]='Generale';quantity:float;unit:Optional[str]='pz';min_threshold:float=0;price_per_unit:Optional[float]=0
class ExpenseIn(BaseModel):name:str;category:str;amount:float;due_date:str;recurrence:Literal['once',_w,'quarterly','yearly']='once';paid:bool=False;notes:Optional[str]=''
class ICalImportIn(BaseModel):url:str;channel:Literal[_Y,_j,_N,_k]=_Y;default_price:float=8e1
class PricingSuggestIn(BaseModel):checkin:str;checkout:str;location:str='Italia';base_price:float=8e1;events:Optional[str]='';occupancy_context:Optional[str]=''
CHANNEL_COMMISSION={_Y:.03,_j:.15,_N:.0,_k:.1}
CEDOLARE_SECCA_RATE=.21
def compute_net(gross,channel):commission=CHANNEL_COMMISSION.get(channel,.1);after_commission=gross*(1-commission);after_tax=after_commission*(1-CEDOLARE_SECCA_RATE);return round(after_tax,2)
def nights_between(checkin,checkout):ci=datetime.fromisoformat(checkin).date();co=datetime.fromisoformat(checkout).date();return max((co-ci).days,0)
def serialize_booking(doc):doc[_A]=str(doc.pop(_D));return doc
@api.post('/auth/register')
async def register(payload,response):
	email=payload.email.lower()
	if await db.users.find_one({_F:email}):raise HTTPException(status_code=400,detail='Email già registrata')
	doc={_F:email,_U:hash_password(payload.password),_K:payload.name,_L:_m,_M:datetime.now(timezone.utc).isoformat()};result=await db.users.insert_one(doc);uid=str(result.inserted_id);set_auth_cookies(response,create_access_token(uid,email),create_refresh_token(uid));return{_A:uid,_F:email,_K:payload.name,_L:_m}
@api.post('/auth/login')
async def login(request,response):
	'Rotta di login totalmente slegata da Pydantic/Form per prevenire errori 422 dopo minificazione.';A='password';login_email=_E;login_password=_E
	try:body=await request.json();login_email=body.get(_F);login_password=body.get(A)
	except Exception:pass
	if not login_email or not login_password:
		try:form_data=await request.form();login_email=form_data.get(_F);login_password=form_data.get(A)
		except Exception:pass
	if not login_email or not login_password:raise HTTPException(status_code=400,detail='Credenziali mancanti o malformate')
	login_email=str(login_email).lower().strip();user=await db.users.find_one({_F:login_email})
	if not user or not verify_password(str(login_password),user[_U]):raise HTTPException(status_code=401,detail='Credenziali non valide')
	uid=str(user[_D]);set_auth_cookies(response,create_access_token(uid,login_email),create_refresh_token(uid));return{_A:uid,_F:login_email,_K:user.get(_K),_L:user.get(_L,_m)}
@api.post('/auth/logout')
async def logout(response):response.delete_cookie(_X,path=_J);response.delete_cookie(_h,path=_J);return{_H:_C}
@api.get('/auth/me')
async def me(request):'Verifica sessione raw senza dipendenze esplicite per evitare il 422.';user_data=await get_current_user_raw(request);return user_data
@api.post('/auth/refresh')
async def refresh(request,response):
	token=request.cookies.get(_h)
	if not token:raise HTTPException(status_code=401,detail='No refresh token')
	try:
		payload=jwt.decode(token,JWT_SECRET,algorithms=[JWT_ALGORITHM])
		if payload.get(_W)!=_u:raise HTTPException(status_code=401,detail=_i)
		user=await db.users.find_one({_D:ObjectId(payload[_V])})
		if not user:raise HTTPException(status_code=401,detail=_v)
		access=create_access_token(str(user[_D]),user[_F]);response.set_cookie(_X,access,httponly=_C,secure=_C,samesite=_g,max_age=86400,path=_J);return{_H:_C}
	except jwt.InvalidTokenError:raise HTTPException(status_code=401,detail=_i)
@api.post(_x)
async def create_booking(payload,request):user=await get_current_user_raw(request);nights=nights_between(payload.checkin,payload.checkout);net=compute_net(payload.gross_price,payload.channel);doc=payload.model_dump();doc.update({_I:nights,_P:net,_B:user[_A],_n:_E,_M:datetime.now(timezone.utc).isoformat()});result=await db.bookings.insert_one(doc);doc[_A]=str(result.inserted_id);doc.pop(_D,_E);return doc
@api.get(_x)
async def list_bookings(request):user=await get_current_user_raw(request);docs=await db.bookings.find({_B:user[_A]}).sort(_G,-1).to_list(2000);return[serialize_booking(d)for d in docs]
@api.put(_z)
async def update_booking(bid,payload,request):
	user=await get_current_user_raw(request);nights=nights_between(payload.checkin,payload.checkout);net=compute_net(payload.gross_price,payload.channel);doc=payload.model_dump();doc.update({_I:nights,_P:net});r=await db.bookings.update_one({_D:ObjectId(bid),_B:user[_A]},{_Z:doc})
	if r.matched_count==0:raise HTTPException(404,_y)
	updated=await db.bookings.find_one({_D:ObjectId(bid)});return serialize_booking(updated)
@api.delete(_z)
async def delete_booking(bid,request):
	user=await get_current_user_raw(request);r=await db.bookings.delete_one({_D:ObjectId(bid),_B:user[_A]})
	if r.deleted_count==0:raise HTTPException(404,_y)
	return{_H:_C}
@api.post('/bookings/ical-import')
async def ical_import(payload,request):
	A='isoformat';user=await get_current_user_raw(request)
	try:resp=requests.get(payload.url,timeout=15);resp.raise_for_status();cal=Calendar.from_ical(resp.content)
	except Exception as e:raise HTTPException(400,f"Impossibile leggere iCal: {e}")
	imported=0;skipped=0
	for comp in cal.walk():
		if comp.name!='VEVENT':continue
		uid=str(comp.get('UID',''))
		if not uid:continue
		if await db.bookings.find_one({_n:uid,_B:user[_A]}):skipped+=1;continue
		dtstart=comp.get('DTSTART').dt;dtend=comp.get('DTEND').dt;ci=dtstart.isoformat()if hasattr(dtstart,A)else str(dtstart);co=dtend.isoformat()if hasattr(dtend,A)else str(dtend);summary=str(comp.get('SUMMARY','Ospite iCal'));nights=nights_between(ci,co);gross=payload.default_price*max(nights,1);doc={_a:summary[:40],_b:'(iCal)',_G:ci,'checkout':co,_Q:gross,_c:payload.channel,'notes':str(comp.get('DESCRIPTION','')),_I:nights,_P:compute_net(gross,payload.channel),_B:user[_A],_n:uid,_M:datetime.now(timezone.utc).isoformat()};await db.bookings.insert_one(doc);imported+=1
	return{'imported':imported,'skipped':skipped}
@api.get('/dashboard/stats')
async def dashboard_stats(request):
	A='revenue';user=await get_current_user_raw(request);bookings=await db.bookings.find({_B:user[_A]}).to_list(5000);now=datetime.now(timezone.utc).date();year_start=date(now.year,1,1);total_gross=sum(b.get(_Q,0)for b in bookings);total_net=sum(b.get(_P,0)for b in bookings);year_bookings=[b for b in bookings if b.get(_G,'')>=year_start.isoformat()];year_gross=sum(b.get(_Q,0)for b in year_bookings);year_net=sum(b.get(_P,0)for b in year_bookings);year_nights=sum(b.get(_I,0)for b in year_bookings);days_in_year=366 if now.year%4==0 else 365;occupancy=round(year_nights/days_in_year*100,1)if days_in_year else 0;channels={}
	for b in year_bookings:ch=b.get(_c,_N);channels[ch]=channels.get(ch,0)+b.get(_Q,0)
	monthly={}
	for b in year_bookings:m=b.get(_G,'')[:7];monthly[m]=monthly.get(m,0)+b.get(_Q,0)
	monthly_list=[{'month':k,A:round(v,2)}for(k,v)in sorted(monthly.items())];channel_list=[{_c:k,A:round(v,2)}for(k,v)in channels.items()];return{'total_gross':round(total_gross,2),'total_net':round(total_net,2),'year_gross':round(year_gross,2),'year_net':round(year_net,2),'occupancy_pct':occupancy,'total_bookings':len(bookings),'year_bookings':len(year_bookings),'channels':channel_list,_w:monthly_list}
@api.post(_A1)
async def create_inventory(payload,request):user=await get_current_user_raw(request);doc=payload.model_dump();doc[_B]=user[_A];doc[_A0]=datetime.now(timezone.utc).isoformat();r=await db.inventory.insert_one(doc);doc[_A]=str(r.inserted_id);doc.pop(_D,_E);return doc
@api.get(_A1)
async def list_inventory(request):user=await get_current_user_raw(request);docs=await db.inventory.find({_B:user[_A]}).sort(_K,1).to_list(1000);return[serialize_booking(d)for d in docs]
@api.put(_A3)
async def update_inventory(iid,payload,request):
	user=await get_current_user_raw(request);doc=payload.model_dump();doc[_A0]=datetime.now(timezone.utc).isoformat();r=await db.inventory.update_one({_D:ObjectId(iid),_B:user[_A]},{_Z:doc})
	if r.matched_count==0:raise HTTPException(404,_A2)
	return{_H:_C}
@api.delete(_A3)
async def delete_inventory(iid,request):
	user=await get_current_user_raw(request);r=await db.inventory.delete_one({_D:ObjectId(iid),_B:user[_A]})
	if r.deleted_count==0:raise HTTPException(404,_A2)
	return{_H:_C}
@api.post(_A4)
async def create_expense(payload,request):user=await get_current_user_raw(request);doc=payload.model_dump();doc[_B]=user[_A];doc[_M]=datetime.now(timezone.utc).isoformat();r=await db.expenses.insert_one(doc);doc[_A]=str(r.inserted_id);doc.pop(_D,_E);return doc
@api.get(_A4)
async def list_expenses(request):user=await get_current_user_raw(request);docs=await db.expenses.find({_B:user[_A]}).sort(_r,1).to_list(1000);return[serialize_booking(d)for d in docs]
@api.put(_A6)
async def update_expense(eid,payload,request):
	user=await get_current_user_raw(request);doc=payload.model_dump();r=await db.expenses.update_one({_D:ObjectId(eid),_B:user[_A]},{_Z:doc})
	if r.matched_count==0:raise HTTPException(404,_A5)
	return{_H:_C}
@api.delete(_A6)
async def delete_expense(eid,request):
	user=await get_current_user_raw(request);r=await db.expenses.delete_one({_D:ObjectId(eid),_B:user[_A]})
	if r.deleted_count==0:raise HTTPException(404,_A5)
	return{_H:_C}
@api.post('/pricing/suggest')
async def suggest_price(payload,request):
	G='text';F='parts';E='total_suggested';D='max_price';C='min_price';B='reasoning';A='suggested_price';await get_current_user_raw(request);checkin=payload.checkin;checkout=payload.checkout;nights=nights_between(checkin,checkout);gemini_key=os.environ.get('GEMINI_API_KEY')
	if not gemini_key:suggested=payload.base_price;return{A:suggested,C:round(suggested*.8,2),D:round(suggested*1.4,2),_I:nights,E:round(suggested*nights,2),B:"Configura la variabile d'ambiente GEMINI_API_KEY su Render."}
	prompt=f"Sei un assistente virtuale esperto di Revenue Management for strutture in: {payload.location}..."
	try:url=f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={gemini_key}";data={'contents':[{F:[{G:prompt}]}],'generationConfig':{'responseMimeType':'application/json'}};response=requests.post(url,json=data,timeout=15);parsed_data=response.json()['candidates'][0]['content'][F][0][G];parsed=json.loads(parsed_data);suggested=float(parsed.get(A,payload.base_price));return{A:round(suggested,2),C:round(float(parsed.get(C,suggested*.8)),2),D:round(float(parsed.get(D,suggested*1.4)),2),B:parsed.get(B,"Calcolato dall'IA."),_I:nights,E:round(suggested*nights,2)}
	except Exception as e:return{A:payload.base_price,_I:nights,B:str(e)}
def format_alloggiati_record(b):
	try:ci=datetime.fromisoformat(b[_G]).date();nights=str(b.get(_I,1)).zfill(2);tipo=(b.get('guest_type')or'16').ljust(2);cognome=(b.get(_b)or'').upper().ljust(50)[:50];nome=(b.get(_a)or'').upper().ljust(30)[:30];sesso=(b.get('sex')or'M').ljust(1)[:1];dob=b.get(_A7,'1980-01-01');dob_d=datetime.fromisoformat(dob).date();dob_str=f"{dob_d.day:02d}/{dob_d.month:02d}/{dob_d.year}";comune_nascita=(b.get(_A8)or'').upper().ljust(9)[:9];stato_nascita=(b.get(_A9)or _O).upper().ljust(9)[:9];cittadinanza=(b.get(_AA)or _O).upper().ljust(9)[:9];doc_tipo=(b.get(_AB)or _l).ljust(5)[:5];doc_num=(b.get(_AC)or'').upper().ljust(20)[:20];doc_luogo=(b.get(_AD)or'').upper().ljust(9)[:9];arrivo=f"{ci.day:02d}/{ci.month:02d}/{ci.year}";return f"{tipo}{arrivo}{nights}{cognome}{nome}{sesso}{dob_str}{comune_nascita}  {stato_nascita}{cittadinanza}{doc_tipo}{doc_num}{doc_luogo}"
	except Exception:return
@api.get('/alloggiati/export',response_class=PlainTextResponse)
async def alloggiati_export(start_date,end_date,request):user=await get_current_user_raw(request);bookings=await db.bookings.find({_B:user[_A],_G:{_o:start_date,_p:end_date}}).to_list(1000);lines=[l for b in bookings if(l:=format_alloggiati_record(b))];return PlainTextResponse('\r\n'.join(lines),headers={'Content-Disposition':f'attachment; filename="alloggiati.txt"'})
@api.get('/alloggiati/export-zip')
async def alloggiati_export_zip(start_date,end_date,request):
	user=await get_current_user_raw(request);bookings=await db.bookings.find({_B:user[_A],_G:{_o:start_date,_p:end_date}}).to_list(1000);lines=[l for b in bookings if(l:=format_alloggiati_record(b))];buf=BytesIO()
	with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED)as zf:zf.writestr('alloggiati.txt','\r\n'.join(lines))
	buf.seek(0);return StreamingResponse(buf,media_type='application/zip')
@api.get('/uploads/{filename}')
async def get_upload(filename,request):
	await get_current_user_raw(request);fpath=UPLOAD_DIR/filename
	if not fpath.exists()or'..'in filename:raise HTTPException(404,'File non trovato')
	return FileResponse(str(fpath))
ADMIN_EMAIL_ENV=os.environ.get(_s,'admin@example.com')
@api.get('/public/property-info')
async def public_property_info():return{_K:'Casa B&B','active':_C}
@api.post('/public/registration')
async def public_registration(guest_first_name=Form(...),guest_last_name=Form(...),checkin=Form(...),checkout=Form(...),channel=Form(_N),document_number=Form(...),date_of_birth=Form(...),place_of_birth=Form(...),country_of_birth=Form(_O),citizenship=Form(_O),sex=Form('M'),document_type=Form(_l),document_place=Form(''),photos=File(default=[])):
	proprietario=await db.users.find_one({_F:ADMIN_EMAIL_ENV})
	if not proprietario:raise HTTPException(400,'Configurazione mancante')
	photo_paths=[]
	for f in photos or[]:
		if not f.filename:continue
		safe=f"doc_{uuid.uuid4().hex}{Path(f.filename).suffix.lower()}";(UPLOAD_DIR/safe).write_bytes(await f.read());photo_paths.append(safe)
	nights=nights_between(checkin,checkout);doc={_a:guest_first_name.strip(),_b:guest_last_name.strip(),_G:checkin,'checkout':checkout,_Q:.0,_c:channel,_A7:date_of_birth,_A8:place_of_birth.strip(),_A9:country_of_birth,_AA:citizenship,'sex':sex,_AB:document_type,_AC:document_number,_AD:document_place,_I:nights,_P:.0,_B:str(proprietario[_D]),'photo_paths':photo_paths,_M:datetime.now(timezone.utc).isoformat()};await db.bookings.insert_one(doc);return{_H:_C}
@api.get('/alloggiati/preview')
async def alloggiati_preview(start_date,end_date,request):user=await get_current_user_raw(request);bookings=await db.bookings.find({_B:user[_A],_G:{_o:start_date,_p:end_date}}).to_list(1000);records=[{'guest':f"{b.get(_a)} {b.get(_b)}",'valid':format_alloggiati_record(b)is not _E}for b in bookings];return{'total':len(bookings),'records':records}
class Ross1000Settings(BaseModel):codice_struttura:str;camere_disponibili:int;letti_disponibili:int
@api.get(_AE)
async def get_ross_settings(request):
	user=await get_current_user_raw(request);doc=await db.settings.find_one({_B:user[_A],_R:_S})
	if not doc:return{_d:'',_T:1,_e:2}
	return{_d:doc.get(_d),_T:doc.get(_T),_e:doc.get(_e)}
@api.post(_AE)
async def save_ross_settings(payload,request):user=await get_current_user_raw(request);await db.settings.update_one({_B:user[_A],_R:_S},{_Z:{**payload.model_dump(),_B:user[_A],_R:_S}},upsert=_C);return{_H:_C}
@api.get('/ross1000/preview')
async def ross_preview(year,month,request):user=await get_current_user_raw(request);settings=await db.settings.find_one({_B:user[_A],_R:_S});bookings=await _month_bookings(user[_A],year,month);return compute_month_stats(year,month,bookings,settings.get(_T,1))
@api.get('/ross1000/export-xml',response_class=PlainTextResponse)
async def ross_export_xml(year,month,request):user=await get_current_user_raw(request);settings=await db.settings.find_one({_B:user[_A],_R:_S});bookings=await _month_bookings(user[_A],year,month);xml=build_movimenti_xml(settings.get(_d),year,month,bookings,settings.get(_T,1),settings.get(_e,2));return PlainTextResponse(xml,media_type='application/xml')
app.include_router(api)
globals()['app']=app
