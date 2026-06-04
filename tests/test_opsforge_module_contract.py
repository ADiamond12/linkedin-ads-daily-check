import json
import unittest
from pathlib import Path


class OpsForgeModuleContractTests(unittest.TestCase):
    def test_contract_declares_control_report_boundaries(self) -> None:
        contract_path = Path(__file__).resolve().parents[1] / "docs" / "opsforge-module-contract.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))

        required_fields = [
            "module_id",
            "suite_role",
            "status",
            "primary_user",
            "input_types",
            "output_artifacts",
            "proof_artifacts",
            "local_demo_command",
            "deployment_shape",
            "safety_boundaries",
            "limitations",
        ]
        for field in required_fields:
            self.assertIn(field, contract)
            self.assertTrue(contract[field])

        self.assertIn("HTML daily control report", contract["output_artifacts"])
        self.assertIn("pacing verdict", contract["output_artifacts"])

    def test_contract_avoids_private_or_overclaiming_language(self) -> None:
        contract_path = Path(__file__).resolve().parents[1] / "docs" / "opsforge-module-contract.json"
        text = contract_path.read_text(encoding="utf-8").lower()
        forbidden = ["private key", "seed phrase", "wallet", "profit", "profitable", "live trading", "c:\\users\\"]

        for phrase in forbidden:
            self.assertNotIn(phrase, text)
