import csv
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "sources"
HEADERS = {'marks': ['mark', 'candidate', 'centre', 'task', 'examiner', 'score', 'assessed_day', 'known_day', 'revokes'], 'training_periods': ['candidate', 'track', 'training_day', 'duty_observed_day', 'supervised_hours'], 'qualification_rules': ['rule', 'task', 'min_score', 'validity_days', 'mode'], 'duty_requests': ['duty', 'centre', 'day', 'start', 'end', 'mode'], 'release_terms': ['centre', 'day', 'start', 'end', 'min_independent'], 'availability': ['candidate', 'home', 'accepted_start', 'accepted_end', 'available_day'], 'duty_receipts': ['event', 'duty', 'candidate', 'supervisor', 'day', 'state']}
REFERENCES = [['marks', 'revokes', 'marks', 'mark'], ['duty_receipts', 'duty', 'duty_requests', 'duty'], ['duty_receipts', 'candidate', 'availability', 'candidate'], ['duty_receipts', 'supervisor', 'availability', 'candidate']]
COUNTS = [['marks', 'revokes', 'M15-oversight', 1], ['duty_receipts', 'state', 'accepted', 2], ['duty_requests', 'mode', 'supervised', 2]]


class SourceContractTests(unittest.TestCase):
    def setUp(self):
        self.records = {}
        for name, header in HEADERS.items():
            with (ROOT / f"{name}.csv").open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, header)
                self.records[name] = list(reader)

    def test_record_shape(self):
        for name, records in self.records.items():
            with self.subTest(source=name):
                self.assertTrue(records)
                for row in records:
                    self.assertEqual(set(row), set(HEADERS[name]))
                    self.assertNotIn(None, row.values())
                    self.assertTrue(row[HEADERS[name][0]])
                self.assertEqual(len(records), len({tuple(row.values()) for row in records}))

    def test_cross_source_references(self):
        for child, fk, parent, pk in REFERENCES:
            with self.subTest(source=child, field=fk):
                keys = {row[pk] for row in self.records[parent]}
                values = {row[fk] for row in self.records[child] if row[fk]}
                self.assertTrue(values)
                self.assertFalse(values - keys)

    def test_source_has_relevant_observations(self):
        for source, field, value, minimum in COUNTS:
            with self.subTest(source=source, field=field, value=value):
                self.assertGreaterEqual(sum(row[field] == value for row in self.records[source]), minimum)
