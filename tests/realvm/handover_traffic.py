"""Account for a real probe on a node whose cumulative traffic store survives a restore."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from meridian.core.clients import ClientDetail
from meridian.host_runtime import CONTROL_SERVICE_STATE_DIR
from tests.realvm.upgrade_support import SubscriptionSnapshot, VerificationError, parse_userinfo

if TYPE_CHECKING:
    from tests.realvm.upgrade_verifier import UpgradeVerifier

# This topology has one node. Its latest report and bob's own counters are read in one snapshot.
QUERY = """
SELECT r.counter_epoch, r.seq, COALESCE(c.uplink_bytes,0), COALESCE(c.downlink_bytes,0)
FROM traffic_reports r
LEFT JOIN traffic_counters c ON c.node_id=r.node_id AND c.counter_epoch=r.counter_epoch
 AND c.user_id=(SELECT user_id FROM users WHERE username='bob')
ORDER BY r.arrival DESC LIMIT 1
"""


@dataclass(frozen=True)
class Report:
    epoch: str
    seq: int
    upload: int
    download: int


def read_report(run: UpgradeVerifier) -> Report | None:
    database = f"file:{CONTROL_SERVICE_STATE_DIR}/control.db?mode=ro"
    script = (
        f"import json,sqlite3; db=sqlite3.connect({database!r},uri=True); "
        f"print(json.dumps(db.execute({QUERY!r}).fetchone())); db.close()"
    )
    result = run.execute(
        "Read the node's accepted traffic counters", ["ssh", f"root@{run.ip}", "python3 -c " + shlex.quote(script)]
    )
    row = json.loads(result.stdout)
    return Report(*row) if row is not None else None


def await_report(run: UpgradeVerifier, *, newer: Report | None = None, replay: Report | None = None) -> Report:
    found = None

    def ready() -> bool:
        nonlocal found
        found = read_report(run)
        if found is None:
            return False
        expected = newer or replay
        if expected and found.epoch != expected.epoch:
            if replay:  # an older snapshot can still show a report from a previous epoch
                return False
            raise VerificationError("The disposable node replaced its traffic counter epoch.")
        if newer:
            return found.seq > newer.seq
        if replay and found.seq >= replay.seq:
            if (found.upload, found.download) != (replay.upload, replay.download):
                raise VerificationError("The restored node did not replay the observed per-direction traffic totals.")
        return replay is None or found.seq >= replay.seq

    run.wait_for("A fresh node traffic report for the probe", ready, seconds=150)
    assert found is not None
    return found


def replay_expectations(
    start: Report, end: Report, people: dict[str, ClientDetail], docs: dict[tuple[str, str], SubscriptionSnapshot]
) -> tuple[dict[str, ClientDetail], dict[tuple[str, str], SubscriptionSnapshot]]:
    up, down = end.upload - start.upload, end.download - start.download
    if end.epoch != start.epoch or end.seq <= start.seq or min(up, down) < 0 or up + down == 0:
        raise VerificationError("The node did not account for the original person's first transport probe.")
    bob = people["bob"]
    updated = bob.model_copy(
        update={
            "traffic_used_bytes": bob.traffic_used_bytes + up + down,
            "lifetime_traffic_bytes": bob.lifetime_traffic_bytes + up + down,
        }
    )
    expected_docs = dict(docs)
    for key, snapshot in docs.items():
        if key[0] != "bob":
            continue
        # Substitute only the two attested numbers, preserving the exact header layout and original body.
        original = parse_userinfo(snapshot.userinfo)
        if original["upload"] + original["download"] != bob.traffic_used_bytes:
            raise VerificationError("The original person's period usage does not match the subscription.")
        increments = {"upload": up, "download": down}
        header = re.sub(
            r"\b(upload|download)=([0-9]+)\b", lambda m: f"{m[1]}={int(m[2]) + increments[m[1]]}", snapshot.userinfo
        )
        expected_docs[key] = replace(snapshot, userinfo=header)
    return {**people, "bob": updated}, expected_docs
