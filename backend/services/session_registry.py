"""
session_registry.py — минимальный реестр веб-сессий для предупреждения об истечении.

Зачем он вообще нужен. Сессия у нас stateless: в cookie лежит HMAC-токен с `exp`, и больше
о ней нигде ничего нет. Из токена нельзя получить ответы «кто залогинен» и «когда истекает»,
а значит нельзя и предупредить человека за сутки до истечения — запрос к поллеру был бы
к пустой таблице.

Что здесь есть и чего НЕТ:
  * есть только то, что нужно уведомлению: `expires_at`, `last_seen_at`, `notified_at`;
  * НЕТ проверки прав и НЕТ валидации токенов — это по-прежнему делает auth.verify_session_token.
    Реестр не участвует в решении «пускать или нет», иначе он стал бы вторым источником правды
    об аутентификации.

Запись троттлится (по умолчанию не чаще раза в час на пользователя): сессия продлевается на
каждом авторизованном запросе, и писать строку каждый раз означало бы запись в SQLite на каждый
HTTP-вызов.
"""

import logging
import time
from datetime import datetime, timedelta

import database

logger = logging.getLogger("contextus.session_registry")

#: Как часто разрешено переписывать строку сессии для одного пользователя (секунды)
WRITE_THROTTLE_SECONDS = 3600

#: in-memory отметка последней записи: {user_id: (monotonic, expires_at)}
_last_write: dict[int, tuple[float, datetime]] = {}


def _session():
    """Фабрика сессий по позднему связыванию — тесты подменяют database.SessionLocal."""
    return database.SessionLocal()


def touch(user_id: int, expires_at: datetime) -> None:
    """
    Фиксирует, что у пользователя есть живая сессия до `expires_at`.

    Троттлинг в памяти: если для пользователя писали меньше часа назад И срок почти не сдвинулся,
    запись пропускается ЦЕЛИКОМ (включая чтение БД) — иначе продление сессии на каждом запросе
    превращалось бы в обращение к SQLite на каждый HTTP-вызов.
    """
    if not user_id:
        return

    now_mono = time.monotonic()
    last = _last_write.get(user_id)
    if last is not None:
        last_mono, last_expires = last
        shifted = abs((last_expires - expires_at).total_seconds()) >= WRITE_THROTTLE_SECONDS
        if not shifted and now_mono - last_mono < WRITE_THROTTLE_SECONDS:
            return

    db = _session()
    try:
        from models import WebSession

        row = _get(db, user_id)
        if row is None:
            row = WebSession(user_id=user_id, expires_at=expires_at, last_seen_at=datetime.utcnow())
            db.add(row)
        else:
            row.expires_at = expires_at
            row.last_seen_at = datetime.utcnow()
            if row.notified_at:
                # Сессия продлена после предупреждения — про следующий срок предупредим заново
                row.notified_at = None
        db.commit()
        _last_write[user_id] = (now_mono, expires_at)
    except Exception as e:
        logger.error(f"Не удалось записать сессию пользователя {user_id}: {e}")
    finally:
        db.close()


def _get(db, user_id: int):
    from models import WebSession

    return db.query(WebSession).filter(WebSession.user_id == user_id).first()


def expiring_within(hours: int = 24) -> list[dict]:
    """
    Сессии, которым осталось меньше `hours` и о которых ещё не предупреждали.

    Возвращает [{'user_id': int, 'expires_at': datetime}] — поллер сам решает, как уведомить.
    """
    from models import WebSession

    deadline = datetime.utcnow() + timedelta(hours=hours)
    db = _session()
    try:
        rows = (
            db.query(WebSession)
            .filter(WebSession.expires_at <= deadline, WebSession.expires_at > datetime.utcnow())
            .filter(WebSession.notified_at.is_(None))
            .all()
        )
        return [{"user_id": r.user_id, "expires_at": r.expires_at} for r in rows]
    finally:
        db.close()


def mark_notified(user_id: int) -> None:
    db = _session()
    try:
        row = _get(db, user_id)
        if row is not None:
            row.notified_at = datetime.utcnow()
            db.commit()
    except Exception as e:
        logger.error(f"Не удалось отметить уведомление для {user_id}: {e}")
    finally:
        db.close()


def forget(user_id: int) -> None:
    """Убирает запись (logout или удаление пользователя)."""
    db = _session()
    try:
        row = _get(db, user_id)
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()
    _last_write.pop(user_id, None)


def reset_throttle_cache() -> None:
    """Сброс in-memory кеша троттлинга (для тестов и при перезапуске)."""
    _last_write.clear()
