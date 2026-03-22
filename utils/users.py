# utils/users.py

from memory import UserManager


def get_or_create_internal_id_for_platform(
    platform, external_id, secret_username, updated_by, is_master=False, roles=None
):
    """
    Lookup or create user internal_id for a given platform and external_id, OR via secret_username.
    If both searches fail, create a new user.
    """
    # 1. Try by external_id
    internal_id = UserManager.get_or_create_user_internal_id(
        channel=platform,
        external_id=external_id,
        secret_username=secret_username,
        updated_by=updated_by,
        is_master=is_master,
        roles=roles,
    )
    if internal_id:
        return internal_id

    # 2. Try by secret_username (if provided)
    if secret_username:
        internal_id_by_name = UserManager.get_internal_id_by_secret_username(
            secret_username
        )
        if internal_id_by_name:
            return internal_id_by_name

    # 3. Create new user (if not found by either)
    return UserManager.get_or_create_user_internal_id(
        channel=platform,
        external_id=external_id,
        secret_username=secret_username,
        updated_by=updated_by,
        is_master=is_master,
        roles=roles,
    )
