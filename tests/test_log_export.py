import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from doctorlib.cli import parser,main
from doctorlib import log_export as le
from doctorlib.evidence import sanitize_evidence
from doctorlib.reports import support_summary
from doctorlib.safety import validate_command,validate_log_command

def options(*args):return parser().parse_args(['export-logs']+list(args))
def cp(args,value=None,code=0,out=None,err=''):
    return subprocess.CompletedProcess(args,code,json.dumps(value) if out is None and value is not None else out or '',err)
def pod(name='milvus-0',namespace='ns',restarts=0,sidecar=False,init=False):
    spec={'containers':[{'name':'milvus'}]}
    status={'containerStatuses':[{'name':'milvus','restartCount':restarts}]}
    if sidecar:
        spec['containers'].append({'name':'sidecar'});status['containerStatuses'].append({'name':'sidecar','restartCount':0})
    if init:
        spec['initContainers']=[{'name':'init'}];status['initContainerStatuses']=[{'name':'init','restartCount':0}]
    return {'kind':'Pod','metadata':{'name':name,'namespace':namespace},'spec':spec,'status':status}

class LogExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.output=Path(self.tmp.name)/'case'
    def docker(self,*args):return options('--deployment','docker','--container','milvus','--collect','--output-dir',str(self.output),*args)
    def kube(self,*args):return options('--deployment','helm','--context','reader','--namespace','ns','--release','demo','--collect','--output-dir',str(self.output),*args)
    def logs(self,command,**kwargs):
        validate_log_command(command)
        return cp(command,out='2026-09-14T01:02:03.123456789Z ordinary startup line\n')
    def metadata(self,command,**kwargs):
        validate_command(command)
        return cp(command,{'items':[pod()]})

    def test_plan_is_offline_and_does_not_write(self):
        args=options('--deployment','helm','--context','reader','--namespace','ns','--release','demo','--output-dir',str(self.output))
        with patch.object(le,'run_readonly') as read,patch.object(le,'run_log_readonly') as logs,patch.object(le.os.environ,'get',side_effect=AssertionError('no credentials in plan')):
            result=le.run_export(args)
        self.assertEqual(result['status'],'planned');read.assert_not_called();logs.assert_not_called();self.assertFalse(self.output.exists())

    def test_input_scope_conflicts_and_bounds_rejected(self):
        cases=[[],['--deployment','kubernetes','--context','r','--namespace','ns'],
            ['--deployment','helm','--namespace','ns','--release','demo'],
            ['--deployment','docker','--container','x','--previous'],
            ['--deployment','docker','--container','x','--include-init'],
            ['--deployment','docker','--container','x','--component','etcd'],
            ['--deployment','docker','--compose-project','demo'],
            ['--deployment','compose','--compose-project','demo','--container','x'],
            ['--deployment','kubernetes','--context','r','--namespace','ns','--pod','x','--selector','app=x'],
            ['--deployment','helm','--context','r','--namespace','ns','--selector','app=x','--component','etcd'],
            ['--deployment','operator','--context','r','--namespace','ns','--release','demo'],
            ['--deployment','kubernetes','--context','r','--namespace','ns','--operator-name','demo']]
        cases+= [['--deployment','docker','--container','x',flag,value] for flag,value in [('--since','all'),('--tail','-1'),('--tail','10001'),('--max-bytes','0'),('--max-streams','101'),('--max-total-bytes','999'),('--timeout','nan'),('--total-timeout','301')]]
        with patch.object(le,'run_readonly') as read,patch.object(le,'run_log_readonly') as logs,contextlib.redirect_stderr(io.StringIO()):
            for case in cases:
                with self.subTest(case=case),self.assertRaises((ValueError,SystemExit)):le.run_export(options(*case))
        read.assert_not_called();logs.assert_not_called()

    def test_collection_requires_output_and_refuses_existing_or_link(self):
        for kind in ('directory','file','symlink'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'existing'
                if kind=='directory':path.mkdir()
                elif kind=='file':path.write_text('keep')
                else:path.symlink_to(Path(temp)/'missing')
                with patch.object(le,'run_log_readonly') as logs,self.assertRaises(ValueError):le.run_export(options('--deployment','docker','--container','x','--collect','--output-dir',str(path)))
                logs.assert_not_called()
        with self.assertRaises(ValueError):le.run_export(options('--deployment','docker','--container','x','--collect'))

    def test_timestamped_multiline_secrets_redact_before_persisting(self):
        text='2026-09-14T01:02:03.123456789Z MINIO_ROOT_PASSWORD: |\n2026-09-14T01:02:03.223456789Z   synthetic-multiline-secret\n2026-09-14T01:02:03.323456789Z failed to connect to etcd\n'
        with patch.object(le,'run_log_readonly',return_value=cp([],out=text,err='2026-09-14T01:02:04Z panic: synthetic crash\n')):
            result=le.run_export(self.docker())
        saved=(self.output/'logs/log-0001.log').read_text()
        self.assertNotIn('synthetic-multiline-secret',saved);self.assertIn('2026-09-14T01:02:03',saved)
        self.assertIn('panic:',saved);self.assertIn('DEPENDENCY_UNAVAILABLE',result['log_manifest']['streams'][0]['finding_codes'])
        self.assertNotIn('panic: synthetic crash',json.dumps(result))
        self.assertIn('INTERNAL_PANIC',{f['code'] for f in result['report']['findings']})
        self.assertEqual(result['log_manifest']['streams'][0]['instance'],'container_history')
        self.assertEqual(os.stat(self.output).st_mode&0o777,0o700)
        for path in self.output.rglob('*'):
            if path.is_file():self.assertEqual(os.stat(path).st_mode&0o777,0o600)

    def test_environment_name_value_and_known_secret(self):
        text='2026-09-14T01:02:03Z - name: MILVUS_TOKEN\n2026-09-14T01:02:04Z   value: synthetic-env-secret\n2026-09-14T01:02:05Z opaque-runtime-secret\n'
        with patch.dict(os.environ,{'DOCTOR_TEST_KNOWN':'opaque-runtime-secret'}),patch.object(le,'run_log_readonly',return_value=cp([],out=text)):
            result=le.run_export(self.docker('--token-env','DOCTOR_TEST_KNOWN'))
        for path in self.output.rglob('*'):
            if path.is_file():
                self.assertNotIn('synthetic-env-secret',path.read_text());self.assertNotIn('opaque-runtime-secret',path.read_text())

    def test_per_container_previous_and_opt_in_init(self):
        item=pod(restarts=2,sidecar=True,init=True)
        with patch.object(le,'run_readonly',return_value=cp([],{'items':[item]})),patch.object(le,'run_log_readonly',side_effect=self.logs) as reader:
            result=le.run_export(self.kube('--previous','--include-init','--pod-container','milvus','--pod-container','sidecar','--pod-container','init'))
        self.assertEqual(reader.call_count,4)
        prior=[call.args[0] for call in reader.call_args_list if '--previous=true' in call.args[0]]
        self.assertEqual(len(prior),1);self.assertEqual(prior[0][10],'milvus')
        self.assertEqual(result['report']['evidence']['log_export']['previous_streams'],1)
        self.assertTrue(any(e['target'].get('group')=='init' for e in result['log_manifest']['streams']))

    def test_init_and_previous_not_implicit(self):
        with patch.object(le,'run_readonly',return_value=cp([],{'items':[pod(restarts=2,sidecar=True,init=True)]})),patch.object(le,'run_log_readonly',side_effect=self.logs) as reader:
            le.run_export(self.kube())
        self.assertEqual(reader.call_count,1);self.assertTrue(all('--previous=true' not in c.args[0] for c in reader.call_args_list))

    def test_ambiguous_regular_containers_require_selection(self):
        value=pod(sidecar=True);value['spec']['containers']=[{'name':'custom-server'},{'name':'custom-sidecar'}]
        with patch.object(le,'run_readonly',return_value=cp([],{'items':[value]})),patch.object(le,'run_log_readonly') as reader:
            result=le.run_export(self.kube())
        reader.assert_not_called();self.assertEqual(result['status'],'incomplete')
        self.assertIn('selection_required',{s.get('reason') for s in result['report']['sources']})

    def test_named_container_filter_and_missing_selection(self):
        with patch.object(le,'run_readonly',return_value=cp([],{'items':[pod(sidecar=True)]})),patch.object(le,'run_log_readonly',side_effect=self.logs) as reader:
            result=le.run_export(self.kube('--pod-container','milvus','--pod-container','absent'))
        self.assertEqual(reader.call_count,1);self.assertEqual(result['status'],'incomplete')

    def test_previous_unavailable_is_not_reported_as_healthy(self):
        def read(command,**kwargs):return cp(command,code=1,err='previous container missing password=synthetic-error-secret') if '--previous=true' in command else self.logs(command)
        with patch.object(le,'run_readonly',return_value=cp([],{'items':[pod(restarts=2)]})),patch.object(le,'run_log_readonly',side_effect=read):
            result=le.run_export(self.kube('--previous'))
        self.assertEqual(result['status'],'incomplete');self.assertNotIn('synthetic-error-secret',json.dumps(result))
        self.assertIn('previous_unavailable',{s.get('reason') for s in result['report']['evidence']['sources']})

    def test_unknown_restart_count_keeps_previous_gap(self):
        with patch.object(le,'run_readonly',return_value=cp([],{'items':[pod(restarts=False)]})),patch.object(le,'run_log_readonly',side_effect=self.logs):
            result=le.run_export(self.kube('--previous'))
        self.assertEqual(result['status'],'incomplete')

    def test_permission_denial_keeps_only_fixed_error(self):
        with patch.object(le,'run_log_readonly',return_value=cp([],code=1,err='Forbidden token=synthetic-error-secret')):
            result=le.run_export(self.docker())
        self.assertEqual(result['status'],'incomplete');self.assertNotIn('synthetic-error-secret',json.dumps(result))
        self.assertIn('permission_denied',{s.get('reason') for s in result['report']['sources']})

    def test_authentication_is_not_authorization_and_arbitrary_limits_are_unknown(self):
        self.assertEqual(le._failure('Error from server (Unauthorized): no access')[0],'authentication_failed')
        self.assertEqual(le._failure('server concurrency limit exceeded')[0],'unavailable')
        self.assertEqual(le._failure('container forbidden-app not found')[0],'unavailable')

    def test_inventory_denial_does_not_claim_target_missing(self):
        with patch.object(le,'run_readonly',return_value=cp([],code=1,err='Forbidden')),patch.object(le,'run_log_readonly') as reader:
            result=le.run_export(self.kube())
        reader.assert_not_called();self.assertNotIn('target_not_found',{s.get('reason') for s in result['report']['sources']})

    def test_empty_discovery_and_empty_logs_keep_gaps(self):
        with patch.object(le,'run_readonly',return_value=cp([],{'items':[]})),patch.object(le,'run_log_readonly') as reader:
            result=le.run_export(self.kube())
        reader.assert_not_called();self.assertEqual(result['status'],'incomplete')
        with tempfile.TemporaryDirectory() as temp,patch.object(le,'run_log_readonly',return_value=cp([],out='')):
            result=le.run_export(options('--deployment','docker','--container','x','--collect','--output-dir',str(Path(temp)/'case')))
        self.assertEqual(result['status'],'incomplete');self.assertEqual(result['log_manifest']['streams'][0]['reason'],'logs_empty')

    def test_metadata_namespace_mismatch_cannot_read_logs(self):
        with patch.object(le,'run_readonly',return_value=cp([],{'items':[pod(namespace='other')]})),patch.object(le,'run_log_readonly') as reader:
            result=le.run_export(self.kube())
        reader.assert_not_called();self.assertEqual(result['status'],'incomplete')

    def test_explicit_pod_metadata_is_get_not_namespace_inventory(self):
        args=options('--deployment','kubernetes','--context','reader','--namespace','ns','--pod','milvus-0','--collect','--output-dir',str(self.output))
        with patch.object(le,'run_readonly',return_value=cp([],pod())) as metadata,patch.object(le,'run_log_readonly',side_effect=self.logs):le.run_export(args)
        self.assertIn('milvus-0',metadata.call_args.args[0]);self.assertNotIn('--selector',metadata.call_args.args[0])

    def test_standard_component_selectors_preserve_instance_scope(self):
        a=self.kube('--component','etcd','--component','minio','--component','pulsar','--component','kafka','--selector','team=mine')
        values=le.component_selectors(a)
        self.assertEqual(len(values),6);self.assertTrue(all(s.startswith('team=mine,') for _,s in values))
        self.assertIn(('minio','team=mine,release=demo,app=minio'),values)
        a.deployment='operator';a.operator_name='demo';a.release=None
        values=le.component_selectors(a)
        self.assertIn(('etcd','team=mine,app.kubernetes.io/instance=demo-etcd,app.kubernetes.io/name=etcd'),values)
        self.assertIn(('kafka','team=mine,app.kubernetes.io/instance=demo-kafka,app.kubernetes.io/component=kafka'),values)

    def test_canonical_legacy_minio_dedup_and_missing_dependency(self):
        def meta(command,**kwargs):
            selector=command[-1]
            return cp(command,{'items':[pod('milvus-0' if 'name=milvus' in selector else 'minio-0')]})
        with patch.object(le,'run_readonly',side_effect=meta),patch.object(le,'run_log_readonly',side_effect=self.logs) as reader:
            result=le.run_export(self.kube('--component','minio'))
        self.assertEqual(reader.call_count,2);self.assertEqual(result['log_manifest']['streams_attempted'],2)

    def test_stream_count_cap_is_visible(self):
        a=self.docker('--max-streams','1');a.container=['one','two']
        with patch.object(le,'run_log_readonly',side_effect=self.logs) as reader:result=le.run_export(a)
        self.assertEqual(reader.call_count,1);self.assertEqual(result['status'],'incomplete')

    def test_total_byte_cap_and_failed_reads_are_charged(self):
        a=self.docker('--max-bytes','1024','--max-total-bytes','2048');a.container=['one','two','three']
        with patch.object(le,'run_log_readonly',side_effect=ValueError('Diagnostic command exceeded its output limit')) as reader:result=le.run_export(a)
        self.assertEqual(reader.call_count,2);self.assertEqual(result['log_manifest']['log_byte_budget_charged'],2048)
        self.assertEqual(list((self.output/'logs').iterdir()),[]);self.assertEqual(result['status'],'incomplete')

    def test_batch_deadline_stops_before_execution(self):
        with patch.object(le.time,'monotonic',side_effect=[0,61]),patch.object(le,'run_log_readonly') as reader:
            result=le.run_export(self.docker())
        reader.assert_not_called();self.assertEqual(result['status'],'incomplete')

    def test_compose_default_excludes_dependencies_even_registry_named_milvus(self):
        def metadata(command,**kwargs):
            validate_command(command)
            if command[1]=='ps':
                self.assertIn('label=com.docker.compose.project=demo',command);return cp(command,out='abc123\ndef456\n')
            service,image=('standalone','registry/milvus:tag') if command[-1]=='abc123' else ('minio','milvus/minio:tag')
            return cp(command,[{'Config':{'Image':image,'Labels':{'com.docker.compose.project':'demo','com.docker.compose.service':service}}}])
        args=options('--deployment','compose','--compose-project','demo','--collect','--output-dir',str(self.output))
        with patch.object(le,'run_readonly',side_effect=metadata),patch.object(le,'run_log_readonly',side_effect=self.logs) as reader:result=le.run_export(args)
        self.assertEqual(reader.call_count,1);self.assertEqual(reader.call_args.args[0][-1],'abc123')
        self.assertEqual(result['log_manifest']['streams'][0]['component'],'milvus')

    def test_log_scope_counts_survive_evidence_and_handoff(self):
        with patch.object(le,'run_log_readonly',side_effect=self.logs):result=le.run_export(self.docker())
        evidence=sanitize_evidence(result['report']['evidence'])
        self.assertEqual(evidence['log_export']['since_seconds'],1800)
        summary=support_summary(result['report'])
        self.assertIn('since_seconds=1800',summary);self.assertIn('saved_streams=1',summary)
        self.assertIn('logs.export.container_history: ok',summary)
        evidence['log_export']['raw']='synthetic-private';evidence['log_export']['saved_streams']=True
        safe=sanitize_evidence(evidence)
        self.assertNotIn('raw',safe['log_export']);self.assertNotIn('saved_streams',safe['log_export'])

    def test_cli_reports_partial_and_never_prints_raw_logs(self):
        out=io.StringIO()
        with patch.object(le,'run_log_readonly',return_value=cp([],out='panic: synthetic-unstructured-detail')),contextlib.redirect_stdout(out):
            code=main(['export-logs','--deployment','docker','--container','x','--collect','--output-dir',str(self.output)])
        self.assertEqual(code,1);self.assertNotIn('synthetic-unstructured-detail',out.getvalue())
