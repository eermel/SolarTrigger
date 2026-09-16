import json,zipfile,pytest
from backend.system_maintenance import ethernet_status,validate_release_zip
def test_eth_carrier(tmp_path):
 e=tmp_path/'eth0';e.mkdir();(e/'carrier').write_text('1');assert ethernet_status(tmp_path)['connected']
def test_wifi_not_eth(tmp_path):
 w=tmp_path/'wlan0';w.mkdir();(w/'wireless').mkdir();(w/'carrier').write_text('1');assert not ethernet_status(tmp_path)['connected']
def test_zip(tmp_path):
 p=tmp_path/'x.zip'
 with zipfile.ZipFile(p,'w') as z:z.writestr('manifest.json',json.dumps({'package_type':'solartrigger-release','version':'7.3.0'}));z.writestr('payload/flask_app/app.py','x')
 assert validate_release_zip(p)['version']=='7.3.0'
def test_zip_traversal(tmp_path):
 p=tmp_path/'x.zip'
 with zipfile.ZipFile(p,'w') as z:z.writestr('manifest.json',json.dumps({'package_type':'solartrigger-release','version':'7.3.0'}));z.writestr('payload/../../etc/passwd','x')
 with pytest.raises(ValueError):validate_release_zip(p)
