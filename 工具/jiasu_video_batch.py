"""Jiasu reference-video submission. No automatic paid retries."""
from __future__ import annotations
import argparse, hashlib, json, os, sys, time, uuid
from pathlib import Path
import requests
import wanwan_video_batch as shared
ROOT=Path(__file__).resolve().parents[1]

def write_json(path,value):
 shared.save_json(path,value)

def load(story,units):
 cfg=json.loads((ROOT/'工具'/'api配置.json').read_text(encoding='utf-8'))['providers']['jiasu_video']
 secret=json.loads(Path(cfg['credential_file']).read_text(encoding='utf-8'))[cfg['credential_field']]
 client=requests.Session();client.headers.update({'Authorization':'Bearer '+secret,'Accept':'application/json'})
 prepared={u['number']:u for u in shared.parse_story(story) if u['number'] in units}
 if set(prepared)!=set(units):raise RuntimeError('Requested unit missing')
 return cfg,client,prepared

def get_json(client,url):
 r=client.get(url,timeout=30)
 if r.status_code!=200:raise RuntimeError('GET failed HTTP '+str(r.status_code))
 return r.json()

def preflight(story,units,model,batch,resolution):
 cfg,c,prepared=load(story,units);base=cfg['base_url'].rstrip('/')
 catalog=get_json(c,base+'/v1/models')
 if model not in [x.get('id') for x in catalog.get('data',[])]:raise RuntimeError('Requested model unavailable for current key')
 pricing=get_json(c,base+'/api/pricing');rows=[x for x in pricing.get('data',[]) if x.get('model_name')==model]
 if len(rows)!=1 or rows[0].get('quota_type')!=1:raise RuntimeError('Current fixed-price model could not be confirmed')
 price=rows[0];params=json.loads(price.get('api_parameters') or '[]')
 res=next((x for x in params if x.get('name')=='resolution'),{})
 if resolution not in res.get('range','').lower():raise RuntimeError('Requested resolution not supported by catalog')
 _,wjournal,_=shared.story_paths(story);events=shared.journal_events(wjournal)
 old={e['unit']:e['id'] for e in events if e.get('event') in ['submitted','existing']}
 prior=[]
 with shared.session() as wc:
  for n in units:
   status,j=shared.get_json(wc,'/api/portal/jobs/'+old[n])
   if status!=200 or j.get('status')!='failed' or j.get('hasOutput'):raise RuntimeError('Previous task not confirmed failed U'+str(n))
   prior.append({'unit':n,'id':old[n],'status':j.get('status'),'error':j.get('error'),'refund_status':'not_exposed_by_wanwan'})
 for u in prepared.values():
  if not 1<=len(u['files'])<=9:raise RuntimeError('Reference count invalid')
 status_data=get_json(c,base+'/api/status').get('data',{})
 quote={'model':model,'duration':30,'resolution':resolution,'ratio':'9:16','unit_count':len(units),'units':units,'unique_images':len({p for u in prepared.values() for p in u['files']}),'price_record':price,'group_ratio':pricing.get('group_ratio'),'display':{k:status_data.get(k) for k in ['custom_currency_symbol','custom_currency_exchange_rate','usd_exchange_rate','price']},'prior_failed':prior}
 write_json(batch/'jiasu-preflight.json',quote)
 print(json.dumps({'preflight':'passed','model':model,'units':units,'unique_images':quote['unique_images'],'listed_unit_price':price.get('model_price'),'listed_total':float(price['model_price'])*len(units)},ensure_ascii=False),flush=True)
 return cfg,c,prepared,prior

def upload(cfg,c,files,batch):
 base=cfg['base_url'].rstrip('/');cache_path=batch/'jiasu-media-cache.json'
 cache=json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
 hashes={p:shared.sha256(p.read_bytes()) for p in files};missing=[p for p in files if hashes[p] not in cache]
 if missing:
  session_path=batch/'jiasu-upload-sessions.json'
  if session_path.exists():
   prior=json.loads(session_path.read_text(encoding='utf-8'))
   if any(x['sha256'] not in cache for x in prior):raise RuntimeError('Incomplete upload session exists; reconcile before new upload')
  descriptors=[{'name':p.name,'content_type':'image/png','size':p.stat().st_size,'kind':'image'} for p in missing]
  response=c.post(base+'/v1/media/uploads',json={'items':descriptors},timeout=120)
  if response.status_code!=200:raise RuntimeError('Upload session HTTP '+str(response.status_code))
  result=response.json()
  if result.get('success') is not True:raise RuntimeError('Upload session refused: '+str(result.get('message'))[:200])
  items=result.get('data',{}).get('items',[])
  if len(items)!=len(missing):raise RuntimeError('Upload session count mismatch')
  write_json(session_path,[{'file':str(p),'sha256':hashes[p],'upload_id':item.get('upload_id'),'url':item.get('url')} for p,item in zip(missing,items)])
  for p,item in zip(missing,items):
   put=item.get('put') or {}
   if not put.get('url') or not item.get('upload_id'):raise RuntimeError('Missing upload contract')
   with p.open('rb') as handle:
    r=requests.put(put['url'],data=handle,headers=put.get('headers') or {},timeout=180)
   if r.status_code not in (200,201,204):raise RuntimeError('Storage PUT failed HTTP '+str(r.status_code))
   r=c.post(base+'/v1/media/uploads/'+item['upload_id']+'/complete',json={},timeout=120)
   d=r.json()
   if r.status_code!=200 or d.get('success') is not True:raise RuntimeError('Upload complete failed')
   url=(d.get('data') or {}).get('url') or item.get('url')
   if not isinstance(url,str) or not url.startswith('https://'):raise RuntimeError('Missing public media URL')
   cache[hashes[p]]={'file':p.name,'url':url,'upload_id':item['upload_id'],'bytes':p.stat().st_size};write_json(cache_path,cache)
   print('UPLOADED '+p.name,flush=True)
 return {p:cache[hashes[p]]['url'] for p in files}

