"""Public ledger failures never contain SQL, credentials or stored payloads."""


class LedgerError(Exception):
    pass


class LedgerNotFound(LedgerError):
    pass


class LedgerConflict(LedgerError):
    pass


class LedgerReferenceError(LedgerError):
    pass


class LedgerPermissionError(LedgerError):
    pass


class LedgerStateError(LedgerError):
    pass


class LedgerStorageError(LedgerError):
    pass


class LedgerBusy(LedgerStorageError):
    pass
