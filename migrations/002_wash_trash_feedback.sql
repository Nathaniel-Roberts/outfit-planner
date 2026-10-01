-- In the wash, trash, and wear feedback.
ALTER TABLE outfits ADD COLUMN unavailable_until TEXT;   -- ISO date; hidden from Today until then
ALTER TABLE outfits ADD COLUMN deleted_at TEXT;          -- set when moved to the bin
ALTER TABLE wear_log ADD COLUMN feedback TEXT CHECK (feedback IN ('hot', 'cold', 'ok', 'skip'));
CREATE INDEX outfits_deleted_idx ON outfits(user_id, deleted_at);
