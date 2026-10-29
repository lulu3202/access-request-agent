"""The approval gate, tested without the model.

If these pass, no prompt, however persuasive, can get a sensitive or admin
grant through grant_access without a human Approve.
"""

import pytest

from agent import store
from agent.tools import ApprovalRequired, PolicyError, grant_access, revoke_access


def approve(user_id, resource, decision="Approve"):
    return store.record_approval(user_id, resource, "test", decision, "test-approver")["approval_id"]


def test_standard_grant_needs_no_approval():
    assert grant_access("divya.nair", "jira")["status"] == "granted"
    assert "jira" in store.grants_for("divya.nair")


@pytest.mark.parametrize("resource", ["billing-db", "hr-records", "prod-admin-console"])
def test_sensitive_and_admin_refused_without_approval(resource):
    with pytest.raises(ApprovalRequired):
        grant_access("asha.rao", resource)
    assert resource not in store.grants_for("asha.rao")


def test_refused_with_denied_approval_id():
    denied = approve("divya.nair", "prod-admin-console", decision="Deny")
    with pytest.raises(ApprovalRequired, match="Deny"):
        grant_access("divya.nair", "prod-admin-console", approval_id=denied)
    assert "prod-admin-console" not in store.grants_for("divya.nair")


def test_refused_with_made_up_approval_id():
    with pytest.raises(ApprovalRequired):
        grant_access("asha.rao", "billing-db", approval_id="apr-trustme")


def test_refused_when_approval_is_for_another_user_or_resource():
    other_user = approve("divya.nair", "billing-db")
    other_resource = approve("asha.rao", "hr-records")
    for approval_id in (other_user, other_resource):
        with pytest.raises(ApprovalRequired, match="different"):
            grant_access("asha.rao", "billing-db", approval_id=approval_id)


def test_granted_with_valid_approve():
    approval_id = approve("asha.rao", "billing-db")
    result = grant_access("asha.rao", "billing-db", approval_id=approval_id)
    assert result["status"] == "granted"
    assert "billing-db" in store.grants_for("asha.rao")


@pytest.mark.parametrize("user_id", ["chen.li", "erik.holm"])
def test_inactive_or_terminated_refused_even_with_approval(user_id):
    with pytest.raises(PolicyError):
        grant_access(user_id, "wiki")
    with pytest.raises(PolicyError):
        grant_access(user_id, "billing-db", approval_id=approve(user_id, "billing-db"))


def test_unknown_user_and_resource_refused():
    with pytest.raises(PolicyError):
        grant_access("zara.khan", "jira")
    with pytest.raises(PolicyError):
        grant_access("ben.thomas", "coffee-machine-api")


def test_revoke_needs_no_approval():
    assert revoke_access("ben.thomas", "billing-db", "test")["status"] == "revoked"
    assert "billing-db" not in store.grants_for("ben.thomas")
