-- Initial schema. Everything hangs off users; outfits are the primary object.

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    email         TEXT UNIQUE,              -- NULL for the local admin account
    username      TEXT UNIQUE,              -- set only for the local admin account
    display_name  TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at  TEXT
);

CREATE TABLE tags (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, name)
);

CREATE TABLE outfits (
    id                INTEGER PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name              TEXT,
    notes             TEXT,
    temp_min          REAL,
    temp_max          REAL,
    rain_ok           INTEGER NOT NULL DEFAULT 0,
    windy_ok          INTEGER NOT NULL DEFAULT 0,
    humid_ok          INTEGER NOT NULL DEFAULT 0,
    layers_removable  INTEGER NOT NULL DEFAULT 0,
    favourite         INTEGER NOT NULL DEFAULT 0,
    archived          INTEGER NOT NULL DEFAULT 0,
    last_worn_on      TEXT,                 -- ISO date, denormalised from wear_log
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX outfits_user_idx ON outfits(user_id, archived);

CREATE TABLE outfit_photos (
    id          INTEGER PRIMARY KEY,
    outfit_id   INTEGER NOT NULL REFERENCES outfits(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,              -- base name inside DATA_DIR/photos/<outfit_id>/
    width       INTEGER,
    height      INTEGER,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX outfit_photos_outfit_idx ON outfit_photos(outfit_id, sort_order);

CREATE TABLE outfit_tags (
    outfit_id INTEGER NOT NULL REFERENCES outfits(id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (outfit_id, tag_id)
);

CREATE TABLE outfit_colours (
    outfit_id  INTEGER NOT NULL REFERENCES outfits(id) ON DELETE CASCADE,
    colour     TEXT NOT NULL,               -- palette key, see app/colours.py
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (outfit_id, colour)
);

CREATE TABLE garments (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    type        TEXT,
    colour      TEXT,
    photo_filename TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, name)
);

CREATE TABLE outfit_garments (
    outfit_id  INTEGER NOT NULL REFERENCES outfits(id) ON DELETE CASCADE,
    garment_id INTEGER NOT NULL REFERENCES garments(id) ON DELETE CASCADE,
    PRIMARY KEY (outfit_id, garment_id)
);

CREATE TABLE wear_log (
    id         INTEGER PRIMARY KEY,
    outfit_id  INTEGER NOT NULL REFERENCES outfits(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    worn_on    TEXT NOT NULL,               -- ISO date
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX wear_log_user_date_idx ON wear_log(user_id, worn_on);
CREATE INDEX wear_log_outfit_idx ON wear_log(outfit_id, worn_on);

CREATE TABLE colour_rules (
    id        INTEGER PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    colour_a  TEXT NOT NULL,
    colour_b  TEXT NOT NULL,
    verdict   TEXT NOT NULL CHECK (verdict IN ('good', 'avoid')),
    UNIQUE (user_id, colour_a, colour_b)
);

CREATE TABLE weather_cache (
    key        TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    payload    TEXT NOT NULL
);

CREATE TABLE user_settings (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
