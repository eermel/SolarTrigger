import json,zipfile,pytest
from backend import system_maintenance
from backend.system_maintenance import ethernet_status,internet_available,validate_release_zip
def test_eth_carrier(tmp_path):
 e=tmp_path/'eth0';e.mkdir();(e/'carrier').write_text('1');assert ethernet_status(tmp_path)['connected']
def test_wifi_not_eth(tmp_path):
 w=tmp_path/'wlan0';w.mkdir();(w/'wireless').mkdir();(w/'carrier').write_text('1');assert not ethernet_status(tmp_path)['connected']
def _eth_root(tmp_path):
 e=tmp_path/'eth0';e.mkdir();(e/'carrier').write_text('1');return tmp_path

def test_internet_available_uses_debian_endpoint_over_physical_ethernet(tmp_path,monkeypatch):
 root=_eth_root(tmp_path)
 monkeypatch.setattr(
  system_maintenance.socket,
  'getaddrinfo',
  lambda *a,**k:[(2,1,6,'',('199.232.170.132',80))],
 )
 monkeypatch.setattr(
  system_maintenance,
  '_route_interface',
  lambda address:'eth0',
 )
 class Connection:
  def __enter__(self):return self
  def __exit__(self,*args):return False
 monkeypatch.setattr(
  system_maintenance.socket,
  'create_connection',
  lambda *a,**k:Connection(),
 )
 assert internet_available(root=root)

def test_internet_available_rejects_route_over_wifi(tmp_path,monkeypatch):
 root=_eth_root(tmp_path)
 monkeypatch.setattr(
  system_maintenance.socket,
  'getaddrinfo',
  lambda *a,**k:[(2,1,6,'',('199.232.170.132',80))],
 )
 monkeypatch.setattr(
  system_maintenance,
  '_route_interface',
  lambda address:'wlan0',
 )
 monkeypatch.setattr(
  system_maintenance.socket,
  'create_connection',
  lambda *a,**k:pytest.fail('must not connect through Wi-Fi'),
 )
 assert not internet_available(root=root)

def test_internet_available_fails_closed_when_debian_endpoints_unreachable(tmp_path,monkeypatch):
 root=_eth_root(tmp_path)
 monkeypatch.setattr(
  system_maintenance.socket,
  'getaddrinfo',
  lambda *a,**k:[(2,1,6,'',('199.232.170.132',80))],
 )
 monkeypatch.setattr(
  system_maintenance,
  '_route_interface',
  lambda address:'eth0',
 )
 def fail(*a,**k):
  raise TimeoutError
 monkeypatch.setattr(
  system_maintenance.socket,
  'create_connection',
  fail,
 )
 assert not internet_available(root=root)

def test_zip(tmp_path):
 p=tmp_path/'x.zip'
 with zipfile.ZipFile(p,'w') as z:z.writestr('manifest.json',json.dumps({'package_type':'solartrigger-release','version':'7.3.0'}));z.writestr('payload/flask_app/app.py','x')
 assert validate_release_zip(p)['version']=='7.3.0'
def test_zip_traversal(tmp_path):
 p=tmp_path/'x.zip'
 with zipfile.ZipFile(p,'w') as z:z.writestr('manifest.json',json.dumps({'package_type':'solartrigger-release','version':'7.3.0'}));z.writestr('payload/../../etc/passwd','x')
 with pytest.raises(ValueError):validate_release_zip(p)
