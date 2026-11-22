import csv
import tempfile
import unittest
from pathlib import Path

from app.loader import LoadError, load_dataset, validate_batch

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = {p.name: p.read_text(encoding="utf-8")
                for p in (ROOT / "sources").iterdir()}


def _write(table: str, rows: list[dict], headers: list[str]) -> Path:
    tmp = Path(tempfile.mkdtemp())
    with (tmp / f"{table}.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    return tmp


class LoaderTests(unittest.TestCase):
    def test_base_dataset_loads(self):
        ds = load_dataset([ROOT / "sources"])
        self.assertEqual(len(ds.marks), 119)
        self.assertTrue(ds.rules and ds.duty_requests)

    def test_unknown_revoke_target_rejected(self):
        from app.loader import HEADERS
        batch = _write("marks", [{
            "mark": "RX", "candidate": "P1", "centre": "A", "task": "service",
            "examiner": "A1", "score": "", "assessed_day": 11,
            "known_day": 30, "revokes": "MISSING",
        }], HEADERS["marks"])
        with self.assertRaises(LoadError):
            load_dataset([ROOT / "sources", batch])

    def test_revoke_cross_candidate_rejected(self):
        from app.loader import HEADERS
        batch = _write("marks", [{
            "mark": "RX", "candidate": "P2", "centre": "A", "task": "service",
            "examiner": "A1", "score": "", "assessed_day": 11,
            "known_day": 30, "revokes": "M1-service",
        }], HEADERS["marks"])
        with self.assertRaises(LoadError):
            load_dataset([ROOT / "sources", batch])

    def test_primary_key_conflict_across_batches_rejected(self):
        from app.loader import HEADERS
        batch = _write("duty_requests", [{
            "duty": "D1", "centre": "A", "day": 30, "start": 8,
            "end": 10, "mode": "independent",
        }], HEADERS["duty_requests"])
        with self.assertRaises(LoadError):
            load_dataset([ROOT / "sources", batch])

    def test_idempotent_identical_rows_allowed(self):
        # 完全相同的行重放不报错
        batch = _write("release_terms", [{
            "centre": "A", "day": 29, "start": 8, "end": 12,
            "min_independent": 2,
        }], ["centre", "day", "start", "end", "min_independent"])
        ds = load_dataset([ROOT / "sources", batch])
        self.assertTrue(ds.release_terms)

    def test_receipt_unknown_duty_rejected(self):
        from app.loader import HEADERS
        batch = _write("duty_receipts", [{
            "event": "AX", "duty": "D99", "candidate": "P8",
            "supervisor": "", "day": 29, "state": "accepted",
        }], HEADERS["duty_receipts"])
        with self.assertRaises(LoadError):
            load_dataset([ROOT / "sources", batch])

    def test_validate_batch_reports_present_tables(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "marks.csv").write_text(SOURCE_FILES["marks.csv"],
                                       encoding="utf-8")
        self.assertEqual(validate_batch(tmp), ["marks"])


if __name__ == "__main__":
    unittest.main()
