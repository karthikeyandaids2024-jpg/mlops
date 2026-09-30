"""FinGuard Live: local, same-origin API + frontend. Python 3.10+, stdlib only."""
import base64, csv, hashlib, hmac, io, json, math, os, re, secrets, sqlite3, threading, time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('FINGUARD_DB',str(ROOT/'data'/'finguard.db')))
MODELS=json.loads((ROOT/'models'/'models.json').read_text())
TTL=12*3600
LIMITS={}; LOCK=threading.Lock()
class APIError(Exception):
 def __init__(self,status,message):self.status=status;self.message=message

def connect():
 c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c

def init_db():
 DB.parent.mkdir(parents=True,exist_ok=True)
 with connect() as c:
  c.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,password TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),csrf TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS customers(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,reference TEXT NOT NULL,balance REAL NOT NULL,monthly_transactions INTEGER NOT NULL,products INTEGER NOT NULL,credit_profile TEXT,created_at TEXT NOT NULL,UNIQUE(user_id,reference));
CREATE TABLE IF NOT EXISTS transactions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),customer_id TEXT NOT NULL REFERENCES customers(id),customer_name TEXT NOT NULL,reference TEXT NOT NULL,kind TEXT NOT NULL,channel TEXT NOT NULL,amount REAL NOT NULL,occurred_at TEXT NOT NULL,created_at TEXT NOT NULL,fraud_score REAL NOT NULL,risk TEXT NOT NULL,status TEXT NOT NULL,request_key TEXT NOT NULL,result TEXT NOT NULL,UNIQUE(user_id,request_key));
CREATE TABLE IF NOT EXISTS loan_assessments(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),customer_id TEXT NOT NULL REFERENCES customers(id),request_key TEXT NOT NULL,created_at TEXT NOT NULL,result TEXT NOT NULL,UNIQUE(user_id,request_key));
CREATE INDEX IF NOT EXISTS tx_customer_time ON transactions(customer_id,occurred_at);
''')

def now():return datetime.now(timezone.utc).isoformat()
def string(d,key,minlen=1,maxlen=100):
 v=d.get(key)
 if not isinstance(v,str) or not minlen<=len(v.strip())<=maxlen:raise APIError(400,f'{key.replace("_"," ").title()} must contain {minlen}–{maxlen} characters.')
 return v.strip()
def number(d,key,low,high,integer=False):
 v=d.get(key)
 if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or not low<=v<=high or (integer and int(v)!=v):raise APIError(400,f'{key.replace("_"," ").title()} must be {"a whole number " if integer else ""}between {low} and {high}.')
 return int(v) if integer else float(v)
def password_hash(password):
 salt=secrets.token_bytes(16);digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,310000)
 return base64.b64encode(salt+digest).decode()
def password_check(password,stored):
 raw=base64.b64decode(stored);return hmac.compare_digest(raw[16:],hashlib.pbkdf2_hmac('sha256',password.encode(),raw[:16],310000))
def forest(model,x):
 scores=[]
 for t in model['trees']:
  i=0
  while t['left'][i]!=-1:i=t['left'][i] if x[t['feature'][i]]<=t['threshold'][i] else t['right'][i]
  scores.append(t['probability'][i])
 return sum(scores)/len(scores)
def logistic(model,x):
 l=model['logistic'];z=l['intercept']+sum((v-l['mean'][i])/l['scale'][i]*l['coef'][i] for i,v in enumerate(x))
 return 1/(1+math.exp(-max(-700,min(700,z))))
def assessment(kind,x):
 m=MODELS[kind];p=forest(m,x);baseline=logistic(m,x);effects=[]
 for i,label in enumerate(m['features']):
  alternate=x.copy();alternate[i]=m['medians'][i];effects.append({'label':label,'change_pp':round((p-forest(m,alternate))*100,2)})
 return {'score':p,'risk':'high' if p>=.5 else 'low','comparison_score':baseline,'model_agreement':(p>=.5)==(baseline>=.5),'signals':sorted(effects,key=lambda e:abs(e['change_pp']),reverse=True),'inputs':dict(zip(m['features'],x))}
def segment(c):
 s=MODELS['segments'];x=[c['balance'],c['monthly_transactions'],c['products']]
 distances=[math.sqrt(sum(((v-s['mean'][j])/s['scale'][j]-center[j])**2 for j,v in enumerate(x))) for center in s['centers']];i=distances.index(min(distances));p=s['profiles'][i]
 name='High balance' if p[0]>150000 else 'Multi-product' if p[2]>3 else 'Frequent transactor' if p[1]>45 else 'Everyday banking'
 return {'cluster':i+1,'name':name,'distance':round(distances[i],2),'unusual':distances[i]>3}
def public_customer(row):
 c=dict(row);c.pop('user_id',None);c['credit_profile']=json.loads(c['credit_profile']) if c['credit_profile'] else None;c['segment']=segment(c);return c

def create_customer(user,d):
 name=string(d,'name',2,70);ref=string(d,'reference',2,40)
 balance=round(number(d,'balance',0,10000000),2);freq=number(d,'monthly_transactions',0,300,True);products=number(d,'products',1,5,True)
 credit=None
 if d.get('credit_profile') is not None:
  cp=d['credit_profile']
  if not isinstance(cp,dict):raise APIError(400,'Invalid credit profile.')
  credit={k:number(cp,k,*bounds) for k,bounds in {'income':(15000,160000),'debt_ratio':(0,75),'credit_score':(300,900),'missed_payments':(0,6,True),'loan_ratio':(.3,10)}.items()}
 with connect() as c:
  try:
   cid=secrets.token_hex(12);c.execute('INSERT INTO customers VALUES(?,?,?,?,?,?,?,?,?)',(cid,user['id'],name,ref,balance,freq,products,json.dumps(credit) if credit else None,now()))
  except sqlite3.IntegrityError:raise APIError(409,'A customer with that reference already exists.')
  return public_customer(c.execute('SELECT * FROM customers WHERE id=?',(cid,)).fetchone())

def create_transaction(user,d):
 cid=string(d,'customer_id',1,60);key=string(d,'request_key',8,100);kind=string(d,'kind');channel=string(d,'channel');reference=string(d,'reference',1,80)
 if kind not in ['debit','credit']:raise APIError(400,'Choose debit or credit.')
 if channel not in ['UPI','Card','Bank transfer','ATM','Cash']:raise APIError(400,'Invalid payment channel.')
 amount=round(number(d,'amount',100,200000),2);distance=number(d,'distance',0,2000);new_device=number(d,'new_device',0,1,True);external=number(d,'other_transactions',0,100,True)
 try:
  stamp=datetime.fromisoformat(string(d,'occurred_at',10,40));assert stamp.tzinfo is not None
 except (ValueError,AssertionError):raise APIError(400,'Provide a valid transaction time including timezone.')
 current=datetime.now(timezone.utc)
 if stamp>current+timedelta(minutes=5) or stamp<current-timedelta(days=365):raise APIError(400,'Transaction time must be within the past year and not in the future.')
 utc=stamp.astimezone(timezone.utc).isoformat();warnings=[]
 with connect() as c:
  c.execute('BEGIN IMMEDIATE')
  existing=c.execute('SELECT result FROM transactions WHERE user_id=? AND request_key=?',(user['id'],key)).fetchone()
  if existing:return json.loads(existing['result'])
  row=c.execute('SELECT * FROM customers WHERE id=? AND user_id=?',(cid,user['id'])).fetchone()
  if row is None:raise APIError(404,'Customer not found.')
  customer=public_customer(row)
  prior=c.execute('SELECT count(*) FROM transactions WHERE user_id=? AND customer_id=? AND occurred_at>=? AND occurred_at<=?',(user['id'],cid,(stamp.astimezone(timezone.utc)-timedelta(hours=1)).isoformat(),utc)).fetchone()[0]
  velocity=prior+external+1
  if velocity>12:warnings.append('Hourly count exceeds the training range; the model uses 12. Actual count is saved.')
  fraud=assessment('fraud',[amount,stamp.hour,distance,min(12,velocity),new_device])
  balance=customer['balance'];projected=round(balance+amount if kind=='credit' else balance-amount,2)
  share=amount/balance if balance else None
  debit=None
  if kind=='debit':debit={'risk':'high' if projected<0 or (share is not None and share>=.8) else 'low','insufficient_funds':projected<0,'balance_share':share,'explanation':'Insufficient recorded balance' if projected<0 else 'Uses at least 80% of recorded balance' if share is not None and share>=.8 else 'Amount fits within the recorded balance','method':'Balance rule; not an ML prediction'}
  credit=None;cp=customer['credit_profile']
  if cp:credit=assessment('loan',[cp['income'],cp['debt_ratio'],cp['credit_score'],cp['missed_payments'],cp['loan_ratio']])
  reasons=[]
  if fraud['risk']=='high':reasons.append('High transaction fraud score')
  if debit and debit['risk']=='high':reasons.append(debit['explanation'])
  if credit and credit['risk']=='high':reasons.append('High customer credit-default score')
  risk='high' if reasons else 'low';status='review' if risk=='high' else 'recorded'
  if not fraud['model_agreement']:warnings.append('The two fraud classifiers disagree at 50%; review the transaction context.')
  result={'id':'TX-'+secrets.token_hex(5).upper(),'customer_id':cid,'customer_name':customer['name'],'customer_reference':customer['reference'],'reference':reference,'kind':kind,'channel':channel,'amount':amount,'occurred_at':stamp.isoformat(),'created_at':now(),'risk':risk,'status':status,'review_reasons':reasons,'balance_before':balance,'projected_balance':projected,'recorded_balance':balance,'fraud':fraud,'debit':debit,'credit':credit,'segment':customer['segment'],'hourly_count':velocity,'warnings':warnings,'data_notice':'Models trained on synthetic data; scores are uncalibrated. This app logs and screens entries; it does not execute payments.','balance_notice':'Profile balance is a snapshot and is not changed by screening a transaction.'}
  c.execute('INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(result['id'],user['id'],cid,customer['name'],reference,kind,channel,amount,utc,result['created_at'],fraud['score'],risk,status,key,json.dumps(result)))
  return result


def loan_math(amount,annual_rate,months):
 rate=annual_rate/1200
 factor=1/months if rate==0 else rate/(1-(1+rate)**(-months))
 emi=amount*factor
 return emi,factor

def create_loan(user,d):
 cid=string(d,'customer_id',1,60);key=string(d,'request_key',8,100)
 amount=number(d,'amount',10000,10000000);annual_rate=number(d,'annual_rate',0,36);months=number(d,'months',6,360,True)
 income=number(d,'income',15000,160000);existing=number(d,'existing_emi',0,income);expenses=number(d,'expenses',0,income)
 credit_score=number(d,'credit_score',300,900,True);missed=number(d,'missed_payments',0,6,True)
 with connect() as c:
  c.execute('BEGIN IMMEDIATE')
  prior=c.execute('SELECT result FROM loan_assessments WHERE user_id=? AND request_key=?',(user['id'],key)).fetchone()
  if prior:return json.loads(prior['result'])
  customer=c.execute('SELECT * FROM customers WHERE id=? AND user_id=?',(cid,user['id'])).fetchone()
  if not customer:raise APIError(404,'Customer not found.')
  emi,factor=loan_math(amount,annual_rate,months)
  capacity=max(0,min(income*.40-existing,income-expenses-existing))
  maximum=capacity/factor
  ratio=amount/(income*12);debt_ratio=(existing+emi)/income*100
  supported=.3<=ratio<=10 and debt_ratio<=75
  ml=assessment('loan',[income,debt_ratio,credit_score,missed,ratio]) if supported else None
  checks=[{'label':'Monthly payment fits repayment capacity','passed':emi<=capacity+1e-8,'detail':f'EMI {emi:.2f}; available EMI capacity {capacity:.2f}'},
          {'label':'Credit score at least 650','passed':credit_score>=650,'detail':f'Entered credit score: {credit_score}'},
          {'label':'No more than one missed payment','passed':missed<=1,'detail':f'Entered missed payments: {missed}'}]
  if ml:checks.append({'label':'ML default score below 50%','passed':ml['score']<.5,'detail':f'Model score: {ml["score"]*100:.1f}%'})
  failed=[v['label'] for v in checks if not v['passed']]
  decision='not_eligible' if failed else 'eligible' if supported else 'review'
  schedule=[];balance=amount
  for month in range(1,months+1):
   interest=balance*annual_rate/1200;principal=min(balance,emi-interest);payment=principal+interest;balance=max(0,balance-principal)
   schedule.append({'month':month,'payment':round(payment,2),'principal':round(principal,2),'interest':round(interest,2),'balance':round(balance,2)})
  result={'id':'LN-'+secrets.token_hex(5).upper(),'customer_id':cid,'customer_name':customer['name'],'created_at':now(),'decision':decision,
          'amount':amount,'annual_rate':annual_rate,'months':months,'income':income,'existing_emi':existing,'expenses':expenses,'credit_score':credit_score,'missed_payments':missed,
          'emi':round(emi,2),'total_repayment':round(emi*months,2),'total_interest':round(emi*months-amount,2),'emi_capacity':round(capacity,2),'affordable_amount':math.floor(maximum),'monthly_surplus':round(income-expenses-existing-emi,2),'debt_ratio':round(debt_ratio,2),'loan_income_ratio':round(ratio,3),
          'credit':ml,'checks':checks,'failed_checks':failed,'schedule':schedule,
          'model_notice':None if supported else 'ML score unavailable: this loan-to-annual-income ratio or debt ratio is outside the training range. No score has been invented or extrapolated.',
          'policy_notice':'Illustrative project policy: total EMIs at most 40% of income; positive affordability after entered expenses; credit score at least 650; at most one missed payment; ML score below 50% when available. Not a lender approval.',
          'amount_notice':'Affordable amount is a repayment-capacity estimate at the entered rate and tenure, not an approved or guaranteed loan. Assumes a fixed rate, monthly reducing balance and no fees.'}
  c.execute('INSERT INTO loan_assessments VALUES(?,?,?,?,?,?)',(result['id'],user['id'],cid,key,result['created_at'],json.dumps(result)))
  return result

class Handler(BaseHTTPRequestHandler):
 server_version='FinGuard'
 def log_message(self,fmt,*args):
  # Do not log submitted payloads, passwords, session IDs or customer details.
  if args and isinstance(args[0],str):print(f'[{self.log_date_time_string()}] {args[0].split("?")[0]}')
 def send(self,status,payload,cookie=None,ctype='application/json; charset=utf-8'):
  body=json.dumps(payload,allow_nan=False).encode() if ctype.startswith('application/json') else payload
  self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','same-origin');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
  if cookie:self.send_header('Set-Cookie',cookie)
  self.end_headers();self.wfile.write(body)
 def body(self):
  try:n=int(self.headers.get('Content-Length','0'))
  except ValueError:raise APIError(400,'Invalid content length.')
  if n<1 or n>32768:raise APIError(413,'Request body must be 1–32768 bytes.')
  if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise APIError(415,'JSON required.')
  try:
   obj=json.loads(self.rfile.read(n))
   if not isinstance(obj,dict):raise ValueError()
   return obj
  except (ValueError,UnicodeError):raise APIError(400,'Invalid JSON.')
 def token(self):
  for part in self.headers.get('Cookie','').split(';'):
   if part.strip().startswith('fg_session='):return part.strip().split('=',1)[1]
  return ''
 def auth(self,csrf=False):
  digest=hashlib.sha256(self.token().encode()).hexdigest()
  with connect() as c:r=c.execute('SELECT u.id,u.name,u.email,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?',(digest,time.time())).fetchone()
  if not r:raise APIError(401,'Please sign in to continue.')
  if csrf and not hmac.compare_digest(r['csrf'],self.headers.get('X-CSRF-Token','')):raise APIError(403,'Session verification failed. Refresh and try again.')
  return dict(r)
 def same_origin(self):
  host=self.headers.get('Host','')
  if host not in self.server.allowed_hosts:raise APIError(403,'Invalid host.')
  origin=self.headers.get('Origin')
  if origin and origin!='http://'+host:raise APIError(403,'Cross-origin request denied.')
 def rate_limit(self):
  key=self.client_address[0];t=time.time()
  with LOCK:
   events=[v for v in LIMITS.get(key,[]) if t-v<60]
   if len(events)>=15:raise APIError(429,'Too many sign-in attempts. Wait one minute.')
   LIMITS[key]=events+[t]
 def do_POST(self):
  try:
   self.same_origin();path=urlparse(self.path).path;d=self.body()
   if path in ['/api/signup','/api/login']:
    self.rate_limit();email=string(d,'email',3,254).lower();password=string(d,'password',8,128)
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise APIError(400,'Enter a valid email address.')
    with connect() as c:
     if path=='/api/signup':
      name=string(d,'name',2,70);uid=secrets.token_hex(12)
      try:c.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,name,email,password_hash(password),now()))
      except sqlite3.IntegrityError:raise APIError(409,'That email already has an account. Sign in instead.')
      user={'id':uid,'name':name,'email':email}
     else:
      row=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
      if not row or not password_check(password,row['password']):raise APIError(401,'Incorrect email or password.')
      user={k:row[k] for k in ['id','name','email']}
     token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24);c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),));c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],csrf,time.time()+TTL))
    self.send(200,{'user':user,'csrf':csrf},f'fg_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={TTL}');return
   user=self.auth(True)
   if path=='/api/logout':
    with connect() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(self.token().encode()).hexdigest(),))
    self.send(200,{'ok':True},'fg_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
   elif path=='/api/customers':self.send(201,{'customer':create_customer(user,d)})
   elif path=='/api/transactions':self.send(201,{'transaction':create_transaction(user,d)})
   elif path=='/api/loans':self.send(201,{'loan':create_loan(user,d)})
   else:raise APIError(404,'Endpoint not found.')
  except APIError as e:self.send(e.status,{'error':e.message})
  except Exception as e:
   print('API error:',type(e).__name__);self.send(500,{'error':'The request could not be saved. Please try again.'})
 def do_GET(self):
  try:
   self.same_origin();path=urlparse(self.path).path
   if path=='/api/me':
    user=self.auth();self.send(200,{'user':{k:user[k] for k in ['id','name','email']},'csrf':user['csrf']});return
   if path.startswith('/api/'):
    user=self.auth()
    with connect() as c:
     if path=='/api/customers':self.send(200,{'customers':[public_customer(r) for r in c.execute('SELECT * FROM customers WHERE user_id=? ORDER BY created_at DESC',(user['id'],))]})
     elif path=='/api/loans':self.send(200,{'loans':[json.loads(r['result']) for r in c.execute('SELECT result FROM loan_assessments WHERE user_id=? ORDER BY created_at DESC',(user['id'],))]})
     elif path=='/api/transactions':self.send(200,{'transactions':[json.loads(r['result']) for r in c.execute('SELECT result FROM transactions WHERE user_id=? ORDER BY created_at DESC',(user['id'],))]})
     else:raise APIError(404,'Endpoint not found.')
    return
   files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8'),'/favicon.svg':('favicon.svg','image/svg+xml')}
   if path not in files:raise APIError(404,'Not found.')
   file,ctype=files[path];self.send(200,(ROOT/'frontend'/file).read_bytes(),ctype=ctype)
  except APIError as e:self.send(e.status,{'error':e.message})
  except (BrokenPipeError,ConnectionResetError):pass

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8000);args=parser.parse_args();init_db()
 try:
  server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.allowed_hosts={f'127.0.0.1:{args.port}',f'localhost:{args.port}'}
 except OSError:
  print(f'Port {args.port} is busy. Stop the old server with Ctrl+C or run: python server.py --port 8001');raise SystemExit(1)
 print(f'\nFinGuard Live is running: http://127.0.0.1:{args.port}\nKeep this window open. Press Ctrl+C to stop.\n')
 try:server.serve_forever()
 except KeyboardInterrupt:print('\nStopped.')
 finally:server.server_close()
