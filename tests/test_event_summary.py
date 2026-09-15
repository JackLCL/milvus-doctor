import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from doctorlib.reports import build_report,support_summary

class EventSummaryTests(unittest.TestCase):
    def test_confirmed_event_categories_in_summary_without_raw_names(self):
        snapshot={'schema_version':1,'sources':[{'name':'kubernetes.events','status':'ok'}],
                  'kubernetes':{'events':[{'type':'Warning','reason':'ProvisioningFailed',
                    'message':'storageclass.storage.k8s.io "private-storage-token" not found',
                    'involvedObject':{'kind':'PersistentVolumeClaim','name':'private-pvc'},
                    'count':3,'lastTimestamp':'2026-09-13T14:00:00Z'}]}}
        summary=support_summary(build_report(snapshot,[]))
        self.assertIn('a provisioning event reported',summary)
        self.assertIn('historical event',summary)
        self.assertNotIn('private-storage-token',summary)
        self.assertNotIn('private-pvc',summary)

    def test_malicious_saved_event_fields_never_reach_summary(self):
        report=build_report({'sources':[{'name':'kubernetes.events','status':'ok'}],'kubernetes':{'events':[]}},[])
        report['evidence']['kubernetes']['events']=[{'alias':'private-name','categories':['token=secret-value'],
                                                  'last_observed_at':'private-address','message':'private-body'}]
        summary=support_summary(report)
        for value in ('private-name','secret-value','private-address','private-body'):
            self.assertNotIn(value,summary)
