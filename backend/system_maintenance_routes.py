from pathlib import Path
import os,tempfile
from flask import jsonify,request
from backend.system_maintenance import JOB,SYSTEM_HELPER,RELEASE_HELPER,ethernet_status,internet_available,validate_release_zip
def register_system_maintenance_routes(app,trigger_snapshot):
 def busy():
  s=trigger_snapshot() or {};return bool(s.get('running') or any((r or {}).get('running') for r in (s.get('rigs') or {}).values()))
 @app.get('/api/system/maintenance/status')
 def status():
  e=ethernet_status();d=JOB.snapshot();d['ethernet']=e;d['internet']=internet_available() if e['connected'] else False;return jsonify(d)
 def apt(kind,action):
  if busy():return jsonify(error='Trigger is running'),409
  if not ethernet_status()['connected']:return jsonify(error='Physical Ethernet link is required'),409
  if not internet_available():return jsonify(error='Internet is unavailable through Ethernet'),409
  try:JOB.start(kind,['sudo','-n',SYSTEM_HELPER,action])
  except RuntimeError as e:return jsonify(error=str(e)),409
  return jsonify(status='started'),202
 @app.post('/api/system/maintenance/check-updates')
 def check():return apt('apt-check','check')
 @app.post('/api/system/maintenance/update-system')
 def upgrade():return apt('apt-upgrade','upgrade')
 @app.post('/api/system/maintenance/upload-release')
 def upload():
  if busy() or JOB.snapshot()['running']:return jsonify(error='System is busy'),409
  f=request.files.get('file')
  if not f or not f.filename.lower().endswith('.zip'):return jsonify(error='Select a SolarTrigger ZIP package'),400
  fd,name=tempfile.mkstemp(prefix='solartrigger-',suffix='.zip');os.close(fd);p=Path(name)
  try:
   f.save(p);m=validate_release_zip(p);q=p.with_name('solartrigger-'+str(m['version'])+'-'+p.name+'.zip');p.replace(q);return jsonify(status='validated',version=m['version'],upload_token=q.name)
  except ValueError as e:p.unlink(missing_ok=True);return jsonify(error=str(e)),400
 @app.post('/api/system/maintenance/install-release')
 def install():
  if busy():return jsonify(error='Trigger is running'),409
  token=str((request.get_json(silent=True) or {}).get('upload_token') or '')
  if not token or Path(token).name!=token:return jsonify(error='Invalid upload token'),400
  p=Path('/tmp')/token
  try:validate_release_zip(p);JOB.start('release-install',['sudo','-n',RELEASE_HELPER,'install',str(p)])
  except (ValueError,RuntimeError) as e:return jsonify(error=str(e)),400
  return jsonify(status='started'),202
 @app.post('/api/system/maintenance/rollback-release')
 def rollback():
  if busy():return jsonify(error='Trigger is running'),409
  try:JOB.start('release-rollback',['sudo','-n',RELEASE_HELPER,'rollback'])
  except RuntimeError as e:return jsonify(error=str(e)),409
  return jsonify(status='started'),202
