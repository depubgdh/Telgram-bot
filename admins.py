"""Adminlarni aniqlash: username yoki ID bo'yicha. Topilgan admin ID si bazada eslab qolinadi."""
import config
from db import get_str_setting, set_setting

_ids: set[int] = set(config.ADMIN_IDS)
_loaded = False


async def _load():
    global _loaded
    if _loaded:
        return
    raw = await get_str_setting("admin_ids_dyn", "")
    _ids.update(int(x) for x in raw.split(",") if x.strip().lstrip("-").isdigit())
    _loaded = True


async def check_admin(tg_id: int, username: str | None) -> bool:
    await _load()
    if tg_id in _ids:
        return True
    if username and username.lower() in config.ADMIN_USERNAMES:
        _ids.add(tg_id)
        await set_setting("admin_ids_dyn", ",".join(str(i) for i in sorted(_ids)))
        return True
    return False


async def is_admin(user) -> bool:
    """user — aiogram User obyekti."""
    return await check_admin(user.id, user.username)


async def admin_ids() -> list[int]:
    """Adminga xabar yuborish uchun barcha admin ID lar."""
    await _load()
    return sorted(_ids)
