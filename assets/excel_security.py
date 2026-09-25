"""Password protection for Excel files.

Exports are encrypted (Excel's normal "Encrypt with Password" — the file
asks for the password when opened), and uploads that are encrypted with the
same password are decrypted on the way in so an exported file can be edited
and re-imported without removing the password first.

The password is set / reset by the Super Admin in the app (Manage -> Export
Password) and stored encrypted in the database. EXPORT_FILE_PASSWORD in .env
is only an optional fallback used until one is set there. Never hard-coded.
"""

import io

from django.conf import settings

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # encrypted .xlsx files are OLE2 containers


class ExcelPasswordError(ValueError):
    """Password missing/wrong for an encrypted workbook."""


def _fernet():
    """Key derived from the project's SECRET_KEY, so the stored password is
    unreadable from a database dump alone."""
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def get_export_password():
    """The active export password: the one the Super Admin set in the app;
    if none has been set there, the EXPORT_FILE_PASSWORD from .env (optional
    fallback); otherwise '' (export stays blocked)."""
    try:
        from .models import ExportPassword

        row = ExportPassword.objects.order_by("-id").first()
        if row:
            return _fernet().decrypt(row.encrypted_value.encode()).decode()
    except Exception:
        # Table not migrated yet, or SECRET_KEY changed since it was saved.
        pass
    return (getattr(settings, "EXPORT_FILE_PASSWORD", "") or "").strip()


def set_export_password(new_password, user=None):
    """Super Admin set / reset. Replaces any previous password."""
    from .models import ExportPassword

    token = _fernet().encrypt(new_password.encode()).decode()
    row = ExportPassword.objects.order_by("-id").first()
    if row is None:
        row = ExportPassword()
    row.encrypted_value = token
    row.updated_by = user
    row.save()
    ExportPassword.objects.exclude(pk=row.pk).delete()
    return row


def export_password_status():
    """(is_set, source, updated_at, updated_by) for the settings page.
    Never returns the password itself."""
    from .models import ExportPassword

    row = ExportPassword.objects.select_related("updated_by").order_by("-id").first()
    if row:
        return True, "app", row.updated_at, row.updated_by
    if (getattr(settings, "EXPORT_FILE_PASSWORD", "") or "").strip():
        return True, "env", None, None
    return False, None, None, None


def encrypt_xlsx_bytes(raw_bytes, password):
    """Returns the .xlsx bytes encrypted so Excel asks for `password` to open."""
    import msoffcrypto

    out = io.BytesIO()
    msoffcrypto.OfficeFile(io.BytesIO(raw_bytes)).encrypt(password, out)
    return out.getvalue()


def decrypt_if_encrypted(raw_bytes):
    """Returns plain bytes. If `raw_bytes` is a password-protected workbook,
    it is decrypted with the export password; ExcelPasswordError is raised if
    that password isn't configured or doesn't match. Anything that isn't an
    encrypted workbook (normal .xlsx, old .xls, csv) is returned untouched."""
    if raw_bytes[:8] != OLE2_MAGIC:
        return raw_bytes
    try:
        import msoffcrypto

        office = msoffcrypto.OfficeFile(io.BytesIO(raw_bytes))
        if not office.is_encrypted():
            return raw_bytes
    except Exception:
        return raw_bytes  # plain old .xls etc.

    password = get_export_password()
    if not password:
        raise ExcelPasswordError(
            "This Excel file is password-protected, but no password is configured "
            "on the server (EXPORT_FILE_PASSWORD)."
        )
    try:
        office.load_key(password=password)
        out = io.BytesIO()
        office.decrypt(out)
        return out.getvalue()
    except Exception:
        raise ExcelPasswordError(
            "This Excel file is password-protected and the password doesn't match "
            "the register's export password."
        )
