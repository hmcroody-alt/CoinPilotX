import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import marketplace_goods_policy as goods

def test_allowed_category_passes():
    out = goods.evaluate({"category": "Education", "title": "Python workbook"})
    assert out["decision"] == "ALLOWED"
    assert out["policy_version"] == "MARKETPLACE_GOODS_V1"

def test_prohibited_category_is_blocked():
    out = goods.evaluate({"category": "Weapons", "title": "Collector item"})
    assert out["decision"] == "PROHIBITED"
    assert not out["compliance_ready"]

def test_restricted_category_fails_closed_without_compliance_infrastructure():
    out = goods.evaluate({"category": "Medical Devices", "title": "Device"})
    assert out["decision"] == "MANUAL_REVIEW_REQUIRED"
    assert not goods.purchasable({"category": "Medical Devices"})

def test_high_confidence_counterfeit_signal_is_blocked():
    out = goods.evaluate({"category": "Fashion", "description": "Replica branded handbag"})
    assert out["decision"] == "PROHIBITED"
    assert out["reason_code"] == "counterfeit_goods"
