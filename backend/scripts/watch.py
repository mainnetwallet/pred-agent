"""scripts/watch.py — live and upcoming fixtures with books.

CLI for Phase 2: shows fixtures, best bid/ask, spread, game_status, data age.
"""

from __future__ import annotations

import sys

from pred_agent.scanner import Scanner
from pred_agent.store.models import Fixture, Market
from pred_agent.store.session import create_session_manager


def main() -> int:
    database_url = "sqlite:///./pred_agent.db"
    engine, session_factory = create_session_manager(database_url)
    session = session_factory()

    scanner = Scanner()
    print("Scanning PRED discovery feed...")
    results = scanner.scan_active_moneyline()
    count = scanner.upsert_to_db(session, results)
    print(f"Upserted {count} fixture/market rows.")

    print("\nActive moneyline fixtures:")
    fixtures = session.query(Fixture).all()
    for fx in fixtures[:20]:
        markets = session.query(Market).filter_by(parent_market_id=fx.parent_market_id).all()
        for m in markets:
            if m.ltp:
                print(f"  {fx.home_team} vs {fx.away_team} | {m.outcome_name} | LTP={m.ltp} | status={m.status}")
    print(f"\nTotal fixtures tracked: {len(fixtures)}")
    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
