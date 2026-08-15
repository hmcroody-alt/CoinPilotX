from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_group_activation_reuses_canonical_membership_and_audit_tables():
    create = BOT[BOT.index('def api_pulse_group_create():'):BOT.index('def api_pulse_group_join():')]
    assert "pulse_group_members" in create
    assert "pulse_group_roles" in create
    assert "pulse_group_creation_attempts" in create
    assert "Group creation limit reached" in create


def test_room_activation_reuses_text_conversations_without_rtc_capabilities():
    start = BOT.index('def api_pulse_communications_rooms():')
    end = BOT.index('@webhook_app.route("/api/pulse/communications/groups"', start)
    route = BOT[start:end]
    assert "pulse_conversations" in route
    assert "pulse_conversation_participants" in route
    assert '"voice": False' not in route  # media capabilities are never granted by creation
    assert "Room creation limit reached" in route


def test_private_room_join_requires_invitation_and_owner_cannot_leave():
    join_start = BOT.index("def api_pulse_community_room_join")
    leave_end = BOT.index('@webhook_app.route("/api/pulse/communications/groups"', join_start)
    routes = BOT[join_start:leave_end]
    assert "private room requires an invitation" in routes
    assert "instead of leaving it ownerless" in routes
    assert "status='deleted'" in routes


def test_private_group_invitation_can_be_accepted_by_joining():
    start = BOT.index("def pulse_group_join_common")
    end = BOT.index("def api_pulse_group_join_slug", start)
    route = BOT[start:end]
    assert "status='pending'" in route
    assert "status='accepted'" in route
    assert "group_invite_accepted" in route


def test_group_core_management_is_not_hidden_behind_advanced_content_flag():
    for function_name in (
        "api_pulse_group_chat_open",
        "api_pulse_group_invite",
        "api_pulse_group_update",
        "api_pulse_group_member_role",
        "api_pulse_group_ban_member",
        "api_pulse_group_delete_id",
    ):
        start = BOT.index(f"def {function_name}")
        next_route = BOT.find("@webhook_app.route", start + 10)
        source = BOT[start:next_route if next_route > start else None]
        assert "GROUPS_ADVANCED_MODE" not in source


def test_admin_can_find_and_lifecycle_manage_groups_and_rooms_with_audit():
    assert 'route("/api/admin/community", methods=["GET"])' in BOT
    assert 'route("/api/admin/community/<kind>/<int:target_id>", methods=["POST"])' in BOT
    assert 'require_admin_api("pulse.moderate")' in BOT
    assert 'log_admin_audit(admin.get("id"), f"community.{kind}.{action}"' in BOT
