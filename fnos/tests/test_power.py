import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
sys.path.insert(0, str(ROOT/'docker'))
import api
import power
from power_collector import PowerReader
from test_integration import Server, SNAPSHOT


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reader = PowerReader(self.root)

    def domain(self, folder, name='package-0', energy=1_000_000):
        path = self.root/'devices/virtual/powercap'/folder
        path.mkdir(parents=True, exist_ok=True)
        for key, value in {'name':name, 'energy_uj':energy, 'max_energy_range_uj':100_000_000_000}.items():
            (path/key).write_text(str(value))
        return path

    def advance(self, path, energy):
        (path/'energy_uj').write_text(str(energy))

    def test_no_sensor_is_unknown_not_zero(self):
        value = self.reader.sample(10)
        self.assertFalse(value['valid']); self.assertIsNone(value['watts'])

    def test_energy_delta_units_and_warmup(self):
        p = self.domain('intel-rapl/intel-rapl:0')
        self.assertFalse(self.reader.sample(10)['valid'])
        self.advance(p, 61_000_000)
        v = self.reader.sample(15)
        self.assertEqual(v['watts'], 12); self.assertEqual(v['scope'], 'cpu_package')

    def test_zero_delta_is_valid_zero(self):
        self.domain('intel-rapl:0'); self.reader.sample(10)
        self.assertEqual(self.reader.sample(15)['watts'], 0)

    def test_platform_preferred_without_adding_package(self):
        p = self.domain('intel-rapl:0'); s = self.domain('intel-rapl:1','psys')
        self.reader.sample(10)
        self.advance(p, 51_000_000); self.advance(s, 151_000_000)
        v = self.reader.sample(15)
        self.assertEqual(v['scope'],'platform');self.assertEqual(v['watts'],30)
        self.assertEqual(v['sources']['platform']['watts'],30)
        self.assertEqual(v['sources']['cpu_package']['watts'],10)

    def test_subdomains_not_double_counted(self):
        p = self.domain('intel-rapl:0')
        c = self.domain('intel-rapl:0/intel-rapl:0:0', 'core')
        self.reader.sample(10)
        self.advance(p, 101_000_000);self.advance(c,51_000_000)
        self.assertEqual(self.reader.sample(15)['watts'],20)

    def test_multiple_packages_sum(self):
        a=self.domain('intel-rapl:0');b=self.domain('intel-rapl:1','package-1')
        self.reader.sample(10);self.advance(a,51_000_000);self.advance(b,101_000_000)
        self.assertEqual(self.reader.sample(15)['watts'],30)

    def test_missing_counter_prevents_partial_total(self):
        a=self.domain('intel-rapl:0');b=self.domain('intel-rapl:1','package-1')
        self.reader.sample(10);self.advance(a,51_000_000);self.advance(b,'invalid')
        self.assertFalse(self.reader.sample(15)['valid'])
        self.advance(a,101_000_000)
        self.assertFalse(self.reader.sample(20)['valid'])

    def test_disabled_package_prevents_partial_total(self):
        self.domain('intel-rapl:0'); b=self.domain('intel-rapl:1','package-1')
        (b/'enabled').write_text('0')
        self.reader.sample(10)
        self.assertFalse(self.reader.sample(15)['valid'])

    def test_msr_mmio_same_domain_not_added(self):
        a=self.domain('intel-rapl:0');b=self.domain('intel-rapl-mmio:0')
        self.reader.sample(10);self.advance(a,51_000_000);self.advance(b,51_000_000)
        self.assertEqual(self.reader.sample(15)['watts'],10)

    def test_disappeared_counter_does_not_shrink_package_total(self):
        a=self.domain('intel-rapl:0');b=self.domain('intel-rapl:1','package-1')
        self.reader.sample(10);(b/'energy_uj').unlink()
        self.advance(a,51_000_000);self.assertFalse(self.reader.sample(15)['valid'])
        self.advance(a,101_000_000);self.assertFalse(self.reader.sample(20)['valid'])

    @unittest.skipUnless(sys.platform=='linux','symlinks')
    def test_class_alias_is_deduplicated(self):
        p=self.domain('intel-rapl/intel-rapl:0')
        (self.root/'class/powercap').mkdir(parents=True)
        (self.root/'class/powercap/intel-rapl:0').symlink_to(p)
        self.reader.sample(10);self.advance(p,51_000_000)
        self.assertEqual(self.reader.sample(15)['watts'],10)

    def test_wrap_is_handled(self):
        p=self.domain('intel-rapl:0',energy=99_999_000_000)
        self.reader.sample(10);self.advance(p,49_000_000)
        self.assertEqual(self.reader.sample(15)['watts'],10)

    def test_reset_does_not_become_power_spike(self):
        p=self.domain('intel-rapl:0',energy=50_000_000_000)
        self.reader.sample(10);self.advance(p,10)
        self.assertFalse(self.reader.sample(15)['valid'])

    def test_long_gap_and_impossible_power_are_rejected(self):
        p=self.domain('intel-rapl:0');self.reader.sample(10)
        self.advance(p,51_000_000);self.assertFalse(self.reader.sample(30)['valid'])
        self.advance(p,50_000_000_000);self.assertFalse(self.reader.sample(35)['valid'])

    def test_small_counter_range_cannot_hide_multiple_wraps(self):
        p=self.domain('intel-rapl:0');(p/'max_energy_range_uj').write_text('100000000')
        self.reader.sample(10);self.advance(p,51_000_000)
        self.assertFalse(self.reader.sample(15)['valid'])

    def test_new_domain_requires_fresh_baseline(self):
        a=self.domain('intel-rapl:0');self.reader.sample(10)
        self.advance(a,51_000_000);self.domain('intel-rapl:1','package-1')
        self.assertFalse(self.reader.sample(15)['valid'])


