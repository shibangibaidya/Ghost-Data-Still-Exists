"""Resolves a Request Email to a Subject and the Identifier Set known at a point in time."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from deletion_audit.dataset import IdentityChange, IdentityRow
from deletion_audit.model import IdentifierSet, Subject

_FIELDS = ("email", "phone", "email_hash")


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def normalize_hash(value: str) -> str:
    return value.strip().lower()


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


_NORMALIZERS = {"email": normalize_email, "phone": normalize_phone, "email_hash": normalize_hash}


class IdentityDirectory:
    def __init__(self, rows: Iterable[IdentityRow], changes: Iterable[IdentityChange]) -> None:
        self._rows = {row.user_id: row for row in rows}
        self._changes: dict[str, list[IdentityChange]] = defaultdict(list)
        for change in sorted(changes, key=lambda item: (item.event_time, item.audit_id)):
            self._changes[change.user_id].append(change)
        self._users_by_email: dict[str, set[str]] = defaultdict(set)
        for row in self._rows.values():
            self._users_by_email[normalize_email(row.email)].add(row.user_id)
        for user_changes in self._changes.values():
            for change in user_changes:
                if change.field_name == "email":
                    for value in (change.old_value, change.new_value):
                        self._users_by_email[normalize_email(value)].add(change.user_id)

    def resolve(self, email: str, as_of: datetime) -> Subject | None:
        """The Subject whose current or Historical Identifiers include `email`.

        Only Identifiers held at or before `as_of` are included. When an email belongs to
        several Subjects, the one that held it at `as_of` wins and the others are reported.
        """
        key = normalize_email(email)
        candidates = sorted(self._users_by_email.get(key, ()))
        if not candidates:
            return None
        holding = [user_id for user_id in candidates if key in self._known(user_id, as_of)["email"]]
        chosen = (holding or candidates)[0]
        others = tuple(user_id for user_id in candidates if user_id != chosen)
        return Subject(chosen, self._identifier_set(chosen, as_of), ambiguous_with=others)

    def _identifier_set(self, user_id: str, as_of: datetime) -> IdentifierSet:
        known = self._known(user_id, as_of)
        row = self._rows.get(user_id)
        return IdentifierSet(
            user_id=user_id,
            emails=frozenset(known["email"]),
            phones=frozenset(known["phone"]),
            email_hashes=frozenset(known["email_hash"]),
            names=frozenset({normalize_name(row.name)} if row else ()),
        )

    def _known(self, user_id: str, as_of: datetime) -> dict[str, set[str]]:
        """Values held at any time up to `as_of`: the value before the first recorded change,
        every value a change introduced at or before `as_of`, and the current snapshot value
        when no change happened after `as_of`."""
        row = self._rows.get(user_id)
        known: dict[str, set[str]] = {}
        for name in _FIELDS:
            history = [change for change in self._changes.get(user_id, []) if change.field_name == name]
            values = {history[0].old_value} if history else set()
            values |= {change.new_value for change in history if change.event_time <= as_of}
            if row is not None and all(change.event_time <= as_of for change in history):
                values.add(getattr(row, name))
            known[name] = {_NORMALIZERS[name](value) for value in values if value}
        return known
