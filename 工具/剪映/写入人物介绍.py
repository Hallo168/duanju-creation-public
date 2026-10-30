# -*- coding: utf-8 -*-
"""按方案复用142人物介绍模板；时间、大小、位置供用户在剪映细调。"""
import argparse,copy,json,re,shutil,sys,uuid
from datetime import datetime
from pathlib import Path
from 环境 import ROOT,找草稿,草稿文件,读草稿JSON,写草稿JSON,是加密草稿,展开模板资源
UUID_RE=re.compile(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')
def new_id():return str(uuid.uuid4()).upper()
def uuid_values(value):
 if isinstance(value,dict):
  for k,v in value.items():
   if k!='name':yield from uuid_values(v)
 elif isinstance(value,list):
  for v in value:yield from uuid_values(v)
 elif isinstance(value,str) and UUID_RE.fullmatch(value):yield value

def remap(value,ids):
 if isinstance(value,dict):return {k:remap(v,ids) for k,v in value.items()}
 if isinstance(value,list):return [remap(v,ids) for v in value]
 return ids.get(value,value) if isinstance(value,str) else value

def text_replace(material,text):
 for field in ('content','base_content'):
  if material.get(field):
   content=json.loads(material[field]);content['text']=text
   for style in content.get('styles',[]):style['range']=[0,len(text)]
   material[field]=json.dumps(content,ensure_ascii=False,separators=(',',':'))

def prepare_dependencies(data,draft):
 for template in data.get('materials',{}).get('text_templates',[]):
  package=Path(template.get('path',''));depends=package/'depends'
  if not depends.is_file():continue
  original=depends.read_text(encoding='utf-8-sig')
  def resolve(match):
   value=match.group(2).replace('\\','/')
   if '/Cache/' in value:value='./工具/剪映/模板/人物介绍-资源/'+value.split('/Cache/',1)[1]
   resolved=展开模板资源(value)
   if resolved!=value and not Path(resolved).exists():raise FileNotFoundError(resolved)
   return match.group(1)+resolved
  updated=re.sub(r'(?m)^(\d+\\path=)([^\r\n]+)',resolve,original)
  if updated==original:continue
  target=draft/'.houqi_resources'/package.name
  shutil.copytree(package,target,dirs_exist_ok=True)
  (target/'depends').write_text(updated,encoding='utf-8')
  template['path']=target.as_posix()

def main():
 ap=argparse.ArgumentParser();ap.add_argument('plan');ap.add_argument('--dry-run',action='store_true');args=ap.parse_args()
 spec=json.loads(Path(args.plan).read_text(encoding='utf-8-sig'))
 tp=Path(spec['template']);tp=tp if tp.is_absolute() else ROOT/tp
 template=展开模板资源(json.loads(tp.read_text(encoding='utf-8-sig')))
 for resource in template['materials']['text_templates'][0].get('resources',[]):
  if resource.get('path') and not Path(resource['path']).exists():raise FileNotFoundError(resource['path'])
 draft=找草稿(spec['draft']);paths=草稿文件(draft);encrypted=是加密草稿(draft)
 outputs=[]
 for file in paths:
  data=读草稿JSON(file)
  if any(t.get('name')==spec['track_name'] for t in data['tracks']):raise ValueError('人物介绍轨已存在，请在剪映手调或明确替换；不重复叠加')
  track=copy.deepcopy(template['track']);track.update(id=new_id(),name=spec['track_name'],is_default_name=False,segments=[])
  render_track=max((t.get('segments',[{}])[0].get('track_render_index',0) for t in data['tracks'] if t.get('segments')),default=0)+1
  for character in sorted(spec['characters'],key=lambda c:c['start_us']):
   payload={'segment':copy.deepcopy(template['segment']),'materials':copy.deepcopy(template['materials'])}
   ids={v:new_id() for v in uuid_values(payload)};payload=remap(payload,ids)
   segment=payload['segment'];materials=payload['materials'];segment['target_timerange']={'start':int(character['start_us']),'duration':int(template['duration_us'])};segment['track_render_index']=render_track
   if segment['target_timerange']['start']+template['duration_us']>data['duration']:raise ValueError('介绍超出草稿时长')
   text_template=materials['text_templates'][0];lookup={m['id']:m for m in materials['texts']}
   for info,text in zip(text_template['text_info_resources'],(character['name'],character['identity'])):text_replace(lookup[info['text_material_id']],text)
   for collection,values in materials.items():data['materials'].setdefault(collection,[]).extend(values)
   track['segments'].append(segment)
  data['tracks'].append(track);outputs.append((file,data))
 result={'draft':str(draft),'dry_run':args.dry_run,'encrypted_draft':encrypted,'track_name':spec['track_name'],'card_count':len(spec['characters']),'duration_s':template['duration_us']/1e6,'characters':spec['characters'],'content_files':[str(p) for p in paths]}
 if not args.dry_run:
  for file,data in outputs:prepare_dependencies(data,draft)
  backup=draft/'.backup'/('人物介绍前_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
  for file in paths:
   dest=backup/file.relative_to(draft);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,dest)
  (backup/'applied_plan.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
  for file,data in outputs:写草稿JSON(file,data,加密=encrypted)
  result['backup_dir']=str(backup)
 print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
