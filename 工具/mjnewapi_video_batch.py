#!/usr/bin/env python3
"""MJNewAPI adapter: live preflight, ordered uploads, one-shot submit, poll and verify.

Credentials stay in the configured source file. POST creation is never retried
automatically; a submitting/unknown ledger entry blocks another submission.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time
from urllib import request, error, parse
import uuid

from canvas_video_batch import unit_section, video_prompt, mount_tokens, resolve_mount, parse_units
from video_provider_catalog import read_config, token_for, model_rows

ROOT=Path(__file__).resolve().parent.parent
PROVIDER='mjnewapi_video'

def now(): return datetime.now(timezone.utc).isoformat()
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temp,path)
def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def append(path,row):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8',newline='\n') as f:
        f.write(json.dumps({'at':now(),'platform':PROVIDER,**row},ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())
def ledger_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None

class Client:
    def __init__(self):
        self.provider=read_config()['providers'][PROVIDER]
        if self.provider.get('disabled') or self.provider.get('enabled') is False: raise RuntimeError('MJNewAPI渠道停用')
        self.base=self.provider['base_url'].rstrip('/')
        self.token=token_for(self.provider)
        if not self.token: raise RuntimeError('MJNewAPI凭据不可用')
        self.opener=request.build_opener(NoRedirect)
    def clean(self,value):
        raw=json.dumps(value,ensure_ascii=False) if not isinstance(value,str) else value
        return raw.replace(self.token,'[REDACTED]')
    def endpoint(self,name,task_id=''):
        verb,path=self.provider['endpoints'][name].split(' ',1)
        path=path.replace('{task_id}',parse.quote(str(task_id),safe=''))
        return verb,self.base+path if path.startswith('/') else path
    def call(self,name,payload=None,blob=None,content_type=None,task_id='',timeout=30):
        method,url=self.endpoint(name,task_id)
        headers={'Authorization':'Bearer '+self.token,'Accept':'application/json','User-Agent':'ShortDramaMJ/1.0'}
        body=blob
        if payload is not None:
            body=json.dumps(payload,ensure_ascii=False).encode('utf-8');content_type='application/json; charset=utf-8'
        if content_type:headers['Content-Type']=content_type
        try:response=self.opener.open(request.Request(url,data=body,headers=headers,method=method),timeout=timeout)
        except error.HTTPError as exc:response=exc
        with response:
            status=response.code
            raw=response.read(8_000_000).decode('utf-8','replace')
        try:data=json.loads(self.clean(raw))
        except ValueError:data={'message':self.clean(raw)[:1200]}
        return status,data

def unwrap(data):
    return data['data'] if isinstance(data,dict) and isinstance(data.get('data'),dict) else data

def local_inputs(args):
    story=Path(args.story_root).resolve()
    markdown=(story/args.prompt_file).read_text(encoding='utf-8')
    if not re.search(r'(?m)^fenjing_ready:\s*true\s*$',markdown):raise RuntimeError('fenjing_ready必须为true')
    result=[]
    for unit in parse_units(args.units):
        section=unit_section(markdown,unit);prompt=video_prompt(section,unit)
        tokens=mount_tokens(section,unit);refs=[]
        for i,token in enumerate(tokens,1):
            exact=re.search(r'（([^（）]+\.(?:png|jpg|jpeg|webp))）',token,re.I)
            path=(story/'资产图'/exact[1]).resolve() if exact else resolve_mount(story,token,unit).resolve()
            if not path.is_relative_to((story/'资产图').resolve()) or '历史版本' in path.parts:raise RuntimeError('挂载落在当前资产范围之外')
            if not path.is_file() or not 0<path.stat().st_size<=20*1024*1024:raise RuntimeError('素材不存在或超过20MB: '+path.name)
            head=path.read_bytes()[:24]; size=struct.unpack('>II',head[16:24]) if head[:8]==b'\x89PNG\r\n\x1a\n' else None
            refs.append({'index':i,'path':str(path),'sha256':sha(path),'bytes':path.stat().st_size,'dimensions':size})
        duration=int(re.search(r'(?m)^生成参数：\s*(\d+)秒',section)[1])
        ratio=re.search(r'(?m)^生成参数：[^\n]*?(\d+:\d+)',section)[1]
        indexes=set(map(int,re.findall(r'图片(\d+)',prompt)))
        if indexes!=set(range(1,len(refs)+1)):raise RuntimeError('图片编号与挂载不一致')
        result.append({'unit':unit,'prompt':prompt,'prompt_sha256':hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                       'duration':duration,'aspect_ratio':ratio,'resolution':args.resolution,'references':refs})
    return result

def supports_duration(spec,duration):
    if spec.get('values') is not None:
        return duration in spec['values']
    return spec.get('min') is not None and spec.get('max') is not None and spec['min']<=duration<=spec['max']

def item_quote(price,item):
    mode=price.get('task_billing_mode')
    if mode not in ['per_call','per_second']:raise RuntimeError('实时计价模式未知，不能套用历史预算')
    rate=Decimal(str(price['model_price']))
    if not rate.is_finite() or rate<0:raise RuntimeError('实时价格无效')
    quantity=item['duration'] if mode=='per_second' else 1
    return {'billing_mode':mode,'unit_price_yuan':float(rate),'billable_quantity':quantity,'budget_yuan':float(rate*Decimal(str(quantity)))}

def output_geometry_matches(width,height,resolution,aspect_ratio):
    if resolution not in ['480p','720p']:return False
    short_edge=int(resolution[:-1])
    if min(width,height)!=short_edge:return False
    ratio_width,ratio_height=map(int,aspect_ratio.split(':'))
    if ratio_width<=0 or ratio_height<=0:return False
    if (ratio_width<ratio_height and width>=height) or (ratio_width>ratio_height and width<=height):return False
    expected_long=short_edge*max(ratio_width,ratio_height)/min(ratio_width,ratio_height)
    # 480p may align the long edge to a 32-pixel block; preserve native pixels.
    tolerance=16 if resolution=='480p' else 0
    return abs(max(width,height)-expected_long)<=tolerance

def live_preflight(client,args,items):
    status,data=client.call('models')
    if status!=200:raise RuntimeError(f'模型目录HTTP {status}: '+client.clean(data)[:500])
    model=next((m for m in model_rows(data) if isinstance(m,dict) and m.get('id')==args.model),None)
    if not model:raise RuntimeError('实时目录没有指定完整模型名')
    status,data=client.call('pricing')
    if status!=200:raise RuntimeError(f'价格目录HTTP {status}')
    price=next((m for m in model_rows(data) if isinstance(m,dict) and m.get('model_name')==args.model),None)
    if not price:raise RuntimeError('未获得指定模型实时计价')
    cap=model.get('video_capabilities') or price.get('video_capabilities') or {}
    for item in items:
        if not supports_duration(cap.get('duration',{}),item['duration']):raise RuntimeError('模型时长不匹配')
        if item['aspect_ratio'] not in cap.get('aspect_ratios',[]):raise RuntimeError('模型画幅不匹配')
        if item['resolution'] not in cap.get('resolutions',[]):raise RuntimeError('模型分辨率不匹配')
        limits=cap.get('references',{})
        if not limits.get('min_images',0)<=len(item['references'])<=limits.get('max_images',0):raise RuntimeError('参考图数量超限')
    quotes=[{'unit':item['unit'],**item_quote(price,item)} for item in items]
    budget=float(sum(Decimal(str(quote['budget_yuan'])) for quote in quotes))
    report={'checked_at':now(),'provider':PROVIDER,'model':args.model,'catalog_model':model,'catalog_price':price,
            'unit_count':len(items),'budget_yuan':budget,'unit_quotes':quotes,'pricing_basis':'平台公开计价模式及单价；按秒模型乘本单元时长，实际扣费以任务账单为准',
            'items':[{k:v for k,v in item.items() if k!='prompt'} for item in items]}
    save(args.task_dir/'preflight.json',report)
    print(json.dumps({'preflight':'passed','model':args.model,'units':[i['unit'] for i in items],
                      'duration':[i['duration'] for i in items],'resolution':args.resolution,'billing_mode':price['task_billing_mode'],'budget_yuan':budget,
                      'reference_counts':[len(i['references']) for i in items]},ensure_ascii=False),flush=True)
    return report

def upload(client,args,items):
    cache_path=args.task_dir/'materials.json'
    cache=read(cache_path) if cache_path.exists() else {}
    for item in items:
        for ref in item['references']:
            digest=ref['sha256']
            if digest in cache:continue
            path=Path(ref['path']); boundary='mj-'+uuid.uuid4().hex
            prefix=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="reference-{ref["index"]}{path.suffix}"\r\nContent-Type: {mimetypes.guess_type(path.name)[0] or "application/octet-stream"}\r\n\r\n').encode('ascii')
            blob=prefix+path.read_bytes()+f'\r\n--{boundary}--\r\n'.encode('ascii')
            status,data=client.call('materials',blob=blob,content_type='multipart/form-data; boundary='+boundary,timeout=150)
            if status not in [200,201]:raise RuntimeError(f'素材{ref["index"]}上传HTTP {status}: '+client.clean(data)[:600])
            material=unwrap(data)
            url=material.get('url') or material.get('content_url')
            if not url:raise RuntimeError('上传响应缺少素材URL: '+client.clean(material)[:500])
            url=parse.urljoin(client.base,url)
            if parse.urlsplit(url).scheme!='https':raise RuntimeError('素材返回非HTTPS地址')
            cache[digest]={'id':material.get('id'),'url':url,'uploaded_at':now(),'source_sha256':digest}
            save(cache_path,cache)
            print(json.dumps({'uploaded_reference':ref['index'],'unit':item['unit'],'bytes':ref['bytes']},ensure_ascii=False),flush=True)
    return cache

def submit(client,args):
    # Any prior creation attempt is a hard barrier; this adapter never auto-retries a POST.
    if ledger_rows(args.ledger):raise RuntimeError('账本已有提交记录，请先按任务号对账，禁止重复POST')
    items=local_inputs(args);report=live_preflight(client,args,items)
    if args.confirm_cost is None or abs(report['budget_yuan']-args.confirm_cost)>0.000001:raise RuntimeError('实时预算与本批确认成本不一致')
    cache_path=args.task_dir/'materials.json'
    if not cache_path.exists():raise RuntimeError('尚未上传素材')
    cache=read(cache_path)
    for item in items:
        urls=[cache[r['sha256']]['url'] for r in item['references']]
        body={k:item[k] for k in ['prompt','duration','aspect_ratio','resolution']}
        body.update({'model':args.model,'images':urls})
        save(args.task_dir/f'unit{item["unit"]}-request.json',body)
        attempt=str(uuid.uuid4())
        row={'unit':item['unit'],'model':args.model,'attempt_id':attempt,'duration':item['duration'],
             'resolution':item['resolution'],'aspect_ratio':item['aspect_ratio'],'prompt_sha256':item['prompt_sha256'],
             'reference_sha256s':[r['sha256'] for r in item['references']],**item_quote(report['catalog_price'],item)}
        append(args.ledger,{**row,'status':'submitting'})
        try:
            status,data=client.call('submit',payload=body,timeout=int(client.provider.get('submit_timeout_seconds',600)))
        except Exception as exc:
            append(args.ledger,{**row,'status':'unknown_outcome','error':client.clean(str(exc))[:1000]})
            raise RuntimeError('视频创建结果不明，已停止，不能重投: '+client.clean(str(exc))) from None
        save(args.task_dir/f'unit{item["unit"]}-create-response.json',data)
        if status not in [200,201,202]:
            state='submit_failed' if 400<=status<500 else 'unknown_outcome'
            append(args.ledger,{**row,'status':state,'http_status':status,'error':client.clean(data)[:1200]})
            raise RuntimeError(f'视频创建HTTP {status}: '+client.clean(data)[:600])
        task=unwrap(data); task_id=task.get('id') or task.get('task_id')
        if not task_id:
            append(args.ledger,{**row,'status':'unknown_outcome','error':'成功响应缺少任务号'})
            raise RuntimeError('响应没有任务号，不能重投')
        append(args.ledger,{**row,'task_id':str(task_id),'status':task.get('status','queued'),'progress':task.get('progress')})
        print(json.dumps({'submitted_unit':item['unit'],'task_id':task_id,'status':task.get('status','queued')},ensure_ascii=False),flush=True)

def download(client,task_id,target):
    _,url=client.endpoint('download',task_id)
    for _ in range(5):
        headers={'User-Agent':'ShortDramaMJ/1.0'}
        if parse.urlsplit(url).netloc==parse.urlsplit(client.base).netloc:headers['Authorization']='Bearer '+client.token
        try:resp=client.opener.open(request.Request(url,headers=headers),timeout=150)
        except error.HTTPError as exc:resp=exc
        with resp:
            if resp.code in [301,302,303,307,308]:
                url=parse.urljoin(url,resp.headers['Location'])
                if parse.urlsplit(url).scheme!='https':raise RuntimeError('下载重定向不是HTTPS')
                continue
            if resp.code!=200:raise RuntimeError('下载HTTP '+str(resp.code))
            if 'json' in resp.headers.get('Content-Type',''):
                data=json.load(resp); url=unwrap(data).get('url') or unwrap(data).get('video_url')
                if not url or parse.urlsplit(url).scheme!='https':raise RuntimeError('下载未返回视频或有效地址')
                continue
            with target.open('wb') as f:shutil.copyfileobj(resp,f,1024*1024)
            return
    raise RuntimeError('下载重定向过多')

def tool(binary):
    local=ROOT/(binary+'.exe')
    found=str(local) if local.is_file() else shutil.which(binary)
    if not found:raise RuntimeError('缺少'+binary)
    return found

def probe(path):
    r=subprocess.run([tool('ffprobe'),'-v','error','-show_streams','-show_format','-of','json',str(path)],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=45)
    if r.returncode:raise RuntimeError('ffprobe读取失败')
    return json.loads(r.stdout.decode('utf-8'))

def verify_and_collect(client,args,row):
    unit=row['unit'];out=(Path(args.story_root).resolve()/args.prompt_file).parent/'成片'
    out.mkdir(parents=True,exist_ok=True)
    target=out/f'{unit}单元-mjnewapi-{args.version}.mp4'
    original=out/f'{unit}单元-mjnewapi-{args.version}-原片.mp4'
    if not target.exists() and not original.exists():
        temp=out/f'{unit}单元-mjnewapi-{args.version}.mp4.part'
        download(client,row['task_id'],temp)
        if not temp.stat().st_size:raise RuntimeError('视频下载为空')
        raw=probe(temp)
        video=next((s for s in raw['streams'] if s['codec_type']=='video'),{})
        audio=next((s for s in raw['streams'] if s['codec_type']=='audio'),{})
        os.replace(temp,target if video.get('codec_name')=='h264' and audio.get('codec_name')=='aac' else original)
    if not target.exists():
        raw=probe(original)
        if not any(s['codec_type']=='audio' for s in raw['streams']):raise RuntimeError('原片没有音轨，不能冒充带对白成片')
        r=subprocess.run([tool('ffmpeg'),'-nostdin','-v','error','-n','-i',str(original),'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(target)],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=180)
        if r.returncode:raise RuntimeError('视频转码失败')
    info=probe(target)
    v=next(s for s in info['streams'] if s['codec_type']=='video')
    a=next((s for s in info['streams'] if s['codec_type']=='audio'),{})
    duration=float(info['format']['duration'])
    if v['codec_name']!='h264' or a.get('codec_name')!='aac':raise RuntimeError('成片不是H.264/AAC')
    if not output_geometry_matches(v['width'],v['height'],row.get('resolution','720p'),row.get('aspect_ratio','9:16')) or abs(duration-row['duration'])>0.75:raise RuntimeError('成片尺寸或时长与本批参数不符')
    r=subprocess.run([tool('ffmpeg'),'-nostdin','-v','error','-xerror','-err_detect','explode','-i',str(target),'-f','null','-'],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=150)
    if r.returncode:raise RuntimeError('成片完整解码失败')
    result={'unit':unit,'task_id':row['task_id'],'status':'succeeded','output_path':str(target),'video_codec':v['codec_name'],
            'audio_codec':a['codec_name'],'width':v['width'],'height':v['height'],'duration':duration,
            'requested_resolution':row.get('resolution','720p'),'requested_aspect_ratio':row.get('aspect_ratio','9:16'),
            'frame_rate':v.get('avg_frame_rate'),'bytes':target.stat().st_size,'sha256':sha(target),'full_decode':True}
    save(args.task_dir/f'unit{unit}-technical-report.json',result)
    append(args.ledger,result)
    print(json.dumps(result,ensure_ascii=False),flush=True)

def poll(client,args):
    deadline=time.monotonic()+args.timeout
    while True:
        rows=ledger_rows(args.ledger); latest={}
        for row in rows:
            if row.get('task_id'):latest[row['unit']]={**latest.get(row['unit'],{}),**row}
        if not latest:raise RuntimeError('账本没有可查询任务号，不创建新任务')
        pending=0
        for unit,row in latest.items():
            if row['status']=='succeeded':continue
            status,data=client.call('detail',task_id=row['task_id'])
            if status!=200:raise RuntimeError(f'查询HTTP {status}: '+client.clean(data)[:600])
            save(args.task_dir/f'unit{unit}-last-response.json',data)
            task=unwrap(data);state=str(task.get('status') or task.get('phase') or 'unknown').lower()
            progress=task.get('progress')
            summary={'unit':unit,'task_id':row['task_id'],'status':state,'progress':progress}
            if state!=row.get('status') or progress!=row.get('progress'):append(args.ledger,summary)
            print(json.dumps(summary,ensure_ascii=False),flush=True)
            if state in ['completed','succeeded','success']:
                verify_and_collect(client,args,row)
            elif state in ['failed','error','canceled','cancelled']:
                message=client.clean(task.get('error') or task.get('message') or task.get('fail_reason') or state)
                append(args.ledger,{**summary,'error':message[:1500]})
                raise RuntimeError('平台任务终止: '+message[:800])
            else:pending+=1
        if not pending:return 0
        if not args.watch or time.monotonic()>=deadline:return 3
        time.sleep(args.interval)

def recover(client,args):
    selected=parse_units(args.units)
    if len(selected)!=1 or not args.task_id:raise RuntimeError('recover需要一个单元和明确的--task-id，不创建任务')
    unit=selected[0]
    rows=[r for r in ledger_rows(args.ledger) if r.get('unit')==unit]
    if not rows:raise RuntimeError('缺少本地原始提交记录')
    known=[r['task_id'] for r in rows if r.get('task_id')]
    if known and any(str(x)!=str(args.task_id) for x in known):raise RuntimeError('账本已有不同任务号，请先人工核对')
    status,data=client.call('detail',task_id=args.task_id)
    if status!=200:raise RuntimeError(f'任务号查询HTTP {status}: '+client.clean(data)[:500])
    task=unwrap(data)
    if task.get('model') and task['model']!=rows[0]['model']:raise RuntimeError('任务返回的模型与原提交不同，暂不接管')
    base=next(r for r in rows if r.get('prompt_sha256') and r.get('duration'))
    save(args.task_dir/f'unit{unit}-recovered-response.json',data)
    append(args.ledger,{**{k:v for k,v in base.items() if k not in ['at','status','error']},
                       'task_id':str(args.task_id),'status':task.get('status','reconciled'),
                       'recovered':True,'recovery_source':'user_provided_task_id'})
    print(json.dumps({'unit':unit,'task_id':str(args.task_id),'recovered':True,'status':task.get('status')},ensure_ascii=False),flush=True)
    return poll(client,args)

def main():
    p=argparse.ArgumentParser(description='MJNewAPI视频生成与技术交付')
    p.add_argument('mode',choices=['preflight','upload','submit','poll','recover'])
    p.add_argument('--story-root',required=True);p.add_argument('--prompt-file',default='分镜-15秒/分镜提示词.md')
    p.add_argument('--units',default='1');p.add_argument('--model');p.add_argument('--resolution',default='720p')
    p.add_argument('--task-dir',required=True);p.add_argument('--ledger');p.add_argument('--confirm-cost',type=float)
    p.add_argument('--task-id',help='仅recover使用已有平台任务号，绝不重发创建请求')
    p.add_argument('--version',default='v1');p.add_argument('--watch',action='store_true');p.add_argument('--interval',type=int,default=30);p.add_argument('--timeout',type=int,default=3600)
    args=p.parse_args();args.task_dir=Path(args.task_dir).resolve();args.task_dir.mkdir(parents=True,exist_ok=True)
    args.ledger=Path(args.ledger).resolve() if args.ledger else args.task_dir/'mjnewapi-tasks.jsonl'
    client=Client();args.model=args.model or client.provider['default_model']
    try:
        if args.mode=='recover':return recover(client,args)
        if args.mode=='poll':return poll(client,args)
        if args.mode=='submit':submit(client,args);return 0
        items=local_inputs(args);live_preflight(client,args,items)
        if args.mode=='upload':upload(client,args,items)
        return 0
    except Exception as exc:
        print('ERROR '+client.clean(str(exc)),flush=True);return 1

if __name__=='__main__':raise SystemExit(main())