def submit(args):
 story=args.story.resolve();units=[int(n) for n in args.units.split(',')];batch=args.batch.resolve();batch.mkdir(parents=True,exist_ok=True)
 journal=batch/'jiasu-submissions.jsonl'
 existing=shared.journal_events(journal)
 if any(e.get('event') in ['attempt','submitted','unknown_outcome'] for e in existing):raise RuntimeError('Prior attempt exists; poll or reconcile, never automatically resubmit')
 resolution=args.resolution
 cfg,c,prepared,prior=preflight(story,units,args.model,batch,resolution);base=cfg['base_url'].rstrip('/')
 if args.command=='prepare':return
 files=list(dict.fromkeys(p for u in prepared.values() for p in u['files']))
 media=upload(cfg,c,files,batch)
 for n in units:
  u=prepared[n];rid=str(uuid.uuid5(uuid.NAMESPACE_URL,str(story)+'|'+str(n)+'|'+args.model+'|jiasu-v1-20261016|'+resolution+'|'+u['hash']))
  payload={'model':args.model,'prompt':u['prompt'],'duration':30,'ratio':'9:16','resolution':resolution,'images':[{'url':media[p],'name':'图'+str(i)} for i,p in enumerate(u['files'],1)]}
  write_json(batch/('jiasu-U%02d-request.json'%n),payload)
  common={'platform':'jiasu','unit':n,'model':args.model,'request_id':rid,'prompt_sha256':u['hash'],'duration':30,'resolution':resolution,'ratio':'9:16','reference_files':[p.name for p in u['files']],'parent_task_id':next(x['id'] for x in prior if x['unit']==n)}
  shared.append_event(journal,{'event':'attempt',**common})
  try:r=c.post(base+'/v1/video/generations',json=payload,headers={'X-Request-ID':rid},timeout=240)
  except requests.RequestException as e:
   shared.append_event(journal,{'event':'unknown_outcome',**common,'error_type':type(e).__name__});raise RuntimeError('Submission outcome unknown; stop and reconcile')
  try:d=r.json()
  except ValueError:d={}
  if r.status_code not in (200,201,202):
   error=d.get('error') or {'message':d.get('message')}
   shared.append_event(journal,{'event':'submit_failed',**common,'http':r.status_code,'error':error});raise RuntimeError('Submission failed HTTP '+str(r.status_code)+' '+str(error)[:300])
  data=d.get('data') or d;tid=data.get('id') or data.get('task_id')
  if not tid:
   shared.append_event(journal,{'event':'unknown_outcome',**common,'http':r.status_code});raise RuntimeError('No task ID; stop and reconcile')
  entry={'event':'submitted',**common,'task_id':tid,'status':data.get('status'),'created_at':data.get('created_at')};shared.append_event(journal,entry)
  print('SUBMITTED U%02d %s %s'%(n,tid,data.get('status')),flush=True)
 poll(args)

def poll(args):
 cfg=json.loads((ROOT/'工具'/'api配置.json').read_text(encoding='utf-8'))['providers']['jiasu_video'];secret=json.loads(Path(cfg['credential_file']).read_text(encoding='utf-8'))[cfg['credential_field']]
 c=requests.Session();c.headers['Authorization']='Bearer '+secret;base=cfg['base_url'].rstrip('/');journal=args.batch/'jiasu-submissions.jsonl';rows=[]
 for e in shared.journal_events(journal):
  if e.get('event')!='submitted':continue
  d=get_json(c,base+'/v1/videos/tasks/'+str(e['task_id']));d=d.get('data') or d
  row={'event':'queried','unit':e['unit'],'task_id':e['task_id'],'model':d.get('model'),'status':d.get('status'),'progress':d.get('progress'),'has_output':bool(d.get('result_urls')),'error':d.get('error')};rows.append(row);shared.append_event(journal,row)
 write_json(args.batch/'jiasu-latest-status.json',rows);print(json.dumps(rows,ensure_ascii=False),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','submit','poll']);p.add_argument('--story',type=Path,required=True);p.add_argument('--batch',type=Path,required=True);p.add_argument('--units',default='4,6,7,10,21');p.add_argument('--model',default='dola-sd-2.5');p.add_argument('--resolution',choices=['480p','720p','1080p'],default='1080p');args=p.parse_args()
 try:
  if args.command=='poll':poll(args)
  else:submit(args)
 except Exception as e:
  print('STOP: '+(str(e) if isinstance(e,RuntimeError) else type(e).__name__),file=sys.stderr,flush=True);sys.exit(1)
