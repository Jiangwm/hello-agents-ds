from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


CHAPTER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CHAPTER_DIR))

ALLOWLIST = frozenset(
    {
        "dataset_profile",
        "trend_analysis",
        "anomaly_detection",
        "specification_lookup",
        "evidence_export",
    }
)

from industrial_agents.exceptions import (
    ApprovalRequiredError,
    ToolExecutionError,
    ToolNotAllowedError,
    ToolValidationError,
)
from industrial_agents.schemas import (
    ApprovalContext,
    IndustrialAgentConfig,
    RiskLevel,
    ToolResult,
)
from industrial_agents.tools import (
    IndustrialTool,
    create_default_tool_registry,
)


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_dir = CHAPTER_DIR / "data"
        self.config = IndustrialAgentConfig(
            data_root=self.data_dir,
            tool_allowlist=ALLOWLIST,
            max_input_rows=100,
            max_result_rows=100,
        )
        self.registry = create_default_tool_registry()

    def test_profile_succeeds_and_records_deterministic_evidence(self) -> None:
        first = self.registry.execute(
            "dataset_profile",
            {"file_path": "industrial_timeseries.csv"},
            self.config,
        )
        second = self.registry.execute(
            "dataset_profile",
            {"file_path": "industrial_timeseries.csv"},
            self.config,
        )

        self.assertEqual(12, first.output["row_count"])
        self.assertEqual("datetime", first.output["field_types"]["timestamp"])
        self.assertEqual(0.0, first.output["missing_rates"]["pressure_mpa"])
        self.assertIn("至", first.output["time_range"])
        self.assertEqual(first.evidence[0].evidence_id, second.evidence[0].evidence_id)
        self.assertEqual(2, len(self.registry.audit_records))
        self.assertEqual("completed", self.registry.audit_records[-1].status.value)

    def test_profile_reports_total_and_sampled_row_counts(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "bounded.csv"
        lines = ["timestamp,equipment_id,pressure_mpa"]
        lines.extend(
            f"2026-07-21T{index:02d}:00:00,E-TEST,{index}"
            for index in range(12)
        )
        path.write_text("\n".join(lines), encoding="utf-8")
        config = IndustrialAgentConfig(
            data_root=Path(temporary.name),
            tool_allowlist=ALLOWLIST,
            max_input_rows=5,
            max_result_rows=5,
        )

        result = self.registry.execute(
            "dataset_profile",
            {"file_path": "bounded.csv"},
            config,
        )

        self.assertEqual(12, result.output["row_count"])
        self.assertEqual(5, result.output["sampled_row_count"])
        self.assertTrue(result.truncated)

    def test_parameter_error_is_audited(self) -> None:
        with self.assertRaises(ToolValidationError):
            self.registry.execute(
                "trend_analysis",
                {"file_path": "industrial_timeseries.csv"},
                self.config,
            )

        record = self.registry.audit_records[-1]
        self.assertEqual("failed", record.status.value)
        self.assertEqual("ToolValidationError", record.error_type)

    def test_missing_file_and_empty_file_are_execution_errors(self) -> None:
        with self.assertRaises(ToolExecutionError):
            self.registry.execute(
                "dataset_profile",
                {"file_path": "missing.csv"},
                self.config,
            )

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        empty_file = Path(temporary.name) / "empty.csv"
        empty_file.write_text("timestamp,equipment_id\n", encoding="utf-8")
        empty_config = IndustrialAgentConfig(
            data_root=Path(temporary.name), tool_allowlist=ALLOWLIST
        )
        with self.assertRaises(ToolExecutionError):
            self.registry.execute(
                "dataset_profile",
                {"file_path": "empty.csv"},
                empty_config,
            )

    def test_empty_parquet_is_rejected_explicitly(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "empty.parquet").write_bytes(b"fixture")
        config = IndustrialAgentConfig(
            data_root=root,
            tool_allowlist=ALLOWLIST,
        )

        class EmptyFrame:
            columns = ("timestamp",)

            def __len__(self) -> int:
                return 0

        pandas = SimpleNamespace(read_parquet=lambda _: EmptyFrame())
        with mock.patch.dict(sys.modules, {"pandas": pandas}):
            with self.assertRaisesRegex(ToolExecutionError, "数据文件为空"):
                self.registry.execute(
                    "dataset_profile",
                    {"file_path": "empty.parquet"},
                    config,
                )

    def test_path_escape_and_unapproved_field_are_rejected(self) -> None:
        with self.assertRaises(ToolNotAllowedError):
            self.registry.execute(
                "dataset_profile",
                {"file_path": "../industrial_timeseries.csv"},
                self.config,
            )

        restricted = IndustrialAgentConfig(
            data_root=self.data_dir,
            tool_allowlist=ALLOWLIST,
            field_allowlist=frozenset({"timestamp", "equipment_id", "pressure_mpa"}),
        )
        with self.assertRaises(ToolNotAllowedError):
            self.registry.execute(
                "trend_analysis",
                {"file_path": "industrial_timeseries.csv", "field": "temperature_c"},
                restricted,
            )

    def test_anomaly_output_is_truncated_at_configured_limit(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "many.csv"
        lines = ["timestamp,equipment_id,pressure_mpa"]
        lines.extend(
            f"2026-07-21T{index:02d}:00:00,E-TEST,{index}" for index in range(10)
        )
        path.write_text("\n".join(lines), encoding="utf-8")
        config = IndustrialAgentConfig(
            data_root=Path(temporary.name),
            tool_allowlist=ALLOWLIST,
            max_input_rows=10,
            max_result_rows=2,
        )

        result = self.registry.execute(
            "anomaly_detection",
            {"file_path": "many.csv", "field": "pressure_mpa", "z_threshold": 0.1},
            config,
        )

        self.assertTrue(result.truncated)
        self.assertEqual(10, result.output["anomaly_count"])
        self.assertEqual(2, len(result.output["anomalies"]))

    def test_export_requires_approval_then_creates_read_only_package(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config = IndustrialAgentConfig(
            data_root=root,
            tool_allowlist=ALLOWLIST,
            export_directory="evidence_packages",
        )
        arguments = {
            "package_name": "case-e101",
            "evidence": {"items": [{"id": "EV-1"}]},
        }

        with self.assertRaises(ApprovalRequiredError):
            self.registry.execute("evidence_export", arguments, config)
        with self.assertRaises(ToolValidationError):
            self.registry.execute(
                "evidence_export",
                arguments,
                config,
                approval=True,
            )
        with self.assertRaises(ToolValidationError):
            self.registry.execute(
                "evidence_export",
                {
                    "package_name": "case-e101",
                    "evidence": [{"id": "EV-1"}],
                },
                config,
                approval=ApprovalContext(
                    approval_id="APR-CH7-INVALID",
                    approver="质量负责人",
                    reason="验证输入边界",
                ),
            )

        approval = ApprovalContext(
            approval_id="APR-CH7-001",
            approver="质量负责人",
            reason="允许导出本地只读教学证据包",
        )
        result = self.registry.execute(
            "evidence_export",
            arguments,
            config,
            approval=approval,
        )
        exported = Path(str(result.output["export_path"]))
        self.addCleanup(self._remove_read_only_file, exported)
        self.assertTrue(exported.is_file())
        self.assertFalse(bool(exported.stat().st_mode & stat.S_IWRITE))
        self.assertEqual(RiskLevel.HIGH, next(tool for tool in self.registry.discover() if tool.name == "evidence_export").risk_level)
        record = self.registry.audit_records[-1]
        self.assertEqual(approval.approval_id, record.approval_id)
        self.assertEqual(approval.approver, record.approver)
        self.assertEqual(approval.reason, record.approval_reason)
        self.assertEqual(approval.approved_at, record.approved_at)
        self.assertEqual(approval.to_dict(), result.metadata["approval"])
        with self.assertRaises(ToolExecutionError):
            self.registry.execute(
                "evidence_export",
                arguments,
                config,
                approval=approval,
            )

    def test_trend_resampling_and_multisource_lookup_are_bounded(self) -> None:
        trend = self.registry.execute(
            "trend_analysis",
            {
                "file_path": "industrial_timeseries.csv",
                "field": "pressure_mpa",
                "frequency": "hour",
                "rolling_window": 2,
                "group_by": "equipment_id",
            },
            self.config,
        )
        lookup = self.registry.execute(
            "specification_lookup",
            {"query": "pressure"},
            self.config,
        )

        self.assertEqual("hour", trend.output["frequency"])
        self.assertEqual("equipment_id", trend.output["group_by"])
        self.assertEqual("mixed", trend.output["direction"])
        self.assertEqual(
            {"E-101", "E-102"},
            {item["group"] for item in trend.output["group_summaries"]},
        )
        self.assertTrue(trend.output["series"])
        self.assertEqual(
            {"process_specs.json", "data_dictionary.json", "cases.json"},
            set(lookup.output["sources"]),
        )
        self.assertGreaterEqual(lookup.output["match_count"], 2)

    def test_empty_allowlist_denies_tool_execution(self) -> None:
        denied_config = IndustrialAgentConfig(data_root=self.data_dir)

        with self.assertRaises(ToolNotAllowedError):
            self.registry.execute(
                "dataset_profile",
                {"file_path": "industrial_timeseries.csv"},
                denied_config,
            )

    def test_model_tools_discovers_new_allowlisted_tool_without_agent_changes(self) -> None:
        tool = IndustrialTool(
            name="custom_readonly",
            description="读取自定义离线数据。",
            parameters=(),
            input_schema={"type": "object", "properties": {}, "required": []},
            output_schema={"type": "object"},
            risk_level=RiskLevel.LOW,
            requires_approval=False,
            handler=lambda _arguments, _config: ToolResult(output={"ok": True}),
        )
        self.registry.register(tool)
        config = IndustrialAgentConfig(
            data_root=self.data_dir,
            tool_allowlist=frozenset({"custom_readonly"}),
        )

        definitions = self.registry.model_tools(config)

        self.assertEqual(("custom_readonly",), tuple(
            definition["function"]["name"]
            for definition in definitions
        ))

    @staticmethod
    def _remove_read_only_file(path: Path) -> None:
        if path.exists():
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            path.unlink()
        if path.parent.exists():
            shutil.rmtree(path.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
