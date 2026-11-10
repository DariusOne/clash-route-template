from __future__ import annotations

import contextlib
import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import template_sync as t


def fixture(*names, target=None):
    return {
        "proxy-groups": [
            {"name": t.MANUAL, "type": "select"},
            {"name": "测试地区节点", "type": "url-test"},
            *[{"name": name, "type": "select", "use": ["private-placeholder"]} for name in names],
        ],
        "proxies": [{"name": "must-not-copy"}],
        "proxy-providers": {"private-placeholder": {"url": "https://example.invalid/private"}},
        "dns": {"nameserver": ["https://example.invalid/dns"]},
        "rules": ["RULE-SET,sample,DIRECT", "MATCH," + (target or names[-1])],
        "rule-providers": {"sample": {"type": "http", "behavior": "domain",
                                    "url": "https://example.com/public.yaml"}},
    }


class TemplateTests(unittest.TestCase):
    def test_public_projection_keeps_fixed_direct_only(self):
        doc = t.normalize(fixture("业务甲", "🍎 苹果服务"))
        self.assertEqual([g["name"] for g in doc["proxy-groups"]], ["🍎 苹果服务", "业务甲", t.DIRECT_GROUP])
        self.assertEqual(doc["proxy-groups"][0]["default-selected"], t.DIRECT_GROUP)
        self.assertEqual(doc["proxy-groups"][0]["proxies"], [t.MANUAL, t.DIRECT_GROUP])
        self.assertTrue({"proxies", "proxy-providers", "dns"}.isdisjoint(doc))

    def test_existing_order_and_new_business_group(self):
        old = t.normalize(fixture("业务甲", "业务乙"))
        new = t.normalize(fixture("新业务二", "业务乙", "新业务一", "业务甲"), old)
        self.assertEqual([g["name"] for g in new["proxy-groups"]],
                         ["业务甲", "业务乙", "新业务二", "新业务一", t.DIRECT_GROUP])

    def test_valid_removal_and_rename_of_final_match(self):
        old = t.normalize(fixture("业务甲", "🐟 漏网之鱼"))
        new = t.normalize(fixture("业务甲", "新兜底"), old)
        self.assertEqual(new["rules"][-1], "MATCH,新兜底")
        self.assertNotIn("🐟 漏网之鱼", [g["name"] for g in new["proxy-groups"]])

    def test_removed_policy_referenced_leaves_output_unchanged(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "out.yaml"
            before = t.encoded(t.normalize(fixture("业务甲", "业务乙")))
            target.write_bytes(before)
            bad = root / "bad.yaml"
            bad.write_text(yaml.safe_dump(fixture("业务甲", target="业务乙"), allow_unicode=True), encoding="utf-8")
            with patch.object(sys, "argv", ["sync", "--source", str(bad), "--output", str(target)]), \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                t.main()
            self.assertEqual(target.read_bytes(), before)

    def test_surviving_group_cannot_reference_removed_business_group(self):
        old = t.normalize(fixture("业务甲", "业务乙"))
        candidate = fixture("业务甲")
        candidate["proxy-groups"][-1]["proxies"] = ["业务乙"]
        with self.assertRaisesRegex(t.InvalidTemplate, "仍引用已删除"):
            t.normalize(candidate, old)

    def test_provider_and_subrule_references(self):
        source = fixture("业务甲")
        source["rules"].insert(0, "SUB-RULE,(AND,((RULE-SET,sample),(NETWORK,TCP))),x")
        source["sub-rules"] = {"x": ["MATCH,业务甲"]}
        t.normalize(source)
        invalid = copy.deepcopy(source)
        invalid["rules"][0] = invalid["rules"][0].replace("RULE-SET,sample", "RULE-SET,missing")
        with self.assertRaisesRegex(t.InvalidTemplate, "规则集"):
            t.normalize(invalid)
        invalid = copy.deepcopy(source)
        invalid["sub-rules"]["x"] = ["SUB-RULE,(NETWORK,TCP),x", "MATCH,业务甲"]
        with self.assertRaisesRegex(t.InvalidTemplate, "循环"):
            t.normalize(invalid)
        invalid = copy.deepcopy(source)
        invalid["sub-rules"]["x"] = ["MATCH,旧业务"]
        with self.assertRaisesRegex(t.InvalidTemplate, "策略组"):
            t.normalize(invalid)

    def test_duplicate_names_and_private_resources_rejected(self):
        with self.assertRaisesRegex(t.InvalidTemplate, "重复"):
            t.normalize(fixture("业务甲", "业务甲"))
        with self.assertRaisesRegex(t.InvalidTemplate, "重复"):
            t.read_yaml(b"rules: []\nrules: []\n")
        source = fixture("业务甲")
        source["rule-providers"]["sample"]["url"] += "?token=demo"
        with self.assertRaisesRegex(t.InvalidTemplate, "公网 HTTPS"):
            t.normalize(source)

    def test_second_normalization_is_identical(self):
        first = t.normalize(fixture("业务甲", "Ⓜ️ 微软服务"))
        self.assertEqual(t.encoded(t.normalize(t.read_yaml(t.encoded(first)), first)), t.encoded(first))


if __name__ == "__main__":
    unittest.main()
