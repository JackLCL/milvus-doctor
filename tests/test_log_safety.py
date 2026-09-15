import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from doctorlib.safety import validate_command, validate_log_command, run_log_readonly, run_readonly

DOCKER = ['docker','logs','--since','30m','--tail','2000','--timestamps','milvus']
KUBE = ['kubectl','--context','reader','--namespace','ns','--request-timeout','8s','logs','milvus-0','--container','milvus','--since','30m','--tail','2000','--timestamps=true','--limit-bytes','1048577']

class LogCommandTests(unittest.TestCase):
    def test_explicit_bounded_commands(self):
        for command in (DOCKER,KUBE,KUBE+['--previous=true']):validate_log_command(command)

    def test_metadata_runner_does_not_gain_log_access(self):
        with patch('doctorlib.safety.subprocess.Popen') as spawn:
            for command in (DOCKER,KUBE):
                with self.assertRaises(ValueError):run_readonly(command)
            spawn.assert_not_called()

    def test_log_runner_rejects_escape_and_unbounded_variants_before_spawn(self):
        bad=[['docker','logs','milvus'],['kubectl','logs','milvus'],DOCKER+['--follow'],DOCKER+['--details'],DOCKER+['other'],KUBE+['--all-containers'],KUBE+['--follow'],KUBE+['--insecure-skip-tls-verify'],KUBE+['--previous=false'],['bash','-c','docker logs x']]
        for base,index,values in ((DOCKER,3,['all','0s','169h','-1m','1d','1m;id']), (DOCKER,5,['all','0','-1','10001']),
                                  (DOCKER,7,['--help','x;y','../../foo','x/y']), (KUBE,2,['','--foo','reader\nother']),
                                  (KUBE,4,['','-A','../x']), (KUBE,8,['deploy/milvus','-l','x;id']),
                                  (KUBE,10,['all','--all-containers']), (KUBE,17,['0','-1','4194306']), (KUBE,6,['0s','121s','nan','8s;id'])):
            for value in values:
                cmd=list(base);cmd[index]=value
                # A container literally named "all" is still one explicit container.
                if index==10 and value=='all':continue
                bad.append(cmd)
        with patch('doctorlib.safety.subprocess.Popen') as spawn:
            for command in bad:
                with self.subTest(command=command),self.assertRaises(ValueError):run_log_readonly(command)
            spawn.assert_not_called()

    def test_log_runner_never_allows_mutating_programs(self):
        for command in (['docker','exec','milvus','cat','/etc/passwd'],['kubectl','delete','pod','x'],['docker','compose','logs'],['helm','get','all','x']):
            with self.assertRaises(ValueError):validate_log_command(command)

    def test_real_subprocess_streams_are_bounded_and_reaped(self):
        real_popen=subprocess.Popen
        for code,expected in [('import time; time.sleep(10)',TimeoutError),('import sys; sys.stderr.write("x"*4096)',ValueError)]:
            child=real_popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            with patch('doctorlib.safety.subprocess.Popen',return_value=child):
                with self.assertRaises(expected):run_log_readonly(DOCKER,timeout=.1,max_bytes=1024)
            self.assertIsNotNone(child.poll())

    def test_stdout_and_stderr_both_count_towards_limit(self):
        child=subprocess.Popen([sys.executable,'-c','import sys;sys.stdout.write("a"*700);sys.stderr.write("b"*700)'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        with patch('doctorlib.safety.subprocess.Popen',return_value=child):
            with self.assertRaises(ValueError):run_log_readonly(DOCKER,max_bytes=1024)
        self.assertIsNotNone(child.poll())
