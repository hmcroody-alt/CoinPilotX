from services.business_os.construction_access import resolve_business_construction_access


def test_non_owner_fails_closed_during_construction():
    result = resolve_business_construction_access({"user_id": 22}, public_release=False, owner_ids={7})
    assert result["mode"] == "construction"
    assert result["can_access_private_business_os"] is False


def test_configured_owner_gets_developer_access():
    result = resolve_business_construction_access({"user_id": 7}, public_release=False, owner_ids={7})
    assert result["mode"] == "development"
    assert result["developer_mode"] is True
    assert result["developer_badge"] is True


def test_owner_admin_gets_developer_access_without_display_name_check():
    result = resolve_business_construction_access({"user_id": 9}, is_owner_admin=True, public_release=False, owner_ids=set())
    assert result["can_access_private_business_os"] is True


def test_display_name_alone_never_grants_access():
    result = resolve_business_construction_access(
        {"user_id": 12, "display_name": "Roody Cherie"},
        is_owner_admin=False,
        public_release=False,
        owner_ids=set(),
    )
    assert result["can_access_private_business_os"] is False


def test_public_release_opens_authenticated_access():
    result = resolve_business_construction_access({"user_id": 22}, public_release=True, owner_ids=set())
    assert result["mode"] == "public"
    assert result["can_access_private_business_os"] is True
