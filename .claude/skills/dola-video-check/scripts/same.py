"""Reuse an existing Dola request verbatim; one creation per task directory."""
from pathlib import Path
from types import SimpleNamespace
import argparse,json,sys,hashlib

def adapter(root):
    sys.path.insert(0,str(root/'工具'))
    import dolasd_video_batch
    return dolasd_video_batch

def source_request(api,source):
    raw=json.loads((source/'request.json').read_text(encoding='utf-8'))
    fields=raw.get('fields',raw);fields={k:fields[k] for k in ['model','prompt','duration','size']}
    refs=raw['references'];actual=api.read_references([r['path'] for r in refs])
    if [r['sha256'] for r in refs]!=[r['sha256'] for r in actual]:raise RuntimeError('Original reference files changed; not submitting')
    return fields,actual

def connection(job,api,target):
    binding=target/'connection.json'
    saved=json.loads(binding.read_text(encoding='utf-8')) if binding.exists() else None
    channel_id=saved['channel_id'] if saved else job.get('channel_id','dola-pool')
    revision=saved['channel_revision'] if saved else job.get('channel_revision')
    cfg=json.loads(Path(job.get('channels_file','<个人工作台>/runtime/storyboard/channels.json')).read_text(encoding='utf-8'))
    rows=cfg['channels']+cfg.get('history',[])
    row=next(r for r in rows if r.get('id')==channel_id and (not revision or r.get('revision')==revision))
    if row.get('adapter')!='dolasd_video':raise RuntimeError('This skill only submits to Dola')
    keys=row.get('apiKeys') or ([row.get('apiKey')] if row.get('apiKey') else [])
    if len(keys)!=1:raise RuntimeError('Original single-key Dola connection is unavailable')
    identity={'channel_id':channel_id,'channel_revision':row.get('revision'),'endpoint':row['endpoint'].rstrip('/'),'credential_sha256':hashlib.sha256(keys[0].encode()).hexdigest()}
    if saved and saved!=identity:raise RuntimeError('Original connection identity changed')
    if not identity['endpoint'].startswith('https://'):raise RuntimeError('Dola endpoint must be HTTPS')
    client=api.Client.__new__(api.Client);client.provider=api.read_config()['providers'][api.PROVIDER]
    client.base=identity['endpoint'];client.token=keys[0];client.opener=api.request.build_opener(api.NoRedirect)
    return client,identity

def run(job,dry_run=False):
    root=Path(job.get('project_root','.'));api=adapter(root)
    target=Path(job['task_dir']);ledger=target/'dolasd-tasks.jsonl';operation=job['operation']
    if operation=='submit' and ledger.exists():return {'status':'existing_attempt','state':api.ledger_state(ledger),'new_submissions':0}
    if operation=='submit':
        number=int(job['retry_number']);limit=int(job.get('max_retries',3))
        if not 1<=number<=limit:raise RuntimeError('Cumulative retry limit reached')
        fields,refs=source_request(api,Path(job['source_task_dir']))
        if dry_run:return {'dry_run':True,'model':fields['model'],'duration':fields['duration'],'size':fields['size'],'prompt_sha256':hashlib.sha256(fields['prompt'].encode()).hexdigest(),'reference_sha256s':[r['sha256'] for r in refs],'retry_number':number,'new_submissions':0}
        c,binding=connection(job,api,target);report=api.live_check(c,fields['model'],int(fields['duration']),fields['size'])
        status,payload=c.call('models');row=next(r for r in api.model_rows(payload) if r.get('id')==fields['model'])
        if status!=200 or fields['duration'] not in row.get('durations',[]):raise RuntimeError('Original duration is not currently open')
        if isinstance(report.get('concurrency_limit'),int) and int(report.get('active_concurrency') or 0)>=report['concurrency_limit']:raise RuntimeError('No current Dola concurrency slot')
        target.mkdir(parents=True,exist_ok=True);api.save(target/'connection.json',binding)
        pf=target/'prompt.txt';pf.write_text(fields['prompt'],encoding='utf-8')
        api.save(target/'retry-context.json',{'unit':job['unit'],'retry_number':number,'max_retries':limit,'source_task_dir':str(job['source_task_dir']),'prompt_sha256':hashlib.sha256(fields['prompt'].encode()).hexdigest(),'references_sha256':[r['sha256'] for r in refs],'original_inputs_preserved':True})
        args=SimpleNamespace(ledger=ledger,task_dir=target,prompt_file=str(pf),reference=[r['path'] for r in refs],unit=api.parse_unit(job['unit']),model=fields['model'],duration=int(fields['duration']),size=fields['size'],preflight_report=report)
        api.submit(c,args);return {'state':api.ledger_state(ledger),'new_submissions':1}
    if operation=='poll':
        if not ledger.exists():raise RuntimeError('No original submission ledger')
        state=api.ledger_state(ledger)
        if not state.get('task_id'):raise RuntimeError('No task ID; reconcile, do not create another request')
        if dry_run:return {'dry_run':True,'task_id':state['task_id'],'new_submissions':0}
        if state.get('status') in ['succeeded','failed','error','cancelled','canceled']:return {'state':state,'new_submissions':0}
        c,_=connection(job,api,target)
        args=SimpleNamespace(ledger=ledger,task_dir=target,output_dir=Path(job['output_dir']),timeout=0,watch=False,interval=1200)
        api.poll(c,args);return {'state':api.ledger_state(ledger),'new_submissions':0}
    raise ValueError('Operation must be submit or poll')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--job',required=True);parser.add_argument('--dry-run',action='store_true');args=parser.parse_args()
    result=run(json.loads(Path(args.job).read_text(encoding='utf-8-sig')),args.dry_run)
    print(json.dumps(result,ensure_ascii=False))
