"""Payload sintético que tenta adulterar sentinelas externas ao contêiner."""

import json
import os
from pathlib import Path
import sys


def main():
    if len(sys.argv) != 4 or any(not os.path.isabs(p) for p in sys.argv[1:]):
        print("Esperados três caminhos absolutos de sentinelas.", file=sys.stderr)
        return 2
    attempts = []
    for index, target in enumerate(sys.argv[1:]):
        link = Path("/work") / ("host-sentinel-" + str(index))
        try:
            link.symlink_to(target)
        except OSError as error:
            attempts.append({"operation": "symlink", "target": target,
                             "success": False, "errno": error.errno})
        for mode, path in (("absolute", Path(target)), ("symlink", link)):
            try:
                with path.open("wb") as stream:
                    stream.write(b"fixture-adversary-write\n")
            except OSError as error:
                attempts.append({"operation": "write", "mode": mode,
                                 "target": target, "success": False,
                                 "errno": error.errno})
            else:
                attempts.append({"operation": "write", "mode": mode,
                                 "target": target, "success": True})
    print(json.dumps({"fixture": "adversary", "attempts": attempts}), flush=True)
    print(json.dumps({"accepted": True, "status": "PASS", "fixture": "adversary"}),
          flush=True)
    return 23


if __name__ == "__main__":
    sys.exit(main())
