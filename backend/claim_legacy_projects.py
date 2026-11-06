"""One-off: claim legacy projects (user_id == '') to a Google account.

Projects created before the tenant-isolation fix have an empty owner and are
orphaned — they exist in the DB and on disk but are invisible through the API.
This script claims them to the account you specify.

Usage:
    # show what WOULD be claimed (no changes)
    python claim_legacy_projects.py --email you@gmail.com

    # commit the claim
    python claim_legacy_projects.py --email you@gmail.com --apply

    # claim by google_sub instead of email
    python claim_legacy_projects.py --sub 11432... --apply

    # only claim specific jobs
    python claim_legacy_projects.py --email you@gmail.com --job abc123 --job def456 --apply

Safety:
    - Dry-run by default; nothing is written without --apply.
    - The target user must already exist in the `users` table (they must have
      signed in at least once), or the script refuses.
    - Publishes belonging to the claimed jobs are claimed along with them.
"""
import argparse
import json
import sys
import time

import db

OWNERLESS = ""


def _user_by_email(email: str) -> dict | None:
    with db._lock, db._connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
    return dict(row) if row else None


def orphaned_projects(job_ids: list | None = None) -> list:
    with db._lock, db._connect() as conn:
        if job_ids:
            q = ",".join("?" for _ in job_ids)
            rows = conn.execute(
                f"""SELECT p.*, COUNT(c.idx) AS clip_count
                    FROM projects p LEFT JOIN clips c ON c.job_id = p.job_id
                    WHERE p.user_id = '' AND p.job_id IN ({q})
                    GROUP BY p.job_id ORDER BY p.created_at DESC""",
                job_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT p.*, COUNT(c.idx) AS clip_count
                   FROM projects p LEFT JOIN clips c ON c.job_id = p.job_id
                   WHERE p.user_id = ''
                   GROUP BY p.job_id ORDER BY p.created_at DESC"""
            ).fetchall()
    return [dict(r) for r in rows]


def claim(projects: list, user_id: str) -> int:
    """Assign `user_id` to the projects (and their publish rows)."""
    job_ids = [p["job_id"] for p in projects]
    with db._lock, db._connect() as conn:
        for jid in job_ids:
            conn.execute("UPDATE projects SET user_id = ? WHERE job_id = ?",
                         (user_id, jid))
        if job_ids:
            q = ",".join("?" for _ in job_ids)
            conn.execute(
                f"UPDATE publishes SET user_id = ? "
                f"WHERE job_id IN ({q}) AND user_id = ''", [user_id, *job_ids])
    return len(job_ids)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", help="target user's email (must have signed in)")
    ap.add_argument("--sub", help="target user's google_sub directly")
    ap.add_argument("--job", action="append", help="only claim this job id (repeatable)")
    ap.add_argument("--backfill-publishes", action="store_true",
                    help="also set user_id on ownerless publish rows from their "
                         "project's owner (idempotent, needs no target account)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write changes (default: dry-run)")
    args = ap.parse_args()

    if not (args.email or args.sub) and not args.backfill_publishes:
        ap.error("pass --email/--sub to claim projects, or --backfill-publishes")

    user = None
    if args.email or args.sub:
        if args.sub:
            user = db.get_user(args.sub)
        else:
            user = _user_by_email(args.email)
        if not user:
            print(f"❌ No user found for '{args.email or args.sub}' — the account "
                  f"must sign in to the app once first.", file=sys.stderr)
            sys.exit(1)
        print(f"🎯 Target account: {user.get('email') or args.sub} "
              f"(google_sub: {user['google_sub']})")

    # ── publish-row backfill (independent of project claiming) ───────────
    with db._lock, db._connect() as conn:
        orphan_pub = conn.execute(
            """SELECT COUNT(*) FROM publishes p
               WHERE p.user_id = '' AND EXISTS
                 (SELECT 1 FROM projects pr WHERE pr.job_id = p.job_id
                   AND pr.user_id != '')"""
        ).fetchone()[0]
    if args.backfill_publishes:
        if args.apply:
            with db._lock, db._connect() as conn:
                cur = conn.execute(
                    """UPDATE publishes SET user_id =
                         (SELECT pr.user_id FROM projects pr
                          WHERE pr.job_id = publishes.job_id)
                       WHERE user_id = '' AND EXISTS
                         (SELECT 1 FROM projects pr WHERE pr.job_id = publishes.job_id
                           AND pr.user_id != '')""")
            print(f"✅ Backfilled owner on {cur.rowcount} publish row(s).")
        else:
            print(f"🧪 Dry-run: would backfill owner on {orphan_pub} publish row(s) "
                  f"(run with --apply to write).")

    projects = orphaned_projects(args.job)
    if not projects:
        print("✅ No ownerless (legacy) projects to claim.")
        return

    print(f"\n📋 {len(projects)} ownerless project(s) found:")
    for p in projects:
        created = time.strftime("%Y-%m-%d %H:%M",
                                time.localtime(p.get("created_at") or 0))
        print(f"   • {p['job_id']}  {p.get('clip_count', 0)} clips  "
              f"'{p.get('source_label') or '—'[:24]}'  ({created})")

    if args.job:
        wanted = set(args.job)
        missing = wanted - {p["job_id"] for p in projects}
        if missing:
            print(f"⚠️  Not found / already owned: {', '.join(sorted(missing))}",
                  file=sys.stderr)

    if not args.apply:
        print(f"\n🧪 Dry-run — nothing changed. Re-run with --apply to claim "
              f"these {len(projects)} project(s) to {user['google_sub']}.")
        return

    n = claim(projects, user["google_sub"])
    print(f"\n✅ Claimed {n} project(s) (and their publish rows) to "
          f"{user['google_sub']}.")


if __name__ == "__main__":
    main()
