"""Reproducible synthetic-data ML training. Run from project root: python ml/train.py."""
import json, pathlib, csv
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score, confusion_matrix, silhouette_score
from sklearn.cluster import KMeans
ROOT=pathlib.Path(__file__).resolve().parents[1]; OUT=ROOT/'models'; OUT.mkdir(exist_ok=True)
rng=np.random.default_rng(42); n=6000
sigmoid=lambda z:1/(1+np.exp(-z))
def train(X,y,names):
 a,b,c,d=train_test_split(X,y,test_size=.25,random_state=42,stratify=y)
 forest=RandomForestClassifier(n_estimators=45,max_depth=6,min_samples_leaf=18,class_weight="balanced",random_state=42).fit(a,c)
 scale=StandardScaler().fit(a); log=LogisticRegression(max_iter=1000,random_state=42).fit(scale.transform(a),c)
 def metrics(m,x):
  p=m.predict_proba(x)[:,1]; pred=p>=.5
  return dict(accuracy=accuracy_score(d,pred),precision=precision_score(d,pred,zero_division=0),recall=recall_score(d,pred),f1=f1_score(d,pred),auc=roc_auc_score(d,p),confusion=confusion_matrix(d,pred).tolist())
 trees=[]
 for estimator in forest.estimators_:
  t=estimator.tree_; trees.append(dict(left=t.children_left.tolist(),right=t.children_right.tolist(),feature=t.feature.tolist(),threshold=t.threshold.tolist(),probability=[float(v[0][1]/v[0].sum()) for v in t.value]))
 return dict(features=names,trees=trees,logistic=dict(mean=scale.mean_.tolist(),scale=scale.scale_.tolist(),coef=log.coef_[0].tolist(),intercept=float(log.intercept_[0])),importance=forest.feature_importances_.tolist(),medians=np.median(a,axis=0).tolist(),metrics=metrics(forest,b),baseline=metrics(log,scale.transform(b)),train=len(a),test=len(b),positive_rate=float(y.mean()),test_vectors=[dict(x=x.tolist(),probability=float(p)) for x,p in zip(b[:10],forest.predict_proba(b[:10])[:,1])])
amount=np.clip(rng.lognormal(8.3,1.25,n),100,200000); hour=rng.integers(0,24,n); distance=np.clip(rng.exponential(130,n),0,2000); velocity=rng.integers(1,13,n); new=rng.binomial(1,.24,n)
X=np.column_stack([amount,hour,distance,velocity,new]); y=rng.binomial(1,sigmoid(-5+amount/35000+distance/400+velocity*.24+new*1.5+((hour<6)|(hour>22))*.8))
fraud=train(X,y,['Amount (₹)','Hour of day','Distance (km)','Transactions / hour','New device'])
rows=[dict(id=f'TXN-{1001+i}',amount=round(float(x[0])),hour=int(x[1]),distance=round(float(x[2])),velocity=int(x[3]),device=int(x[4]),actual=int(v)) for i,(x,v) in enumerate(zip(X[:80],y[:80]))]
income=rng.uniform(15000,160000,n); debt=rng.uniform(0,75,n); credit=rng.uniform(300,900,n); missed=rng.integers(0,7,n); ratio=rng.uniform(.3,10,n)
L=np.column_stack([income,debt,credit,missed,ratio]); ly=rng.binomial(1,sigmoid(-1.9+(650-credit)/120+debt*.033+missed*.42+ratio*.11-income/150000))
loan=train(L,ly,['Monthly income (₹)','Debt-to-income (%)','Credit score','Missed payments','Loan / annual income'])
C=np.column_stack([rng.lognormal(10.7,1,n),rng.uniform(2,85,n),rng.integers(1,6,n)]); scaler=StandardScaler().fit(C); Z=scaler.transform(C); km=KMeans(n_clusters=4,n_init=10,random_state=42).fit(Z)
segment=dict(mean=scaler.mean_.tolist(),scale=scaler.scale_.tolist(),centers=km.cluster_centers_.tolist(),profiles=scaler.inverse_transform(km.cluster_centers_).tolist(),counts=np.bincount(km.labels_).tolist(),silhouette=float(silhouette_score(Z,km.labels_,sample_size=1200,random_state=42)))
model=dict(fraud=fraud,loan=loan,segments=segment,transactions=rows,seed=42,rows=n)
(OUT/'models.json').write_text(json.dumps(model,separators=(',',':')))
for title,data,target,names in [('fraud',X,y,fraud['features']),('loan',L,ly,loan['features'])]:
 with (ROOT/'ml'/f'{title}_synthetic.csv').open('w') as f:
  w=csv.writer(f); w.writerow(names+['target']); w.writerows([list(x)+[int(v)] for x,v in zip(data,target)])
print(json.dumps({k:model[k]['metrics'] for k in ['fraud','loan']},indent=2))
