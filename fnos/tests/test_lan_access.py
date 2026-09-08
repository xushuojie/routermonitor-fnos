import sys, unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from web import WebApp

class LanAccessTests(unittest.TestCase):
 def make(self, rows, host=True):
  app=WebApp.__new__(WebApp)
  app.sources=SimpleNamespace(host_network=host,rows={str(i):row for i,row in enumerate(rows)})
  return app
 def test_lan_addresses_real_port_and_container_filter(self):
  rows=[{'name':'eth0','physical':True,'addresses':['10.1.2.3/24','2001:db8::1/64','8.8.8.8/24']},
        {'name':'br0','kind':'bridge','addresses':['192.168.31.20/24']},
        {'name':'bond0','kind':'bond','addresses':['192.168.31.20/24','172.20.1.10/24']},
        {'name':'docker0','kind':'bridge','addresses':['172.17.0.1/16']},
        {'name':'br-123abc','kind':'bridge','addresses':['172.18.0.1/16']},
        {'name':'veth123','kind':'veth','addresses':['192.168.99.1/24']},
        {'name':'eth2','physical':True,'up':False,'addresses':['192.168.1.3/24']},
        {'name':'eth3','physical':True,'addresses':['127.0.0.1/8','169.254.1.2/16','invalid','172.34.0.2/24']}]
  result=self.make(rows).lan_access(18888)
  self.assertEqual(result['port'],18888)
  self.assertEqual(result['addresses'][0]['url'],'http://192.168.31.20:18888')
  self.assertEqual({row['ip'] for row in result['addresses']},{'192.168.31.20','10.1.2.3','172.20.1.10'})
 def test_no_address_has_no_proxy_fallback(self):
  result=self.make([]).lan_access(18199)
  self.assertEqual(result['addresses'],[]);self.assertTrue(result['reason'])
 def test_unconfirmed_host_does_not_advertise_container(self):
  result=self.make([{'name':'eth0','physical':True,'addresses':['172.17.0.2/16']}],host=False).lan_access(18199)
  self.assertEqual(result['addresses'],[])

if __name__=='__main__':unittest.main()
