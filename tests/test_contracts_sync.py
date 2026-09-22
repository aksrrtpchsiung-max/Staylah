"""检测 property_agent/contracts.py 与权威契约文件的漂移。

权威文件在数据包里（目录名带空格，不能直接 import），因此按路径加载后逐类比对注解。
契约再次更新时这个测试会先失败，而不是让副本悄悄落后。
"""
import importlib.util
import unittest
from pathlib import Path

from property_agent import contracts

AUTHORITATIVE = Path(__file__).resolve().parents[1] / "contracts_v0.py"


def load_authoritative():
    spec = importlib.util.spec_from_file_location("contracts_v0_authoritative", AUTHORITATIVE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ContractsSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not AUTHORITATIVE.exists():
            raise unittest.SkipTest(f"找不到权威契约文件：{AUTHORITATIVE}")
        cls.reference = load_authoritative()

    def typed_dict_names(self, module) -> set[str]:
        return {
            name
            for name, value in vars(module).items()
            if isinstance(value, type) and hasattr(value, "__annotations__") and not name.startswith("_")
        }

    def test_no_type_is_missing_from_the_copy(self):
        missing = self.typed_dict_names(self.reference) - self.typed_dict_names(contracts)
        self.assertEqual(missing, set(), "权威契约新增了类型，副本需要同步")

    @staticmethod
    def normalize(annotations: dict) -> dict:
        """去掉模块限定名：两份文件的模块路径本来就不同，比的是字段与类型结构。"""
        def strip(text: str) -> str:
            for prefix in ("contracts_v0_authoritative.", "property_agent.contracts."):
                text = text.replace(prefix, "")
            return text

        return {key: strip(str(value)) for key, value in annotations.items()}

    def test_annotations_match(self):
        for name in sorted(self.typed_dict_names(self.reference)):
            with self.subTest(type=name):
                self.assertEqual(
                    self.normalize(getattr(self.reference, name).__annotations__),
                    self.normalize(getattr(contracts, name).__annotations__),
                )

    def test_amounts_are_integers_not_strings(self):
        """2026-09-14 的破坏性变更：金额一律整数。"""
        self.assertEqual(str(contracts.Price.__annotations__["amount"]), "int | None")
        self.assertEqual(
            str(contracts.HardConstraints.__annotations__["max_price"]), "int | None"
        )

    def test_price_scope_was_replaced_by_listing_scope(self):
        self.assertNotIn("scope", contracts.Price.__annotations__)
        self.assertIn("listing_scope", contracts.ListingAttributes.__annotations__)

    def test_run_context_carries_user_id(self):
        self.assertIn("user_id", contracts.RunContext.__annotations__)


if __name__ == "__main__":
    unittest.main()