CPU={'watts':12.5,'valid':True,'source':'powercap','scope':'cpu_package','label':'CPU 封装功率','age_seconds':1}


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'power.json'
        self.patcher=patch.object(power,'SNAPSHOT',str(self.path));self.patcher.start();self.addCleanup(self.patcher.stop)

    def write(self,row=CPU,age=0):
        self.path.write_text(json.dumps({'schema':1,'sampled_at':time.time()-age,'power':row}))

    def test_ups_preferred_including_zero(self):
        for watts in [0,125]:
            v=power.select({'watts':watts,'valid':True,'source':'reported'},CPU)
            self.assertEqual(v['scope'],'ups_output');self.assertEqual(v['watts'],watts)

    def test_unavailable_ups_uses_cpu_without_relabeling(self):
        value=power.select({'watts':None,'valid':False},CPU)
        self.assertEqual(value['scope'],'cpu_package');self.assertEqual(value['watts'],12.5)

    def test_bad_or_missing_cache_is_unavailable(self):
        self.assertFalse(power.host_power()['valid'])
        for data in ['broken','[]','{"schema":1,"sampled_at":null}','{"schema":1,"sampled_at":1,"power":null}']:
            self.path.write_text(data);self.assertFalse(power.host_power()['valid'])

    def test_valid_cache_and_stale_future_timestamp(self):
        self.write();self.assertEqual(power.host_power()['scope'],'cpu_package')
        for age in [16,-100]:
            self.write(age=age);self.assertFalse(power.host_power()['valid'])

    def test_stale_file_and_invalid_readings(self):
        self.write();os.utime(self.path,(time.time()-20,time.time()-20))
        self.assertFalse(power.host_power()['valid'])
        for change in [{'watts':float('nan')},{'watts':True},{'watts':-1},{'scope':'whole_system'},{'source':'tdp_estimate'}]:
            self.write({**CPU,**change});self.assertFalse(power.host_power()['valid'])

    def test_background_sampling_and_expiry(self):
        self.write();monitor=api.UpsMonitor()
        with patch.object(api,'ups_status',return_value={'watts':None,'valid':False,'reason':'no ups'}):
            monitor.sample()
        now=time.monotonic()
        self.assertIsNone(monitor.snapshot(now)['watts'])
        self.assertEqual(monitor.power_snapshot(now)['scope'],'cpu_package')
        with patch.object(power,'host_power',side_effect=AssertionError('HTTP must not read hardware')):
            self.assertFalse(monitor.power_snapshot(now+16)['valid'])

    def test_ups_recovery_restores_priority(self):
        self.write();monitor=api.UpsMonitor()
        with patch.object(api,'ups_status',return_value={'watts':None,'valid':False}):monitor.sample()
        with patch.object(api,'ups_status',return_value={'watts':30,'valid':True,'source':'reported'}):monitor.sample()
        self.assertEqual(monitor.power_snapshot(time.monotonic())['scope'],'ups_output')

    def test_three_sources_remain_independent_and_selectable(self):
        platform={**CPU,'scope':'platform','watts':25}
        fallback={**platform,'sources':{'platform':platform,'cpu_package':CPU}}
        ups={'watts':40,'valid':True,'source':'reported'}
        for mode,expected in [('auto',40),('ups_output',40),('platform',25),('cpu_package',12.5)]:
            result=power.select(ups,fallback,mode)
            self.assertEqual(result['watts'],expected)
            self.assertEqual([row['watts'] for row in result['sources'].values()],[40,25,12.5])

    def test_manual_unavailable_does_not_fall_back(self):
        for mode in ['ups_output','platform']:
            result=power.select({'valid':False},CPU,mode)
            self.assertFalse(result['valid']);self.assertIsNone(result['watts'])
            self.assertEqual(result['scope'],mode)
            self.assertEqual(result['sources']['cpu_package']['watts'],12.5)

    def test_host_reads_both_and_rejects_each_scope_independently(self):
        self.write({**CPU,'sources':{'platform':{**CPU,'scope':'platform','watts':25},'cpu_package':CPU}})
        result=power.host_power()
        self.assertEqual(result['sources']['platform']['watts'],25)
        self.assertEqual(result['sources']['cpu_package']['watts'],12.5)
        self.write({**CPU,'sources':{'platform':{**CPU,'scope':'cpu_package'},'cpu_package':CPU}})
        result=power.host_power()
        self.assertFalse(result['sources']['platform']['valid'])
        self.assertTrue(result['sources']['cpu_package']['valid'])

    def test_background_selected_mode_and_all_readings_expire(self):
        self.write({**CPU,'sources':{'platform':{**CPU,'scope':'platform','watts':25},'cpu_package':CPU}})
        monitor=api.UpsMonitor()
        with patch.object(api,'load_settings',return_value={'power_mode':'cpu_package'}),patch.object(api,'ups_status',return_value={'watts':40,'valid':True,'source':'reported'}):monitor.sample()
        now=time.monotonic()
        result=monitor.power_snapshot(now)
        self.assertEqual(result['watts'],12.5)
        self.assertEqual(result['sources']['platform']['watts'],25)
        stale=monitor.power_snapshot(now+16)
        self.assertFalse(stale['sources']['platform']['valid'])
        self.assertFalse(stale['sources']['cpu_package']['valid'])

    def test_power_setting_migrates_persists_and_rejects_unknown(self):
        import hardware_settings
        old=dict(hardware_settings.DEFAULTS);old.pop('power_mode')
        hardware_settings.settings_path(self.temp.name).write_text(json.dumps(old))
        loaded=hardware_settings.load_settings(self.temp.name)
        self.assertEqual(loaded['power_mode'],'auto')
        loaded['power_mode']='cpu_package'
        hardware_settings.save_settings(loaded,self.temp.name)
        self.assertEqual(hardware_settings.load_settings(self.temp.name)['power_mode'],'cpu_package')
        with self.assertRaises(ValueError):hardware_settings.validate({**loaded,'power_mode':'estimate'})

    def test_display_protocol_preserves_legacy_ups(self):
        import test_integration
        snapshot=copy.deepcopy(SNAPSHOT);snapshot['power']=CPU.copy()
        with patch.object(test_integration.Metrics,'read_snapshot',return_value=snapshot):
            server=Server(api)
            try:
                status,_,body=server.request('/status?display=1&v=2',headers={'Authorization':'Bearer device-test-token'})
                self.assertEqual(status,200);data=json.loads(body)
                self.assertIsNone(data['ups']['watts'])
                self.assertEqual(data['power']['watts'],12.5)
                self.assertEqual(data['power']['scope'],'cpu_package')
            finally:server.close()


if __name__=='__main__':unittest.main()
