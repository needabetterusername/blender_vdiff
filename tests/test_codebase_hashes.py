from vdiff_core.blenddiff import BlendDiff

import pytest, json

# Expected values
_expected_result_codehash = {'codebase_hash': 'b03bfc6c2cba406dab44afbc31d982a9850083218e6820d661ddf39b2400ab5a'}
_expected_result_policyhash = {'policy_hash': '63cc78ed160936589d09b1e654755433e681160af65ccc8a635f8a70d55eacb4'}

# Test for the _get_codebase_hash function
@pytest.mark.xfail(strict=False, reason="If the codebase hash changes, notify users.")
def test_get_codebase_hash():
    bd = BlendDiff()
    actual_result = bd._get_codebase_hash()
    expected_result = _expected_result_codehash
    assert actual_result == expected_result, f"Codebase hash mismatch:\nExpected: {expected_result}\nActual:   {actual_result}"
    
# Test for the _get_policy_hash function
@pytest.mark.xfail(strict=False, reason="If the default policy hash changes, notify users.")
def test_get_policy_hash():
    bd = BlendDiff()
    actual_result = bd._get_policy_metadata_json()
    actual_result = { "policy_hash": actual_result.get("policy_hash") }
    expected_result = _expected_result_policyhash
    assert actual_result == expected_result, f"Policy mismatch:\nExpected: {expected_result}\nActual:   {actual_result}"
