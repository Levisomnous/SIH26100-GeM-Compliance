from backend import database, recommendations

def run():
    conn = database.connect(read_only=True)
    bids = database.fetch_bids(conn)
    print('found bids:', len(bids))
    if not bids:
        print('no bids to test')
        return
    # pick first bid
    bid = bids[0]
    print('testing bid id', bid['id'])
    rec = recommendations.recommend(bid.get('report') or {})
    print('recommendation:', rec)
    conn.close()

    # test decision persistence
    conn_w = database.connect(read_only=False)
    try:
        bid_id = bid['id']
        print('making decision on', bid_id)
        report_before = database.fetch_bid(conn_w, bid_id)
        print('report before has officer_decision?', 'officer_decision' in (report_before.get('report') or {}))
        audit_before = database.fetch_audit(conn_w)
        print('audit count before', len(audit_before))
        res = database.update_bid_decision(conn_w, bid_id, 'approve', 'UnitTestOfficer', 'This is a test approval with sufficient justification text.')
        print('updated report officer_decision:', res.get('officer_decision'))
        audit_entry = database.append_audit(conn_w, 'UnitTestOfficer', 'OFFICER_APPROVED', bid_id, { 'decision':'approve', 'justification':'This is a test approval with sufficient justification text.' })
        print('appended audit seq', audit_entry.get('seq'))
        audit_after = database.fetch_audit(conn_w)
        print('audit count after', len(audit_after))
    finally:
        conn_w.close()

if __name__ == '__main__':
    run()
