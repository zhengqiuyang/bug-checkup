from mod import parse


def run(buf: bytes) -> list:
    return parse(buf)