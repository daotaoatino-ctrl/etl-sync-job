"""
Test: thiếu RAW_HR_MASTER_DATA → dbt bỏ qua mart_profiles + mart_employee_events và ghi rõ trong log.

Chạy: python -m pytest tests/ -v
"""

import os
import sys
import types
import unittest
from unittest import mock

from google.api_core.exceptions import NotFound

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daily_morning_sync as sync  # noqa: E402

EXPECTED_MESSAGE = "đã bỏ qua 2 mart do thiếu RAW_HR_MASTER_DATA"


def fake_config():
    config = types.ModuleType("config")
    config.PROJECT_ID, config.DATASET_ID = "proj", "ds"
    return {"config": config}


class TestHrMasterExists(unittest.TestCase):
    def check(self, client_factory):
        with mock.patch.dict(sys.modules, fake_config()), \
                mock.patch("google.cloud.bigquery.Client", side_effect=client_factory):
            return sync.hr_master_exists()

    def test_table_exists(self):
        client = mock.Mock()
        self.assertTrue(self.check(lambda project: client))
        client.get_table.assert_called_once_with("proj.ds.RAW_HR_MASTER_DATA")

    def test_table_missing(self):
        client = mock.Mock()
        client.get_table.side_effect = NotFound("missing")
        self.assertFalse(self.check(lambda project: client))

    def test_cannot_check_runs_full_dbt(self):
        client = mock.Mock()
        client.get_table.side_effect = PermissionError("denied")
        with mock.patch("builtins.print"):
            self.assertTrue(self.check(lambda project: client))

    def test_client_creation_error_runs_full_dbt(self):
        def boom(project):
            raise RuntimeError("no credentials")
        with mock.patch("builtins.print"):
            self.assertTrue(self.check(boom))


class TestExcludeArgs(unittest.TestCase):
    def test_present_nothing_excluded(self):
        with mock.patch.object(sync, "hr_master_exists", return_value=True), mock.patch("builtins.print") as printed:
            self.assertEqual(sync.dbt_exclude_args(), "")
        printed.assert_not_called()

    def test_missing_excludes_two_marts_and_logs_clearly(self):
        with mock.patch.object(sync, "hr_master_exists", return_value=False), mock.patch("builtins.print") as printed:
            args = sync.dbt_exclude_args()
        self.assertEqual(args, " --exclude mart_profiles mart_employee_events")
        logs = "\n".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertIn(EXPECTED_MESSAGE, logs)
        self.assertIn(f"::warning title=dbt::{EXPECTED_MESSAGE}", logs)  # hiện trên trang tóm tắt run

    def test_marts_constant(self):
        self.assertEqual(sync.HR_DEPENDENT_MARTS, ["mart_profiles", "mart_employee_events"])


class TestCommandsUseExclude(unittest.TestCase):
    """main() gắn đúng chuỗi exclude vào lệnh dbt run và dbt test."""

    def run_main(self, excl):
        commands = []

        def fake_run_step(name, cmd):
            commands.append((name, cmd))
            return True

        with mock.patch.object(sync, "dbt_exclude_args", return_value=excl), \
                mock.patch.object(sync, "run_step", side_effect=fake_run_step), \
                mock.patch("builtins.print"):
            try:
                sync.main()
            except SystemExit:
                pass
        return commands

    def test_missing_table_commands(self):
        commands = self.run_main(" --exclude mart_profiles mart_employee_events")
        dbt = [cmd for _, cmd in commands if "dbt_wrapper.py" in cmd]
        self.assertEqual(len(dbt), 2)
        for cmd in dbt:
            self.assertTrue(cmd.endswith("--exclude mart_profiles mart_employee_events"), cmd[-80:])

    def test_present_table_commands_unchanged(self):
        commands = self.run_main("")
        dbt = [cmd for _, cmd in commands if "dbt_wrapper.py" in cmd]
        self.assertEqual(len(dbt), 2)
        for cmd in dbt:
            self.assertNotIn("--exclude", cmd)
            self.assertIn("mart_profiles", cmd)  # vẫn nằm trong --select


if __name__ == "__main__":
    unittest.main()
