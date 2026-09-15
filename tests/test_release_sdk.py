"""Messages reproduced using real PyMilvus 2.6.17, with synthetic fields."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from doctorlib.rules import evaluate

def codes(text):
    return {f['code'] for f in evaluate({'schema_version': 1, 'sources': [{'name': 'logs', 'status': 'ok'}], 'logs': [text]})}

class ActualSdkErrorTests(unittest.TestCase):
    def test_actual_int64_string_error(self):
        message = "<DataNotMatchException: (code=1, message=The Input data type is inconsistent with defined schema, {age} field should be a int64, but got a {<class 'str'>} instead. Detail: 'str' object cannot be interpreted as an integer)>"
        self.assertIn('FIELD_TYPE_MISMATCH', codes(message))

    def test_actual_not_loaded_error(self):
        self.assertIn('LOG_COLLECTION_NOT_LOADED', codes('<MilvusException: (code=101, message=failed to search: collection not loaded[collection=123])>'))

    def test_normal_schema_and_other_exceptions_not_type_mismatch(self):
        for message in ('Input data type is consistent with defined schema', 'Field should be indexed before loading', 'DataNotMatchException: vector dimension 4 does not match 8'):
            self.assertNotIn('FIELD_TYPE_MISMATCH', codes(message))
