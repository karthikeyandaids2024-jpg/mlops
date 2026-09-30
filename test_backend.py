"""Run: python test_backend.py. Uses an isolated temporary database."""
import json,os,tempfile,threading,unittest,urllib.request,urllib.error,http.cookiejar
from datetime import datetime,timezone
TMP=tempfile.TemporaryDirectory();os.environ['FINGUARD_DB']=os.path.join(TMP.name,'test.db')
import server
class Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  server.init_db();cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);cls.port=cls.http.server_address[1];cls.http.allowed_hosts={f'127.0.0.1:{cls.port}'};threading.Thread(target=cls.http.serve_forever,daemon=True).start()
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();TMP.cleanup()
 def client(self):return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 def call(self,c,path,body=None,csrf='',extra=None):
  headers={'Content-Type':'application/json','X-CSRF-Token':csrf};headers.update(extra or {});req=urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
  try:r=c.open(req)
  except urllib.error.HTTPError as e:r=e
  raw=r.read();return r.status,json.loads(raw) if 'application/json' in r.headers.get('Content-Type','') else raw
 def test_workflow_and_isolation(self):
  a=self.client();b=self.client();self.assertEqual(self.call(a,'/api/transactions')[0],401)
  status,r=self.call(a,'/api/signup',{'name':'User A','email':'a@example.com','password':'secure-test-123'});self.assertEqual(status,200);csrf=r['csrf']
  cp={'income':65000,'debt_ratio':25,'credit_score':740,'missed_payments':0,'loan_ratio':2}
  customer={'name':'Customer A','reference':'C-001','balance':50000,'monthly_transactions':25,'products':2,'credit_profile':cp}
  self.assertEqual(self.call(a,'/api/customers',customer)[0],403)
  status,r=self.call(a,'/api/customers',customer,csrf);self.assertEqual(status,201);cid=r['customer']['id']
  self.assertEqual(self.call(a,'/api/customers',customer,csrf)[0],409)
  payload={'customer_id':cid,'reference':'PAY-001','request_key':'duplicate-key-001','kind':'debit','channel':'UPI','amount':1000,'distance':2,'new_device':0,'other_transactions':0,'occurred_at':datetime.now(timezone.utc).isoformat()}
  status,r=self.call(a,'/api/transactions',payload,csrf);self.assertEqual(status,201);t=r['transaction'];self.assertIsNotNone(t['credit']);self.assertEqual(t['debit']['risk'],'low');self.assertEqual(t['projected_balance'],49000)
  self.assertEqual(self.call(a,'/api/transactions',payload,csrf)[1]['transaction']['id'],t['id']);self.assertEqual(len(self.call(a,'/api/transactions')[1]['transactions']),1)
  payload.update(request_key='different-key-002',amount=100000);status,r=self.call(a,'/api/transactions',payload,csrf);self.assertEqual(status,201);self.assertEqual(r['transaction']['risk'],'high');self.assertTrue(r['transaction']['debit']['insufficient_funds']);self.assertEqual(r['transaction']['hourly_count'],2)
  payload.update(request_key='different-key-003',kind='credit');status,r=self.call(a,'/api/transactions',payload,csrf);self.assertEqual(status,201);self.assertIsNone(r['transaction']['debit'])
  payload.update(amount=-1,request_key='invalid-key-004');self.assertEqual(self.call(a,'/api/transactions',payload,csrf)[0],400)
  status,r=self.call(b,'/api/signup',{'name':'User B','email':'b@example.com','password':'secure-test-456'});self.assertEqual(status,200);bcsrf=r['csrf'];self.assertEqual(self.call(b,'/api/customers')[1]['customers'],[]);self.assertEqual(self.call(b,'/api/transactions')[1]['transactions'],[])
  payload.update(amount=1000,request_key='attack-key-005');self.assertEqual(self.call(b,'/api/transactions',payload,bcsrf)[0],404)
  self.assertEqual(self.call(a,'/api/customers',customer,csrf,{'Origin':'http://evil.example'})[0],403)
  self.assertEqual(self.call(a,'/server.py')[0],404);self.assertEqual(self.call(a,'/data/finguard.db')[0],404);self.assertEqual(self.call(a,'/')[0],200)
  with server.connect() as c:
   row=c.execute('SELECT password FROM users WHERE email=?',('a@example.com',)).fetchone();self.assertNotEqual(row['password'],'secure-test-123');self.assertTrue(server.password_check('secure-test-123',row['password']))
  self.assertEqual(self.call(a,'/api/logout',{},csrf)[0],200);self.assertEqual(self.call(a,'/api/me')[0],401)
  self.assertEqual(self.call(a,'/api/login',{'email':'a@example.com','password':'wrong-pass'})[0],401)
  self.assertEqual(self.call(a,'/api/login',{'email':'a@example.com','password':'secure-test-123'})[0],200);self.assertEqual(len(self.call(a,'/api/transactions')[1]['transactions']),3)
  server.init_db();self.assertEqual(len(self.call(a,'/api/transactions')[1]['transactions']),3)
 def test_loan_calculations_and_storage(self):
  client=self.client();status,r=self.call(client,'/api/signup',{'name':'Loan user','email':'loan@example.com','password':'secure-loan-123'});self.assertEqual(status,200);csrf=r['csrf']
  status,r=self.call(client,'/api/customers',{'name':'Borrower','reference':'LOAN-01','balance':100000,'monthly_transactions':20,'products':2},csrf);self.assertEqual(status,201);cid=r['customer']['id']
  payload={'customer_id':cid,'request_key':'loan-unique-001','amount':600000,'annual_rate':12,'months':60,'income':100000,'existing_emi':0,'expenses':20000,'credit_score':850,'missed_payments':0}
  status,r=self.call(client,'/api/loans',payload,csrf);self.assertEqual(status,201);loan=r['loan'];self.assertEqual(loan['decision'],'eligible');self.assertAlmostEqual(loan['emi'],13346.67,places=2);self.assertEqual(len(loan['schedule']),60);self.assertEqual(loan['schedule'][-1]['balance'],0)
  self.assertAlmostEqual(sum(v['principal'] for v in loan['schedule']),600000,delta=.7)
  self.assertEqual(self.call(client,'/api/loans',payload,csrf)[1]['loan']['id'],loan['id']);self.assertEqual(len(self.call(client,'/api/loans')[1]['loans']),1)
  payload.update(request_key='loan-unique-002',annual_rate=0);status,r=self.call(client,'/api/loans',payload,csrf);self.assertEqual(status,201);self.assertEqual(r['loan']['emi'],10000);self.assertEqual(r['loan']['total_interest'],0)
  payload.update(request_key='loan-unique-003',amount=5000000,annual_rate=12);status,r=self.call(client,'/api/loans',payload,csrf);self.assertEqual(status,201);self.assertEqual(r['loan']['decision'],'not_eligible')
  payload.update(request_key='loan-unique-004',amount=10000);status,r=self.call(client,'/api/loans',payload,csrf);self.assertEqual(status,201);self.assertEqual(r['loan']['decision'],'review');self.assertIsNone(r['loan']['credit'])
  payload.update(request_key='loan-unique-005',amount=600000,credit_score=400);self.assertEqual(self.call(client,'/api/loans',payload,csrf)[1]['loan']['decision'],'not_eligible')
  payload.update(request_key='loan-unique-006',months=0);self.assertEqual(self.call(client,'/api/loans',payload,csrf)[0],400)
  other=self.client();_,r=self.call(other,'/api/signup',{'name':'Other loan user','email':'loan-other@example.com','password':'secure-loan-456'});self.assertEqual(self.call(other,'/api/loans')[1]['loans'],[])
  payload.update(months=60);self.assertEqual(self.call(other,'/api/loans',payload,r['csrf'])[0],404)
  server.init_db();self.assertEqual(len(self.call(client,'/api/loans')[1]['loans']),5)
 def test_model_parity(self):
  for kind in ['fraud','loan']:
   for fixture in server.MODELS[kind]['test_vectors']:self.assertAlmostEqual(server.forest(server.MODELS[kind],fixture['x']),fixture['probability'],places=12)
if __name__=='__main__':unittest.main(verbosity=2)
