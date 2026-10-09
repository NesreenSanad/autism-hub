"""Doctor collector: pulls providers from allowed sources into Supabase.

Sources turn their data into normalised records (collector.normalize);
collector.store saves them, detects changes and queues risky ones for review.
"""
