import os, sys
import logging

import torch
import numpy as np

from contextlib import contextmanager

logger = logging.getLogger(__name__)

import random
import numpy as np

def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

class Fork(object):
    def __init__(self, file1, file2):
        self.file1 = file1
        self.file2 = file2

    def write(self, data):
        self.file1.write(data)
        self.file2.write(data)

    def flush(self):
        self.file1.flush()
        self.file2.flush()


@contextmanager
def replace_logging_stream(file_):
    root = logging.getLogger()
    if len(root.handlers) != 1:
        print(root.handlers)
        raise ValueError("Don't know what to do with many handlers")
    if not isinstance(root.handlers[0], logging.StreamHandler):
        raise ValueError
    stream = root.handlers[0].stream
    root.handlers[0].stream = file_
    try:
        yield
    finally:
        root.handlers[0].stream = stream


@contextmanager
def replace_standard_stream(stream_name, file_):
    stream = getattr(sys, stream_name)
    setattr(sys, stream_name, file_)
    try:
        yield
    finally:
        setattr(sys, stream_name, stream)

def run_with_redirection(stdout_path, stderr_path, func):
    def func_wrapper(*args, **kwargs):
        with open(stdout_path, 'a', 1) as out_dst:
            with open(stderr_path, 'a', 1) as err_dst:
                out_fork = Fork(sys.stdout, out_dst)
                err_fork = Fork(sys.stderr, err_dst)
                with replace_standard_stream('stderr', err_fork):
                    with replace_standard_stream('stdout', out_fork):
                        with replace_logging_stream(err_fork):
                            func(*args, **kwargs)

    return func_wrapper


def _apply(obj, func):
    if isinstance(obj, (list, tuple)):
        return type(obj)(_apply(el, func) for el in obj)
    if isinstance(obj, dict):
        return {k: _apply(el, func) for k, el in obj.items()}
    return func(obj)


def torch_apply(obj, func):
    fn = lambda t: func(t) if torch.is_tensor(t) else t
    return _apply(obj, fn)


def torch_to(obj, *args, **kargs):
    return torch_apply(obj, lambda t: t.to(*args, **kargs))


def numpy_to_torch(obj):
    fn = lambda a: torch.from_numpy(a) if isinstance(a, np.ndarray) else a
    return _apply(obj, fn)


def save_weights(model, optimizer, filename):
    """
    Save all weights necessary to resume training
    """
    state = {
        'model': model.state_dict(),
        'optimizer': optimizer.state_dict(),
    }
    torch.save(state, filename)


def numpy_to_torch(obj):
    fn = lambda a: torch.from_numpy(a) if isinstance(a, np.ndarray) else a
    return _apply(obj, fn)


def torch_to_numpy(obj, copy=False):
    if copy:
        func = lambda t: t.cpu().detach().numpy().copy()
    else:
        func = lambda t: t.cpu().detach().numpy()
    return torch_apply(obj, func)


def configure_logger(name='',
        console_logging_level=logging.INFO,
        file_logging_level=None,
        log_file=None):
    """
    Configures logger
    :param name: logger name (default=module name, __name__)
    :param console_logging_level: level of logging to console (stdout), None = no logging
    :param file_logging_level: level of logging to log file, None = no logging
    :param log_file: path to log file (required if file_logging_level not None)
    :return instance of Logger class
    """

    if file_logging_level is None and log_file is not None:
        print("Didnt you want to pass file_logging_level?")

    if len(logging.getLogger(name).handlers) != 0:
        print("Already configured logger '{}'".format(name))
        return

    if console_logging_level is None and file_logging_level is None:
        return  # no logging

    logger = logging.getLogger(name)
    logger.handlers = []
    logger.setLevel(logging.DEBUG)
    format = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    if console_logging_level is not None:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(format)
        ch.setLevel(console_logging_level)
        logger.addHandler(ch)

    if file_logging_level is not None:
        if log_file is None:
            raise ValueError("If file logging enabled, log_file path is required")
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=(1048576 * 5), backupCount=7)
        fh.setFormatter(format)
        logger.addHandler(fh)

    logger.info("Logging configured!")

    return logger

@contextmanager
def numpy_seed(seed, *addl_seeds):
    """Context manager which seeds the NumPy PRNG with the specified seed and
    restores the state afterward"""
    if seed is None:
        yield
        return
    if len(addl_seeds) > 0:
        seed = int(hash((seed, *addl_seeds)) % 1e6)
    state = np.random.get_state()
    np.random.seed(seed)
    try:
        yield
    finally:
        np.random.set_state(state)