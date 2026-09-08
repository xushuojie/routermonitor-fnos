import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import api, service
from device_tokens import DeviceTokens, validate
from network_sources import SelectionError
from test_integration import WebApp, Sources, Metrics, Server

class TokenTests(unittest.TestCase):
 def test_validation(self):
  for value in ['', None, 123, 'a b', '中文', 'a\nb', 'a'*513, 'replace-with-a-long-random-token']:
   with self.assertRaises(SelectionError): validate(value)
  for value in ['x', 'a'*512, 'abc!@#$%^&*()_+-=']:
   self.assertEqual(validate(value), value)

 def test_persistence_and_failed_write(self):
  with tempfile.TemporaryDirectory() as folder:
   store=DeviceTokens(folder, 'original'); store.save('custom')
   self.assertEqual(DeviceTokens(folder, 'original').current(), 'custom')
   if os.name == 'posix': self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
   with patch('device_tokens.os.replace', side_effect=OSError('test failure')):
    with self.assertRaises(OSError): store.save('failed')
   self.assertEqual(store.current(), 'custom')
   self.assertEqual(DeviceTokens(folder, 'original').current(), 'custom')
   self.assertEqual(list(Path(folder).glob('.device-token-*')), [])

 def test_startup_override(self):
  with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'NAS_STATUS_HISTORY_DB': str(Path(folder)/'traffic.sqlite3'), 'NAS_STATUS_TOKEN': 'environment-old', 'NAS_STATUS_TOKEN_FILE': str(Path(folder)/'external')}):
   Path(folder, 'external').write_text('external-old')
   DeviceTokens(folder, 'original').save('custom')
   self.assertEqual(api.resolve_token(), 'custom')
   self.assertEqual(service.prepare_credentials(), Path(folder))
   self.assertEqual(api.resolve_token(), 'custom')
   self.assertEqual(Path(folder, 'external').read_text(), 'external-old')

 def test_http_rotation_and_access_control(self):
  with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'NAS_STATUS_HISTORY_DB': str(Path(folder)/'traffic.sqlite3'), 'NAS_STATUS_ADMIN_PASSWORD': '1'}):
   web=WebApp(Sources(), Metrics(), folder); server=Server(api, web)
   try:
    base={'Host':'nas.example:18199','Origin':'http://nas.example:18199'}
    change={'token':'custom-test!','confirm':'custom-test!'}
    self.assertIn(server.request('/api/device-access','PUT',change,base)[0], [401,403])
    status, headers, body=server.request('/api/login','POST',{'password':'1'},base)
    self.assertEqual(status,200)
    auth={**base,'Cookie':headers['Set-Cookie'].split(';')[0],'X-CSRF-Token':json.loads(body)['csrf']}
    self.assertEqual(server.request('/api/device-access','PUT',change,{**auth,'X-CSRF-Token':'wrong'})[0],403)
    for bad in [{'token':'x','confirm':'y'},{'token':'','confirm':''},{'token':'a b','confirm':'a b'}]:
     self.assertEqual(server.request('/api/device-access','PUT',bad,auth)[0],400)
    self.assertEqual(web.device_tokens.current(),'device-test-token')
    self.assertEqual(server.request('/api/device-access','PUT',change,auth)[0],200)
    for route in ['/status','/status?v=2','/status?display=1','/net','/net?v=2']:
     self.assertEqual(server.request(route,headers={'Authorization':'Bearer device-test-token'})[0],401)
     self.assertEqual(server.request(route,headers={'Authorization':'Bearer custom-test!'})[0],200)
    self.assertEqual(json.loads(server.request('/api/device-access',headers=auth)[2])['token'],'custom-test!')
    self.assertEqual(server.request('/api/device-access',headers={'Authorization':'Bearer custom-test!'})[0],401)
    self.assertEqual(server.request('/api/login','POST',{'password':'1'},base)[0],200)
    self.assertNotIn(b'custom-test!',server.request('/api/diagnostics',headers=auth)[2])
    self.assertEqual(WebApp(Sources(),Metrics(),folder).device_tokens.current(),'custom-test!')
   finally: server.close()

if __name__ == '__main__': unittest.main()
