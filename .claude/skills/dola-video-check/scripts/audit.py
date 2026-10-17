"""Offline Dola dialogue evidence; no API submissions or automatic verdicts."""
from pathlib import Path
import argparse,json,re,hashlib,subprocess,sys,wave,shutil

def prompt_for(path,unit):
    text=Path(path).read_text(encoding='utf-8-sig')
    if re.search(r'^## 单元\d+',text,re.M):
        match=re.search(r'^## 单元'+str(unit)+r'（.*?(?=^## 单元\d+（|\Z)',text,re.S|re.M)
        if not match:raise ValueError(f'Missing storyboard unit {unit}')
        block=re.search(r'### 单元提示词\s*\n\s*```[^\n]*\n(.*?)\n```',match.group(),re.S)
        if not block:raise ValueError(f'Missing prompt block for unit {unit}')
        return block.group(1).strip()
    return text.strip()

def ffmpeg_for(root):
    sys.path.insert(0,str(root/'工具/剪映'))
    try:
        from 环境 import ffmpeg路径
        return ffmpeg路径()
    except (ImportError,FileNotFoundError):
        value=str(root/'ffmpeg.exe') if (root/'ffmpeg.exe').exists() else shutil.which('ffmpeg')
        if not value:raise RuntimeError('FFmpeg is unavailable')
        return value

def run(job,dry_run=False):
    root=Path(job.get('project_root','.'));out=Path(job['output_dir'])
    model=root/'工具/运行垃圾/deps/sensevoice/model.int8.onnx'
    for file in [model,model.with_name('tokens.txt')]:
        if not file.is_file():raise FileNotFoundError(file)
    ffmpeg=ffmpeg_for(root);prepared=[];seen=set()
    for row in job['units']:
        unit=int(row['unit']);video=Path(row['video']);source=Path(row['prompt_file'])
        if unit in seen:raise ValueError('Duplicate unit mapping')
        seen.add(unit)
        if not video.is_file():raise FileNotFoundError(video)
        prompt=prompt_for(source,unit);vsha=hashlib.sha256(video.read_bytes()).hexdigest();psha=hashlib.sha256(prompt.encode()).hexdigest()
        prepared.append({'unit':unit,'video':str(video),'video_sha256':vsha,'prompt_sha256':psha,'expected':re.findall(r'\{([^{}]+)\}',prompt),'dir':out/f'U{unit:03d}-{vsha[:12]}-{psha[:8]}'})
    if dry_run:return {'dry_run':True,'units':len(prepared),'dependency_model':str(model),'video_api_calls':0}
    sys.path.insert(0,str(root/'工具/运行垃圾/deps/asr-sherpa'))
    import numpy as np
    import sherpa_onnx
    rec=sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(model),tokens=str(model.with_name('tokens.txt')),language='zh',num_threads=4,use_itn=False)
    results=[]
    for row in prepared:
        directory=row.pop('dir');directory.mkdir(parents=True,exist_ok=True);cache=directory/'dialogue.json'
        result=json.loads(cache.read_text(encoding='utf-8')) if cache.exists() else None
        full=[item for item in result.get('actual',[]) if item.get('kind')=='full'] if result else []
        if not full:
            audio=directory/'audio.wav'
            if not audio.exists():subprocess.run([ffmpeg,'-v','error','-n','-i',row['video'],'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(audio)],check=True,capture_output=True)
            with wave.open(str(audio),'rb') as w:rate=w.getframerate();pcm=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).astype(np.float32)/32768
            duration=len(pcm)/rate
            stream=rec.create_stream();stream.accept_waveform(rate,pcm);rec.decode_stream(stream)
            full=[{'kind':'full','start':0,'end':duration,'text':stream.result.text}]
            result={**row,'duration':duration,'audio':str(audio),'actual':full,'method':'local SenseVoice actual audio; execution agent makes verdict'}
            cache.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        result['actual']=full[:1]
        result.pop('frames',None);result.pop('segment_layout',None)
        results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
    out.mkdir(parents=True,exist_ok=True);(out/'inspection.json').write_text(json.dumps({'units':results,'video_api_calls':0},ensure_ascii=False,indent=2),encoding='utf-8')
    return {'units':len(results),'inspection':str(out/'inspection.json'),'video_api_calls':0}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--job',required=True);parser.add_argument('--dry-run',action='store_true');args=parser.parse_args()
    print(json.dumps(run(json.loads(Path(args.job).read_text(encoding='utf-8-sig')),args.dry_run),ensure_ascii=False))
