"""Serialize service operations that enter the non-thread-safe NetCDF C library.

The lock covers lazy reads, metadata discovery, writes and handle lifetimes
inside a request/job. Health, job polling and catalog filters remain separate.
It is process-wide because different application instances share libnetcdf.
"""
from functools import wraps
from inspect import signature
from threading import RLock
from fastapi.routing import APIRoute

NETCDF_IO_LOCK = RLock()


def serialized_io(function):
    if getattr(function, "_serialized_io", False):
        return function
    @wraps(function)
    def guarded(*args, **kwargs):
        with NETCDF_IO_LOCK:
            return function(*args, **kwargs)
    guarded.__signature__ = signature(function, eval_str=True)
    guarded._serialized_io = True
    return guarded


class NetCDFRoute(APIRoute):
    """Guard endpoints at registration, including FastAPI's lazy inclusions."""
    def __init__(self, path, endpoint, **kwargs):
        super().__init__(path, serialized_io(endpoint), **kwargs)
