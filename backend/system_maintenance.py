from collections import deque
from copy import deepcopy
from pathlib import Path
import json, os, socket, subprocess, threading, zipfile
SYS=Path('/sys/class/net'); SYSTEM_HELPER='/usr/local/sbin/solartrigger-system-update'; RELEASE_HELPER='/usr/local/sbin/solartrigger-release-update'; MAX=256*1024*1024
def ethernet_status(root=SYS):
 out=[]
 try: entries=sorted(root.iterdir())
 except OSError: entries=[]
 for p in entries:
  if p.name=='lo' or (p/'wireless').exists(): continue
  try: carrier=(p/'carrier').read_text().strip()=='1'
  except OSError: carrier=False
  out.append({'name':p.name,'carrier':carrier})
 return {'connected':any(x['carrier'] for x in out),'interfaces':out}
def internet_available(timeout=1.5):
 try:
  with socket.create_connection(('1.1.1.1',53),timeout=timeout): return True
 except OSError:return False
def validate_release_zip(path):
 p=Path(path)
 if not p.is_file() or p.stat().st_size>MAX: raise ValueError('Invalid or oversized update package')
 try:z=zipfile.ZipFile(p)
 except zipfile.BadZipFile as e: raise ValueError('Invalid ZIP package') from e
 with z:
  infos=z.infolist()
  if len(infos)>10000 or sum(i.file_size for i in infos)>768*1024*1024: raise ValueError('Update package is too large')
  for i in infos:
   q=Path(i.filename)
   if q.is_absolute() or '..' in q.parts or '\\' in i.filename: raise ValueError('Unsafe ZIP path')
   if ((i.external_attr>>16)&0o170000)==0o120000: raise ValueError('ZIP symlinks are not allowed')
  try:m=json.loads(z.read('manifest.json'))
  except Exception as e: raise ValueError('manifest.json is required and must be valid') from e
  v=str(m.get('version') or '').strip()
  if m.get('package_type')!='solartrigger-release' or not v or '/' in v or '\\' in v: raise ValueError('Invalid release manifest')
  if not any(i.filename.startswith('payload/') and not i.is_dir() for i in infos): raise ValueError('payload/ is empty')
 return m
class Job:
 def __init__(self): self.lock=threading.RLock();self.running=False;self.kind=None;self.status='idle';self.error=None;self.logs=deque(maxlen=1000)
 def snapshot(self):
  with self.lock:return deepcopy({'running':self.running,'kind':self.kind,'status':self.status,'error':self.error,'logs':list(self.logs)})
 def start(self,kind,cmd):
  with self.lock:
   if self.running:raise RuntimeError('Maintenance operation already running')
   self.running=True;self.kind=kind;self.status='running';self.error=None;self.logs.clear()
  threading.Thread(target=self._run,args=(cmd,),daemon=True).start()
 def _run(self,cmd):
  try:
   p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
   for line in p.stdout or (): self.logs.append(line.rstrip())
   if p.wait(): raise RuntimeError('Maintenance helper failed')
   self.status='success'
  except Exception as e:self.status='failed';self.error=str(e);self.logs.append('FAILED: '+str(e))
  finally:self.running=False
JOB=Job()
