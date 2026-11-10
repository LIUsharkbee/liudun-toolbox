# -*- coding: utf-8 -*-
"""revit 兼容层：doc / uidoc / HOST_APP / Transaction / get_selection"""
from Autodesk.Revit.DB import Transaction as _RevitTransaction

doc = None
uidoc = None
HOST_APP = None
VERSION_MAJOR = 2020


class Transaction(object):
    """with revit.Transaction(u"名字"): ..."""

    def __init__(self, name=u"Transaction", doc=None, *args, **kwargs):
        self._name = name
        self._doc = doc if doc is not None else globals()["doc"]
        self._transaction = None

    def __enter__(self):
        self._transaction = _RevitTransaction(self._doc, self._name)
        self._transaction.Start()
        return self._transaction

    def __exit__(self, exc_type, exc_value, tb):
        if self._transaction is None:
            return False
        if exc_type is None:
            self._transaction.Commit()
        else:
            self._transaction.RollBack()
        return False


def get_selection():
    if uidoc is None:
        return []
    return uidoc.Selection.GetElementIds()


def get_active_view():
    if uidoc is None:
        return None
    return uidoc.ActiveView


def get_links():
    return []
